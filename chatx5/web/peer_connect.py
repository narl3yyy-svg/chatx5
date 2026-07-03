"""Auto-extracted from web/server.py — PeerConnect layer."""

import asyncio
import time

from aiohttp import web

from chatx5.core.contacts import (
    contact_connect_meta,
    contact_has_hash,
)
from chatx5.core.lan_rns import (
    patch_udp_interface_unicast,
    serial_interface_online,
)
from chatx5.core.rns_interfaces import (
    configured_serial_enabled,
    configured_tcp_lan_enabled,
    configured_udp_lan_enabled,
    dedupe_serial_interfaces,
    ensure_runtime_serial,
    ensure_runtime_tcp_lan_server,
    lan_discovery_configured,
    normalize_interface_list,
    prune_dead_serial_interfaces,
)
from chatx5.utils.platform import (
    is_android,
    physical_lan_reachable,
)
from chatx5.web.rns_utils import (
    detect_lan_ip,
)


class PeerConnectMixin:
    def _discovery_peer_for_connect(self, peer_ip, hash_hex, via=None):
        from chatx5.core.discovery import normalize_hash
        if not self.discovery:
            return None
        clean = normalize_hash(hash_hex)
        requested = (via or "").strip().lower()
        if clean:
            serial_row = None
            lan_row = None
            for p in self._scoped_peers():
                ph = normalize_hash(p.get("hash"))
                if ph != clean:
                    continue
                if (p.get("via") or "").strip() == "serial":
                    serial_row = p
                else:
                    lan_row = lan_row or p
            if requested == "serial" and serial_row:
                return serial_row
            if requested != "lan" and serial_row and not lan_row:
                return serial_row
        by_hash = None
        by_serial = None
        by_rns = None
        by_ip = None
        for p in self._scoped_peers():
            ph = normalize_hash(p.get("hash"))
            ih = normalize_hash(p.get("identity_hash"))
            if peer_ip and p.get("ip") == peer_ip:
                by_ip = p
            if clean and (ph == clean or ih == clean):
                pvia = (p.get("via") or "").strip()
                if pvia == "serial":
                    by_serial = p
                else:
                    by_hash = by_hash or p
                if pvia == "rns":
                    by_rns = p
        if requested == "serial":
            return by_serial
        if requested in ("lan", "rns", "beacon"):
            return by_rns or by_hash
        if by_serial and by_rns:
            from chatx5.utils.lan_scope import peer_in_scope

            rns_ip = (by_rns.get("ip") or "").strip()
            scope = self._discovery_scope_ip()
            in_scope = rns_ip and (not scope or peer_in_scope(rns_ip, scope))
            if peer_ip and rns_ip and peer_ip == rns_ip:
                return by_rns
            if peer_ip and rns_ip and peer_ip != rns_ip:
                return by_serial
            if not in_scope:
                return by_serial
            return by_rns or by_hash
        if by_serial:
            return by_serial
        if by_rns:
            return by_rns
        if by_hash:
            return by_hash
        if clean:
            return None
        return by_ip

    def _resolve_connect_target(self, peer_hash, peer_ip=None):
        resolved = self._resolve_peer_hash(peer_hash)
        if not self.discovery:
            return resolved
        from chatx5.core.discovery import normalize_hash
        clean = normalize_hash(resolved)
        for p in self._scoped_peers():
            ph = normalize_hash(p.get("hash"))
            ih = normalize_hash(p.get("identity_hash"))
            if clean and (ph == clean or ih == clean):
                return self._resolve_peer_hash(p.get("hash"))
        if peer_ip and not clean:
            for p in self._scoped_peers():
                if p.get("ip") == peer_ip:
                    return self._resolve_peer_hash(p.get("hash"))
        return resolved

    async def handle_connect(self, request):
        if self._shutting_down:
            return web.json_response({"error": "server shutting down"}, status=503)
        try:
            data = await request.json()
            peer_hash = data.get("hash", "").strip()
            if not peer_hash:
                return web.json_response({"error": "hash required"}, status=400)
            peer_ip = (data.get("ip") or "").strip() or None
            peer_port = data.get("port") or 8742
            prefer_via = (data.get("via") or "").strip() or None
            if prefer_via in ("serial", "usb"):
                peer_ip = None
            transport_hash = self._contact_hash_for_transport(peer_hash, prefer_via)
            if transport_hash:
                peer_hash = transport_hash
            self._enable_discovery(clear=False)
            settings = self.load_settings()
            configured = settings.get("rns_interfaces")
            if (
                self.messaging
                and configured_serial_enabled(configured)
                and not lan_discovery_configured(configured)
            ):
                await self._run_blocking(self.messaging._burst_serial_announce, 1)
            resolved_hash = self._resolve_connect_target(peer_hash, peer_ip)
            resolved_hash = self._resolve_current_peer_hash(
                resolved_hash, peer_ip, prefer_via=prefer_via,
            )
            hub_role = settings.get("hub_role", "off")
            scope_ip = self._discovery_scope_ip()
            if (
                scope_ip
                and not contact_has_hash(self.config_dir, resolved_hash)
                and not self._peer_in_discovery_scope(resolved_hash)
            ):
                return web.json_response({
                    "error": (
                        f"Peer is outside pinned LAN scope ({scope_ip}) — "
                        "pick the matching IPv4 on both devices"
                    ),
                }, status=400)
            saved_contact = self._find_saved_contact(peer_hash) or self._find_saved_contact(
                resolved_hash,
            )
            if (
                self.discovery
                and hub_role == "off"
                and not saved_contact
                and not contact_has_hash(self.config_dir, peer_hash)
                and not contact_has_hash(self.config_dir, resolved_hash)
                and not self._peer_is_current(resolved_hash)
                and not (
                    self.messaging
                    and self.messaging._peer_link_active(resolved_hash)
                )
            ):
                return web.json_response({
                    "error": "Stale peer hash — use the peer in Discovered or wait for Announce",
                }, status=400)
            peer_info = self._discovery_peer_for_connect(
                peer_ip, resolved_hash, via=prefer_via,
            )
            if not peer_info:
                peer_info = self._peer_in_discovery(resolved_hash, peer_ip)
            if peer_info:
                from chatx5.core.discovery import register_identity_from_peer
                if register_identity_from_peer(peer_info):
                    print(
                        f"[connect] Pre-registered identity from discovery "
                        f"({peer_info.get('ip', '?')})"
                    )
                if not peer_ip and peer_info.get("ip"):
                    peer_ip = peer_info.get("ip")
                    peer_port = peer_info.get("port") or peer_port
            peer_ip, peer_port = self._resolve_peer_connect_ip(resolved_hash, peer_ip, peer_port)
            caller_ip = detect_lan_ip() or (self.host if self.host not in ("127.0.0.1", "0.0.0.0") else "")
            if is_android() and not caller_ip:
                print("[connect] Warning: could not detect Android LAN IP - reverse connect may fail")
            ok = await self._run_blocking(
                self.messaging.connect_to,
                resolved_hash,
                peer_ip,
                peer_port,
                lambda ip, h: self._discovery_peer_for_connect(ip, h, via=prefer_via),
                caller_ip,
                self.port,
                False,
                False,
                False,
                True,
                prefer_via,
            )
            if self._shutting_down or ok is None:
                return web.json_response({"error": "server shutting down"}, status=503)
            if ok:
                clean = self._peer_dest_hash(
                    self.messaging.active_peer_hash or resolved_hash
                )
                self.active_peer = clean
                return web.json_response({
                    "status": "ok",
                    "hash": clean,
                    "linked_peers": self.messaging.linked_peers(),
                })
            return web.json_response({"error": "connection failed"}, status=400)
        except asyncio.CancelledError:
            return web.json_response({"error": "server shutting down"}, status=503)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def _reverse_connect_task(self, peer_hash, peer_ip, peer_port, caller_ip, caller_port):
        """Background outbound link for /api/request_connect (must return HTTP quickly)."""
        try:
            result = await self._run_blocking(
                self.messaging.connect_to,
                peer_hash,
                peer_ip,
                peer_port,
                self._discovery_peer_for_connect,
                caller_ip,
                caller_port,
                False,
                False,
                True,
            )
            if self._shutting_down or result is None:
                return
            if result:
                clean = self._peer_dest_hash(peer_hash)
                if getattr(self.messaging, "_connect_user_initiated", False):
                    self.active_peer = clean
                print(f"[connect] Outbound-connect established with {clean[:16]}...")
            else:
                await self._broadcast({"type": "connect_fail", "error": "reverse connect failed"})
        except Exception as e:
            print(f"[connect] Reverse-connect task error: {e}")
            try:
                await self._broadcast({"type": "connect_fail", "error": str(e)})
            except Exception:
                pass

    async def handle_request_connect(self, request):
        """Peer asks us to open an outbound RNS link (reverse connect for Android)."""
        ok, err = await self._wait_for_rns()
        if not ok:
            return web.json_response({"error": err or "not ready"}, status=400)
        try:
            data = await request.json()
            peer_hash = (data.get("hash") or "").strip()
            if not peer_hash:
                return web.json_response({"error": "hash required"}, status=400)
            peer_ip = (data.get("ip") or "").strip() or None
            peer_port = data.get("port") or 8742
            caller_ip = detect_lan_ip() or (self.host if self.host not in ("127.0.0.1", "0.0.0.0") else "")
            resolved = self._resolve_connect_target(peer_hash, peer_ip)
            if self.messaging and self.messaging.is_user_disconnected(resolved):
                return web.json_response({
                    "status": "ok",
                    "passive": True,
                    "connected": False,
                })
            if self.messaging and self.messaging._peer_link_active(resolved):
                return web.json_response({
                    "status": "ok",
                    "connected": True,
                    "linked_peers": self.messaging.linked_peers(),
                })
            if self.messaging and self.messaging.active_link:
                if self._peers_equivalent(resolved, self.messaging.active_peer_hash):
                    return web.json_response({
                        "status": "ok",
                        "connected": True,
                        "linked_peers": self.messaging.linked_peers(),
                    })
            dedupe_key = f"{peer_ip or 'unknown'}:{resolved[:16]}"
            now = time.time()
            if now - self._reverse_connect_last.get(dedupe_key, 0) < 3.0:
                return web.json_response({"status": "ok", "connecting": True, "deduped": True})
            self._reverse_connect_last[dedupe_key] = now
            caller_from = (data.get("ip") or "").strip()
            if caller_from:
                from chatx5.core.lan_rns import register_udp_peer_ip
                register_udp_peer_ip(caller_from)
            print(
                f"[connect] Outbound-connect request from {caller_from or peer_ip or 'unknown'} "
                f"for {resolved[:16]}..."
            )
            asyncio.create_task(
                self._reverse_connect_task(
                    resolved, caller_from or peer_ip, peer_port, caller_ip, self.port
                )
            )
            return web.json_response({"status": "ok", "connecting": True})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    def _peer_in_discovery(self, peer_hash, peer_ip=None):
        from chatx5.core.discovery import normalize_hash
        if not self.discovery:
            return None
        clean = normalize_hash(peer_hash)
        by_hash = None
        by_ip = None
        for p in self.discovery.get_peers():
            ph = normalize_hash(p.get("hash"))
            ih = normalize_hash(p.get("identity_hash"))
            if peer_ip and p.get("ip") == peer_ip:
                by_ip = p
            if clean and (
                ph == clean
                or ih == clean
                or self._peers_equivalent(ph, clean)
                or (ih and self._peers_equivalent(ih, clean))
            ):
                by_hash = p
        return by_hash or by_ip

    def _peer_connect_meta(self, peer_hash):
        peer_ip = None
        peer_port = 8742
        meta = self._discovery_peer_for_connect(None, peer_hash)
        if meta:
            via = (meta.get("via") or "").strip()
            if via == "serial":
                return None, meta.get("port") or peer_port
            if meta.get("ip"):
                return meta.get("ip"), meta.get("port") or peer_port
            peer_port = meta.get("port") or peer_port
        stored_ip, stored_port = contact_connect_meta(
            self.config_dir, peer_hash, self._peers_equivalent
        )
        if stored_ip and not (
            meta and (meta.get("via") or "").strip() == "serial"
        ):
            peer_ip = stored_ip
            peer_port = stored_port or peer_port
        peer = self._peer_in_discovery(peer_hash)
        if peer and not (
            (peer.get("via") or "").strip() == "serial" or (meta and not meta.get("ip"))
        ):
            if peer.get("ip"):
                peer_ip = peer.get("ip")
            peer_port = peer.get("port") or peer_port
        return peer_ip, peer_port

    def _resolve_peer_connect_ip(self, peer_hash, peer_ip=None, peer_port=8742):
        """Fill peer IP/port from discovery when the UI did not pass them (common on Android)."""
        meta = self._discovery_peer_for_connect(peer_ip, peer_hash)
        if meta and (meta.get("via") or "").strip() == "serial":
            return None, meta.get("port") or peer_port
        if peer_ip:
            return peer_ip, peer_port
        resolved_ip, resolved_port = self._peer_connect_meta(peer_hash)
        if resolved_ip:
            return resolved_ip, resolved_port or peer_port
        return peer_ip, peer_port

    async def _resume_session_task(self, peer, peer_ip, peer_port):
        try:
            if self.messaging and self.messaging.is_user_disconnected(peer):
                return
            result = await self._run_blocking(
                self.messaging.resume_session_peer,
                peer_ip,
                peer_port,
                self._discovery_peer_for_connect,
                detect_lan_ip(),
                self.port,
            )
            if self._shutting_down or result is None:
                return
            if result:
                clean = self._peer_dest_hash(self.messaging.active_peer_hash or peer)
                self.active_peer = clean
                await self._broadcast({"type": "link_established", "data": {"hash": clean}})
                print(f"[connect] Session resumed with {clean[:16]}...")
        except Exception as e:
            print(f"[connect] Session resume error: {e}")

    async def _link_failover_loop(self):
        """Detect dead or migrated RNS paths and reconnect without server restart."""
        physical_lan_was_up = physical_lan_reachable()
        while not self._shutting_down:
            try:
                await asyncio.sleep(8)
            except asyncio.CancelledError:
                break
            if self._shutting_down or not self.messaging:
                continue
            from chatx5.core.lan_rns import (
                clear_paths_on_family,
                prune_bridged_lan_paths,
                prune_cross_zone_paths,
                prune_stale_lan_paths,
                suppress_offline_lan_transports,
            )
            physical_lan_up = physical_lan_reachable()
            if physical_lan_up and not physical_lan_was_up and self.messaging:
                await self._run_blocking(self.messaging._silent_announce)
                self.messaging._transport_reconnect_pending = True
                self.messaging._failover_last_attempt = 0
                print("[network] LAN restored — refreshing paths and reconnecting")
            physical_lan_was_up = physical_lan_up
            await self._run_blocking(suppress_offline_lan_transports)
            await self._run_blocking(dedupe_serial_interfaces)
            if not serial_interface_online():
                await self._run_blocking(prune_dead_serial_interfaces)
                await self._run_blocking(clear_paths_on_family, "serial")
            await self._run_blocking(prune_stale_lan_paths)
            await self._run_blocking(prune_bridged_lan_paths)
            serial_peers = []
            if self.discovery:
                for p in self.discovery.get_peers():
                    if (p.get("via") or "").strip() == "serial":
                        for key in ("hash", "identity_hash"):
                            h = (p.get(key) or "").strip()
                            if h:
                                serial_peers.append(h)
            if serial_peers:
                await self._run_blocking(prune_cross_zone_paths, serial_peers)
            if self.messaging.dual_identity_mode:
                continue

            peer = self._peer_dest_hash(
                getattr(self.messaging, "_session_peer_hash", None)
                or self.messaging.active_peer_hash
                or self.active_peer
            )
            if not peer:
                continue

            needs, reason = self.messaging.session_needs_reconnect()
            if not needs:
                continue

            settings = self.load_settings()
            interfaces = normalize_interface_list(settings.get("rns_interfaces"))
            hub_role = settings.get("hub_role", "off")
            if hub_role != "off" and not lan_discovery_configured(interfaces):
                continue
            if configured_udp_lan_enabled(interfaces):
                await self._run_blocking(patch_udp_interface_unicast)
            if configured_tcp_lan_enabled(interfaces) and hub_role != "server":
                await self._run_blocking(ensure_runtime_tcp_lan_server, settings, self.config_dir)
            await self._run_blocking(ensure_runtime_serial, interfaces)

            peer_ip, peer_port = self._peer_connect_meta(peer)
            if not physical_lan_reachable() and configured_serial_enabled(interfaces):
                peer_ip = None
            if (
                configured_udp_lan_enabled(interfaces)
                and physical_lan_reachable()
                and self.lan_beacon
            ):
                await self._run_blocking(self.lan_beacon.send, 1, False)
            print(f"[connect] Failover triggered: {reason}")
            if self._shutting_down:
                continue

            result = await self._run_blocking(
                self.messaging.reconnect_active_peer,
                peer_ip,
                peer_port,
                self._discovery_peer_for_connect,
                detect_lan_ip(),
                self.port,
                reason,
            )
            if result:
                clean = self._peer_dest_hash(self.messaging.active_peer_hash or peer)
                self.active_peer = clean
                print(f"[connect] Failover complete with {clean[:16]}...")
            else:
                print(f"[connect] Failover attempt failed ({reason})")

