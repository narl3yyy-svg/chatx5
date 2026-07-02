"""Auto-extracted from web/server.py — QueueRoutes layer."""


from aiohttp import web


class QueueRoutesMixin:
    async def handle_queue(self, request):
        if not self.messaging:
            return web.json_response({"count": 0, "total": 0, "items": []})
        total = self.messaging.queue_size()
        peer = request.query.get("peer", "").strip()
        if peer:
            peer_clean = self._peer_dest_hash(peer)
            count = self.messaging.queue_size_for(peer_clean)
            items = [
                e for e in self.messaging.message_queue
                if self.messaging._queue_matches_target(e, peer_clean)
            ]
        else:
            count = total
            items = self.messaging.message_queue[-20:]
        return web.json_response({
            "count": count,
            "total": total,
            "items": items[-20:],
        })

    async def handle_queue_clear(self, request):
        cleared = 0
        if self.messaging:
            peer = None
            if request.can_read_body:
                try:
                    data = await request.json()
                    peer = (data.get("peer") or "").strip() or None
                except Exception:
                    pass
            if not peer:
                peer = request.query.get("peer", "").strip() or None
            before = self.messaging.queue_size()
            if peer:
                self.messaging.clear_queue(self._peer_dest_hash(peer))
            else:
                self.messaging.clear_queue()
            cleared = before - self.messaging.queue_size()
            if cleared:
                self.message_history = [
                    m for m in self.message_history if m.get("status") != "queued"
                ]
                self._save_history()
        await self._broadcast({"type": "queue_cleared", "data": {"count": cleared}})
        return web.json_response({"status": "ok", "cleared": cleared})

