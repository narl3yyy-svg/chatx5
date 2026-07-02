"""Auto-extracted from web/server.py — DiscoveryRoutes layer."""

import asyncio

from aiohttp import web

from chatx5.core.lan_rns import (
    lan_ip_reachable,
    serial_interface_online,
)
from chatx5.core.rns_interfaces import (
    configured_serial_enabled,
    lan_discovery_configured,
)
from chatx5.utils.platform import (
    is_android,
)


class DiscoveryRoutesMixin:
    async def handle_discover(self, request):
        peers = self._scoped_peers()
        if self.active_peer and not any(p.get("hash", "").replace(":", "") == self.active_peer for p in peers):
            peers.append({
                "hash": self.active_peer,
                "name": self.active_peer[:8],
                "app": "chatx5",
                "connected": True,
            })
        return web.json_response({"peers": peers})

    async def handle_discover_refresh(self, request):
        """Re-announce, purge stale discovery rows, and return an authoritative peer list."""
        ok, err = await self._wait_for_rns()
        if not ok:
            return web.json_response({"error": err or "not ready"}, status=400)
        if self.discovery:
            self.discovery.purge_misclassified_serial()
            self.discovery.purge_ipless_non_serial()
            self.discovery.purge_stale_probes()
        settings = self.load_settings()
        configured = settings.get("rns_interfaces")
        try:
            if self.messaging:
                if lan_discovery_configured(configured) and lan_ip_reachable():
                    await asyncio.to_thread(
                        self.messaging._silent_announce, also_serial=False,
                    )
                if configured_serial_enabled(configured) and serial_interface_online():
                    await asyncio.to_thread(
                        self.messaging._burst_serial_announce, 1, force=True,
                    )
            if self.lan_beacon and lan_ip_reachable():
                await asyncio.to_thread(self.lan_beacon.send, 1, is_android())
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=500)
        peers = await self._broadcast_peers(authoritative=True)
        print(f"[discovery] Manual refresh — {len(peers)} peer(s)")
        return web.json_response({"peers": peers, "count": len(peers)})

