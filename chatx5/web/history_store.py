"""Auto-extracted from web/server.py — HistoryStore layer."""

import asyncio
import json
import os
import time

from aiohttp import web

from chatx5.core.contacts import (
    find_contact_by_hash,
)
from chatx5.core.messaging import HUB_GROUP_PEER
from chatx5.core.messaging.constants import MESSAGE_TYPE_SHARE_BROWSE
from chatx5.utils.helpers import (
    media_type_for_filename,
)
from chatx5.web.rns_utils import (
    SESSION_SYSTEM_LINK_CLOSED_TTL,
)


class HistoryStoreMixin:
    async def _remove_history_message(self, msg_id):
        clean = (msg_id or "").strip()
        if not clean:
            return False
        before = len(self.message_history)
        self.message_history = [
            m for m in self.message_history
            if (m.get("msg_id") or "") != clean
        ]
        if len(self.message_history) == before:
            return False
        self._save_history()
        await self._broadcast({"type": "message_removed", "data": {"msg_id": clean}})
        return True

    def _enrich_message(self, entry, outgoing=None):
        enriched = dict(entry)
        if outgoing is not None:
            enriched["outgoing"] = bool(outgoing)
        elif "outgoing" not in enriched:
            sender = self._peer_dest_hash(enriched.get("sender"))
            enriched["outgoing"] = bool(sender and sender == self._my_sender_hash())
        peer = enriched.get("chat_peer") or enriched.get("peer")
        if not peer:
            if enriched.get("outgoing"):
                peer = enriched.get("peer") or self.active_peer
            else:
                peer = enriched.get("sender")
        enriched["chat_peer"] = self._peer_dest_hash(peer)
        if enriched.get("file_name") and enriched.get("type") == "file":
            inferred = media_type_for_filename(enriched["file_name"])
            if inferred != "file":
                enriched["type"] = inferred
        if enriched.get("content") and enriched.get("type") in ("image", "video", "file", "voice"):
            url = self._file_url(enriched["content"])
            if url:
                enriched["file_url"] = url
        if enriched.get("type") == MESSAGE_TYPE_SHARE_BROWSE:
            share = enriched.get("share")
            if not share and enriched.get("content"):
                try:
                    share = json.loads(enriched["content"])
                    enriched["share"] = share
                except Exception:
                    share = None
            if share and not enriched.get("file_name"):
                enriched["file_name"] = share.get("root_name") or "Shared folder"
        sender = enriched.get("sender")
        if sender and sender != "system":
            sender_name = self._peer_display_name(sender)
            if sender_name:
                enriched["sender_name"] = sender_name
        return enriched

    def _is_session_system_message(self, entry):
        if isinstance(entry, str):
            content = entry
        else:
            if entry.get("type") != "system" and entry.get("sender") != "system":
                return False
            content = entry.get("content") or ""
        return (
            content.startswith("Link established with ")
            or "Link closed" in content
            or content.startswith("Connected to ")
        )

    def _prune_stale_session_system_messages(self):
        now = time.time()
        kept = []
        for m in self.message_history:
            if not self._is_session_system_message(m):
                kept.append(m)
                continue
            content = m.get("content") or ""
            if "Link closed" in content and now - m.get("timestamp", 0) < SESSION_SYSTEM_LINK_CLOSED_TTL:
                kept.append(m)
        if len(kept) != len(self.message_history):
            self.message_history = kept
            self._save_history()

    def _session_peer_at(self, timestamp):
        session_peer = None
        for m in self.message_history:
            ts = m.get("timestamp", 0)
            if ts > timestamp:
                break
            if m.get("type") != "system":
                continue
            content = m.get("content") or ""
            if content.startswith("Link established with "):
                session_peer = self._peer_dest_hash(m.get("chat_peer") or content.split("with ", 1)[-1].strip())
            elif "Link closed" in content:
                session_peer = None
        return session_peer

    def _history_for_peer(self, peer_hash, limit=500):
        peer = self._peer_dest_hash(peer_hash)
        if peer == HUB_GROUP_PEER:
            filtered = [
                self._enrich_message(m)
                for m in self.message_history
                if m.get("hub_group") or self._peer_dest_hash(m.get("chat_peer") or m.get("peer")) == HUB_GROUP_PEER
            ]
            return filtered[-limit:]
        if not peer:
            return self.message_history[-limit:]
        aliases = self._history_peer_aliases(peer)
        filtered = []
        for m in self.message_history:
            if self._is_session_system_message(m):
                continue
            cp = self._peer_dest_hash(m.get("chat_peer") or m.get("peer"))
            if cp and self._history_matches_peer(cp, aliases):
                filtered.append(self._enrich_message(m))
                continue
            sender = self._peer_dest_hash(m.get("sender"))
            if sender and self._history_matches_peer(sender, aliases) and m.get("sender") != "system":
                filtered.append(self._enrich_message(m))
                continue
            if not m.get("outgoing") and m.get("sender") != "system":
                if self._is_self_hash(cp) or self._is_self_hash(sender):
                    session_peer = self._session_peer_at(m.get("timestamp", 0))
                    if session_peer and self._peers_equivalent(session_peer, peer):
                        repaired = dict(m)
                        repaired["chat_peer"] = peer
                        repaired["peer"] = peer
                        if self._is_self_hash(sender):
                            repaired["sender"] = peer
                        filtered.append(self._enrich_message(repaired, outgoing=False))
        return filtered[-limit:]

    def _history_file(self):
        return os.path.join(self.config_dir, "history.json")

    def _history_peer(self, entry):
        if not entry:
            return ""
        return self._peer_dest_hash(entry.get("chat_peer") or entry.get("peer"))

    def _should_persist_history(self, peer_hash):
        peer = self._peer_dest_hash(peer_hash)
        if not peer or peer == "unknown":
            return False
        return True

    def _persisted_history_entries(self):
        return [
            m for m in self.message_history
            if self._should_persist_history(self._history_peer(m))
        ]

    def _load_history(self):
        try:
            with open(self._history_file()) as f:
                loaded = json.load(f)
            return [
                m for m in loaded
                if self._should_persist_history(self._history_peer(m))
            ]
        except Exception:
            return []

    def _save_history(self):
        try:
            with open(self._history_file(), "w") as f:
                json.dump(self._persisted_history_entries()[-1000:], f)
        except Exception:
            pass

    def _prune_ephemeral_history_disk(self):
        """Drop non-contact chat history from disk (e.g. after app restart on Android)."""
        self._save_history()

    def _apply_retention(self):
        retention = self.load_settings().get("history_retention", "never")
        if retention == "never":
            return
        now = time.time()
        limits = {
            "1d": 86400,
            "1w": 604800,
            "1m": 2592000,
            "6m": 15552000,
            "12m": 31536000,
        }
        seconds = limits.get(retention)
        if seconds:
            self.message_history = [
                m for m in self.message_history
                if now - m.get("timestamp", 0) < seconds
            ]

    def _history_peer_aliases(self, peer_hash):
        peer = self._peer_dest_hash(peer_hash)
        if not peer:
            return set()
        aliases = {peer}
        if self.messaging:
            for alias in self.messaging.peer_aliases_for(peer):
                clean = self._peer_dest_hash(alias)
                if clean:
                    aliases.add(clean)
        from chatx5.core.contacts import _contact_hashes
        contact = find_contact_by_hash(self.config_dir, peer)
        if contact:
            aliases.update(_contact_hashes(contact))
        return aliases

    def _history_matches_peer(self, entry_peer, target_aliases):
        if not entry_peer or not target_aliases:
            return False
        clean = self._peer_dest_hash(entry_peer)
        if not clean:
            return False
        if clean in target_aliases:
            return True
        return any(self._peers_equivalent(clean, alias) for alias in target_aliases)

    def _clear_history_for_peer(self, peer_hash, extra_aliases=None):
        aliases = self._history_peer_aliases(peer_hash)
        if extra_aliases:
            for alias in extra_aliases:
                clean = self._peer_dest_hash(alias)
                if clean:
                    aliases.add(clean)
        if not aliases:
            return 0
        before = len(self.message_history)
        self.message_history = [
            m for m in self.message_history
            if not self._history_matches_peer(m.get("chat_peer") or m.get("peer"), aliases)
        ]
        self._save_history()
        return before - len(self.message_history)

    async def handle_history_clear(self, request):
        peer = request.query.get("peer", "").strip()
        extra_aliases = None
        if not peer and request.can_read_body:
            try:
                data = await request.json()
                peer = (data.get("peer") or "").strip()
                raw_aliases = data.get("aliases")
                if isinstance(raw_aliases, list):
                    extra_aliases = raw_aliases
            except Exception:
                pass
        elif request.can_read_body:
            try:
                data = await request.json()
                raw_aliases = data.get("aliases")
                if isinstance(raw_aliases, list):
                    extra_aliases = raw_aliases
            except Exception:
                pass
        if peer:
            removed = self._clear_history_for_peer(peer, extra_aliases=extra_aliases)
            peer_clean = self._peer_dest_hash(peer)
            await self._broadcast({
                "type": "peer_history_cleared",
                "data": {"peer": peer_clean, "removed": removed},
            })
            return web.json_response({"status": "ok", "peer": peer_clean, "removed": removed})
        self.message_history = []
        self._save_history()
        return web.json_response({"status": "ok", "removed": "all"})

    async def handle_delete_message(self, request):
        msg_id = request.match_info.get("msg_id", "")
        if not msg_id:
            return web.json_response({"error": "msg_id required"}, status=400)
        before = len(self.message_history)
        self.message_history = [m for m in self.message_history if m.get("msg_id") != msg_id]
        if len(self.message_history) == before:
            return web.json_response({"error": "not found"}, status=404)
        self._save_history()
        await self._broadcast({"type": "message_deleted", "data": {"msg_id": msg_id}})
        return web.json_response({"status": "ok"})

    async def handle_history(self, request):
        self._apply_retention()
        limit = int(request.query.get("limit", 500))
        peer = request.query.get("peer", "")
        if peer:
            return web.json_response(self._history_for_peer(peer, limit))
        rows = [
            self._enrich_message(m)
            for m in self.message_history[-limit:]
            if not self._is_session_system_message(m)
        ]
        return web.json_response(rows)

    async def _history_maintenance_loop(self):
        while True:
            await asyncio.sleep(60)
            if self._shutting_down:
                return
            self._prune_stale_session_system_messages()

