"""Auto-extracted from web/server.py — DebugRoutes layer."""

import asyncio
import os

import RNS
from aiohttp import web

from chatx5.core.lan_beacon import BEACON_PORT
from chatx5.utils.debug_log import (
    debug_log_path,
    debug_log_tail,
    export_debug_logs,
    list_debug_log_files,
)
from chatx5.utils.platform import (
    is_android,
)


class DebugRoutesMixin:
    async def handle_debug(self, request):
        peers = self._scoped_peers()
        settings = self.load_settings()
        received_dir = settings.get("received_dir", os.path.join(self.config_dir, "received"))
        payload = {
            "identity_hash": self.identity_mgr.get_hex_hash() if self.identity_mgr else None,
            "ws_clients": self._ws_client_count(),
            "discovered_peers": peers,
            "discovery_running": self.discovery.running if self.discovery else False,
            "discovery_active": bool(self.discovery and self.discovery.accept_peers),
            "lan_beacon_port": BEACON_PORT,
            "lan_beacon_running": bool(self.lan_beacon and self.lan_beacon.running),
            "lan_beacon_targets": self.lan_beacon.last_send_targets if self.lan_beacon else [],
            "lan_beacon_sent": self.lan_beacon.packets_sent if self.lan_beacon else 0,
            "lan_beacon_received": self.lan_beacon.packets_received if self.lan_beacon else 0,
            "active_peer": self.active_peer,
            "message_count": len(self.message_history),
            "loop_running": self._loop is not None and self._loop.is_running(),
            "rns_interfaces": len(RNS.Transport.interfaces) if hasattr(RNS.Transport, 'interfaces') else "unknown",
            "received_files_dir": received_dir,
            "settings": settings,
        }
        if is_android():
            payload["debug_log_path"] = debug_log_path()
            payload["debug_log_files"] = list_debug_log_files()
            tail = debug_log_tail()
            if tail:
                payload["debug_log_tail"] = tail
        return web.json_response(payload)

    async def handle_debug_export(self, request):
        if not is_android():
            return web.json_response(
                {"error": "Debug log export is for Android debug builds"},
                status=400,
            )
        try:
            data = await request.json()
            dest = (data.get("path") or "").strip()
            if not dest:
                return web.json_response({"error": "path required"}, status=400)
            copied, err = await asyncio.to_thread(export_debug_logs, dest)
            if err and copied == 0:
                return web.json_response({"error": err}, status=400)
            return web.json_response({
                "status": "ok",
                "copied": copied,
                "path": dest,
                "warning": err,
            })
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=500)

