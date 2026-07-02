"""Auto-extracted from web/server.py — TransferRoutes layer."""

import base64
import mimetypes
import os
import shutil
import tempfile
import time
import uuid
import zipfile
from urllib.parse import unquote

from aiohttp import web

from chatx5.core.voice import VoicePlayer
from chatx5.utils.file_serve import stream_file_response
from chatx5.utils.helpers import (
    media_type_for_filename,
    safe_basename,
    safe_path_under,
    safe_rel_path_under,
)


class TransferRoutesMixin:
    async def handle_file_upload(self, request):
        if not self.messaging:
            return web.json_response({"error": "not ready"}, status=400)
        peer_hint = request.query.get("peer", "").strip()
        if peer_hint:
            self._ui_state["viewing_peer"] = self._peer_dest_hash(peer_hint)
        try:
            reader = await request.multipart()
            field = await reader.next()
            if not field:
                return web.json_response({"error": "no file"}, status=400)
            fname = safe_basename(field.filename, default=f"file_{int(time.time())}")
            msg_type = media_type_for_filename(fname)

            sent_dir = os.path.join(self.config_dir, "sent")
            os.makedirs(sent_dir, exist_ok=True)
            save_path = safe_path_under(sent_dir, fname)
            if not save_path:
                return web.json_response({"error": "invalid filename"}, status=400)
            size = 0
            with open(save_path, "wb") as f:
                while True:
                    chunk = await field.read_chunk(8192)
                    if not chunk:
                        break
                    f.write(chunk)
                    size += len(chunk)

            queue_target = self._queue_target_hash()
            transfer_id = str(uuid.uuid4())[:12]
            linked_to_target = bool(
                queue_target and self.messaging._peer_link_active(queue_target)
            )
            if not linked_to_target or self.messaging._has_active_transfer():
                self.messaging.enqueue(
                    msg_type, save_path,
                    target_hash=queue_target,
                    file_name=fname, file_size=size, file_path=save_path,
                    msg_id=transfer_id,
                )
                my_hash = self._my_sender_hash()
                chat_peer = self._peer_dest_hash(queue_target) or self._session_chat_peer()
                entry = self._enrich_message({
                    "type": msg_type,
                    "content": save_path,
                    "sender": my_hash,
                    "peer": chat_peer,
                    "chat_peer": chat_peer,
                    "timestamp": time.time(),
                    "file_name": fname,
                    "file_size": size,
                    "msg_id": transfer_id,
                    "status": "queued",
                }, outgoing=True)
                self.message_history.append(entry)
                self._save_history()
                await self._broadcast({"type": "message", "data": entry})
                return web.json_response({
                    "status": "queued",
                    "name": fname,
                    "size": size,
                    "msg_id": transfer_id,
                    "reason": None if not self.messaging.active_link else "transfer in progress",
                })
            my_hash = self._my_sender_hash()
            ts = time.time()
            chat_peer = self._peer_dest_hash(queue_target) or self._session_chat_peer()
            transfer_id = str(uuid.uuid4())[:12]
            entry = self._enrich_message({
                "type": msg_type,
                "content": save_path,
                "sender": my_hash,
                "peer": chat_peer,
                "chat_peer": chat_peer,
                "timestamp": ts,
                "file_name": fname,
                "file_size": size,
                "msg_id": transfer_id,
                "status": "sent",
            }, outgoing=True)
            self.message_history.append(entry)
            self._save_history()
            await self._broadcast({"type": "message", "data": entry})

            result = self.messaging.send_file(
                save_path, msg_type,
                progress_callback=self._make_progress_callback(fname, size, transfer_id),
                transfer_id=transfer_id,
                target_peer=queue_target,
            )
            if result:
                method = "lan_http" if size >= 2 * 1024 * 1024 and self.host in ("0.0.0.0", "::") else "resource"
                return web.json_response({"status": "ok", "name": fname, "size": size, "method": method})
            return web.json_response({"error": "send failed"}, status=400)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def handle_folder_upload(self, request):
        if not self.messaging:
            return web.json_response({"error": "not ready"}, status=400)
        peer_hint = request.query.get("peer", "").strip()
        if peer_hint:
            self._ui_state["viewing_peer"] = self._peer_dest_hash(peer_hint)
        try:
            folder_name = safe_basename(
                request.query.get("name", f"folder_{int(time.time())}"),
                default=f"folder_{int(time.time())}",
            )
            reader = await request.multipart()
            tmpdir = tempfile.mkdtemp(prefix="chatx5_folder_")
            total_size = 0
            file_count = 0
            while True:
                field = await reader.next()
                if not field:
                    break
                fpath = safe_rel_path_under(
                    tmpdir,
                    field.filename,
                    default_name=f"file_{file_count}",
                )
                if not fpath:
                    continue
                os.makedirs(os.path.dirname(fpath), exist_ok=True)
                with open(fpath, "wb") as f:
                    while True:
                        chunk = await field.read_chunk(8192)
                        if not chunk:
                            break
                        f.write(chunk)
                        total_size += len(chunk)
                file_count += 1
            if file_count == 0:
                shutil.rmtree(tmpdir, ignore_errors=True)
                return web.json_response({"error": "no files"}, status=400)
            zip_name = folder_name.rstrip("/") + ".zip"
            sent_dir = os.path.join(self.config_dir, "sent")
            os.makedirs(sent_dir, exist_ok=True)
            zip_path = os.path.join(sent_dir, zip_name)
            zip_entries = []
            for root, dirs, files in os.walk(tmpdir):
                for fname in files:
                    fpath = os.path.join(root, fname)
                    zip_entries.append((fpath, os.path.relpath(fpath, tmpdir)))
            total_entries = len(zip_entries)
            await self._broadcast({"type": "progress", "data": {
                "stage": "zipping",
                "file_name": zip_name,
                "progress": 0,
                "direction": "send",
                "status": "active",
                "current": 0,
                "total": total_entries,
            }})
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for idx, (fpath, arcname) in enumerate(zip_entries):
                    zf.write(fpath, arcname)
                    pct = int(((idx + 1) / max(total_entries, 1)) * 100)
                    await self._broadcast({"type": "progress", "data": {
                        "stage": "zipping",
                        "file_name": zip_name,
                        "progress": pct,
                        "direction": "send",
                        "status": "active",
                        "current": idx + 1,
                        "total": total_entries,
                    }})
            shutil.rmtree(tmpdir, ignore_errors=True)
            zsize = os.path.getsize(zip_path)
            print(f"[folder] Created {zip_name} ({zsize} bytes, {file_count} files)")
            queue_target = self._queue_target_hash()
            linked_to_target = bool(
                queue_target and self.messaging._peer_link_active(queue_target)
            )
            if not linked_to_target or self.messaging._has_active_transfer():
                transfer_id = str(uuid.uuid4())[:12]
                self.messaging.enqueue(
                    "file", zip_path,
                    target_hash=queue_target,
                    file_name=zip_name, file_size=zsize, file_path=zip_path,
                    msg_id=transfer_id,
                )
                my_hash = self._my_sender_hash()
                chat_peer = self._peer_dest_hash(queue_target) or self._session_chat_peer()
                entry = self._enrich_message({
                    "type": "file",
                    "content": zip_path,
                    "sender": my_hash,
                    "peer": chat_peer,
                    "chat_peer": chat_peer,
                    "timestamp": time.time(),
                    "file_name": zip_name,
                    "file_size": zsize,
                    "msg_id": transfer_id,
                    "status": "queued",
                }, outgoing=True)
                self.message_history.append(entry)
                self._save_history()
                await self._broadcast({"type": "message", "data": entry})
                return web.json_response({
                    "status": "queued",
                    "name": zip_name,
                    "size": zsize,
                    "msg_id": transfer_id,
                    "reason": None if not linked_to_target else "transfer in progress",
                })
            my_hash = self._my_sender_hash()
            ts = time.time()
            chat_peer = self._peer_dest_hash(queue_target) or self._session_chat_peer()
            transfer_id = str(uuid.uuid4())[:12]
            entry = self._enrich_message({
                "type": "file",
                "content": zip_path,
                "sender": my_hash,
                "peer": chat_peer,
                "chat_peer": chat_peer,
                "timestamp": ts,
                "file_name": zip_name,
                "file_size": zsize,
                "msg_id": transfer_id,
                "status": "sent",
            }, outgoing=True)
            self.message_history.append(entry)
            self._save_history()
            await self._broadcast({"type": "message", "data": entry})
            result = self.messaging.send_file(
                zip_path, "file",
                progress_callback=self._make_progress_callback(zip_name, zsize, transfer_id),
                transfer_id=transfer_id,
                target_peer=queue_target,
            )
            if result:
                return web.json_response({"status": "ok", "name": zip_name, "size": zsize})
            return web.json_response({"error": "send failed"}, status=400)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def handle_transfer_cancel(self, request):
        if not self.messaging:
            return web.json_response({"error": "not ready"}, status=400)
        try:
            data = await request.json() if request.can_read_body else {}
        except Exception:
            data = {}
        transfer_id = data.get("transfer_id")
        file_name = data.get("file_name", "")
        cancelled = self.messaging.cancel_transfer(
            transfer_id, file_name=file_name, notify_peer=True,
        )
        if not cancelled and self.messaging.active_link:
            cancelled = self.messaging._cancel_incoming_resources(
                self.messaging.active_link,
                transfer_id=transfer_id,
                file_name=file_name,
            )
        if cancelled:
            await self._broadcast({"type": "progress", "data": {
                "status": "cancelled",
                "progress": 0,
                "file_name": file_name,
                "transfer_id": transfer_id,
            }})
            if transfer_id:
                await self._remove_history_message(transfer_id)
        return web.json_response({"status": "ok" if cancelled else "noop"})

    async def handle_voice_upload(self, request):
        if not self.messaging:
            return web.json_response({"error": "not ready"}, status=400)
        try:
            data = await request.json()
            peer_hint = (data.get("peer") or "").strip()
            if peer_hint:
                self._ui_state["viewing_peer"] = self._peer_dest_hash(peer_hint)
            audio_b64 = data.get("audio", "")
            if not audio_b64:
                return web.json_response({"error": "no audio data"}, status=400)
            audio_bytes = base64.b64decode(audio_b64)
            sent_dir = os.path.join(self.config_dir, "sent")
            os.makedirs(sent_dir, exist_ok=True)
            voice_path = os.path.join(sent_dir, f"voice_{int(time.time())}.webm")
            with open(voice_path, "wb") as f:
                f.write(audio_bytes)

            queue_target = self._queue_target_hash()
            linked_to_target = bool(
                queue_target and self.messaging._peer_link_active(queue_target)
            )
            if not linked_to_target or self.messaging._has_active_transfer():
                voice_name = os.path.basename(voice_path)
                transfer_id = str(uuid.uuid4())[:12]
                self.messaging.enqueue(
                    "voice", voice_path, target_hash=queue_target,
                    file_name=voice_name,
                    file_size=len(audio_bytes), file_path=voice_path,
                    msg_id=transfer_id,
                )
                my_hash = self._my_sender_hash()
                chat_peer = self._peer_dest_hash(queue_target) or self._session_chat_peer()
                entry = self._enrich_message({
                    "type": "voice",
                    "content": voice_path,
                    "sender": my_hash,
                    "peer": chat_peer,
                    "chat_peer": chat_peer,
                    "timestamp": time.time(),
                    "file_name": voice_name,
                    "file_size": len(audio_bytes),
                    "msg_id": transfer_id,
                    "status": "queued",
                }, outgoing=True)
                self.message_history.append(entry)
                self._save_history()
                await self._broadcast({"type": "message", "data": entry})
                return web.json_response({
                    "status": "queued",
                    "msg_id": transfer_id,
                    "reason": None if not linked_to_target else "transfer in progress",
                })

            my_hash = self._my_sender_hash()
            ts = time.time()
            chat_peer = self._peer_dest_hash(queue_target) or self._session_chat_peer()
            voice_name = os.path.basename(voice_path)
            transfer_id = str(uuid.uuid4())[:12]
            entry = self._enrich_message({
                "type": "voice",
                "content": voice_path,
                "sender": my_hash,
                "peer": chat_peer,
                "chat_peer": chat_peer,
                "timestamp": ts,
                "file_name": voice_name,
                "file_size": len(audio_bytes),
                "msg_id": transfer_id,
                "status": "sent",
            }, outgoing=True)
            self.message_history.append(entry)
            self._save_history()
            await self._broadcast({"type": "message", "data": entry})

            result = self.messaging.send_file(
                voice_path, "voice",
                progress_callback=self._make_progress_callback(voice_name, len(audio_bytes), transfer_id),
                transfer_id=transfer_id,
                target_peer=queue_target,
            )
            if result:
                return web.json_response({"status": "ok"})
            return web.json_response({"error": "send failed"}, status=400)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def handle_play_voice(self, request):
        try:
            data = await request.json()
            path = data.get("path", "")
            received_dir = self._received_dir()
            sent_dir = self._sent_dir()
            allowed = None
            if path:
                norm = os.path.normpath(path)
                if norm.startswith(received_dir + os.sep) or norm == received_dir:
                    allowed = norm
                elif norm.startswith(sent_dir + os.sep) or norm == sent_dir:
                    allowed = norm
            if allowed and os.path.isfile(allowed):
                VoicePlayer.play(allowed)
                return web.json_response({"status": "ok"})
            return web.json_response({"error": "file not found"}, status=404)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def handle_serve_file(self, request):
        filepath = unquote(request.match_info["filepath"])
        received_dir = self._received_dir()
        sent_dir = self._sent_dir()
        if filepath.startswith("received/"):
            rel = "/".join(unquote(p) for p in filepath[9:].split("/"))
            full_path = os.path.normpath(os.path.join(received_dir, rel))
        elif filepath.startswith("sent/"):
            rel = "/".join(unquote(p) for p in filepath[5:].split("/"))
            full_path = os.path.normpath(os.path.join(sent_dir, rel))
        else:
            rel = "/".join(unquote(p) for p in filepath.split("/"))
            full_path = os.path.normpath(os.path.join(self.config_dir, rel))

        allowed = (
            full_path.startswith(received_dir + os.sep) or full_path == received_dir or
            full_path.startswith(sent_dir + os.sep) or full_path == sent_dir
        )
        if not allowed:
            return web.Response(text="Forbidden", status=403)
        if not os.path.exists(full_path) or not os.path.isfile(full_path):
            return web.Response(text="Not found", status=404)
        ct, _ = mimetypes.guess_type(full_path)
        if not ct:
            ext = os.path.splitext(full_path)[1].lower().lstrip(".")
            basename = os.path.basename(full_path)
            if ext == "webm" and basename.startswith("voice_"):
                ct = "audio/webm"
            else:
                ct = {
                    "webm": "video/webm",
                    "mp4": "video/mp4",
                    "m4v": "video/mp4",
                    "mkv": "video/x-matroska",
                    "mov": "video/quicktime",
                    "avi": "video/x-msvideo",
                    "ogv": "video/ogg",
                    "mpeg": "video/mpeg",
                    "mpg": "video/mpeg",
                }.get(ext)
        resp = await stream_file_response(request, full_path, content_type=ct)
        if resp is not None:
            return resp
        return web.Response(text="Not found", status=404)

