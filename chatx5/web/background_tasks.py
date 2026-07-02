"""Auto-extracted from web/server.py — BackgroundTasks layer."""

import asyncio
import threading
import time


class BackgroundTasksMixin:
    def _probe_interval_s(self, transport="lan", settings=None):
        from chatx5.core.peer_probe import (
            PROBE_INTERVAL_S,
            clamp_probe_interval,
            clamp_serial_probe_interval,
        )
        settings = settings or self.load_settings()
        if transport == "serial":
            return clamp_serial_probe_interval(
                settings.get("serial_probe_interval_s", PROBE_INTERVAL_S),
            )
        return clamp_probe_interval(
            settings.get("lan_probe_interval_s", settings.get("probe_interval_s", PROBE_INTERVAL_S)),
        )

    def _apply_probe_interval_settings(self, settings=None):
        from chatx5.core.peer_probe import clamp_announce_interval, clamp_probe_interval
        settings = settings or self.load_settings()
        lan_probe = clamp_probe_interval(
            settings.get("lan_probe_interval_s", settings.get("probe_interval_s", 30)),
        )
        if self.lan_beacon:
            self.lan_beacon.set_interval(lan_probe or 30)
        if self.messaging:
            self.messaging.announce_interval = lan_probe or 30
            self.messaging.lan_announce_interval_s = clamp_announce_interval(
                settings.get("lan_announce_interval_s", 0),
            )
            self.messaging.serial_announce_interval_s = clamp_announce_interval(
                settings.get("serial_announce_interval_s", 0),
            )
            auto = (
                self.messaging.lan_announce_interval_s > 0
                or self.messaging.serial_announce_interval_s > 0
            )
            self.messaging.auto_announce = auto
            if auto and not self.messaging._announce_thread:
                self.messaging._announce_thread = threading.Thread(
                    target=self.messaging._announce_loop, daemon=True,
                )
                self.messaging._announce_thread.start()

    def _probe_discovered_peers(self):
        if not self.discovery or not self.discovery.accept_peers:
            return 0, False
        if self.messaging and self.messaging._has_active_transfer():
            return 0, False
        from chatx5.core.peer_probe import (
            link_rtt_ms,
            probe_packet_bytes,
            probe_serial_path,
            probe_udp_peer,
        )

        now = time.time()
        settings = self.load_settings()
        rtt_updated = False
        for peer in list(self.discovery.peers.values()):
            hash_hex = peer.get("hash") or ""
            via = (peer.get("via") or "").strip()
            ip = (peer.get("ip") or "").strip()
            is_serial = via == "serial"
            probe_interval = self._probe_interval_s(
                "serial" if is_serial else "lan", settings=settings,
            )
            if probe_interval <= 0:
                continue
            last_probe = float(peer.get("last_rtt_probe_at") or 0)
            if last_probe and (now - last_probe) < probe_interval:
                continue
            peer["last_rtt_probe_at"] = now
            probe_transport = "serial" if is_serial else "lan"
            link_rtt = (
                link_rtt_ms(self.messaging, hash_hex, transport=probe_transport)
                if self.messaging else None
            )
            if is_serial:
                rtt = link_rtt
                if rtt is None:
                    rtt = probe_serial_path(hash_hex, timeout_s=1.5)
                if rtt is not None:
                    self.discovery.update_peer_probe(
                        hash_hex, rtt_ms=rtt, ok=True, via=probe_transport,
                    )
                    rtt_updated = True
                else:
                    if self.discovery.clear_peer_rtt(hash_hex, via=probe_transport):
                        rtt_updated = True
                    self.discovery.update_peer_probe(hash_hex, ok=False, via=probe_transport)
                continue
            if ip:
                rtt = probe_udp_peer(ip, timeout_s=1.5, packet_bytes=probe_packet_bytes())
                if rtt is not None:
                    self.discovery.update_peer_probe(
                        hash_hex, rtt_ms=rtt, ok=True, via=probe_transport,
                    )
                    rtt_updated = True
                else:
                    if self.discovery.clear_peer_rtt(hash_hex, via=probe_transport):
                        rtt_updated = True
                    self.discovery.update_peer_probe(hash_hex, ok=False, via=probe_transport)
                continue
            if link_rtt is not None:
                self.discovery.update_peer_probe(
                    hash_hex, rtt_ms=link_rtt, ok=True, via=probe_transport,
                )
                rtt_updated = True
        removed = self.discovery.purge_stale_probes()
        if rtt_updated and self.websockets and self._loop:
            self._schedule_peers_broadcast()
        return removed, bool(rtt_updated)

    async def _peer_probe_loop(self):
        await asyncio.sleep(6)
        while not self._shutting_down:
            probe_interval = self._probe_interval_s()
            scope_changed = False
            try:
                scope_changed = await asyncio.to_thread(self._maybe_apply_live_scope_change)
            except Exception as exc:
                print(f"[probe] Live scope check failed: {exc}")
            if scope_changed:
                try:
                    await self._broadcast_peers(authoritative=True)
                except Exception as exc:
                    print(f"[probe] Peers broadcast after scope drift failed: {exc}")
            try:
                if self.discovery and self.discovery.accept_peers:
                    removed, rtt_updated = await asyncio.to_thread(
                        self._probe_discovered_peers
                    )
                    if removed or rtt_updated or scope_changed:
                        await self._broadcast_peers(authoritative=bool(removed))
            except Exception as exc:
                print(f"[probe] Peer probe failed: {exc}")
            try:
                await asyncio.sleep(probe_interval)
            except asyncio.CancelledError:
                return

    async def _discovery_broadcaster(self):
        print("[broadcaster] Started")
        last_snapshot = None
        while True:
            await asyncio.sleep(1)
            if not self.websockets or not self.discovery:
                continue
            peers = self._scoped_peers()
            snapshot = tuple(
                sorted(
                    (
                        (p.get("hash") or ""),
                        (p.get("identity_hash") or ""),
                        (p.get("via") or ""),
                        (p.get("ip") or ""),
                        int(p.get("last_seen", 0)),
                        p.get("rtt_ms"),
                        p.get("rtt_avg_ms"),
                    )
                    for p in peers
                )
            )
            self._prune_websockets()
            if snapshot != last_snapshot:
                count = len(peers)
                print(f"[broadcaster] {count} peer(s), {self._ws_client_count()} ws client(s)")
                last_snapshot = snapshot
                await self._broadcast({"type": "peers", "data": peers})

    async def _queue_retry_loop(self):
        while not self._shutting_down:
            try:
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                break
            if self._shutting_down or not self.messaging:
                continue
            if not self.messaging.message_queue:
                continue
            try:
                sent = await asyncio.to_thread(self.messaging.retry_queue)
                if sent and self.websockets:
                    await self._broadcast({
                        "type": "queue_drained",
                        "data": {"sent": sent, "remaining": self.messaging.queue_size()},
                    })
            except Exception as e:
                print(f"[queue] Server retry error: {e}")

