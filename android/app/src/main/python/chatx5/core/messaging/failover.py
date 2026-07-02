"""Failover and session-health decision logic for :class:`MessagingBackend`.

These helpers decide *whether* an active peer link should fail over, *which*
transport to try next, and whether a dropped session should be reconnected.
They read live RNS path/interface state (via :mod:`chatx5.core.lan_rns`) plus
the peer's configured and expected transports, returning either an ordered
list of transport families to attempt or a ``(needs, reason)`` tuple consumed
by the reconnect loop in ``chatx5/web/peer_connect.py``.

Split out of ``backend.py`` to keep the orchestrator focused on lifecycle,
identity, and the send path. ``FailoverMixin`` holds no state of its own;
every attribute and method it references is provided by ``MessagingBackend``
and the other mixins it is combined with.
"""

import time

import RNS

from chatx5.core.lan_rns import (
    clear_paths_except_families,
    clear_paths_on_family,
    clear_peer_path_unless_family,
    detach_unhealthy_interfaces,
    lan_mesh_has_peer,
    online_interfaces,
    prune_bridged_lan_paths,
    prune_lan_path_for_peer,
    prune_stale_lan_paths,
    register_udp_peer_ip,
    reinforce_serial_peer_path,
    request_paths_for_hash,
    restore_serial_path_from_announce,
    suppress_offline_lan_transports,
    wait_for_peer_path_families,
)
from chatx5.core.messaging.constants import (
    DUAL_PATH_DISCONNECTED_COOLDOWN_S,
    DUAL_PATH_FAILOVER_COOLDOWN_S,
    DUAL_PATH_RECONNECT_MIN_IDLE_S,
    LINK_FAILOVER_GRACE_S,
    LINK_STALE_FAILOVER_IDLE_S,
    RECEIPT_FAILOVER_MIN_PENDING,
    RECEIPT_FAILOVER_TIMEOUT_S,
    SERIAL_PATH_PRIME_TIMEOUT_S,
    SERIAL_SPEED_MARGIN,
    SESSION_RECONNECT_MIN_IDLE_S,
)
from chatx5.core.rns_interfaces import (
    configured_serial_enabled,
    dedupe_serial_interfaces,
    ensure_tcp_client_to_peer,
    lan_discovery_configured,
    load_settings_interfaces,
    prune_dead_serial_interfaces,
)


def _backend():
    """Return the backend module for delegated transport predicates.

    The failover helpers resolve a handful of transport predicates
    (``physical_lan_reachable``, ``interface_family``, the LAN-enabled checks,
    serial-interface probes) through the backend module rather than importing
    them directly, so that tests can patch a single surface —
    ``chatx5.core.messaging.backend.<name>`` — and have the stub apply here
    too. This mirrors the delegation used in ``connect.py`` and ``announce.py``.
    The import is lazy because ``backend`` imports :class:`FailoverMixin`.
    """
    import chatx5.core.messaging.backend as bm

    return bm


def physical_lan_reachable():
    return _backend().physical_lan_reachable()


def interface_family(iface):
    return _backend().interface_family(iface)


def serial_interface_online():
    return _backend().serial_interface_online()


def is_serial_interface(iface):
    return _backend().is_serial_interface(iface)


def configured_udp_lan_enabled(interfaces):
    return _backend().configured_udp_lan_enabled(interfaces)


def configured_tcp_lan_enabled(interfaces):
    return _backend().configured_tcp_lan_enabled(interfaces)


