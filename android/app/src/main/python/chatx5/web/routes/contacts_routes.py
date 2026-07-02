"""Auto-extracted from web/server.py — ContactRoutes layer."""


from aiohttp import web

from chatx5.core.contacts import (
    delete_contact as delete_saved_contact,
)
from chatx5.core.contacts import (
    save_contact,
)


class ContactRoutesMixin:
    async def handle_add_contact(self, request):
        try:
            data = await request.json()
            peer_hash = data.get("hash", "").strip().replace(":", "")
            name = data.get("name", peer_hash).strip()
            if not peer_hash:
                return web.json_response({"error": "hash required"}, status=400)
            entry = save_contact(
                self.config_dir,
                peer_hash,
                name=name or peer_hash,
                ip=data.get("ip"),
                port=data.get("port"),
                identity_hash=data.get("identity_hash"),
                via=data.get("via"),
                lan_hash=data.get("lan_hash"),
                serial_hash=data.get("serial_hash"),
                lan_identity_hash=data.get("lan_identity_hash"),
                serial_identity_hash=data.get("serial_identity_hash"),
                custom_name=bool(data.get("custom_name")),
            )
            self._schedule_contacts_broadcast()
            return web.json_response({"status": "ok", "contact": entry})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def handle_delete_contact(self, request):
        try:
            peer_hash = request.match_info["hash"].replace(":", "")
            if delete_saved_contact(self.config_dir, peer_hash):
                resolved = self._peer_dest_hash(peer_hash)
                if self.messaging and resolved:
                    self.messaging.disconnect_peer(resolved, user_initiated=True)
                    self.messaging.mark_user_disconnected(resolved)
                    self.messaging.clear_session_peer()
                if self.active_peer and self._peers_equivalent(self.active_peer, peer_hash):
                    self.active_peer = None
                if self._ui_state.get("viewing_peer") and self._peers_equivalent(
                    self._ui_state.get("viewing_peer"), peer_hash
                ):
                    self._ui_state["viewing_peer"] = None
                self._schedule_contacts_broadcast()
                return web.json_response({"status": "ok"})
            return web.json_response({"error": "not found"}, status=404)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

