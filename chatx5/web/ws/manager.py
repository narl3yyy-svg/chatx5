"""WebSocket lifecycle, broadcasting, and inbound message dispatch."""

import asyncio
import json
import time
import uuid

from aiohttp import web

from chatx5.core.contacts import list_contacts
from chatx5.core.messaging import HUB_GROUP_PEER, is_hub_peer_hash
from chatx5.utils.platform import lan_ip as detect_lan_ip


class WebSocketMixin:
    """WebSocket clients, peer/contact broadcasts, and WS protocol handlers."""

    def _prune_websockets(self):
        """Drop closed sockets (Android WebView reloads leave zombie connections)."""
        dead = [ws for ws in list(self.websockets) if ws.closed]
        for ws in dead:
            self.websockets.discard(ws)
        return len(self.websockets)

    def _ws_client_count(self):
        return self._prune_websockets()

    async def _broadcast_peers(self, authoritative=False):
        peers = self._scoped_peers()
        payload = {"type": "peers", "data": peers}
        if authoritative:
            payload["authoritative"] = True
        await self._broadcast(payload)
        return peers

    def _schedule_peers_broadcast(self, authoritative=False):
        if not (self.websockets and self._loop):
            return
        asyncio.run_coroutine_threadsafe(
            self._broadcast_peers(authoritative=authoritative),
            self._loop,
        )

    async def _broadcast(self, data):
        msg = json.dumps(data)
        for ws in self.websockets.copy():
            if ws.closed:
                self.websockets.discard(ws)
                continue
            try:
                await ws.send_str(msg)
            except Exception:
                self.websockets.discard(ws)

    def _schedule_contacts_broadcast(self):
        if not (self.websockets and self._loop):
            return
        contacts = list_contacts(self.config_dir)
        asyncio.run_coroutine_threadsafe(
            self._broadcast({"type": "contacts", "data": contacts}),
            self._loop,
        )

    async def _send_peers_to(self, ws):
        if self.discovery:
            peers = self.discovery.get_peers()
            try:
                await ws.send_str(json.dumps({"type": "peers", "data": peers}))
            except Exception:
                pass

    async def handle_websocket(self, request):
        self._prune_websockets()
        ws = web.WebSocketResponse(heartbeat=30.0)
        await ws.prepare(request)
        self.websockets.add(ws)
        print(f"[ws] Client connected ({self._ws_client_count()} total)")

        await self._send_peers_to(ws)
        if self.messaging:
            peer = self._peer_dest_hash(
                getattr(self.messaging, "_session_peer_hash", None) or self.active_peer
            )
            if (
                peer
                and not self.messaging.active_link
                and not self.messaging.is_user_disconnected(peer)
            ):
                now = time.time()
                if (
                    now - self._session_resume_last >= 45.0
                    and not getattr(self.messaging, "_connect_in_progress", False)
                    and not getattr(self.messaging, "_failover_in_progress", False)
                    and (now - getattr(self.messaging, "_failover_last_attempt", 0))
                    >= getattr(self.messaging, "_failover_cooldown", lambda: 20.0)()
                ):
                    self._session_resume_last = now
                    peer_ip, peer_port = self._peer_connect_meta(peer)
                    asyncio.create_task(self._resume_session_task(peer, peer_ip, peer_port))

        try:
            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                        await self._handle_ws_message(ws, data)
                    except json.JSONDecodeError:
                        pass
                elif msg.type == web.WSMsgType.ERROR:
                    break
        except Exception:
            pass
        finally:
            self.websockets.discard(ws)
            print(f"[ws] Client disconnected ({self._ws_client_count()} total)")
        return ws

    async def _handle_ws_message(self, ws, data):
        msg_type = data.get("type")
        if msg_type == "send":
            text = data.get("text", "")
            if text and self.messaging:
                peer_hint = data.get("peer") or data.get("hash") or ""
                if peer_hint:
                    self._ui_state["viewing_peer"] = self._peer_dest_hash(peer_hint)
                hub_send = is_hub_peer_hash(peer_hint) or data.get("hub_group") is True
                settings = self.load_settings()
                hub_role = settings.get("hub_role", "off")
                if hub_send:
                    if hub_role == "off":
                        await ws.send_str(json.dumps({
                            "type": "info",
                            "data": "Hub mode is off - enable in Network settings",
                        }))
                        return

                    def on_receipt(status, receipt):
                        if self._loop:
                            asyncio.run_coroutine_threadsafe(
                                self._broadcast({
                                    "type": "receipt",
                                    "data": {
                                        "msg_id": receipt.get("msg_id"),
                                        "status": status,
                                    },
                                }),
                                self._loop,
                            )

                    result = self.messaging.send_hub_message(
                        text,
                        receipt_callback=on_receipt,
                        hub_server_hash=settings.get("hub_server_hash"),
                        hub_server_mode=(hub_role == "server"),
                    )
                    if result:
                        my_hash = self._my_sender_hash()
                        entry = self._enrich_message({
                            "type": result.msg_type,
                            "content": result.content,
                            "sender": my_hash,
                            "peer": HUB_GROUP_PEER,
                            "chat_peer": HUB_GROUP_PEER,
                            "timestamp": result.timestamp,
                            "msg_id": result.msg_id,
                            "hub_group": True,
                            "status": "sent",
                        }, outgoing=True)
                        self.message_history.append(entry)
                        self._save_history()
                        if self.debug:
                            print(f"[chat] send hub msg_id={entry['msg_id'][:8]}")
                        await self._broadcast({"type": "message", "data": entry})
                    else:
                        msg_id = str(uuid.uuid4())[:12]
                        self.messaging.enqueue(
                            "text", text, target_hash=HUB_GROUP_PEER, msg_id=msg_id,
                        )
                        my_hash = self._my_sender_hash()
                        entry = self._enrich_message({
                            "type": "text",
                            "content": text,
                            "sender": my_hash,
                            "peer": HUB_GROUP_PEER,
                            "chat_peer": HUB_GROUP_PEER,
                            "timestamp": time.time(),
                            "msg_id": msg_id,
                            "hub_group": True,
                            "status": "queued",
                        }, outgoing=True)
                        self.message_history.append(entry)
                        self._save_history()
                        await self._broadcast({"type": "message", "data": entry})
                        qsize = self.messaging.queue_size()
                        await ws.send_str(json.dumps({
                            "type": "info",
                            "data": f"Message queued ({qsize} pending)",
                        }))
                    return
                prefer_via = (data.get("via") or "").strip() or None
                target_hash = self._peer_dest_hash(peer_hint) if peer_hint else (
                    self._queue_target_hash()
                )
                if not target_hash and self.messaging._session_peer_hash:
                    target_hash = self.messaging._session_peer_hash
                if target_hash:
                    if not self._peer_in_discovery_scope(target_hash):
                        await ws.send_str(json.dumps({
                            "type": "info",
                            "data": (
                                "Peer is outside your LAN scope — change Settings → "
                                "Network IPv4 or reconnect on the same subnet"
                            ),
                        }))
                        return
                    peer_ip = None
                    meta = self._discovery_peer_for_connect(
                        None, target_hash, via=prefer_via,
                    )
                    if meta:
                        peer_ip = meta.get("ip")
                    target_hash = self._resolve_current_peer_hash(
                        target_hash, peer_ip, prefer_via=prefer_via,
                    )
                    if (
                        self.discovery
                        and not self._peer_is_current(target_hash)
                        and not self.messaging.peer_send_ready(
                            target_hash, prefer_transport=prefer_via,
                        )
                    ):
                        await ws.send_str(json.dumps({
                            "type": "info",
                            "data": "Stale peer hash — open the peer from Discovered",
                        }))
                        return
                linked_to_target = bool(
                    target_hash
                    and self.messaging.peer_send_ready(
                        target_hash, prefer_transport=prefer_via,
                    )
                )
                if linked_to_target:

                    def on_receipt(status, receipt):
                        if self._loop:
                            asyncio.run_coroutine_threadsafe(
                                self._broadcast({
                                    "type": "receipt",
                                    "data": {
                                        "msg_id": receipt.get("msg_id"),
                                        "status": status,
                                    },
                                }),
                                self._loop,
                            )

                    result = self.messaging.send_message(
                        text,
                        receipt_callback=on_receipt,
                        target_peer=target_hash,
                        prefer_transport=prefer_via,
                    )
                    if result:
                        my_hash = self._my_sender_hash()
                        chat_peer = (
                            target_hash
                            or self._session_chat_peer()
                            or self._peer_dest_hash(self.active_peer)
                        )
                        entry = self._enrich_message({
                            "type": result.msg_type,
                            "content": result.content,
                            "sender": my_hash,
                            "peer": chat_peer,
                            "chat_peer": chat_peer,
                            "timestamp": result.timestamp,
                            "msg_id": result.msg_id,
                            "status": "sent",
                        }, outgoing=True)
                        self.message_history.append(entry)
                        self._save_history()
                        if self.debug:
                            print(
                                f"[chat] send type={entry['type']} "
                                f"peer={chat_peer[:16]} msg_id={entry['msg_id'][:8]}"
                            )
                        await self._broadcast({"type": "message", "data": entry})
                else:
                    msg_id = str(uuid.uuid4())[:12]
                    self.messaging.enqueue(
                        "text", text, target_hash=target_hash, msg_id=msg_id,
                    )
                    my_hash = self._my_sender_hash()
                    chat_peer = (
                        target_hash
                        or self._session_chat_peer()
                        or self._peer_dest_hash(self.active_peer)
                    )
                    entry = self._enrich_message({
                        "type": "text",
                        "content": text,
                        "sender": my_hash,
                        "peer": chat_peer,
                        "chat_peer": chat_peer,
                        "timestamp": time.time(),
                        "msg_id": msg_id,
                        "status": "queued",
                    }, outgoing=True)
                    self.message_history.append(entry)
                    self._save_history()
                    await self._broadcast({"type": "message", "data": entry})
                    qsize = self.messaging.queue_size()
                    await ws.send_str(json.dumps({
                        "type": "info",
                        "data": f"Message queued ({qsize} pending)",
                    }))
        elif msg_type == "connect":
            peer_hash = data.get("hash", "")
            if peer_hash and self.messaging:
                peer_ip = (data.get("ip") or "").strip() or None
                peer_port = data.get("port") or 8742
                resolved_hash = self._resolve_connect_target(peer_hash, peer_ip)
                peer_ip, peer_port = self._resolve_peer_connect_ip(
                    resolved_hash, peer_ip, peer_port,
                )
                caller_ip = detect_lan_ip() or (
                    self.host if self.host not in ("127.0.0.1", "0.0.0.0") else ""
                )
                ok = await self._run_blocking(
                    self.messaging.connect_to,
                    resolved_hash,
                    peer_ip,
                    peer_port,
                    self._discovery_peer_for_connect,
                    caller_ip,
                    self.port,
                    False,
                    False,
                    False,
                    True,
                )
                if self._shutting_down or ok is None:
                    await ws.send_str(json.dumps({
                        "type": "connect_fail",
                        "error": "server shutting down",
                    }))
                elif ok:
                    clean = self._peer_dest_hash(resolved_hash)
                    self.active_peer = clean
                    await ws.send_str(json.dumps({
                        "type": "connect_ok",
                        "hash": clean,
                        "linked_peers": self.messaging.linked_peers(),
                    }))
                else:
                    await ws.send_str(json.dumps({
                        "type": "connect_fail",
                        "error": "connection failed",
                    }))
        elif msg_type == "viewing":
            peer = data.get("peer") or ""
            self._ui_state["viewing_peer"] = self._peer_dest_hash(peer) if peer else None
        elif msg_type == "visibility":
            self._ui_state["hidden"] = bool(data.get("hidden"))
        elif msg_type == "announce":
            result = await self._perform_announce()
            if result.get("ok"):
                await ws.send_str(json.dumps({
                    "type": "announce_ok",
                    "debounced": result.get("debounced", False),
                    "discovered_count": result.get("discovered_count", 0),
                    "beacon_sent": result.get("beacon_sent", 0),
                }))
            else:
                err = result.get("error") or "not ready"
                await ws.send_str(json.dumps({
                    "type": "info",
                    "data": "Announce failed: " + err,
                }))
        elif msg_type == "read_receipt":
            msg_id = data.get("msg_id", "")
            if msg_id and self.messaging:
                target = self._queue_target_hash() or self._peer_dest_hash(self.active_peer)
                link = self.messaging._link_for_peer(target) if target else None
                link = link or self.messaging.active_link
                if link:
                    self.messaging.send_read_receipt(link, msg_id)