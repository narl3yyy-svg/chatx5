"""Peer link map, transport matching, and link lifecycle helpers."""

import time

import RNS

from chatx5.core.discovery import normalize_hash
from chatx5.core.messaging.peers import is_hub_peer_hash


def _backend():
    import chatx5.core.messaging.backend as bm
    return bm


def interface_family(iface):
    return _backend().interface_family(iface)


def interface_is_healthy(iface):
    return _backend().interface_is_healthy(iface)


def peer_path_entry(dest_hash):
    return _backend().peer_path_entry(dest_hash)


def peer_path_on_family(dest_hash, family):
    return _backend().peer_path_on_family(dest_hash, family)


def scrub_peer_path(dest_hash):
    return _backend().scrub_peer_path(dest_hash)


def is_serial_interface(iface):
    return _backend().is_serial_interface(iface)


def physical_lan_reachable():
    return _backend().physical_lan_reachable()


class PeerLinkMixin:
    """Link registry, transport zones, and per-peer link selection."""

    @staticmethod
    def _normalize_transport(via):
        v = (via or "lan").strip().lower()
        if v in ("serial", "usb"):
            return "serial"
        if v in ("tcp", "tcp_hub", "hub"):
            return "tcp"
        return "lan"

    def _link_map_key(self, peer_hash, transport=None):
        peer = self.dest_hash_for(peer_hash)
        if not peer or peer == "unknown":
            return ""
        if transport:
            return f"{peer}:{self._normalize_transport(transport)}"
        return peer

    @staticmethod
    def _peer_from_link_key(key):
        text = str(key or "")
        if ":" in text:
            return text.rsplit(":", 1)[0]
        return text

    def _transport_from_link(self, link):
        fam = interface_family(self._link_attached_interface(link))
        if fam == "serial":
            return "serial"
        if fam == "tcp":
            return "tcp"
        return "lan"

    def _link_transport_matches(self, link, transport):
        if not transport:
            return True
        return self._transport_from_link(link) == self._normalize_transport(transport)

    def _peer_discovery_meta(self, peer_hash):
        peer = self.dest_hash_for(peer_hash)
        if not peer or peer == "unknown":
            return None
        if self.peer_transport_resolver:
            try:
                return self.peer_transport_resolver(peer)
            except Exception:
                return None
        return None

    def _peer_discovery_meta_serial(self, peer_hash):
        peer = self.dest_hash_for(peer_hash)
        if not peer or peer == "unknown" or not self.peer_transport_resolver:
            return None
        try:
            row = self.peer_transport_resolver(peer, via="serial")
            if row and (row.get("via") or "").strip() == "serial":
                if normalize_hash(row.get("hash")) == peer:
                    return row
        except TypeError:
            try:
                row = self.peer_transport_resolver(peer)
                if row and (row.get("via") or "").strip() == "serial":
                    if normalize_hash(row.get("hash")) == peer:
                        return row
            except Exception:
                return None
        except Exception:
            return None
        return None

    def _peer_has_lan_discovery_row(self, peer_hash):
        peer = self.dest_hash_for(peer_hash)
        if not peer or peer == "unknown" or not self.peer_transport_resolver:
            return False
        for via in ("lan", "rns"):
            try:
                row = self.peer_transport_resolver(peer, via=via)
            except TypeError:
                return False
            except Exception:
                continue
            if not row:
                continue
            if normalize_hash(row.get("hash")) != peer:
                continue
            if (row.get("via") or "").strip() != "serial":
                return True
        return False

    def _peer_hash_is_serial_endpoint(self, peer_hash):
        peer = self.dest_hash_for(peer_hash)
        if not peer or peer == "unknown":
            return False
        if self._peer_has_lan_discovery_row(peer):
            return False
        return self._peer_discovery_meta_serial(peer) is not None

    def _serial_inbound_scope_ok(self, peer_hash, link=None):
        """Allow USB serial inbound when RNS has not attached an interface yet."""
        if not self._serial_transport_ready():
            return False
        if link:
            iface = self._link_attached_interface(link)
            if iface and not is_serial_interface(iface):
                return False
        peer = self.dest_hash_for(peer_hash) if peer_hash and peer_hash != "unknown" else ""
        if peer and self._peer_has_path_on_family(peer, "serial"):
            return True
        row = self._peer_discovery_meta_serial(peer) if peer else None
        if row:
            return True
        if peer and self._peer_hash_is_serial_endpoint(peer):
            return True
        if link and (not peer or peer == "unknown"):
            return True
        return False

    def _peer_expected_transport_families(self, peer_hash):
        """Transport families allowed for a peer (serial vs LAN isolation)."""
        peer = self.dest_hash_for(peer_hash)
        if not peer or peer == "unknown" or is_hub_peer_hash(peer):
            return set()
        serial_ready = self._serial_transport_ready()
        if serial_ready and self._peer_hash_is_serial_endpoint(peer):
            return {"serial"}
        meta = self._peer_discovery_meta(peer)
        if meta:
            via = (meta.get("via") or "").strip()
            ip = (meta.get("ip") or "").strip()
            if via in ("rns", "beacon", "lan"):
                if ip and self._peer_lan_ip_usable(ip):
                    return {"udp", "lan", "tcp"}
            if via == "serial":
                return {"serial"} if serial_ready else set()
            if ip and self._peer_lan_ip_usable(ip):
                return {"udp", "lan", "tcp"}
            if not ip and serial_ready:
                return {"serial"}
            if (
                serial_ready
                and ip
                and not self._peer_lan_ip_usable(ip)
                and self._peer_has_path_on_family(peer, "serial")
            ):
                return {"serial"}
        if serial_ready and self._lan_transport_ready():
            if self._peer_has_path_on_family(peer, "serial"):
                if not self._peer_has_path_on_family(peer, "udp") and not self._peer_has_path_on_family(peer, "tcp"):
                    return {"serial"}
                meta = meta or self._peer_discovery_meta(peer)
                if meta and (meta.get("via") or "").strip() == "serial":
                    return {"serial"}
                if meta and not (meta.get("ip") or "").strip():
                    return {"serial"}
        if (
            serial_ready
            and self._peer_has_path_on_family(peer, "serial")
            and not self._peer_has_path_on_family(peer, "udp")
            and not self._peer_has_path_on_family(peer, "tcp")
        ):
            return {"serial"}
        if meta and (meta.get("via") or "").strip() == "serial" and serial_ready:
            return {"serial"}
        if serial_ready and self._peer_has_path_on_family(peer, "serial"):
            meta = meta or self._peer_discovery_meta(peer) or {}
            ip = (meta.get("ip") or "").strip()
            via = (meta.get("via") or "").strip()
            if via == "serial" or not ip or not self._peer_lan_ip_usable(ip):
                return {"serial"}
        if self._peer_has_path_on_family(peer, "udp") or self._peer_has_path_on_family(peer, "tcp"):
            return {"udp", "lan", "tcp"}
        return set()

    def _link_remote_peer_hash(self, link):
        """Resolved destination hash for a link's remote party (authoritative when known)."""
        if not link:
            return ""
        identity_peer = self._peer_hash_from_link_identity(link)
        if identity_peer and identity_peer != "unknown" and not self._is_self_hash(identity_peer):
            return self.dest_hash_for(identity_peer)
        cached = self._link_peer_hashes.get(getattr(link, "link_id", None))
        if cached and cached != "unknown" and not self._is_self_hash(cached):
            return self.dest_hash_for(cached)
        return ""

    def _link_matches_peer(self, link, peer_hash):
        if not link:
            return False
        peer = self.dest_hash_for(peer_hash)
        if not peer or peer == "unknown":
            return False
        remote = self._link_remote_peer_hash(link)
        if remote:
            return self.hashes_equivalent(remote, peer)
        if self.peer_links.get(peer) is link:
            return True
        for cached_peer, cached_link in self.peer_links.items():
            if cached_link is link and self.hashes_equivalent(cached_peer, peer):
                return True
        return False

    def _link_acceptable_for_peer(self, link, peer_hash):
        if not link:
            return False
        peer = self.dest_hash_for(peer_hash)
        if not peer or peer == "unknown":
            if is_serial_interface(self._link_attached_interface(link)):
                return True
            return False
        expected = self._peer_expected_transport_families(peer)
        if not expected:
            return True
        fam = interface_family(self._link_attached_interface(link))
        if fam == "serial":
            return "serial" in expected
        if fam in ("udp", "lan", "tcp"):
            return bool(expected & {"udp", "lan", "tcp"})
        return fam in expected

    def _peer_path_interface_for_peer(self, peer_hash):
        """Return path interface only when it matches the peer's transport zone."""
        peer = self.dest_hash_for(peer_hash)
        if not peer or peer == "unknown":
            return None
        scrub_peer_path(peer)
        _, path_iface = peer_path_entry(peer)
        if not path_iface or not interface_is_healthy(path_iface):
            return None
        expected = self._peer_expected_transport_families(peer)
        if not expected:
            return path_iface
        fam = interface_family(path_iface)
        if fam == "serial":
            return path_iface if "serial" in expected else None
        if fam in ("udp", "lan", "tcp"):
            return path_iface if expected & {"udp", "lan", "tcp"} else None
        return path_iface if fam in expected else None

    def _parallel_sessions_allowed(self):
        """True when USB serial and LAN are both up — independent peer links per transport."""
        limit = int(getattr(self, "max_peer_links", 0) or 0)
        if limit == 1:
            return False
        try:
            from chatx5.core.transport_isolation import dual_transport_isolation_enabled
            return dual_transport_isolation_enabled()
        except Exception:
            return False

    def _enforce_max_peer_links(self, keep_keys=None):
        """Close oldest active links when over the configured connection limit."""
        limit = int(getattr(self, "max_peer_links", 0) or 0)
        if limit <= 0:
            return 0
        keep_keys = {k for k in (keep_keys or []) if k}
        evicted = 0
        while True:
            active = []
            for key, link in list(self.peer_links.items()):
                try:
                    if getattr(link, "status", None) == RNS.Link.CLOSED:
                        continue
                except Exception:
                    pass
                if not self._link_interface_healthy(link):
                    continue
                active.append((self._link_connect_order.get(key, 0), key, link))
            if len(active) <= limit:
                break
            active.sort(key=lambda item: item[0])
            victim = None
            for _, key, link in active:
                if key not in keep_keys:
                    victim = (key, link)
                    break
            if not victim:
                break
            key, link = victim
            try:
                link.teardown()
                evicted += 1
            except Exception:
                pass
            peer = self._peer_from_link_key(key)
            transport = None
            if ":" in str(key):
                transport = str(key).rsplit(":", 1)[-1]
            self._unlink_peer(peer, transport=transport)
            self._link_connect_order.pop(key, None)
        if evicted:
            print(f"[connect] Evicted {evicted} link(s) — max_peer_links={limit}")
        return evicted

    def _teardown_other_peer_links(self, keep_peer_hash, handoff=False):
        """Close active links to every peer except the one being connected."""
        if self._parallel_sessions_allowed():
            return 0
        keep = self.dest_hash_for(keep_peer_hash)
        if not keep or keep == "unknown":
            return 0
        closed = 0
        for link in list(self.links.values()):
            remote = self.dest_hash_for(self._peer_for_link(link))
            if not remote or remote == "unknown" or self.hashes_equivalent(remote, keep):
                continue
            try:
                if handoff:
                    self._link_handoff = True
                link.teardown()
                closed += 1
            except Exception:
                pass
            finally:
                if handoff:
                    self._link_handoff = False
        if closed:
            print(f"[connect] Closed {closed} link(s) to other peer(s)")
        return closed

    def _peer_allowed_by_scope(self, peer_hash, link=None):
        if not peer_hash or peer_hash == "unknown":
            if link and is_serial_interface(self._link_attached_interface(link)):
                return True
            if link and self._serial_inbound_scope_ok(peer_hash, link):
                return True
            if link and self._inbound_link_is_hub_tcp(link):
                return True
            if self._hub_transport_active():
                role, _ = self._load_hub_settings()
                if role == "server":
                    return True
            return not self.peer_scope_checker
        peer = self.dest_hash_for(peer_hash)
        if (
            peer
            and self._serial_transport_ready()
            and self._peer_hash_is_serial_endpoint(peer)
        ):
            iface = self._link_attached_interface(link) if link else None
            if not iface or is_serial_interface(iface):
                return True
        if link:
            iface = self._link_attached_interface(link)
            if is_serial_interface(iface):
                return True
            if self._link_is_hub_transport(iface):
                return True
            if self._inbound_link_is_hub_tcp(link):
                return True
            if not iface and self._serial_inbound_scope_ok(peer_hash, link):
                return True
            if iface and not self._link_acceptable_for_peer(link, peer_hash):
                return False
        if not self.peer_scope_checker:
            return True
        if is_hub_peer_hash(peer_hash):
            return True
        try:
            return bool(self.peer_scope_checker(peer_hash, link=link))
        except TypeError:
            try:
                return bool(self.peer_scope_checker(peer_hash))
            except Exception:
                return True
        except Exception:
            return True

    def _link_for_peer(self, peer_hash, transport=None):
        raw = str(peer_hash or "")
        if ":" in raw and not transport:
            base, suffix = raw.rsplit(":", 1)
            peer = self.dest_hash_for(base)
            transport = suffix
        else:
            peer = self.dest_hash_for(peer_hash)
        if not peer or peer == "unknown":
            return None
        if transport:
            key = self._link_map_key(peer, transport)
            link = self.peer_links.get(key)
            if link and self._link_matches_peer(link, peer):
                return link
            return None
        link = self.peer_links.get(peer)
        if link and self._link_matches_peer(link, peer):
            return link
        for cached_key, cached_link in self.peer_links.items():
            if not self.hashes_equivalent(self._peer_from_link_key(cached_key), peer):
                continue
            if self._link_matches_peer(cached_link, peer):
                return cached_link
        for link_id, cached in self._link_peer_hashes.items():
            if self.hashes_equivalent(cached, peer):
                link = self.links.get(link_id)
                if link and self._link_matches_peer(link, peer):
                    return link
        return None

    def _register_peer_link(self, link, peer_hash, transport=None):
        peer = self.dest_hash_for(peer_hash)
        if not peer or peer == "unknown" or not link:
            return
        remote = self._link_remote_peer_hash(link)
        if remote and not self.hashes_equivalent(remote, peer):
            # The link's cryptographically-proven remote identity is
            # authoritative. The caller-supplied hash is often just a different
            # alias for the same peer (discovery identity hash vs message-dest
            # hash) — this split is what left hub TCP links uncounted, breaking
            # group chat. Register under the real remote hash instead of
            # dropping the link. For direct P2P, remote == peer so this branch
            # never triggers; when the remote is genuinely a different peer we
            # still avoid mapping the link to the wrong (requested) hash.
            peer = self.dest_hash_for(remote)
            if not peer or peer == "unknown":
                return
        t = transport or self._transport_from_link(link)
        key = self._link_map_key(peer, t)
        self.peer_links[key] = link
        if key != peer:
            self.peer_links.pop(peer, None)
        self._link_connect_order[key] = time.time()
        self._cache_link_peer(link, peer)

    def _unlink_peer(self, peer_hash, transport=None):
        peer = self.dest_hash_for(peer_hash)
        if not peer:
            return
        if transport:
            self.peer_links.pop(self._link_map_key(peer, transport), None)
            return
        self.peer_links.pop(peer, None)
        for key in list(self.peer_links.keys()):
            if self.hashes_equivalent(self._peer_from_link_key(key), peer):
                self.peer_links.pop(key, None)

    def _other_active_links_for_peer(self, peer_hash, except_link=None):
        peer = self.dest_hash_for(peer_hash)
        if not peer or peer == "unknown":
            return []
        matches = []
        for link_id, cached in list(self._link_peer_hashes.items()):
            if not self.hashes_equivalent(cached, peer):
                continue
            link = self.links.get(link_id)
            if not link or (except_link and link.link_id == except_link.link_id):
                continue
            try:
                if link.status == RNS.Link.ACTIVE:
                    matches.append(link)
            except Exception:
                matches.append(link)
        return matches

    def _adopt_healthy_peer_link(self, peer_hash, promote_session=None):
        """Promote a healthy background link for one peer (optionally the UI session)."""
        peer = self.dest_hash_for(peer_hash)
        if not peer or peer == "unknown":
            return None
        session_peer = self.dest_hash_for(
            self._session_peer_hash or self.active_peer_hash or ""
        )
        if promote_session is None:
            promote_session = bool(
                session_peer and self.hashes_equivalent(peer, session_peer)
            )
        if self.active_link and self._peer_link_active(peer):
            if self._link_interface_healthy(self.active_link):
                if self.hashes_equivalent(
                    self._peer_for_link(self.active_link), peer
                ):
                    return self.active_link
        for link in self._other_active_links_for_peer(peer):
            if not self._link_interface_healthy(link):
                continue
            if not self._link_acceptable_for_peer(link, peer):
                continue
            if not self._link_matches_peer(link, peer):
                continue
            self._register_peer_link(link, peer)
            if promote_session:
                self._notify_link_established(
                    link, peer, promote_active=True, background=False,
                )
            return link
        return None

    def _teardown_stale_peer_links(self, peer_hash, handoff=False):
        """Close dead or wrong-transport links to one peer before reconnect."""
        peer = self.dest_hash_for(peer_hash)
        if not peer or peer == "unknown":
            return 0
        closed = 0
        for link_id, link in list(self.links.items()):
            cached = self._link_peer_hashes.get(link_id)
            if not cached or not self.hashes_equivalent(cached, peer):
                continue
            try:
                if link.status == RNS.Link.ACTIVE and self._link_interface_healthy(link):
                    continue
            except Exception:
                pass
            try:
                if handoff:
                    self._link_handoff = True
                link.teardown()
                closed += 1
            except Exception:
                pass
            finally:
                if handoff:
                    self._link_handoff = False
        return closed

    def _consolidate_peer_links(self, peer_hash, keep_link=None, transport=None):
        """Keep one active link per peer per transport — tear down duplicate sessions."""
        peer = self.dest_hash_for(peer_hash)
        if not peer or peer == "unknown":
            return 0
        keep_id = getattr(keep_link, "link_id", None) if keep_link else None
        keep_fam = interface_family(self._link_attached_interface(keep_link)) if keep_link else None
        parallel = self._parallel_sessions_allowed()
        closed = 0
        for link in list(self._links_for_peer(peer)):
            if keep_id and link.link_id == keep_id:
                continue
            if parallel and keep_fam:
                fam = interface_family(self._link_attached_interface(link))
                if fam != keep_fam:
                    continue
            if transport and not self._link_transport_matches(link, transport):
                continue
            try:
                if getattr(link, "status", None) == RNS.Link.CLOSED:
                    continue
            except Exception:
                pass
            try:
                link.teardown()
                closed += 1
            except Exception:
                pass
        if closed:
            print(f"[messaging] Closed {closed} duplicate link(s) for {peer[:16]}...")
        return closed

    def _finish_connect(self, peer_hash, link=None, user_initiated=None, transport=None):
        """After a successful connect: one link per peer per transport and drain queue."""
        peer = self.dest_hash_for(peer_hash)
        if not peer or peer == "unknown":
            return True
        initiated = (
            bool(user_initiated)
            if user_initiated is not None
            else bool(getattr(self, "_connect_user_initiated", False))
        )
        use_link = link
        if not use_link:
            use_link = (
                self._adopt_healthy_peer_link(peer)
                or self._best_outgoing_link(peer)
            )
        if use_link:
            self._consolidate_peer_links(
                peer, keep_link=use_link, transport=transport,
            )
        if not self.is_user_disconnected(peer):
            self._schedule_queue_drain(peer, link=use_link, include_files=True)
            if initiated:
                self._schedule_hub_queue_drain()
        return True

    def linked_peers(self):
        out = []
        for key, link in list(self.peer_links.items()):
            try:
                if getattr(link, "status", None) == RNS.Link.CLOSED:
                    continue
            except Exception:
                pass
            peer = self._peer_from_link_key(key)
            if ":" in str(key):
                out.append(str(key))
            else:
                out.append(f"{peer}:{self._transport_from_link(link)}")
        return out

    def _link_attached_interface(self, link):
        if not link:
            return None
        iface = getattr(link, "attached_interface", None)
        if iface:
            return iface
        for attr in ("interface", "parent_interface"):
            iface = getattr(link, attr, None)
            if iface:
                return iface
        return None

    def _link_interface_healthy(self, link):
        return self._interface_healthy(self._link_attached_interface(link))

    def _teardown_mismatched_links(self, target_peer):
        """Close links whose resolved peer hash disagrees with the target connect hash."""
        target = self.dest_hash_for(target_peer)
        if not target or target == "unknown":
            return 0
        closed = 0
        for link in list(self.links.values()):
            resolved = self._peer_hash_from_link_identity(link)
            if not resolved or self.hashes_equivalent(resolved, target):
                continue
            try:
                ident = link.get_remote_identity()
                if ident:
                    ident_dest = self._dest_hash_from_identity(ident)
                    if ident_dest and self.hashes_equivalent(ident_dest, target):
                        self._cache_link_peer(link, ident_dest)
                        self._register_peer_link(link, ident_dest)
                        continue
            except Exception:
                pass
            try:
                link.teardown()
                closed += 1
            except Exception:
                pass
        if closed:
            self._pending_sends.clear()
        return closed

    def _link_path_score(self, link):
        if not link:
            return 0
        if not self._link_interface_healthy(link):
            return 0
        try:
            iface = (
                self._link_attached_interface(link)
                or getattr(link, "interface", None)
                or getattr(link, "parent_interface", None)
            )
            fam = interface_family(iface)
            if fam == "serial":
                score = 85 if not physical_lan_reachable() else 45
            elif fam == "tcp":
                score = 95
            elif fam == "lan":
                score = 100
            elif fam == "udp":
                score = 80
            else:
                score = 50
            rtt = getattr(link, "rtt", None)
            if rtt is not None:
                try:
                    score = max(score, int(100 - min(float(rtt) * 8, 95)))
                except Exception:
                    pass
            return score
        except Exception:
            return 50

    def _peer_hash_from_link_identity(self, link):
        if not link:
            return ""
        try:
            ident = link.get_remote_identity()
            if not ident or not getattr(ident, "hash", None):
                return ""
            dest = self._dest_hash_from_identity(ident)
            if dest and not self._is_self_hash(dest):
                return dest
        except Exception:
            pass
        return ""

    def _find_active_link_for_peer(self, dest_hex, alt_hex=None):
        targets = []
        for raw in (dest_hex, alt_hex):
            clean = self.dest_hash_for(raw)
            if clean and clean != "unknown" and clean not in targets:
                targets.append(clean)
        if not targets:
            return None
        for link in list(self.links.values()):
            try:
                if link.status != RNS.Link.ACTIVE:
                    continue
            except Exception:
                continue
            peer = self._peer_hash_from_link_identity(link)
            if not peer or peer == "unknown":
                cached = self._link_peer_hashes.get(link.link_id)
                if cached:
                    peer = self.dest_hash_for(cached)
            if not peer or peer == "unknown":
                continue
            for target in targets:
                if self.hashes_equivalent(peer, target):
                    return link
        return None

    def _resolve_incoming_link_peer(self, link, peer_hash):
        identity_peer = self._peer_hash_from_link_identity(link)
        if identity_peer and identity_peer != "unknown" and not self._is_self_hash(identity_peer):
            return identity_peer
        peer_hash = self.dest_hash_for(peer_hash)
        if is_hub_peer_hash(peer_hash):
            peer_hash = ""
        if peer_hash and peer_hash != "unknown" and not self._is_self_hash(peer_hash):
            if identity_peer and not self.hashes_equivalent(peer_hash, identity_peer):
                peer_hash = ""
        if self.peer_resolver:
            try:
                ident_hex = ""
                computed_dest = ""
                ident = link.get_remote_identity()
                if ident and hasattr(ident, "hash") and ident.hash:
                    ident_hex = normalize_hash(RNS.hexrep(ident.hash))
                    computed_dest = self._dest_hash_from_identity(ident)
                fixed = self.peer_resolver(
                    ident_hex=ident_hex,
                    computed_dest=computed_dest,
                    link=link,
                )
                if fixed and not self._is_self_hash(fixed):
                    return self.dest_hash_for(fixed)
            except Exception as e:
                print(f"[messaging] incoming peer resolve fallback: {e}")
        resolved = self._resolve_remote_peer(link)
        if resolved and resolved != "unknown" and not self._is_self_hash(resolved):
            return self.dest_hash_for(resolved)
        if self.active_peer_hash and not self._is_self_hash(self.active_peer_hash):
            if (
                not is_hub_peer_hash(self.active_peer_hash)
                and self._incoming_matches_active_session(link)
                and self._link_acceptable_for_peer(link, self.active_peer_hash)
            ):
                return self.dest_hash_for(self.active_peer_hash)
        return peer_hash or "unknown"

    def _incoming_matches_active_session(self, link):
        if not self.active_peer_hash or not self.active_link:
            return False
        if not self._link_acceptable_for_peer(link, self.active_peer_hash):
            return False
        try:
            ident = link.get_remote_identity()
            if ident:
                computed_dest = self._dest_hash_from_identity(ident)
                if computed_dest and self.hashes_equivalent(computed_dest, self.active_peer_hash):
                    return True
                ident_hex = normalize_hash(RNS.hexrep(ident.hash))
                if ident_hex and self.hashes_equivalent(ident_hex, self.active_peer_hash):
                    return True
        except Exception:
            pass
        return False

    def _handoff_to_link(self, link, peer_hash):
        peer_hash = self.dest_hash_for(peer_hash)
        old = self.active_link
        old_id = old.link_id if old else None
        old_score = self._link_path_score(old)
        new_score = self._link_path_score(link)
        self._link_handoff = True
        self._last_handoff = new_score > old_score + 8
        try:
            print(
                f"[messaging] Path switch to {peer_hash[:16]} "
                f"(score {self._link_path_score(link)} vs {self._link_path_score(old)})"
            )
            self._setup_link(link)
            self._cache_link_peer(link, peer_hash)
            self._notify_link_established(link, peer_hash)
            self._send_link = link
            self._migrate_pending_files(old_id, link.link_id)
            if old and old.link_id != link.link_id:
                try:
                    old.teardown()
                except Exception:
                    pass
            self._schedule_queue_drain(peer_hash, link=link, include_files=False, delay=0.5)
        finally:
            self._link_handoff = False

    def _links_for_peer(self, peer_hash):
        peer = self.dest_hash_for(peer_hash)
        if not peer or peer == "unknown":
            return []
        seen = set()
        out = []
        for link in self._other_active_links_for_peer(peer):
            lid = getattr(link, "link_id", None)
            if lid and lid in seen:
                continue
            if lid:
                seen.add(lid)
            out.append(link)
        for cached_key, link in list(self.peer_links.items()):
            if not self.hashes_equivalent(self._peer_from_link_key(cached_key), peer):
                continue
            lid = getattr(link, "link_id", None)
            if lid and lid in seen:
                continue
            if lid:
                seen.add(lid)
            out.append(link)
        return out

    def _best_outgoing_link(self, peer_hash=None):
        """Pick the best link for sends, locked to the peer's transport zone."""
        peer = self.dest_hash_for(
            peer_hash or self.active_peer_hash or self._session_peer_hash or ""
        )
        if not peer or peer == "unknown":
            return None
        session_transport = None
        if self._session_peer_hash and self.hashes_equivalent(peer, self._session_peer_hash):
            session_transport = self._session_transport
        if session_transport:
            preferred = self._link_for_peer(peer, transport=session_transport)
            if preferred and self._link_interface_healthy(preferred):
                if self._link_matches_peer(preferred, peer) and self._link_acceptable_for_peer(preferred, peer):
                    return preferred
        expected = self._peer_expected_transport_families(peer)
        if expected == {"serial"}:
            prefer = ("serial",)
        elif expected & {"udp", "lan", "tcp"}:
            prefer = ("tcp", "lan", "udp")
        else:
            prefer = ("tcp", "lan", "udp", "serial")
        best = None
        best_score = -1
        hub_p2p = self._hub_transport_active() and not self._peer_uses_hub_transport(peer)
        for link in self._links_for_peer(peer):
            if not self._link_matches_peer(link, peer):
                continue
            if not self._link_interface_healthy(link):
                continue
            if not self._link_acceptable_for_peer(link, peer):
                continue
            iface = self._link_attached_interface(link)
            if hub_p2p and self._link_is_hub_transport(iface):
                continue
            fam = interface_family(iface)
            if expected:
                if fam == "serial" and "serial" not in expected:
                    continue
                if fam in ("udp", "lan", "tcp") and not (expected & {"udp", "lan", "tcp"}):
                    continue
            fam_rank = len(prefer) - prefer.index(fam) if fam in prefer else 0
            score = self._link_path_score(link) + fam_rank * 5
            if score > best_score:
                best_score = score
                best = link
        if best:
            return best
        for link in self._links_for_peer(peer):
            if not self._link_matches_peer(link, peer):
                continue
            if not self._link_acceptable_for_peer(link, peer):
                continue
            iface = self._link_attached_interface(link)
            if hub_p2p and self._link_is_hub_transport(iface):
                continue
            try:
                if link.status == RNS.Link.ACTIVE:
                    return link
            except Exception:
                return link
        return self._link_for_peer(peer)

    def _peer_link_active(self, dest_hex, alt_hex=None, transport=None):
        for raw in (dest_hex, alt_hex):
            if not raw:
                continue
            peer = self.dest_hash_for(raw)
            link = self._link_for_peer(peer, transport=transport)
            if not link:
                continue
            try:
                active = link.status == RNS.Link.ACTIVE
            except Exception:
                active = True
            if (
                active
                and self._link_matches_peer(link, peer)
                and self._link_acceptable_for_peer(link, peer)
                and self._link_transport_matches(link, transport)
            ):
                return True
        if transport:
            return False
        found = self._find_active_link_for_peer(dest_hex, alt_hex)
        if not found:
            return False
        peer = self.dest_hash_for(dest_hex or alt_hex)
        return (
            self._link_matches_peer(found, peer)
            and self._link_acceptable_for_peer(found, peer)
        )

    def _peer_link_usable(self, dest_hex, alt_hex=None, transport=None):
        """True when an active link also has a healthy interface and RNS path."""
        if not self._peer_link_active(dest_hex, alt_hex, transport=transport):
            return False, None
        peer = self.dest_hash_for(dest_hex)
        adopt = self._link_for_peer(peer, transport=transport)
        if not adopt and transport:
            return False, None
        if not adopt:
            adopt = self._find_active_link_for_peer(dest_hex, alt_hex)
        if not adopt:
            return False, None
        if transport and not self._link_transport_matches(adopt, transport):
            return False, None
        if not self._link_interface_healthy(adopt) or not self._peer_has_path(dest_hex):
            return False, adopt
        return True, adopt
