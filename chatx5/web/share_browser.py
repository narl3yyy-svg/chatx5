"""Shared folder browse/upload sessions for P2P and hub group chats."""

import json
import os
import time
import uuid
from urllib.parse import urlencode

import aiohttp
from aiohttp import web

from chatx5.core.messaging import HUB_GROUP_PEER
from chatx5.core.messaging.constants import MESSAGE_TYPE_SHARE_BROWSE
from chatx5.core.messaging.models import ChatMessage
from chatx5.utils.helpers import safe_basename, safe_rel_path_under

SHARE_SESSION_TTL_S = 7200


class ShareBrowserMixin:
    """Create and serve time-limited shared folder sessions."""

    def _init_share_sessions(self):
        self._share_sessions = {}

    def _prune_share_sessions(self):
        now = time.time()
        expired = [
            sid for sid, sess in self._share_sessions.items()
            if float(sess.get("expires") or 0) < now
        ]
        for sid in expired:
            self._share_sessions.pop(sid, None)

    def _share_session(self, session_id, token=None):
        self._prune_share_sessions()
        sess = self._share_sessions.get((session_id or "").strip())
        if not sess:
            return None
        if token and (sess.get("token") or "") != token:
            return None
        return sess

    def _share_token_from_request(self, request):
        return (
            request.headers.get("X-Share-Token")
            or request.query.get("token")
            or ""
        ).strip()

    def _share_listing(self, root, rel_path=""):
        path = safe_rel_path_under(root, rel_path, "")
        if not path or not os.path.isdir(path):
            return None
        entries = []
        try:
            names = sorted(os.listdir(path), key=lambda n: (not os.path.isdir(os.path.join(path, n)), n.lower()))
        except OSError:
            return None
        for name in names:
            if name.startswith("."):
                continue
            full = os.path.join(path, name)
            try:
                st = os.stat(full)
            except OSError:
                continue
            rel = os.path.relpath(full, root).replace("\\", "/")
            entries.append({
                "name": name,
                "path": rel,
                "dir": os.path.isdir(full),
                "size": 0 if os.path.isdir(full) else int(st.st_size),
                "mtime": float(st.st_mtime),
            })
        parent = ""
        if rel_path:
            parent = os.path.dirname(rel_path.replace("\\", "/").strip("/"))
        return {"path": rel_path.replace("\\", "/"), "parent": parent, "entries": entries}

    def _send_share_offer(self, offer, target_peer, hub_group=False):
        if not self.messaging:
            return False
        payload = json.dumps(offer)
        msg = ChatMessage(MESSAGE_TYPE_SHARE_BROWSE, payload)
        if hub_group:
            msg.hub_group = True
            result = self.messaging.send_hub_message(payload, msg_id=msg.msg_id)
            return bool(result)
        if hasattr(self.messaging, "send_chat_message"):
            return bool(self.messaging.send_chat_message(
                msg, target_peer=target_peer,
            ))
        peer = self.messaging.dest_hash_for(target_peer or "")
        if not peer or peer == "unknown":
            return False
        link = self.messaging._queue_send_link(peer)
        if not link:
            return False
        import RNS
        data = msg.to_json().encode("utf-8")
        RNS.Packet(link, data).send()
        return True

    async def handle_share_start(self, request):
        if not self.messaging:
            return web.json_response({"error": "not ready"}, status=400)
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "invalid json"}, status=400)
        path = (data.get("path") or "").strip()
        if not path or not os.path.isdir(path):
            return web.json_response({"error": "folder not found"}, status=400)
        root = os.path.normpath(path)
        peer_hint = (data.get("peer") or "").strip()
        hub_group = bool(data.get("hub_group")) or peer_hint == HUB_GROUP_PEER
        writable = bool(data.get("writable", True))
        target = HUB_GROUP_PEER if hub_group else self._peer_dest_hash(peer_hint)
        if not hub_group and (not target or target == "unknown"):
            return web.json_response({"error": "peer required"}, status=400)
        from chatx5.web.server import detect_lan_ip
        host = detect_lan_ip() or (
            self.host if self.host not in ("127.0.0.1", "0.0.0.0", "::") else "127.0.0.1"
        )
        session_id = str(uuid.uuid4())[:12]
        token = str(uuid.uuid4()).replace("-", "")
        offer = {
            "session_id": session_id,
            "token": token,
            "root_name": os.path.basename(root) or root,
            "host": host,
            "port": int(self.port),
            "writable": writable,
            "owner": self._my_sender_hash(),
            "hub_group": hub_group,
        }
        self._share_sessions[session_id] = {
            **offer,
            "root": root,
            "target": target,
            "created": time.time(),
            "expires": time.time() + SHARE_SESSION_TTL_S,
        }
        sent = self._send_share_offer(offer, target, hub_group=hub_group)
        my_hash = self._my_sender_hash()
        chat_peer = HUB_GROUP_PEER if hub_group else target
        entry = self._enrich_message({
            "type": MESSAGE_TYPE_SHARE_BROWSE,
            "content": json.dumps(offer),
            "sender": my_hash,
            "peer": chat_peer,
            "chat_peer": chat_peer,
            "timestamp": time.time(),
            "msg_id": str(uuid.uuid4())[:12],
            "file_name": offer["root_name"],
            "hub_group": hub_group,
            "status": "sent" if sent else "queued",
            "share": offer,
        }, outgoing=True)
        self.message_history.append(entry)
        self._save_history()
        await self._broadcast({"type": "message", "data": entry})
        if not sent and not hub_group:
            self.messaging.enqueue(
                MESSAGE_TYPE_SHARE_BROWSE,
                json.dumps(offer),
                target_hash=target,
                msg_id=entry["msg_id"],
            )
        print(f"[share] Started session {session_id} for {offer['root_name']} → {target[:16] if target else 'hub'}...")
        return web.json_response({"status": "ok", "session_id": session_id, "sent": sent, "offer": offer})

    async def handle_share_list(self, request):
        session_id = request.match_info.get("session_id", "")
        token = self._share_token_from_request(request)
        sess = self._share_session(session_id, token)
        if not sess:
            return web.json_response({"error": "session not found"}, status=404)
        rel = request.query.get("path", "")
        listing = self._share_listing(sess["root"], rel)
        if listing is None:
            return web.json_response({"error": "invalid path"}, status=400)
        listing["writable"] = bool(sess.get("writable"))
        listing["root_name"] = sess.get("root_name") or ""
        return web.json_response(listing)

    async def handle_share_download(self, request):
        session_id = request.match_info.get("session_id", "")
        token = self._share_token_from_request(request)
        sess = self._share_session(session_id, token)
        if not sess:
            return web.Response(status=404, text="session not found")
        rel = request.query.get("path", "")
        full = safe_rel_path_under(sess["root"], rel, "")
        if not full or not os.path.isfile(full):
            return web.Response(status=404, text="file not found")
        return web.FileResponse(full, headers={
            "Content-Disposition": f'attachment; filename="{safe_basename(os.path.basename(full))}"',
        })

    async def handle_share_upload(self, request):
        session_id = request.match_info.get("session_id", "")
        token = self._share_token_from_request(request)
        sess = self._share_session(session_id, token)
        if not sess:
            return web.json_response({"error": "session not found"}, status=404)
        if not sess.get("writable"):
            return web.json_response({"error": "read-only session"}, status=403)
        reader = await request.multipart()
        rel = ""
        field = await reader.next()
        while field:
            if field.name == "path":
                rel = (await field.read()).decode("utf-8", errors="replace")
            elif field.name == "file":
                filename = safe_basename(field.filename or "upload.bin")
                dest_dir = safe_rel_path_under(sess["root"], rel, "")
                if not dest_dir:
                    return web.json_response({"error": "invalid path"}, status=400)
                os.makedirs(dest_dir, exist_ok=True)
                dest = safe_rel_path_under(dest_dir, filename, filename)
                if not dest:
                    return web.json_response({"error": "invalid filename"}, status=400)
                size = 0
                with open(dest, "wb") as out:
                    while True:
                        chunk = await field.read_chunk()
                        if not chunk:
                            break
                        out.write(chunk)
                        size += len(chunk)
                print(f"[share] Uploaded {filename} ({size} bytes) to session {session_id}")
                return web.json_response({"status": "ok", "path": os.path.relpath(dest, sess["root"]).replace("\\", "/"), "size": size})
            field = await reader.next()
        return web.json_response({"error": "file required"}, status=400)

    def _share_remote_params(self, request):
        host = (request.query.get("host") or "").strip()
        port = request.query.get("port")
        session_id = (request.query.get("session_id") or "").strip()
        token = self._share_token_from_request(request)
        if not host or not port or not session_id or not token:
            return None
        try:
            port_n = int(port)
        except (TypeError, ValueError):
            return None
        if port_n <= 0 or port_n > 65535:
            return None
        return {"host": host, "port": port_n, "session_id": session_id, "token": token}

    async def _share_remote_fetch(self, params, path_suffix, query=None):
        query = dict(query or {})
        query["token"] = params["token"]
        url = (
            f"http://{params['host']}:{params['port']}"
            f"/api/share/{params['session_id']}/{path_suffix}?{urlencode(query)}"
        )
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                body = await resp.read()
                return resp.status, resp.headers.get("Content-Type", ""), body

    async def handle_share_remote_list(self, request):
        params = self._share_remote_params(request)
        if not params:
            return web.json_response({"error": "host, port, session_id, token required"}, status=400)
        rel = request.query.get("path", "")
        status, _, body = await self._share_remote_fetch(
            params, "list", {"path": rel},
        )
        if status != 200:
            return web.json_response({"error": "remote list failed"}, status=status or 502)
        try:
            return web.json_response(json.loads(body.decode("utf-8")))
        except Exception:
            return web.json_response({"error": "invalid remote response"}, status=502)

    async def handle_share_remote_download(self, request):
        params = self._share_remote_params(request)
        if not params:
            return web.Response(status=400, text="host, port, session_id, token required")
        rel = request.query.get("path", "")
        status, content_type, body = await self._share_remote_fetch(
            params, "download", {"path": rel},
        )
        if status != 200:
            return web.Response(status=status or 502, text="remote download failed")
        headers = {}
        if content_type:
            headers["Content-Type"] = content_type
        return web.Response(body=body, headers=headers)

    async def handle_share_remote_upload(self, request):
        params = self._share_remote_params(request)
        if not params:
            return web.json_response({"error": "host, port, session_id, token required"}, status=400)
        url = (
            f"http://{params['host']}:{params['port']}"
            f"/api/share/{params['session_id']}/upload"
        )
        reader = await request.multipart()
        form = aiohttp.FormData()
        has_file = False
        field = await reader.next()
        while field:
            if field.name == "path":
                form.add_field("path", (await field.read()).decode("utf-8", errors="replace"))
            elif field.name == "file":
                has_file = True
                form.add_field(
                    "file",
                    await field.read(),
                    filename=field.filename or "upload.bin",
                    content_type=field.headers.get(
                        "Content-Type", "application/octet-stream",
                    ),
                )
            field = await reader.next()
        if not has_file:
            return web.json_response({"error": "file required"}, status=400)
        timeout = aiohttp.ClientTimeout(total=120)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                url,
                data=form,
                headers={"X-Share-Token": params["token"]},
            ) as resp:
                body = await resp.read()
                if resp.status != 200:
                    return web.json_response({"error": "remote upload failed"}, status=resp.status)
                try:
                    return web.json_response(json.loads(body.decode("utf-8")))
                except Exception:
                    return web.json_response({"error": "invalid remote response"}, status=502)