class FailoverMixin:
    """Transport failover and session-reconnect decisions (see module docstring)."""

    def _dual_path_configured(self):
        interfaces = load_settings_interfaces(self.config_dir)
        return configured_serial_enabled(interfaces) and lan_discovery_configured(interfaces)

    def _session_reconnect_min_idle(self):
        peer = self.dest_hash_for(self._session_peer_hash or self.active_peer_hash or "")
        if peer and self._peer_expected_transport_families(peer) == {"serial"}:
            return SESSION_RECONNECT_MIN_IDLE_S
        if self._dual_path_configured():
            return DUAL_PATH_RECONNECT_MIN_IDLE_S
        return SESSION_RECONNECT_MIN_IDLE_S

    def _failover_cooldown(self):
        peer = self.dest_hash_for(self._session_peer_hash or self.active_peer_hash or "")
        serial_only = bool(peer and self._peer_expected_transport_families(peer) == {"serial"})
        disconnected = (
            not self.active_link
            and bool(self.dest_hash_for(self._session_peer_hash or ""))
        )
        if serial_only:
            return self._failover_cooldown_s
        if self._dual_path_configured():
            if disconnected:
                return DUAL_PATH_DISCONNECTED_COOLDOWN_S
            return DUAL_PATH_FAILOVER_COOLDOWN_S
        if disconnected:
            return DUAL_PATH_RECONNECT_MIN_IDLE_S
        return self._failover_cooldown_s

    def _link_rtt_seconds(self, link):
        if not link:
            return None
        rtt = getattr(link, "rtt", None)
        if rtt is None:
            return None
        try:
            return float(rtt)
        except Exception:
            return None

    def _serial_faster_than_lan(self, peer):
        """True when serial is confirmed up and measurably faster than LAN/UDP."""
        if not self._serial_transport_ready():
            return False
        if not physical_lan_reachable() or not self._has_online_family("udp"):
            return True
        if not self._peer_has_path_on_family(peer, "serial"):
            return False
        serial_rtt = None
        for link in self._links_for_peer(peer):
            if interface_family(self._link_attached_interface(link)) == "serial":
                serial_rtt = self._link_rtt_seconds(link)
                if serial_rtt is not None:
                    break
        if serial_rtt is None and self.active_link:
            if interface_family(self._link_attached_interface(self.active_link)) == "serial":
                serial_rtt = self._link_rtt_seconds(self.active_link)
        if serial_rtt is None:
            return False
        lan_rtt = None
        lan_fams = ("udp", "lan", "tcp")
        for link in self._links_for_peer(peer):
            fam = interface_family(self._link_attached_interface(link))
            if fam in lan_fams:
                lan_rtt = self._link_rtt_seconds(link)
                if lan_rtt is not None:
                    break
        if lan_rtt is None and self.active_link:
            fam = interface_family(self._link_attached_interface(self.active_link))
            if fam in lan_fams:
                lan_rtt = self._link_rtt_seconds(self.active_link)
        if lan_rtt is None:
            return False
        return serial_rtt * SERIAL_SPEED_MARGIN < lan_rtt

    def _failover_families_to_try(self, peer, peer_ip=None):
        """Ordered transports to attempt when reconnecting (LAN preferred unless serial is faster)."""
        raw_session = (self._session_transport or "").strip().lower()
        session_transport = self._normalize_transport(raw_session) if raw_session else None
        if session_transport == "serial" and self._serial_transport_ready():
            return ["serial"]
        if session_transport == "lan":
            interfaces = load_settings_interfaces(self.config_dir)
            udp_lan = configured_udp_lan_enabled(interfaces)
            tcp_lan = configured_tcp_lan_enabled(interfaces)
            if tcp_lan and not udp_lan:
                return ["tcp"]
            if udp_lan:
                return ["udp", "tcp"] if tcp_lan else ["udp"]
            return ["udp", "tcp", "lan"]
        if self._hub_transport_active() and self._peer_uses_hub_transport(peer):
            return ["tcp"]
        meta = self._peer_discovery_meta(peer)
        if meta and (meta.get("via") or "").strip() == "serial":
            if self._serial_transport_ready():
                return ["serial"]
            return []
        interfaces = load_settings_interfaces(self.config_dir)
        udp_lan = configured_udp_lan_enabled(interfaces)
        tcp_lan = configured_tcp_lan_enabled(interfaces)
        peer_lan_down = bool(peer_ip and self._peer_lan_recently_unreachable(peer_ip))
        lan_up = physical_lan_reachable() and not peer_lan_down and (
            (udp_lan and self._has_online_family("udp"))
            or (tcp_lan and self._has_online_family("tcp"))
        )
        serial_up = self._serial_transport_ready()
        if tcp_lan and not udp_lan:
            if lan_up and serial_up:
                order = (
                    ("serial", "tcp") if self._serial_faster_than_lan(peer) else ("tcp", "serial")
                )
            elif lan_up:
                order = ("tcp", "serial")
            elif serial_up:
                order = ("serial", "tcp")
            else:
                order = ("tcp", "serial")
        elif lan_up and serial_up:
            if self._serial_faster_than_lan(peer):
                order = ("serial", "udp", "tcp", "lan") if tcp_lan else ("serial", "udp", "lan")
            else:
                order = ("udp", "tcp", "lan", "serial") if tcp_lan else ("udp", "lan", "serial")
        elif lan_up:
            order = ("udp", "tcp", "lan", "serial") if tcp_lan else ("udp", "lan", "serial")
        elif serial_up:
            order = ("serial", "udp", "tcp", "lan") if tcp_lan else ("serial", "udp", "lan")
        else:
            order = ("udp", "tcp", "serial", "lan") if tcp_lan else ("udp", "serial", "lan")
        expected = self._peer_expected_transport_families(peer)
        seen = set()
        out = []
        for fam in order:
            if not fam or fam in seen:
                continue
            if expected:
                if fam == "serial" and "serial" not in expected:
                    continue
                if fam in ("udp", "lan", "tcp") and not (expected & {"udp", "lan", "tcp"}):
                    continue
            seen.add(fam)
            out.append(fam)
        if expected == {"serial"} and "serial" in seen:
            return ["serial"]
        return out

    def _failover_announce(self, prefer_family, peer_ip=None):
        """Refresh RNS path on the target transport before failover reconnect."""
        if prefer_family == "tcp":
            if peer_ip:
                ensure_tcp_client_to_peer(peer_ip, config_dir=self.config_dir)
            self._silent_announce(peer_ip=peer_ip)
            return
        if prefer_family == "serial":
            if self._serial_transport_ready():
                self._burst_serial_announce(count=1, force=True)
            return
        if prefer_family in ("udp", "lan"):
            if physical_lan_reachable():
                self._silent_announce(peer_ip=peer_ip, also_serial=False)
            elif self._serial_transport_ready():
                self._burst_serial_announce(count=1, force=True)
            return
        self._silent_announce(peer_ip=peer_ip if physical_lan_reachable() else None)

    def _preferred_failover_family(self, peer, attached=None, peer_ip=None):
        if self._hub_transport_active() and self._peer_uses_hub_transport(peer):
            return "tcp"
        attached = attached or self._link_attached_interface(self.active_link)
        att_fam = interface_family(attached)
        serial_up = self._serial_transport_ready()
        physical_lan = physical_lan_reachable()
        interfaces = load_settings_interfaces(self.config_dir)
        udp_lan = configured_udp_lan_enabled(interfaces)
        tcp_lan = configured_tcp_lan_enabled(interfaces)
        udp_up = self._has_online_family("udp") if udp_lan else False
        tcp_up = self._has_online_family("tcp") if tcp_lan else False
        peer_lan_down = bool(peer_ip and self._peer_lan_recently_unreachable(peer_ip))
        path_iface = self._peer_path_interface(peer)
        path_fam = interface_family(path_iface) if path_iface else ""
        if peer_lan_down and serial_up:
            return "serial"
        if path_fam == "serial" and serial_up and not physical_lan:
            return "serial"
        if physical_lan and tcp_lan and not udp_lan and tcp_up and not peer_lan_down:
            if att_fam == "serial" and serial_up:
                if self._serial_faster_than_lan(peer) and self._peer_has_path_on_family(peer, "serial"):
                    return "serial"
                return "tcp"
            return "tcp"
        # LAN primary whenever physical ethernet/Wi-Fi is up and peer answers on LAN.
        if physical_lan and (udp_up or tcp_up) and not peer_lan_down:
            prefer = "tcp" if (tcp_lan and tcp_up and not udp_lan) else "udp"
            if att_fam == "serial" and serial_up:
                if self._serial_faster_than_lan(peer) and self._peer_has_path_on_family(peer, "serial"):
                    return "serial"
                return prefer
            if att_fam == "serial" and not serial_up:
                return prefer
            if tcp_lan and tcp_up and udp_lan and udp_up:
                return "tcp"
            return prefer
        if physical_lan and lan_mesh_has_peer() and att_fam == "serial":
            return "lan"
        if serial_up and not physical_lan:
            return "serial"
        if att_fam in ("udp", "lan", "tcp") and not physical_lan and serial_up:
            return "serial"
        if att_fam == "serial":
            if tcp_up:
                return "tcp"
            if udp_up:
                return "udp"
            if self._has_online_family("lan"):
                return "lan"
        if att_fam == "lan" and not lan_mesh_has_peer():
            if bool(online_interfaces(family="udp")):
                return "udp"
            if serial_up:
                return "serial"
        if att_fam == "udp" and not physical_lan and serial_up:
            return "serial"
        if att_fam == "udp" and lan_mesh_has_peer():
            return "lan"
        path_iface = self._peer_path_interface(peer)
        if path_iface and self._interface_healthy(path_iface):
            fam = interface_family(path_iface)
            if fam != att_fam:
                return fam
        if self._has_online_family("udp"):
            return "udp"
        if self._has_online_family("lan"):
            return "lan"
        if self._has_online_family("serial"):
            return "serial"
        return None

    def _prepare_failover_path(self, peer, prefer_family=None, peer_ip=None, peer_port=None):
        if self._interrupted():
            return False
        self._ensure_runtime_serial_transport()
        if peer_ip and self._peer_lan_recently_unreachable(peer_ip):
            peer_ip = None
            if prefer_family in ("udp", "lan"):
                prefer_family = "serial" if self._has_online_family("serial") else prefer_family
            clear_paths_on_family("udp")
        suppress_offline_lan_transports()
        dedupe_serial_interfaces()
        prune_dead_serial_interfaces()
        if not self._serial_transport_ready():
            serial_cleared = clear_paths_on_family("serial")
            if serial_cleared:
                print(f"[connect] Cleared {serial_cleared} stale serial path(s)")
        pruned = prune_stale_lan_paths()
        if pruned:
            print(f"[connect] Cleared {pruned} stale LAN path(s)")
        bridged = prune_bridged_lan_paths()
        if bridged:
            print(f"[connect] Cleared {bridged} bridged LAN path(s)")
        if prefer_family == "serial":
            keep_families = ("serial",)
        elif prefer_family == "tcp":
            keep_families = ("tcp",)
        elif prefer_family in ("lan", "udp"):
            keep_families = ("udp", "lan")
        else:
            keep_families = None
        if keep_families:
            cleared = clear_paths_except_families(keep_families)
            if cleared:
                print(f"[connect] Cleared {cleared} path(s) off {prefer_family} transport")
        detached = detach_unhealthy_interfaces()
        if detached:
            print(f"[connect] Detached {detached} offline RNS interface(s)")
        stop = self._interrupted
        physical_lan = physical_lan_reachable()
        self._failover_announce(prefer_family, peer_ip=peer_ip)
        if prefer_family == "serial":
            if not self._serial_transport_ready():
                print("[connect] Serial interface offline — skipping serial path prep")
                clear_paths_on_family("serial")
                return False
            prune_lan_path_for_peer(peer)
            clear_peer_path_unless_family(peer, "serial")
            restored = restore_serial_path_from_announce(peer)
            if not restored:
                reinforce_serial_peer_path(peer)
            path_iface = restored or wait_for_peer_path_families(
                peer, families=("serial",), timeout_s=18.0, should_stop=stop,
            )
            if not path_iface:
                self._prime_serial_path(peer, timeout_s=SERIAL_PATH_PRIME_TIMEOUT_S)
                path_iface = wait_for_peer_path_families(
                    peer, families=("serial",), timeout_s=10.0, should_stop=stop,
                )
        elif prefer_family in ("lan", "udp") and self._lan_transport_ready():
            if peer_ip and physical_lan:
                register_udp_peer_ip(peer_ip)
                self._wake_peer(
                    peer_ip, peer_port or 8742, self.my_dest_hash or "",
                )
            elif peer_ip and not physical_lan:
                peer_ip = None
            request_paths_for_hash(peer, family="udp")
            families = ("udp", "lan") if prefer_family == "lan" else (prefer_family,)
            path_iface = wait_for_peer_path_families(
                peer, families=families, timeout_s=14.0, should_stop=stop,
            )
            if not path_iface:
                self._prime_udp_path(peer, peer_ip=peer_ip, timeout_s=6.0)
                path_iface = wait_for_peer_path_families(
                    peer, families=families, timeout_s=8.0, should_stop=stop,
                )
        elif prefer_family == "tcp":
            if peer_ip and physical_lan:
                register_udp_peer_ip(peer_ip)
                self._wake_peer(
                    peer_ip, peer_port or 8742, self.my_dest_hash or "",
                )
            if peer_ip:
                ensure_tcp_client_to_peer(peer_ip, config_dir=self.config_dir)
            request_paths_for_hash(peer, family="tcp")
            path_iface = wait_for_peer_path_families(
                peer, families=("tcp",), timeout_s=14.0, should_stop=stop,
            )
            if not path_iface:
                self._prime_tcp_path(peer, peer_ip=peer_ip, timeout_s=6.0)
                path_iface = wait_for_peer_path_families(
                    peer, families=("tcp",), timeout_s=8.0, should_stop=stop,
                )
        else:
            request_paths_for_hash(peer, family=prefer_family)
            families = (prefer_family,) if prefer_family else (None,)
            wait_s = 12.0 if prefer_family in ("lan", "udp", None) else 18.0
            path_iface = wait_for_peer_path_families(
                peer, families=families, timeout_s=wait_s, should_stop=stop,
            )
        if path_iface:
            fam = interface_family(path_iface)
            print(f"[connect] Path ready on {type(path_iface).__name__} ({fam or prefer_family})")
            return True
        print(f"[connect] Waiting for path to {peer[:16]}... (no {prefer_family or 'usable'} path yet)")
        return False

    def link_needs_failover(self):
        if self.dual_identity_mode:
            return False, ""
        if not self.active_link or not self.active_peer_hash:
            return False, ""
        if self._has_active_transfer():
            return False, ""
        peer = self.dest_hash_for(self.active_peer_hash)
        if not peer or peer == "unknown":
            return False, ""

        attached = self._link_attached_interface(self.active_link)
        if self._hub_transport_active() and self._peer_uses_hub_transport(peer):
            att_fam = interface_family(attached)
            if att_fam == "tcp" and self._link_interface_healthy(self.active_link):
                return False, ""
            if self._has_online_family("tcp") and not self._link_interface_healthy(self.active_link):
                return True, "hub TCP link offline"
            if att_fam != "tcp" and self._has_online_family("tcp"):
                return True, "hub path on TCP"
            return False, ""
        in_grace = (time.time() - self._last_link_established_at) < LINK_FAILOVER_GRACE_S

        if not self._link_interface_healthy(self.active_link):
            return True, f"link interface offline ({type(attached).__name__ if attached else 'none'})"

        path_iface = self._peer_path_interface_for_peer(peer)
        att_fam = interface_family(attached)
        path_fam = interface_family(path_iface) if path_iface else ""

        if path_iface and attached and not self._interfaces_equivalent(path_iface, attached):
            if self._interface_healthy(path_iface):
                new_score = self._interface_path_score(path_iface)
                old_score = self._interface_path_score(attached)
                # UDP-LAN: ignore path-table flaps while the current link is healthy.
                if path_fam == att_fam == "udp" and self._link_interface_healthy(self.active_link):
                    pass
                elif path_fam != att_fam:
                    if not in_grace and new_score > old_score + 10:
                        return True, f"path moved to {path_fam} (link on {att_fam})"
                elif not in_grace and new_score > old_score + 25:
                    return True, f"better path on {type(path_iface).__name__}"

        if att_fam == "lan" and not lan_mesh_has_peer():
            if bool(online_interfaces(family="udp")):
                return True, "AutoInterface down, UDP available"
            if self._has_online_family("serial"):
                return True, "LAN down, serial available"

        if att_fam == "udp" and not self._lan_transport_ready():
            if self._has_online_family("serial"):
                return True, "LAN down, serial available"
            if lan_mesh_has_peer():
                return True, "UDP down, AutoInterface available"

        if att_fam == "udp" and not physical_lan_reachable() and self._has_online_family("serial"):
            if not in_grace:
                return True, "ethernet down, serial available"

        serial_only = self._peer_expected_transport_families(peer) == {"serial"}
        lan_only = bool(
            self._peer_expected_transport_families(peer)
            and "serial" not in self._peer_expected_transport_families(peer)
        )
        parallel = self._parallel_sessions_allowed()

        expected = self._peer_expected_transport_families(peer)
        if parallel and expected:
            if not self._link_interface_healthy(self.active_link):
                return True, f"link interface offline ({type(attached).__name__ if attached else 'none'})"
            if serial_only and att_fam == "serial":
                return False, ""
            if lan_only and att_fam in ("udp", "lan", "tcp"):
                return False, ""
            if serial_only and att_fam in ("udp", "lan", "tcp") and not in_grace:
                return True, "serial peer requires serial transport"
            if lan_only and att_fam == "serial" and not in_grace:
                return True, "LAN peer requires LAN transport"

        if serial_only and att_fam == "serial" and self._link_interface_healthy(self.active_link):
            return False, ""

        if (
            not parallel
            and att_fam in ("udp", "lan")
            and self._has_online_family("serial")
            and self._peer_has_path_on_family(peer, "serial")
            and not in_grace
            and not serial_only
        ):
            return True, "peer path on serial"

        if att_fam == "serial" and not self._serial_transport_ready():
            if (self._has_online_family("udp") or self._has_online_family("lan")) and physical_lan_reachable():
                if not serial_only:
                    return True, "serial offline, LAN available"

        if (
            not parallel
            and att_fam == "serial"
            and physical_lan_reachable()
            and self._has_online_family("udp")
            and not in_grace
            and not serial_only
        ):
            if self._serial_faster_than_lan(peer) and self._peer_has_path_on_family(peer, "serial"):
                return False, ""
            path_iface = self._peer_path_interface_for_peer(peer)
            if path_iface and interface_family(path_iface) == "serial":
                if self._serial_faster_than_lan(peer):
                    return False, ""
            return True, "LAN available, upgrading from serial"

        if len(self._pending_sends) >= RECEIPT_FAILOVER_MIN_PENDING:
            oldest = min(self._pending_sends.values())
            if (time.time() - oldest) > RECEIPT_FAILOVER_TIMEOUT_S:
                try:
                    if getattr(self.active_link, "status", None) == RNS.Link.STALE:
                        return True, "send receipt timeout (link stale)"
                except Exception:
                    pass
                if (time.time() - self._last_link_established_at) > LINK_FAILOVER_GRACE_S:
                    return True, "send receipt timeout (link may be dead)"

        if not self._peer_has_path(peer) and not in_grace:
            if (
                att_fam == "serial"
                and self._link_interface_healthy(self.active_link)
                and self._peer_link_active(peer)
            ):
                pass
            else:
                alt = self._preferred_failover_family(peer, attached)
                if alt and self._has_online_family(alt):
                    return True, f"path lost, trying {alt}"
                if not self._link_interface_healthy(self.active_link):
                    return True, "no path to peer (link interface dead)"

        try:
            if getattr(self.active_link, "status", None) == RNS.Link.STALE:
                inactive = self.active_link.inactive_for()
                if inactive > LINK_STALE_FAILOVER_IDLE_S:
                    return True, f"link stale ({inactive:.0f}s idle)"
        except Exception:
            pass

        return False, ""

    def session_needs_reconnect(self):
        """True when the primary session peer's RNS link is missing or unhealthy."""
        if self.dual_identity_mode:
            return False, ""
        if self._connect_in_progress:
            return False, ""
        if self._has_active_transfer():
            return False, ""
        peer = self.dest_hash_for(self._session_peer_hash or self.active_peer_hash or "")
        if not peer or peer == "unknown":
            return False, ""
        if self.is_user_disconnected(peer):
            return False, ""
        if self._peer_expected_transport_families(peer) == {"serial"}:
            if configured_serial_enabled(load_settings_interfaces(self.config_dir)):
                if serial_interface_online() is None:
                    return False, ""
        adopted = self._adopt_healthy_peer_link(peer)
        if adopted:
            if self.active_link and is_serial_interface(
                self._link_attached_interface(self.active_link)
            ):
                return False, ""
            if self.active_link and self._link_interface_healthy(self.active_link):
                needs, reason = self.link_needs_failover()
                if needs:
                    return needs, reason
                return False, ""
        if self._peer_link_active(peer):
            if self.active_link and not self._link_interface_healthy(self.active_link):
                return True, "link interface offline"
            if self.active_link and is_serial_interface(self._link_attached_interface(self.active_link)):
                return False, ""
            if self.active_link:
                needs, reason = self.link_needs_failover()
                if needs:
                    return needs, reason
            return False, ""
        healthy_links = [
            link for link in self._links_for_peer(peer)
            if self._link_interface_healthy(link)
        ]
        if healthy_links:
            self._adopt_healthy_peer_link(peer)
            return False, ""
        in_grace = (time.time() - self._last_link_established_at) < LINK_FAILOVER_GRACE_S
        if in_grace and self._links_for_peer(peer):
            return False, ""
        if self._failover_in_progress:
            return False, ""
        if self.active_link:
            return self.link_needs_failover()
        if self._last_link_lost_at and (time.time() - self._last_link_lost_at) < self._session_reconnect_min_idle():
            return False, ""
        if self._transport_reconnect_pending:
            return True, "transport available — reconnecting"
        if time.time() - self._failover_last_attempt < self._failover_cooldown():
            return False, ""
        return True, "link dropped — reconnecting"
