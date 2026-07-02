"""Auto-extracted from web/server.py — IdentityRoutes layer."""


import RNS
from aiohttp import web

from chatx5._version import __version__ as APP_VERSION
from chatx5.core.contacts import (
    list_contacts,
)
from chatx5.core.lan_rns import serial_interface_online
from chatx5.core.rns_interfaces import (
    configured_serial_enabled,
    configured_serial_port,
)
from chatx5.utils.debug_log import (
    debug_log_path,
)
from chatx5.utils.platform import (
    is_android,
)


class IdentityRoutesMixin:
    async def handle_identity(self, request):
        if not self.identity_mgr.identity:
            try:
                self.identity_mgr.load_or_create()
            except Exception:
                pass
        from chatx5.core.discovery import normalize_hash
        from chatx5.core.peer_identity import connect_hash_for_manager

        connect = ""
        if self.messaging and self.messaging.my_dest_hash:
            connect = normalize_hash(self.messaging.my_dest_hash)
        elif self.messaging and self.messaging.destination:
            connect = normalize_hash(RNS.hexrep(self.messaging.destination.hash))
        else:
            connect = connect_hash_for_manager(
                self.identity_mgr,
                getattr(self.messaging, "destination", None) if self.messaging else None,
            )
        if not connect:
            connect = normalize_hash(self.destination_hash or "")
        if not connect:
            connect = self.identity_mgr.get_connect_hash()
        identity_raw = normalize_hash(self.identity_mgr.get_hex_hash() if self.identity_mgr else "")
        contacts = list_contacts(self.config_dir)
        discovered = self._scoped_peers()
        link_active = bool(self.messaging and self.messaging.active_link)
        connected = self.active_peer if link_active and self.active_peer else None
        linked_peers = self.messaging.linked_peers() if self.messaging else []
        settings = self.load_settings()
        id_payload = self.identity_mgr.identity_payload() if hasattr(self.identity_mgr, "identity_payload") else {}
        serial_connect = ""
        if self.messaging and getattr(self.messaging, "my_dest_hash_serial", None):
            serial_connect = normalize_hash(self.messaging.my_dest_hash_serial)
        elif id_payload.get("serial"):
            serial_connect = normalize_hash(id_payload["serial"].get("connect_hash") or "")
        configured = settings.get("rns_interfaces")
        serial_port, _ = configured_serial_port(configured) if configured else ("", 0)
        serial_configured = bool(
            configured and configured_serial_enabled(configured)
        )
        serial_in_rns = bool(serial_port and serial_interface_online(serial_port))
        serial_active = serial_configured or serial_in_rns
        return web.json_response({
            "hash": connect,
            "connect_hash": connect,
            "identity_hash": identity_raw,
            "lan": id_payload.get("lan") or {"connect_hash": connect, "identity_hash": identity_raw},
            "serial": id_payload.get("serial") or (
                {"connect_hash": serial_connect, "identity_hash": self.identity_mgr.get_hex_hash("serial")}
                if serial_connect or self.identity_mgr.get_hex_hash("serial") else None
            ),
            "name": settings.get("name", ""),
            "connected": connected,
            "linked_peers": linked_peers,
            "contacts": contacts,
            "discovered": discovered,
            "platform": self._platform_name(),
            "app_version": APP_VERSION,
            "rns_ready": bool(self.messaging and self.messaging.destination),
            "rns_error": self.rns_init_error,
            "debug_log_path": debug_log_path() if is_android() else None,
            "serial_active": serial_active,
            "serial_configured": serial_configured,
            "serial_in_rns": serial_in_rns,
        })

