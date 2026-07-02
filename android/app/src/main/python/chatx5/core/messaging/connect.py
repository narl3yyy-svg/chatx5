"""Outbound connect, path priming, wake, and identity wait helpers."""

import json
import threading
import time
from contextlib import contextmanager
from urllib import request as urlrequest

import RNS

from chatx5.core.discovery import (
    normalize_hash,
    register_identity_from_peer,
    message_dest_hash_for_identity,
)
from chatx5.core.lan_rns import (
    clear_peer_path_unless_family,
    clear_peer_path_unless_lan_families,
    clear_paths_on_family,
    clear_peer_path,
    ensure_serial_path_pinned,
    peer_path_entry,
    pin_serial_path,
    prune_bridged_lan_paths,
    prune_lan_path_for_peer,
    prune_stale_lan_paths,
    register_udp_peer_ip,
    reinforce_serial_peer_path,
    request_path_for_hash,
    request_paths_for_hash,
    restore_serial_path_from_announce,
    scrub_peer_path,
    serial_interface_online,
    serial_path_is_pinned,
    suppress_offline_lan_transports,
    unpin_serial_path,
    wait_for_peer_path_families,
)
from chatx5.core.messaging.constants import (
    ANDROID_IDENTITY_WAIT_TIMEOUT_S,
    ANDROID_INITIATOR_INBOUND_WAIT_S,
    ANDROID_LINK_CONNECT_TIMEOUT_S,
    ANDROID_REVERSE_CONNECT_WAIT_S,
    APP_NAME,
    FAILOVER_CONNECT_TIMEOUT_S,
    HTTP_WAKE_TIMEOUT_S,
    IDENTITY_WAIT_TIMEOUT_S,
    INITIATOR_INBOUND_WAIT_S,
    LINK_CONNECT_POLL_S,
    LINK_CONNECT_TIMEOUT_S,
    PEER_LAN_UNREACHABLE_TTL_S,
    QUICK_OUTBOUND_TIMEOUT_S,
    REVERSE_CONNECT_WAIT_S,
    SERIAL_CONNECT_PRIME_INTERVAL_S,
    SERIAL_IDENTITY_WAIT_TIMEOUT_S,
    SERIAL_INBOUND_FIRST_WAIT_S,
    SERIAL_INBOUND_WAIT_S,
    SERIAL_LINK_CONNECT_TIMEOUT_S,
    SERIAL_PATH_PRIME_TIMEOUT_S,
)
from chatx5.core.rns_interfaces import (
    configured_serial_enabled,
    configured_tcp_lan_enabled,
    configured_udp_lan_enabled,
    ensure_runtime_serial,
    ensure_tcp_client_to_peer,
    lan_discovery_configured,
    load_settings_interfaces,
    dedupe_serial_interfaces,
    prune_dead_serial_interfaces,
)
from chatx5.core.serial_transfer import (
    boost_serial_establishment_timeout,
    is_serial_interface,
    tune_serial_link,
)


@contextmanager
def _null_context():
    yield


def _backend():
    import chatx5.core.messaging.backend as bm
    return bm


def physical_lan_reachable():
    return _backend().physical_lan_reachable()


def is_android():
    return _backend().is_android()


class ConnectMixin:
    """Connect orchestration: wake, path prime, outbound/inbound link establishment."""

    def _discovery_peer_meta(self, dest_hex, peer_ip=None, peer_lookup=None):
        if not peer_lookup:
            return None
        try:
            return peer_lookup(peer_ip, dest_hex)
        except TypeError:
            try:
                return peer_lookup(dest_hex)
            except Exception:
                return None
        except Exception:
            return None

    def _should_prefer_serial_connect(self, dest_hex, peer_ip=None, peer_lookup=None):
        """True when the peer is a direct USB neighbor (no usable in-scope LAN IP)."""
        if not self._serial_transport_ready():
            return False
        meta = self._discovery_peer_meta(dest_hex, peer_ip=peer_ip, peer_lookup=peer_lookup)
        if meta:
            if (meta.get("via") or "").strip() == "serial":
                return True
            if not (meta.get("ip") or "").strip():
                return True
        if peer_ip and self._peer_lan_ip_usable(peer_ip):
            return False
        if not peer_ip:
            return True
        return False

    def _udp_connect_ready(self, dest_hex, peer_ip=None, peer_lan_down=False, prefer_serial=False):
        if prefer_serial or peer_lan_down or not physical_lan_reachable() or not self._lan_transport_ready():
            return False
        if not configured_udp_lan_enabled(load_settings_interfaces(self.config_dir)):
            return False
        if peer_ip:
            return self._peer_lan_ip_usable(peer_ip)
        return self._peer_has_path_on_family(dest_hex, "udp")

    def _tcp_connect_ready(self, dest_hex, peer_ip=None, peer_lan_down=False, prefer_serial=False):
        if prefer_serial or peer_lan_down or not physical_lan_reachable() or not self._lan_transport_ready():
            return False
        if not configured_tcp_lan_enabled(load_settings_interfaces(self.config_dir)):
            return False
        if peer_ip:
            return self._peer_lan_ip_usable(peer_ip)
        return self._peer_has_path_on_family(dest_hex, "tcp")

    def _hub_path_connect_ready(self, dest_hex):
        """True when dest is the configured hub server and hub TCP transport is up."""
        if not self._hub_transport_active():
            return False
        role, hub_hash = self._load_hub_settings()
        if role != "client":
            return False
        target = self.dest_hash_for(hub_hash)
        if not target or target == "unknown":
            return False
        if not self.hashes_equivalent(dest_hex, target):
            return False
        return self._hub_tcp_transport_online()

    def _mark_peer_lan_unreachable(self, peer_ip):
        peer_ip = (peer_ip or "").strip()
        if peer_ip:
            self._peer_lan_unreachable[peer_ip] = time.time() + PEER_LAN_UNREACHABLE_TTL_S

    def _clear_peer_lan_unreachable(self, peer_ip):
        self._peer_lan_unreachable.pop((peer_ip or "").strip(), None)

    def _peer_lan_recently_unreachable(self, peer_ip):
        peer_ip = (peer_ip or "").strip()
        if not peer_ip:
            return False
        return time.time() < self._peer_lan_unreachable.get(peer_ip, 0)

    def _ensure_runtime_serial_transport(self):
        try:
            return ensure_runtime_serial(load_settings_interfaces(self.config_dir))
        except Exception as exc:
            print(f"[serial] Runtime serial ensure failed: {exc}")
            return None

    def _http_peer_post(self, peer_ip, peer_port, path, payload=None, timeout=HTTP_WAKE_TIMEOUT_S):
        if not peer_ip or self._interrupted() or not physical_lan_reachable():
            return False
        if self.shutdown_requested:
            timeout = min(timeout, 0.5)
        port = int(peer_port or 8742)
        url = f"http://{peer_ip}:{port}{path}"
        try:
            data = None
            headers = {}
            if payload is not None:
                data = json.dumps(payload).encode("utf-8")
                headers["Content-Type"] = "application/json"
            req = urlrequest.Request(url, data=data, headers=headers, method="POST")
            with urlrequest.urlopen(req, timeout=timeout) as resp:
                return 200 <= resp.status < 300
        except Exception as exc:
            print(f"[connect] HTTP {path} to {peer_ip} failed: {exc}")
            return False

    def _request_peer_announce(self, peer_ip, peer_port):
        """Ask peer to refresh RNS path only (no discovery/beacon broadcast)."""
        return self._http_peer_post(peer_ip, peer_port, "/api/path_wake", payload={})

    def _request_peer_connect(self, peer_ip, peer_port, my_hash, caller_ip=None, caller_port=8742):
        """Ask peer to open outbound RNS link back to us (we wait inbound)."""
        payload = {
            "hash": normalize_hash(my_hash or self.my_dest_hash or ""),
            "ip": caller_ip or "",
            "port": int(caller_port or 8742),
            "outbound": True,
        }
        return self._http_peer_post(peer_ip, peer_port, "/api/request_connect", payload=payload)

    def _wake_peer(self, peer_ip, peer_port, my_hash, caller_ip=None, caller_port=8742):
        """Wake peer for reverse RNS connect and refresh its LAN announces."""
        if not peer_ip or self._interrupted() or not physical_lan_reachable():
            return False
        register_udp_peer_ip(peer_ip)
        results = {"connect": False, "announce": False}

        def _connect():
            results["connect"] = self._request_peer_connect(
                peer_ip, peer_port, my_hash,
                caller_ip=caller_ip, caller_port=caller_port,
            )

        def _announce():
            results["announce"] = self._request_peer_announce(peer_ip, peer_port)

        t_connect = threading.Thread(target=_connect, daemon=True)
        t_announce = threading.Thread(target=_announce, daemon=True)
        t_connect.start()
        t_announce.start()
        t_connect.join(timeout=HTTP_WAKE_TIMEOUT_S + 0.5)
        t_announce.join(timeout=HTTP_WAKE_TIMEOUT_S + 0.5)
        ok = results["connect"] or results["announce"]
        if ok:
            self._clear_peer_lan_unreachable(peer_ip)
        # HTTP wake failure (e.g. Connection refused on 127.0.0.1-only web UI)
        # must not mark the LAN peer unreachable — RNS/UDP may still work.
        return ok

    def _prime_udp_path(self, dest_hex, peer_ip=None, timeout_s=None):
        """Establish a UDP RNS path before opening a link (required for Android peers)."""
        if self._interrupted():
            return False
        if timeout_s is None:
            timeout_s = 6.0 if is_android() else 4.0
        self._silent_announce(peer_ip=peer_ip if physical_lan_reachable() else None)
        request_paths_for_hash(dest_hex, family="udp")
        path_iface = wait_for_peer_path_families(
            dest_hex, families=("udp",), timeout_s=timeout_s, should_stop=self._interrupted,
        )
        if path_iface:
            print(f"[connect] UDP path ready via {type(path_iface).__name__}")
            return True
        return False

    def _prime_tcp_path(self, dest_hex, peer_ip=None, timeout_s=None):
        """Establish a TCP LAN RNS path before opening a link."""
        if self._interrupted():
            return False
        if timeout_s is None:
            timeout_s = 6.0 if is_android() else 4.0
        if peer_ip:
            ensure_tcp_client_to_peer(peer_ip, config_dir=self.config_dir)
        self._silent_announce(peer_ip=peer_ip if physical_lan_reachable() else None)
        request_paths_for_hash(dest_hex, family="tcp")
        path_iface = wait_for_peer_path_families(
            dest_hex, families=("tcp",), timeout_s=timeout_s, should_stop=self._interrupted,
        )
        if path_iface:
            print(f"[connect] TCP path ready via {type(path_iface).__name__}")
            return True
        return False

    def _prime_lan_path(self, dest_hex, peer_ip=None, timeout_s=None):
        """Prime UDP or TCP LAN path depending on configured transport."""
        interfaces = load_settings_interfaces(self.config_dir)
        if configured_tcp_lan_enabled(interfaces) and not configured_udp_lan_enabled(interfaces):
            return self._prime_tcp_path(dest_hex, peer_ip=peer_ip, timeout_s=timeout_s)
        return self._prime_udp_path(dest_hex, peer_ip=peer_ip, timeout_s=timeout_s)

    def _prime_serial_path(self, dest_hex, timeout_s=None):
        """Establish an RNS path over USB serial (no LAN/HTTP wake required)."""
        if not self._serial_transport_ready():
            print("[connect] Serial path blocked — Serial in RNS: no")
            return False
        clear_peer_path_unless_family(dest_hex, "serial")
        suppress_offline_lan_transports()
        dedupe_serial_interfaces()
        restored = restore_serial_path_from_announce(dest_hex)
        if restored:
            print(f"[connect] Serial path ready via {type(restored).__name__} (announce)")
            return True
        if self._peer_has_path_on_family(dest_hex, "serial"):
            return True
        if timeout_s is None:
            timeout_s = SERIAL_PATH_PRIME_TIMEOUT_S
        print(f"[connect] Priming serial RNS path ({timeout_s:.0f}s)...")
        deadline = time.time() + timeout_s
        last_burst = 0.0
        while time.time() < deadline:
            if self._interrupted():
                return False
            now = time.time()
            if now - last_burst >= SERIAL_CONNECT_PRIME_INTERVAL_S:
                self._burst_serial_announce(count=1, force=True)
                reinforce_serial_peer_path(dest_hex)
                last_burst = now
            restored = restore_serial_path_from_announce(dest_hex)
            if restored:
                print(f"[connect] Serial path ready via {type(restored).__name__} (announce)")
                return True
            path_iface = wait_for_peer_path_families(
                dest_hex, families=("serial",), timeout_s=2.0, poll_s=0.2,
                should_stop=self._interrupted,
            )
            if path_iface:
                print(f"[connect] Serial path ready via {type(path_iface).__name__}")
                return True
        print(
            "[connect] Serial path not ready — both ends need Serial in RNS: yes, "
            "same baud, tap Announce on each, then Connect"
        )
        return False

    def _connect_serial_peer(self, destination, dest_hex, clean, old_link=None,
                             prime_timeout=8.0):
        """Single serial connect: prime once, brief inbound wait, outbound, inbound fallback."""
        if not self._serial_transport_ready():
            print("[connect] Serial path blocked — Serial in RNS: no")
            return False
        pin_serial_path(dest_hex)
        try:
            clear_peer_path_unless_family(dest_hex, "serial")
            prune_lan_path_for_peer(dest_hex)
            suppress_offline_lan_transports()
            dedupe_serial_interfaces()
            restored = restore_serial_path_from_announce(dest_hex)
            if restored:
                print(f"[connect] Serial path ready via {type(restored).__name__} (announce)")
            elif not self._peer_has_path_on_family(dest_hex, "serial"):
                if not self._prime_serial_path(dest_hex, timeout_s=prime_timeout):
                    return False
            else:
                print("[connect] Serial path ready via SerialInterface")
            ensure_serial_path_pinned(dest_hex)
            print(
                f"[connect] Serial peer — listening for inbound "
                f"({SERIAL_INBOUND_FIRST_WAIT_S}s)..."
            )
            if self._wait_for_peer_link(
                dest_hex, alt_hex=clean, timeout_s=SERIAL_INBOUND_FIRST_WAIT_S,
            ):
                return True
            ensure_serial_path_pinned(dest_hex)
            print(f"[connect] Serial outbound ({SERIAL_LINK_CONNECT_TIMEOUT_S}s)...")
            if self._establish_outbound_link(
                destination, dest_hex, clean, old_link=old_link,
                timeout_s=SERIAL_LINK_CONNECT_TIMEOUT_S, serial=True,
            ):
                return True
            if self._peer_link_active(dest_hex, clean):
                return True
            print(f"[connect] Waiting for serial inbound ({SERIAL_INBOUND_WAIT_S}s)...")
            if self._wait_for_peer_link(
                dest_hex, alt_hex=clean, timeout_s=SERIAL_INBOUND_WAIT_S,
            ):
                return True
            return False
        finally:
            unpin_serial_path(dest_hex)

    def _promote_outbound_link(self, link, dest_hex, old_link=None, promote_active=None):
        if not link:
            return False
        try:
            if link.status != RNS.Link.ACTIVE:
                return False
        except Exception:
            return False
        if old_link and old_link.link_id != link.link_id:
            old_peer = self._link_peer_hashes.get(old_link.link_id)
            if old_peer and self.hashes_equivalent(old_peer, dest_hex):
                self._link_handoff = True
                try:
                    old_link.teardown()
                except Exception:
                    pass
                finally:
                    self._link_handoff = False
                self._last_handoff = True
            else:
                self._last_handoff = False
        else:
            self._last_handoff = False
        self._setup_link(link)
        if promote_active is None:
            promote_active = (
                getattr(self, "_connect_failover", False)
                or (self._connect_user_initiated and not self._connect_background)
                or (
                    self.active_peer_hash
                    and self.hashes_equivalent(dest_hex, self.active_peer_hash)
                )
                or (
                    self._session_peer_hash
                    and self.hashes_equivalent(dest_hex, self._session_peer_hash)
                )
            )
        background = not promote_active
        self._notify_link_established(
            link, dest_hex,
            promote_active=promote_active,
            background=background,
        )
        if promote_active:
            self._send_link = link
        try:
            iface = self._link_attached_interface(link)
            ident = (
                self.identity_serial
                if iface and is_serial_interface(iface) and self.identity_serial
                else self.identity
            )
            if ident:
                link.identify(ident)
        except Exception:
            pass
        print("[connect] Link established")
        self._schedule_queue_drain(
            dest_hex, link=link, include_files=not self._has_active_transfer(),
        )
        return True

    def _establish_outbound_link(self, destination, dest_hex, clean, old_link=None,
                                 timeout_s=LINK_CONNECT_TIMEOUT_S, promote_active=None,
                                 serial=False):
        """Try to open an outbound RNS link within timeout_s."""
        link = None
        try:
            if serial:
                ensure_serial_path_pinned(dest_hex)
            link_ctx = (
                boost_serial_establishment_timeout(timeout_s)
                if serial else _null_context()
            )
            with link_ctx:
                link = RNS.Link(destination)
            deadline = time.time() + timeout_s
            while time.time() < deadline:
                if self._interrupted():
                    self._teardown_outbound_attempt(link)
                    return False
                if serial:
                    ensure_serial_path_pinned(dest_hex, request=False)
                time.sleep(LINK_CONNECT_POLL_S)
                if self._peer_link_active(dest_hex, clean):
                    existing = self._link_for_peer(dest_hex) or self._link_for_peer(clean)
                    if existing:
                        self._notify_link_established(
                            existing, dest_hex,
                            promote_active=True, background=False,
                        )
                    self._teardown_outbound_attempt(link)
                    return True
                try:
                    if link.status == RNS.Link.ACTIVE:
                        return self._promote_outbound_link(
                            link, dest_hex, old_link=old_link, promote_active=promote_active,
                        )
                    if link.status == RNS.Link.CLOSED:
                        break
                except Exception:
                    pass
                if self.active_link and link and self.active_link.link_id == link.link_id:
                    return True
            if self._promote_outbound_link(
                link, dest_hex, old_link=old_link, promote_active=promote_active,
            ):
                return True
            if self._adopt_healthy_peer_link(dest_hex):
                return True
        except Exception as e:
            print(f"[connect] Link failed: {e}")
        finally:
            active = False
            try:
                active = link and link.status == RNS.Link.ACTIVE
            except Exception:
                active = False
            if not active and not self._peer_link_active(dest_hex, clean):
                self._teardown_outbound_attempt(link)
        if self._adopt_healthy_peer_link(dest_hex):
            return True
        return self._peer_link_active(dest_hex, clean)


    def _wait_for_peer_link(self, dest_hex, alt_hex=None, timeout_s=REVERSE_CONNECT_WAIT_S):
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if self._interrupted():
                return False
            if serial_path_is_pinned(dest_hex) or serial_path_is_pinned(alt_hex or ""):
                ensure_serial_path_pinned(dest_hex, request=False)
            if self._peer_link_active(dest_hex, alt_hex):
                found = self._find_active_link_for_peer(dest_hex, alt_hex)
                if found and not self._link_for_peer(dest_hex):
                    self._notify_link_established(
                        found, dest_hex, promote_active=True, background=False,
                    )
                else:
                    self._adopt_healthy_peer_link(dest_hex)
                return True
            time.sleep(LINK_CONNECT_POLL_S)
        return False

    def _wait_for_reverse_link(self, dest_hex, alt_hex=None, timeout_s=REVERSE_CONNECT_WAIT_S):
        return self._wait_for_peer_link(dest_hex, alt_hex=alt_hex, timeout_s=timeout_s)

    def _teardown_outbound_attempt(self, link):
        if not link:
            return
        try:
            if link.status != RNS.Link.ACTIVE:
                link.teardown()
                if link.link_id in self.links:
                    del self.links[link.link_id]
        except Exception:
            pass

    def _identity_hash_candidates(self, hash_hex):
        clean = normalize_hash(hash_hex)
        if len(clean) != 32:
            return []
        candidates = [clean]
        mapped_dest = self.dest_hash_for(clean)
        if mapped_dest and mapped_dest not in candidates:
            candidates.append(mapped_dest)
        ident_hex = self.dest_to_identity.get(clean)
        if ident_hex and ident_hex not in candidates:
            candidates.append(ident_hex)
        for ih, dest in self.identity_to_dest.items():
            if ih == clean or dest == clean:
                for h in (ih, dest):
                    if h and h not in candidates:
                        candidates.append(h)
        return candidates

    def _identity_for_hash(self, hash_hex):
        for candidate in self._identity_hash_candidates(hash_hex):
            try:
                raw = bytes.fromhex(candidate)
            except Exception:
                continue
            ident = self._recall_identity_bytes(raw)
            if ident:
                dest = message_dest_hash_for_identity(ident)
                if dest:
                    self.register_peer_mapping(
                        dest, normalize_hash(RNS.hexrep(ident.hash))
                    )
                return ident
        return None

    def _hash_from_peer_info(self, peer_info):
        if not peer_info:
            return ""
        for key in ("hash", "identity_hash"):
            candidate = normalize_hash(peer_info.get(key))
            if not candidate or len(candidate) != 32:
                continue
            ident = self._identity_for_hash(candidate)
            if ident:
                dest = message_dest_hash_for_identity(ident)
                if dest:
                    self.register_peer_mapping(dest, normalize_hash(RNS.hexrep(ident.hash)))
                    return dest
        return normalize_hash(peer_info.get("hash"))

    def _wait_for_identity(self, hash_hex, peer_ip=None, peer_port=None, peer_lookup=None,
                          caller_ip=None, caller_port=8742):
        clean = normalize_hash(hash_hex)
        serial_wait = (
            not lan_discovery_configured(load_settings_interfaces(self.config_dir))
            and self._serial_transport_ready()
        )
        if serial_wait:
            wait_s = SERIAL_IDENTITY_WAIT_TIMEOUT_S
        elif is_android():
            wait_s = ANDROID_IDENTITY_WAIT_TIMEOUT_S
        else:
            wait_s = IDENTITY_WAIT_TIMEOUT_S
        deadline = time.time() + wait_s
        last_log = 0
        last_burst = 0.0
        while time.time() < deadline:
            ident = self._identity_for_hash(clean)
            if ident:
                return ident, clean

            if peer_lookup:
                peer = peer_lookup(peer_ip, clean)
                if peer:
                    if register_identity_from_peer(peer):
                        for candidate in self._identity_hash_candidates(clean):
                            ident = self._identity_for_hash(candidate)
                            if ident:
                                resolved = self._hash_from_peer_info(peer) or candidate
                                print(
                                    f"[connect] Identity registered from beacon "
                                    f"({peer.get('ip', '?')}): {resolved[:16]}..."
                                )
                                return ident, resolved
                    alt = self._hash_from_peer_info(peer)
                    if alt and alt != clean:
                        clean = alt
                        ident = self._identity_for_hash(clean)
                        if ident:
                            print(f"[connect] Resolved peer via discovery: {clean[:16]}...")
                            return ident, clean
                    for key in ("hash", "identity_hash"):
                        alt = normalize_hash(peer.get(key))
                        if not alt or alt == clean:
                            continue
                        ident = self._identity_for_hash(alt)
                        if ident:
                            resolved = self._hash_from_peer_info(peer) or alt
                            print(f"[connect] Resolved peer via discovery: {resolved[:16]}...")
                            return ident, resolved

            now = time.time()
            if now - last_log >= 3:
                remaining = int(deadline - now)
                hint = " (serial — tap Announce on peer too)" if serial_wait else ""
                print(f"[connect] Waiting for peer identity ({remaining}s left){hint}...")
                last_log = now
            if serial_wait:
                if now - last_burst >= 2.0:
                    self._burst_serial_announce(count=1)
                    request_paths_for_hash(clean, family="serial")
                    last_burst = now
            elif not lan_discovery_configured(load_settings_interfaces(self.config_dir)):
                self._silent_announce()
                request_paths_for_hash(clean, family="serial")
            else:
                request_path_for_hash(clean)
            time.sleep(0.5)

        return None, clean

    def connect_to(self, destination_hash_hex, peer_ip=None, peer_port=None, peer_lookup=None,
                   caller_ip=None, caller_port=8742, replace=False, failover=False,
                   respond_to_wake=False, user_initiated=False, prefer_transport=None):
        with self._connect_lock:
            if self._interrupted():
                return False

            self._connect_in_progress = True
            self._connect_user_initiated = bool(user_initiated)
            self._connect_background = bool(respond_to_wake and not user_initiated)
            self._connect_failover = bool(failover)

            try:
                return self._connect_to_locked(
                    destination_hash_hex,
                    peer_ip=peer_ip,
                    peer_port=peer_port,
                    peer_lookup=peer_lookup,
                    caller_ip=caller_ip,
                    caller_port=caller_port,
                    replace=replace,
                    failover=failover,
                    respond_to_wake=respond_to_wake,
                    user_initiated=user_initiated,
                    prefer_transport=prefer_transport,
                )
            finally:
                self._connect_in_progress = False

    def _connect_to_locked(self, destination_hash_hex, peer_ip=None, peer_port=None,
                           peer_lookup=None, caller_ip=None, caller_port=8742,
                           replace=False, failover=False, respond_to_wake=False,
                           user_initiated=False, prefer_transport=None):
            clean = normalize_hash(destination_hash_hex)
            requested_transport = (
                self._normalize_transport(prefer_transport)
                if prefer_transport
                else None
            )
            if len(clean) != 32:
                print(f"[connect] Invalid hash length ({len(clean)} chars, expected 32)")
                return False
            if peer_ip and not self._peer_lan_ip_usable(peer_ip):
                if self._serial_transport_ready() or self._peer_has_path_on_family(clean, "serial"):
                    print(
                        f"[connect] Peer LAN IP {peer_ip} outside scope — "
                        "using serial path"
                    )
                peer_ip = None
            if peer_ip:
                register_udp_peer_ip(peer_ip)

            if user_initiated:
                self.clear_user_disconnected(clean)
                session_hash = self.dest_hash_for(clean) or clean
                if requested_transport:
                    self._session_transport = requested_transport
                self._session_peer_hash = session_hash
                limit = int(getattr(self, "max_peer_links", 0) or 0)
                if limit > 0:
                    keep_keys = []
                    if requested_transport:
                        keep_keys.append(self._link_map_key(session_hash, requested_transport))
                    else:
                        keep_keys.append(session_hash)
                    self._enforce_max_peer_links(keep_keys=keep_keys)
                if self._parallel_sessions_allowed():
                    if (
                        not self.active_link
                        or not self.active_peer_hash
                        or self.hashes_equivalent(session_hash, self.active_peer_hash)
                        or (
                            requested_transport
                            and self.active_link
                            and self._transport_from_link(self.active_link) == requested_transport
                        )
                    ):
                        self.active_peer_hash = session_hash
                else:
                    self.active_peer_hash = session_hash
                self._teardown_other_peer_links(session_hash)
                if (
                    peer_ip
                    and physical_lan_reachable()
                    and not respond_to_wake
                    and not self._peer_lan_recently_unreachable(peer_ip)
                ):
                    print(f"[connect] Waking LAN peer at {peer_ip}:{peer_port or 8742}")
                    self._wake_peer(
                        peer_ip, peer_port, self.my_dest_hash or "",
                        caller_ip=caller_ip, caller_port=caller_port,
                    )
                    pruned = self._teardown_stale_peer_links(clean, handoff=True)
                    if pruned:
                        print(f"[connect] Closed {pruned} stale link(s) for {clean[:16]}...")
                pruned = self._teardown_mismatched_links(clean)
                if pruned:
                    print(f"[connect] Closed {pruned} stale link(s) for {clean[:16]}...")
            elif respond_to_wake and self.is_user_disconnected(clean):
                print(
                    f"[connect] Passive mode — not reverse-connecting to "
                    f"{clean[:16]}... (user disconnected)"
                )
                inbound = self._find_active_link_for_peer(clean)
                if inbound:
                    self._notify_link_established(
                        inbound, clean, promote_active=False, background=True, passive=True,
                    )
                return bool(inbound)

            if requested_transport == "tcp":
                hub_link = self._hub_link_for_peer(clean)
                if hub_link and self._link_interface_healthy(hub_link):
                    print(f"[connect] Hub TCP link active to {clean[:16]}...")
                    return self._finish_connect(
                        clean, link=hub_link, transport="tcp",
                    )

            old_link = None
            if self.active_link and self.active_peer_hash and self.hashes_equivalent(clean, self.active_peer_hash):
                active_transport = self._transport_from_link(self.active_link)
                if self._link_is_hub_tcp(self.active_link):
                    link_ok = self._link_interface_healthy(self.active_link)
                else:
                    link_ok = (
                        self._link_interface_healthy(self.active_link)
                        and self._peer_has_path(clean)
                    )
                transport_ok = (
                    not requested_transport
                    or active_transport == requested_transport
                )
                if requested_transport == "tcp" and not self._link_is_hub_tcp(self.active_link):
                    link_ok = False
                    transport_ok = False
                if not replace:
                    if link_ok and transport_ok:
                        print(
                            f"[connect] Already connected to {self.active_peer_hash[:16]}..."
                            f" ({active_transport})"
                        )
                        return self._finish_connect(
                            clean, link=self.active_link, transport=requested_transport,
                        )
                    if link_ok and not transport_ok:
                        print(
                            f"[connect] Active {active_transport} link — "
                            f"opening separate {requested_transport} session..."
                        )
                    elif not link_ok:
                        if (
                            requested_transport == "tcp"
                            and self.active_link
                            and not self._link_is_hub_tcp(self.active_link)
                        ):
                            print(
                                f"[connect] Active {active_transport} link — "
                                f"opening separate {requested_transport} session..."
                            )
                        else:
                            print(
                                f"[connect] Stale link to {self.active_peer_hash[:16]}..."
                                " — reconnecting"
                            )
                            self._teardown_active_link(preserve_peer=True, handoff=True)
                elif self._link_path_score(self.active_link) >= 90 and link_ok and transport_ok:
                    return self._finish_connect(
                        clean, link=self.active_link, transport=requested_transport,
                    )
                else:
                    old_link = self.active_link
                    self._teardown_active_link(preserve_peer=True, handoff=True)
                    print(f"[connect] Replacing link to {self.active_peer_hash[:16]} for better path...")
            elif self._peer_link_active(clean, transport=requested_transport):
                usable, adopt = self._peer_link_usable(clean, transport=requested_transport)
                if usable:
                    print(
                        f"[connect] Already linked to {clean[:16]}... "
                        f"({self._transport_from_link(adopt) if adopt else requested_transport or 'active'})"
                    )
                    if user_initiated and adopt:
                        self._notify_link_established(
                            adopt, clean, promote_active=True, background=False,
                        )
                    return self._finish_connect(
                        clean, link=adopt, transport=requested_transport,
                    )
                pruned = self._teardown_stale_peer_links(clean, handoff=True)
                if pruned:
                    print(f"[connect] Closed {pruned} stale link(s) for {clean[:16]}...")

            known_identity = self._identity_for_hash(clean)
            if known_identity is None:
                known_identity, clean = self._wait_for_identity(
                    clean,
                    peer_ip=peer_ip,
                    peer_port=peer_port,
                    peer_lookup=peer_lookup,
                    caller_ip=caller_ip,
                    caller_port=caller_port,
                )
            if known_identity is None:
                print(f"[connect] No known identity for {clean[:16]}...")
                print("[connect] Peer identity not learned yet (beacon pubkey or RNS announce).")
                if peer_ip:
                    print(f"[connect] Ensure chatx5 is open on {peer_ip} and try Announce in the UI.")
                else:
                    print("[connect] On the peer device: open chatx5, wait ~15s, or tap Announce.")
                return False

            ident_hex = normalize_hash(RNS.hexrep(known_identity.hash))
            try:
                destination = RNS.Destination(
                    known_identity,
                    RNS.Destination.OUT,
                    RNS.Destination.SINGLE,
                    APP_NAME,
                    "messages"
                )
            except Exception as e:
                print(f"[connect] Destination creation failed: {e}")
                return False

            dest_hex = normalize_hash(RNS.hexrep(destination.hash))
            self.register_peer_mapping(dest_hex, ident_hex)

            if self._hub_transport_active() and not self._peer_uses_hub_transport(dest_hex):
                _, path_iface = peer_path_entry(dest_hex)
                if path_iface and self._link_is_hub_transport(path_iface):
                    clear_peer_path(dest_hex)

            my_hash = normalize_hash(self.my_dest_hash or dest_hex)
            inbound = self._find_active_link_for_peer(dest_hex, clean)
            if inbound and self._link_transport_matches(inbound, requested_transport):
                self._cache_link_peer(inbound, dest_hex)
                self._notify_link_established(
                    inbound, dest_hex, promote_active=True, background=False,
                )
                print(f"[connect] Adopted inbound link to {dest_hex[:16]}...")
                return self._finish_connect(
                    dest_hex, link=inbound, transport=requested_transport,
                )
            usable, adopt = self._peer_link_usable(
                dest_hex, clean, transport=requested_transport,
            )
            if usable:
                print(f"[connect] Already linked to {dest_hex[:16]}... (inbound)")
                return self._finish_connect(
                    dest_hex, link=adopt, transport=requested_transport,
                )
            if adopt:
                pruned = self._teardown_stale_peer_links(dest_hex, handoff=True)
                if pruned:
                    print(f"[connect] Closed {pruned} stale link(s) for {dest_hex[:16]}...")

            physical_lan = physical_lan_reachable()
            peer_lan_down = bool(peer_ip and self._peer_lan_recently_unreachable(peer_ip))
            if peer_lan_down:
                peer_ip = None
                clear_paths_on_family("udp")
            self._ensure_runtime_serial_transport()
            lan_ready = self._lan_transport_ready() and physical_lan and not peer_lan_down
            serial_ready = self._serial_transport_ready()
            prefer_serial = self._should_prefer_serial_connect(
                dest_hex, peer_ip=peer_ip, peer_lookup=peer_lookup,
            )
            if requested_transport == "serial":
                prefer_serial = True
                peer_ip = None
            elif (
                requested_transport != "lan"
                and self._peer_hash_is_serial_endpoint(dest_hex)
            ):
                prefer_serial = True
                peer_ip = None
            elif requested_transport == "lan":
                prefer_serial = False
                from chatx5.core.lan_rns import (
                    clear_peer_path_unless_lan_families,
                    prune_serial_path_for_peer,
                )
                prune_serial_path_for_peer(dest_hex)
                clear_peer_path_unless_lan_families(dest_hex)
            elif requested_transport == "tcp":
                prefer_serial = False
                peer_ip = None
                prune_lan_path_for_peer(dest_hex)
                clear_peer_path_unless_family(dest_hex, "tcp")
            hub_tcp_only = (
                requested_transport == "tcp"
                or self._hub_path_connect_ready(dest_hex)
            )
            serial_only = serial_ready and (prefer_serial or not lan_ready or peer_lan_down)
            prune_stale_lan_paths()
            bridged = prune_bridged_lan_paths()
            if bridged:
                print(f"[connect] Cleared {bridged} bridged LAN path(s)")
            if prefer_serial:
                clear_peer_path_unless_family(dest_hex, "serial")
                peer_ip = None

            if requested_transport == "serial":
                serial_only_peer = serial_ready
            elif requested_transport == "lan":
                serial_only_peer = False
            else:
                serial_only_peer = (
                    prefer_serial
                    or serial_only
                    or self._peer_expected_transport_families(dest_hex) == {"serial"}
                )
            if serial_ready and serial_only_peer and requested_transport != "lan":
                prime_timeout = 12.0 if not physical_lan else 8.0
                if self._connect_serial_peer(
                    destination, dest_hex, clean, old_link=old_link,
                    prime_timeout=prime_timeout,
                ):
                    adopt = (
                        self._link_for_peer(dest_hex, transport="serial")
                        or self.active_link
                    )
                    return self._finish_connect(
                        dest_hex, link=adopt, transport="serial",
                    )
                print("[connect] Peer not reachable (serial)")
                return False

            if hub_tcp_only and self._hub_tcp_transport_online():
                request_paths_for_hash(dest_hex, family="tcp")
                wait_for_peer_path_families(
                    dest_hex, families=("tcp",), timeout_s=8.0,
                    should_stop=self._interrupted,
                )
                print(
                    f"[hub] Hub TCP RNS link to {dest_hex[:16]}... "
                    f"({QUICK_OUTBOUND_TIMEOUT_S}s)"
                )
                if self._establish_outbound_link(
                    destination, dest_hex, clean, old_link=old_link,
                    timeout_s=QUICK_OUTBOUND_TIMEOUT_S,
                ):
                    adopt = (
                        self._hub_link_for_peer(dest_hex)
                        or self._link_for_peer(dest_hex, transport="tcp")
                        or self.active_link
                    )
                    return self._finish_connect(
                        dest_hex, link=adopt, transport="tcp",
                    )
                adopt = self._hub_link_for_peer(dest_hex)
                if adopt:
                    return self._finish_connect(
                        dest_hex, link=adopt, transport="tcp",
                    )

            if (
                not hub_tcp_only
                and self._tcp_connect_ready(dest_hex, peer_ip, peer_lan_down, prefer_serial=prefer_serial)
            ):
                if peer_ip:
                    self._prime_tcp_path(dest_hex, peer_ip=peer_ip, timeout_s=2.5)
                print(f"[connect] LAN/TCP path ready — quick connect ({QUICK_OUTBOUND_TIMEOUT_S}s)")
                if self._establish_outbound_link(
                    destination, dest_hex, clean, old_link=old_link,
                    timeout_s=QUICK_OUTBOUND_TIMEOUT_S,
                ):
                    return self._finish_connect(dest_hex)
                if self._peer_link_active(dest_hex, clean):
                    adopt = self._link_for_peer(dest_hex) or self._find_active_link_for_peer(dest_hex, clean)
                    return self._finish_connect(dest_hex, link=adopt)

            if (
                not hub_tcp_only
                and self._udp_connect_ready(dest_hex, peer_ip, peer_lan_down, prefer_serial=prefer_serial)
            ):
                if peer_ip:
                    self._prime_udp_path(dest_hex, peer_ip=peer_ip, timeout_s=2.5)
                print(f"[connect] LAN/UDP path ready — quick connect ({QUICK_OUTBOUND_TIMEOUT_S}s)")
                if self._establish_outbound_link(
                    destination, dest_hex, clean, old_link=old_link,
                    timeout_s=QUICK_OUTBOUND_TIMEOUT_S,
                ):
                    return self._finish_connect(dest_hex)
                if self._peer_link_active(dest_hex, clean):
                    adopt = self._link_for_peer(dest_hex) or self._find_active_link_for_peer(dest_hex, clean)
                    return self._finish_connect(dest_hex, link=adopt)

            if (
                peer_ip
                and not respond_to_wake
                and lan_ready
                and not self._peer_link_active(dest_hex, clean, transport=requested_transport)
            ):
                self._prime_lan_path(dest_hex, peer_ip=peer_ip, timeout_s=2.5)
                if self._peer_has_path(dest_hex):
                    print(f"[connect] Path known — quick outbound attempt ({QUICK_OUTBOUND_TIMEOUT_S}s)")
                    if self._establish_outbound_link(
                        destination, dest_hex, clean, old_link=old_link,
                        timeout_s=QUICK_OUTBOUND_TIMEOUT_S,
                    ):
                        adopt = self._link_for_peer(dest_hex) or self.active_link
                        return self._finish_connect(dest_hex, link=adopt)
                print(f"[connect] Waking peer at {peer_ip}:{peer_port or 8742}")
                self._wake_peer(
                    peer_ip, peer_port, my_hash,
                    caller_ip=caller_ip, caller_port=caller_port,
                )
                inbound_wait = (
                    ANDROID_INITIATOR_INBOUND_WAIT_S if is_android()
                    else INITIATOR_INBOUND_WAIT_S
                )
                if self._wait_for_peer_link(dest_hex, alt_hex=clean, timeout_s=1.5):
                    print("[connect] Link established (inbound after wake)")
                    adopt = self._link_for_peer(dest_hex) or self.active_link
                    return self._finish_connect(dest_hex, link=adopt)
                print(f"[connect] Waiting for peer outbound link ({inbound_wait}s)...")
                if self._wait_for_peer_link(dest_hex, alt_hex=clean, timeout_s=inbound_wait):
                    print("[connect] Link established (inbound after wake)")
                    adopt = self._link_for_peer(dest_hex) or self.active_link
                    return self._finish_connect(dest_hex, link=adopt)
                print("[connect] Peer did not connect back — trying outbound fallback...")
            elif serial_ready and peer_ip and not lan_ready and requested_transport != "lan":
                print("[connect] LAN unreachable — using serial only (no HTTP wake)")
                peer_ip = None
                self._prime_serial_path(dest_hex)
            elif peer_ip and respond_to_wake:
                print(
                    f"[connect] Outbound to caller at {peer_ip}:{peer_port or 8742} "
                    f"({dest_hex[:16]}...)"
                )
            elif serial_ready and not peer_ip and requested_transport != "lan":
                self._prime_serial_path(dest_hex, timeout_s=12.0)

            scrub_peer_path(dest_hex)
            if requested_transport == "lan":
                if peer_ip or is_android():
                    self._prime_lan_path(dest_hex, peer_ip=peer_ip, timeout_s=6.0)
                else:
                    request_paths_for_hash(dest_hex, family="udp")
            elif requested_transport == "serial":
                request_paths_for_hash(dest_hex, family="serial")
            elif serial_only or (serial_ready and not lan_ready):
                request_paths_for_hash(dest_hex, family="serial")
            elif peer_ip or is_android():
                self._prime_lan_path(dest_hex, peer_ip=peer_ip)
            else:
                request_paths_for_hash(dest_hex)
            if is_android() and not peer_ip and not serial_ready:
                print("[connect] Android: no peer IP — connect from Discovered list or add contact with LAN IP")
            if requested_transport == "lan":
                connect_timeout = LINK_CONNECT_TIMEOUT_S
            elif requested_transport == "serial":
                connect_timeout = SERIAL_LINK_CONNECT_TIMEOUT_S
            elif serial_only or (serial_ready and not lan_ready):
                connect_timeout = SERIAL_LINK_CONNECT_TIMEOUT_S
            elif failover:
                connect_timeout = FAILOVER_CONNECT_TIMEOUT_S
            elif is_android():
                connect_timeout = ANDROID_LINK_CONNECT_TIMEOUT_S
            else:
                connect_timeout = LINK_CONNECT_TIMEOUT_S
            if requested_transport == "lan":
                path_hint = "lan"
            elif requested_transport == "serial":
                path_hint = "serial"
            else:
                path_hint = "serial" if (serial_only or (serial_ready and not lan_ready)) else "auto"
            print(f"[connect] Connecting to {dest_hex[:16]}... ({path_hint}, timeout {connect_timeout}s)")

            if self._establish_outbound_link(
                destination, dest_hex, clean, old_link=old_link,
                timeout_s=connect_timeout,
            ):
                adopt = self._link_for_peer(dest_hex) or self.active_link
                return self._finish_connect(dest_hex, link=adopt)

            if self._peer_link_active(dest_hex, clean):
                adopt = self._adopt_healthy_peer_link(dest_hex)
                print("[connect] Link established (inbound after outbound attempt)")
                return self._finish_connect(dest_hex, link=adopt)

            if peer_ip and lan_ready and physical_lan:
                reverse_wait = ANDROID_REVERSE_CONNECT_WAIT_S if is_android() else REVERSE_CONNECT_WAIT_S
                print(f"[connect] Outbound timed out — waiting for reverse connect ({reverse_wait}s)...")
                if not respond_to_wake:
                    self._wake_peer(
                        peer_ip, peer_port, my_hash,
                        caller_ip=caller_ip, caller_port=caller_port,
                    )
                if self._wait_for_reverse_link(dest_hex, alt_hex=clean, timeout_s=reverse_wait):
                    print("[connect] Reverse connect established")
                    adopt = self._link_for_peer(dest_hex) or self.active_link
                    return self._finish_connect(dest_hex, link=adopt)

            if (
                requested_transport != "lan"
                and serial_ready
                and not serial_only
                and (peer_lan_down or not physical_lan)
                and not self._peer_link_active(dest_hex, clean)
            ):
                print("[connect] Retrying over serial after LAN path failed...")
                scrub_peer_path(dest_hex)
                request_paths_for_hash(dest_hex, family="serial")
                self._prime_serial_path(dest_hex, timeout_s=14.0)
                if self._establish_outbound_link(
                    destination, dest_hex, clean, old_link=old_link,
                    timeout_s=SERIAL_LINK_CONNECT_TIMEOUT_S,
                ):
                    adopt = self._link_for_peer(dest_hex) or self.active_link
                    return self._finish_connect(dest_hex, link=adopt)
                if self._wait_for_peer_link(
                    dest_hex, alt_hex=clean, timeout_s=REVERSE_CONNECT_WAIT_S,
                ):
                    adopt = self._adopt_healthy_peer_link(dest_hex)
                    print("[connect] Link established (serial inbound after LAN failure)")
                    return self._finish_connect(dest_hex, link=adopt)

            print("[connect] Peer not reachable")
            return False
