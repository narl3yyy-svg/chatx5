"""File transfer, RNS resources, and LAN HTTP fallback."""

import json
import os
import threading
import time
from urllib import request as urlrequest

import RNS

from chatx5.core.lan_rns import interface_family
from chatx5.core.lan_transfer import register_offer, remove_offer
from chatx5.core.messaging.constants import (
    _NO_COMPRESS_SUFFIXES,
    LAN_HTTP_CHUNK,
    LAN_HTTP_MIN_BYTES,
    MAX_CONCURRENT_RECEIVES,
    MESSAGE_TYPE_FILE,
    MESSAGE_TYPE_IMAGE,
    MESSAGE_TYPE_LAN_HTTP,
    MESSAGE_TYPE_LONGTEXT,
    MESSAGE_TYPE_TEXT,
    MESSAGE_TYPE_TRANSFER_CANCEL,
    MESSAGE_TYPE_VIDEO,
)
from chatx5.core.messaging.models import ChatMessage
from chatx5.core.serial_transfer import (
    is_serial_interface,
    tune_incoming_resource,
    tune_outgoing_resource,
    tune_serial_link,
)
from chatx5.utils.helpers import format_speed
from chatx5.utils.platform import lan_ip, physical_lan_reachable


class TransferMixin:
    """File sends/receives, progress, cancellation, and LAN HTTP offers."""

    def _has_active_transfer(self):
        """True while a file send or receive is in progress on any link."""
        if self._current_transfer_id or self._active_resources:
            return True
        with self._pending_lock:
            for queue in self._pending_files.values():
                if queue:
                    return True
        for link in self.links.values():
            incoming = getattr(link, "incoming_resources", None) or []
            if incoming:
                return True
        return False

    def _migrate_pending_files(self, old_link_id, new_link_id):
        if not old_link_id or old_link_id == new_link_id:
            return
        with self._pending_lock:
            queue = self._pending_files.pop(old_link_id, [])
            if queue:
                self._pending_files.setdefault(new_link_id, []).extend(queue)
                print(f"[transfer] Migrated {len(queue)} pending receive(s) to new link")

    def _flush_pending_files_failed(self, link_id):
        with self._pending_lock:
            queue = self._pending_files.pop(link_id, [])
        for chat_msg in queue:
            print(f"[transfer] Dropped pending receive: {chat_msg.file_name}")
            self._emit_progress(
                chat_msg.file_name or "file",
                0,
                total_size=chat_msg.file_size or 0,
                direction="receive",
                transfer_id=chat_msg.msg_id,
                status="failed",
            )

    def _best_transfer_link(self, peer_hash=None):
        """Pick the best link for bulk transfer, respecting serial/LAN transport zones."""
        peer = self.dest_hash_for(
            peer_hash or self.active_peer_hash or self._session_peer_hash or ""
        )
        if not peer or peer == "unknown":
            return None
        expected = self._peer_expected_transport_families(peer)
        if expected == {"serial"}:
            prefer = ("serial",)
        else:
            prefer = ("tcp", "lan", "udp", "serial")
        best = None
        best_score = -1
        for link in self._links_for_peer(peer):
            if not self._link_interface_healthy(link):
                continue
            if not self._link_acceptable_for_peer(link, peer):
                continue
            iface = self._link_attached_interface(link)
            fam = interface_family(iface)
            if expected:
                if fam == "serial" and "serial" not in expected:
                    continue
                if fam in ("udp", "lan", "tcp") and not (expected & {"udp", "lan", "tcp"}):
                    continue
            fam_rank = len(prefer) - prefer.index(fam) if fam in prefer else 0
            score = self._link_path_score(link) + fam_rank * 5
            if score > best_score:
                best_score = score
                best = link
        return best or self._best_outgoing_link(peer)

    def cancel_all_transfers(self):
        """Abort in-flight file sends/receives during shutdown."""
        self.shutdown_requested = True
        for tid in list(self._active_resources.keys()):
            self.cancel_transfer(transfer_id=tid)
        if self._current_transfer_id:
            self.cancel_transfer(transfer_id=self._current_transfer_id)

    def _active_incoming_resources(self, link):
        try:
            incoming = getattr(link, "incoming_resources", None) or []
        except Exception:
            return []
        active = []
        for res in incoming:
            try:
                status = getattr(res, "status", None)
                if status in (RNS.Resource.COMPLETE, RNS.Resource.FAILED):
                    continue
            except Exception:
                pass
            active.append(res)
        return active

    def _resource_accept_callback(self, link):
        def callback(resource_ad):
            active = self._active_incoming_resources(link)
            if len(active) >= MAX_CONCURRENT_RECEIVES:
                print(f"[transfer] Deferring resource ({len(active)} receive(s) active)")
                return False
            return True
        return callback

    def _optimise_link_mtu(self, link):
        try:
            iface = self._link_attached_interface(link)
            if is_serial_interface(iface):
                tune_serial_link(link, iface)
                print(
                    f"[messaging] Serial link tuned for file transfer "
                    f"(MTU {getattr(link, 'mtu', '?')}, window=2)"
                )
                return
            hw_mtu = getattr(iface, "HW_MTU", None) if iface else None
            current = int(getattr(link, "mtu", 500) or 500)
            if hw_mtu and current < hw_mtu:
                link.mtu = int(hw_mtu)
                link.update_mdu()
                print(
                    f"[messaging] Link MTU upgraded {current} -> {link.mtu} "
                    f"({type(iface).__name__ if iface else 'iface'})"
                )
        except Exception as exc:
            print(f"[messaging] Link MTU upgrade skipped: {exc}")

    def _resource_started_callback(self, link):
        def callback(resource):
            tune_incoming_resource(
                resource, self._link_attached_interface(link),
            )
        return callback

    def _dequeue_pending_file(self, link_id, resource=None):
        with self._pending_lock:
            queue = self._pending_files.get(link_id, [])
            if queue:
                return queue.pop(0)
        for _ in range(20):
            time.sleep(0.05)
            with self._pending_lock:
                queue = self._pending_files.get(link_id, [])
                if queue:
                    return queue.pop(0)
        fname = None
        if resource is not None:
            for attr in ("name", "title", "file_name"):
                val = getattr(resource, attr, None)
                if val:
                    fname = os.path.basename(str(val))
                    break
            spath = getattr(resource, "storagepath", None)
            if not fname and spath:
                fname = os.path.basename(str(spath))
        msg_type = MESSAGE_TYPE_FILE
        if fname:
            from chatx5.utils.helpers import media_type_for_filename
            msg_type = media_type_for_filename(fname)
        return ChatMessage(msg_type, "", file_name=fname or f"file_{int(time.time())}")

    def _resource_concluded(self, link):
        def callback(resource):
            try:
                print(f"[messaging] Resource concluded, status={resource.status}")
                chat_msg = self._dequeue_pending_file(link.link_id, resource)
                tid = getattr(chat_msg, "msg_id", None) if chat_msg else None
                if tid and tid in self._cancelled_transfers:
                    fname = (chat_msg.file_name if chat_msg else None) or "file"
                    self._emit_progress(
                        fname, 0, direction="receive", transfer_id=tid,
                        status="cancelled",
                    )
                    return
                if resource.status == RNS.Resource.COMPLETE:

                    from chatx5.utils.helpers import safe_basename, safe_path_under
                    os.makedirs(self.receive_dir, exist_ok=True)
                    raw_name = chat_msg.file_name or f"file_{int(time.time())}"
                    fname = safe_basename(raw_name, default=f"file_{int(time.time())}")
                    save_path = safe_path_under(self.receive_dir, fname)
                    if not save_path:
                        print(f"[messaging] Rejected unsafe filename: {raw_name!r}")
                        return

                    if hasattr(resource, 'data') and resource.data is not None:
                        if hasattr(resource.data, 'read'):
                            data = resource.data.read()
                        else:
                            data = resource.data
                        with open(save_path, "wb") as f:
                            f.write(data)
                        print(f"[messaging] File saved to {save_path}")
                    elif hasattr(resource, 'storagepath') and os.path.exists(resource.storagepath):
                        import shutil
                        shutil.copy2(resource.storagepath, save_path)
                        print(f"[messaging] File copied from storage to {save_path}")
                    else:
                        print("[messaging] No data available in resource")
                        return

                    if chat_msg.msg_type == MESSAGE_TYPE_LONGTEXT:
                        try:
                            with open(save_path, encoding="utf-8") as f:
                                long_text = f.read()
                            chat_msg.msg_type = MESSAGE_TYPE_TEXT
                            chat_msg.content = long_text
                            os.unlink(save_path)
                        except Exception as e:
                            print(f"[messaging] Failed to read long text: {e}")
                    else:
                        chat_msg.content = save_path
                    remote_hash = self.dest_hash_for(self._peer_for_link(link))
                    if self.on_message:
                        self.on_message(chat_msg, remote_hash)
                    self._emit_progress(
                        chat_msg.file_name or "file",
                        100,
                        total_size=chat_msg.file_size or 0,
                        direction="receive",
                        transfer_id=chat_msg.msg_id,
                        status="complete",
                    )
                    self._send_receipt(link, chat_msg.msg_id, "received")
                else:
                    print(f"[messaging] Resource transfer failed (status={resource.status})")
                    if not chat_msg:
                        with self._pending_lock:
                            queue = self._pending_files.get(link.link_id, [])
                            chat_msg = queue.pop(0) if queue else None
                    if chat_msg:
                        tid = chat_msg.msg_id
                        status = "cancelled" if tid in self._cancelled_transfers else "failed"
                        self._emit_progress(
                            chat_msg.file_name or "file",
                            0,
                            direction="receive",
                            transfer_id=tid,
                            status=status,
                        )
                    if chat_msg and self.on_message:
                        self.on_message(
                            ChatMessage("system", f"File transfer failed: {chat_msg.file_name}"),
                            self.dest_hash_for(self._peer_for_link(link))
                        )
            except Exception as e:
                print(f"[messaging] Resource concluded error: {e}")
        return callback

    def _calc_transfer_speed(self, transfer_id, bytes_done):
        key = transfer_id or "default"
        now = time.time()
        state = self._transfer_bytes_state.get(key, {})
        last_bytes = state.get("bytes", 0)
        last_ts = state.get("ts", now)
        elapsed = max(now - last_ts, 0.001)
        speed_bps = max(0, int((bytes_done - last_bytes) / elapsed))
        if bytes_done > last_bytes or (now - last_ts) > 1.0:
            self._transfer_bytes_state[key] = {"bytes": bytes_done, "ts": now, "speed": speed_bps}
        return format_speed(self._transfer_bytes_state.get(key, {}).get("speed", speed_bps))

    def _start_receive_progress_watch(self, link, chat_msg):
        def watch():
            deadline = time.time() + 7200
            fname = chat_msg.file_name or "file"
            tid = chat_msg.msg_id
            fsize = chat_msg.file_size or 0
            while time.time() < deadline:
                if link.link_id not in self.links:
                    return
                try:
                    incoming = getattr(link, "incoming_resources", None) or []
                    if not incoming:
                        time.sleep(0.35)
                        continue
                    for res in incoming:
                        pct = int(float(res.get_progress()) * 100)
                        transferred = int(float(res.get_progress()) * fsize) if fsize else 0
                        speed = self._calc_transfer_speed(tid, transferred)
                        self._emit_progress(
                            fname, pct, fsize, speed=speed,
                            direction="receive", transfer_id=tid, status="active",
                        )
                        if getattr(res, "status", None) == RNS.Resource.COMPLETE:
                            return
                except Exception:
                    pass
                time.sleep(0.35)

        threading.Thread(target=watch, name=f"recv-progress-{chat_msg.msg_id[:8]}", daemon=True).start()

    def _emit_progress(self, file_name, progress, total_size=0, speed="", direction="receive",
                       transfer_id=None, status="active", transport=None):
        if transfer_id and transfer_id in self._cancelled_transfers and status == "active":
            return
        if status in ("complete", "cancelled", "failed"):
            self._progress_last.pop(transfer_id or file_name, None)
            self._transfer_bytes_state.pop(transfer_id or file_name, None)
        elif status == "active":
            key = transfer_id or file_name or "default"
            now = time.time()
            last = self._progress_last.get(key, {})
            if last and (now - last.get("ts", 0)) < self._progress_throttle_s:
                if abs(progress - last.get("pct", -1)) < 1:
                    return
            self._progress_last[key] = {"ts": now, "pct": progress}
        if transport is None and self.active_link:
            transport = self._transfer_transport_label(self.active_link)
        if self.on_progress:
            try:
                self.on_progress({
                    "file_name": file_name,
                    "progress": progress,
                    "size": total_size,
                    "speed": speed,
                    "direction": direction,
                    "transfer_id": transfer_id,
                    "status": status,
                    "transport": transport or "",
                })
            except Exception as e:
                print(f"[progress] callback error: {e}")

    def _resolve_transfer_id(self, transfer_id=None, file_name=None):
        tid = transfer_id or self._current_transfer_id
        if tid and tid in self._active_resources:
            return tid
        if tid:
            return tid
        if file_name:
            for rid in list(self._active_resources.keys()):
                msg = self._sent_messages.get(rid)
                if msg and getattr(msg, "file_name", None) == file_name:
                    return rid
        return tid

    def _cleanup_transfer(self, transfer_id):
        self._active_resources.pop(transfer_id, None)
        self._cancel_events.pop(transfer_id, None)
        fh = self._file_handles.pop(transfer_id, None)
        if fh:
            try:
                fh.close()
            except Exception:
                pass

    def _transfer_transport_label(self, link=None):
        link = link or self.active_link
        iface = self._link_attached_interface(link) if link else None
        if is_serial_interface(iface):
            return "serial"
        fam = interface_family(iface) if iface else ""
        if fam in ("udp", "lan", "tcp"):
            return fam or "lan"
        return ""

    def _notify_peer_transfer_cancel(self, transfer_id, file_name=None, link=None):
        """Tell the remote peer to stop receiving an in-flight file."""
        link = link or self.active_link
        if not link or not transfer_id:
            return False
        try:
            if getattr(link, "status", None) != RNS.Link.ACTIVE:
                return False
        except Exception:
            return False
        payload = {"transfer_id": transfer_id, "msg_id": transfer_id}
        if file_name:
            payload["file_name"] = file_name
        meta = ChatMessage(MESSAGE_TYPE_TRANSFER_CANCEL, json.dumps(payload), msg_id=transfer_id)
        try:
            packet = RNS.Packet(link, meta.to_json().encode("utf-8"))
            packet.send()
            print(f"[transfer] Cancel notice sent for {transfer_id[:8]}...")
            return True
        except Exception as exc:
            print(f"[transfer] Cancel notice failed: {exc}")
            return False

    def _cancel_incoming_resources(self, link, transfer_id=None, file_name=None):
        """Abort active incoming RNS resources and drop queued file metadata."""
        if not link:
            return False
        cancelled = False
        fname = file_name or ""
        try:
            for res in list(getattr(link, "incoming_resources", None) or []):
                try:
                    if hasattr(res, "cancel"):
                        res.cancel()
                    elif hasattr(res, "close"):
                        res.close()
                    cancelled = True
                except Exception:
                    pass
        except Exception:
            pass
        with self._pending_lock:
            queue = self._pending_files.get(link.link_id, [])
            kept = []
            for msg in queue:
                match = (
                    (transfer_id and msg.msg_id == transfer_id)
                    or (file_name and msg.file_name == file_name)
                )
                if match:
                    cancelled = True
                    fname = msg.file_name or fname
                    tid = msg.msg_id or transfer_id
                    if tid:
                        self._cancelled_transfers.add(tid)
                    continue
                kept.append(msg)
            self._pending_files[link.link_id] = kept
        if cancelled:
            tid = transfer_id or fname
            transport = self._transfer_transport_label(link)
            self._emit_progress(
                fname or "file",
                0,
                direction="receive",
                transfer_id=transfer_id,
                status="cancelled",
                transport=transport,
            )
            print(f"[transfer] Incoming transfer cancelled: {fname or transfer_id or '?'}")
        return cancelled

    def cancel_transfer(self, transfer_id=None, file_name=None, notify_peer=True):
        cancelled = False
        tid = self._resolve_transfer_id(transfer_id, file_name)
        if not tid:
            return False
        self._cancelled_transfers.add(tid)
        cancel_ev = self._cancel_events.get(tid)
        if cancel_ev:
            cancel_ev.set()
            cancelled = True
        targets = [(rid, res) for rid, res in self._active_resources.items() if rid == tid]
        if not targets and file_name:
            for rid, res in list(self._active_resources.items()):
                msg = self._sent_messages.get(rid)
                if msg and getattr(msg, "file_name", None) == file_name:
                    targets.append((rid, res))
                    tid = rid
                    self._cancelled_transfers.add(tid)
                    ev = self._cancel_events.get(tid)
                    if ev:
                        ev.set()
        for rid, resource in targets:
            try:
                if hasattr(resource, "cancel"):
                    resource.cancel()
                elif hasattr(resource, "close"):
                    resource.close()
                cancelled = True
                print(f"[transfer] Cancelled resource {rid}")
            except Exception as e:
                print(f"[transfer] cancel resource {rid}: {e}")
            self._cleanup_transfer(rid)
        fname = file_name or ""
        msg = self._sent_messages.get(tid)
        if msg and getattr(msg, "file_name", None):
            fname = msg.file_name
        if not fname:
            for entry in reversed(self.message_queue):
                if entry.get("msg_id") == tid:
                    fname = entry.get("file_name", "")
                    break
        if notify_peer:
            self._notify_peer_transfer_cancel(tid, file_name=fname)
        if cancelled or tid in self._cancelled_transfers:
            transport = self._transfer_transport_label()
            self._emit_progress(
                fname, 0, status="cancelled", direction="send",
                transfer_id=tid, transport=transport,
            )
        if self._current_transfer_id == tid:
            self._current_transfer_id = None
        return cancelled or tid in self._cancelled_transfers

    def _send_long_text(self, msg, text, data, receipt_callback, link=None):
        link = link or self._outgoing_link()
        import tempfile as _tf
        tmp = _tf.NamedTemporaryFile(delete=False, suffix=".txt", mode="w", encoding="utf-8")
        tmp.write(text)
        tmp_path = tmp.name
        tmp.close()
        fsize = len(data)
        meta = ChatMessage(
            MESSAGE_TYPE_LONGTEXT,
            json.dumps({"msg_id": msg.msg_id, "file_name": "longtext.txt"}),
            msg_id=msg.msg_id,
            file_name="longtext.txt",
            file_size=fsize,
        )
        try:
            packet = RNS.Packet(link, meta.to_json().encode("utf-8"))
            packet.send()
        except Exception as e:
            print(f"[messaging] Long text metadata send failed: {e}")
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
            return False
        try:
            if not self._wait_for_send_slot(timeout_s=120):
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass
                return False
            f = open(tmp_path, "rb")
            self._file_handles[msg.msg_id] = f
            self._longtext_temp_paths[msg.msg_id] = tmp_path
            self._current_transfer_id = msg.msg_id

            def longtext_done(resource):
                tmp_cleanup = self._longtext_temp_paths.pop(msg.msg_id, None)
                if tmp_cleanup:
                    try:
                        os.unlink(tmp_cleanup)
                    except Exception:
                        pass
                self._resource_send_callback("longtext.txt", msg.msg_id, fsize)(resource)

            resource = RNS.Resource(
                f, link,
                callback=longtext_done,
                progress_callback=None,
                auto_compress=True,
            )
            self._active_resources[msg.msg_id] = resource
            print(f"[messaging] Sent long text: {text[:50]}... ({fsize} bytes as resource)")
            self._sent_messages[msg.msg_id] = msg
            self._pending_sends[msg.msg_id] = time.time()
            if receipt_callback:
                self._receipt_callbacks[msg.msg_id] = receipt_callback
            return msg
        except Exception as e:
            print(f"[messaging] Long text resource send failed: {e}")
            self._longtext_temp_paths.pop(msg.msg_id, None)
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
            self._cleanup_transfer(msg.msg_id)
            return False

    def _wait_for_send_slot(self, timeout_s=180):
        deadline = time.time() + timeout_s
        while self._current_transfer_id or self._active_resources:
            if time.time() > deadline:
                print("[transfer] Timed out waiting for previous transfer to finish")
                return False
            time.sleep(0.15)
        return True

    def _watch_lan_http_send(self, transfer_id, fname, fsize):
        from chatx5.core.lan_transfer import get_offer_state

        deadline = time.time() + max(7200, fsize / (200 * 1024))
        while time.time() < deadline:
            if transfer_id in self._cancelled_transfers:
                remove_offer(transfer_id)
                self._emit_progress(fname, 0, fsize, status="cancelled", direction="send", transfer_id=transfer_id)
                if self._current_transfer_id == transfer_id:
                    self._current_transfer_id = None
                return
            offer = get_offer_state(transfer_id)
            if offer is None:
                self._emit_progress(fname, 100, fsize, status="complete", direction="send", transfer_id=transfer_id)
                if self._current_transfer_id == transfer_id:
                    self._current_transfer_id = None
                return
            sent = int(offer.get("bytes_sent") or 0)
            pct = int((sent / fsize) * 100) if fsize else 0
            speed = self._calc_transfer_speed(transfer_id, sent)
            self._emit_progress(fname, pct, fsize, speed=speed, direction="send", transfer_id=transfer_id)
            time.sleep(0.2)

    def _send_file_lan_http(self, file_path, msg_type, fname, fsize, transfer_id, link, peer, progress_callback):
        peer_ip, _peer_port = self._peer_endpoint(peer)
        host_ip = lan_ip()
        if not peer_ip or not host_ip:
            return None
        token = register_offer(
            transfer_id, file_path, peer,
            host=host_ip, port=self.http_port,
        )
        offer = {
            "transfer_id": transfer_id,
            "token": token,
            "host": host_ip,
            "port": self.http_port,
            "file_name": fname,
            "file_size": fsize,
            "msg_type": msg_type,
        }
        meta = ChatMessage(
            MESSAGE_TYPE_LAN_HTTP,
            json.dumps(offer),
            file_name=fname,
            file_size=fsize,
            msg_id=transfer_id,
        )
        packet = RNS.Packet(link, meta.to_json().encode("utf-8"))
        packet.send()
        print(
            f"[transfer] LAN HTTP offer {fname} ({fsize} bytes) "
            f"http://{host_ip}:{self.http_port}/api/lan-transfer/{transfer_id}"
        )
        threading.Thread(
            target=self._watch_lan_http_send,
            args=(transfer_id, fname, fsize),
            name=f"lan-http-send-{transfer_id[:8]}",
            daemon=True,
        ).start()
        return meta

    def _handle_lan_http_offer(self, chat_msg, remote_hash):
        threading.Thread(
            target=self._download_lan_http_offer,
            args=(chat_msg, remote_hash),
            name=f"lan-http-rx-{chat_msg.msg_id[:8]}",
            daemon=True,
        ).start()

    def _download_lan_http_offer(self, chat_msg, remote_hash):
        from chatx5.utils.helpers import safe_basename, safe_path_under

        try:
            offer = json.loads(chat_msg.content or "{}")
        except Exception as exc:
            print(f"[transfer] Invalid LAN HTTP offer: {exc}")
            return
        host = (offer.get("host") or "").strip()
        port = int(offer.get("port") or self.http_port)
        transfer_id = offer.get("transfer_id") or chat_msg.msg_id
        token = offer.get("token") or ""
        fname = safe_basename(offer.get("file_name") or chat_msg.file_name or f"file_{int(time.time())}")
        fsize = int(offer.get("file_size") or chat_msg.file_size or 0)
        if not host or not token:
            print("[transfer] LAN HTTP offer missing host/token")
            return
        url = f"http://{host}:{port}/api/lan-transfer/{transfer_id}?token={token}"
        os.makedirs(self.receive_dir, exist_ok=True)
        save_path = safe_path_under(self.receive_dir, fname)
        if not save_path:
            print(f"[transfer] Rejected unsafe LAN HTTP filename: {fname!r}")
            return
        self._emit_progress(fname, 0, fsize, direction="receive", transfer_id=transfer_id, status="active")
        received = 0
        try:
            req = urlrequest.Request(url, method="GET")
            with urlrequest.urlopen(req, timeout=max(60, fsize // (512 * 1024))) as resp:
                with open(save_path, "wb") as out:
                    while True:
                        chunk = resp.read(LAN_HTTP_CHUNK)
                        if not chunk:
                            break
                        out.write(chunk)
                        received += len(chunk)
                        pct = int((received / fsize) * 100) if fsize else 0
                        speed = self._calc_transfer_speed(transfer_id, received)
                        self._emit_progress(
                            fname, pct, fsize, speed=speed,
                            direction="receive", transfer_id=transfer_id,
                        )
            print(f"[transfer] LAN HTTP saved {fname} -> {save_path} ({received} bytes)")
            self._emit_progress(fname, 100, fsize, direction="receive", transfer_id=transfer_id, status="complete")
            if self.on_message:
                done = ChatMessage(
                    offer.get("msg_type", MESSAGE_TYPE_FILE),
                    save_path,
                    sender=remote_hash,
                    file_name=fname,
                    file_size=received or fsize,
                    msg_id=transfer_id,
                )
                self.on_message(done, remote_hash)
        except Exception as exc:
            print(f"[transfer] LAN HTTP download failed: {exc}")
            self._emit_progress(fname, 0, fsize, direction="receive", transfer_id=transfer_id, status="failed")
            try:
                if os.path.isfile(save_path) and os.path.getsize(save_path) == 0:
                    os.remove(save_path)
            except OSError:
                pass

    def send_file(self, file_path, msg_type=MESSAGE_TYPE_FILE, progress_callback=None,
                  transfer_id=None, target_peer=None, link=None, hub_group=False):
        peer = self.dest_hash_for(target_peer or self.active_peer_hash or "")
        link = link or self._best_transfer_link(peer) or self._outgoing_link(peer)
        if link:
            self._optimise_link_mtu(link)
        if not link or not os.path.exists(file_path):
            print(f"[messaging] send_file: no link to {peer[:16] if peer else 'peer'} or missing file")
            return False
        try:
            if getattr(link, "status", None) != RNS.Link.ACTIVE:
                print("[messaging] send_file: link not active")
                return False
        except Exception:
            pass
        with self._file_send_lock:
            if not self._wait_for_send_slot(timeout_s=300):
                return False
            fname = os.path.basename(file_path)
            fsize = os.path.getsize(file_path)
            chat_msg = ChatMessage(msg_type, str(time.time()), file_name=fname, file_size=fsize, msg_id=transfer_id)
            if hub_group:
                chat_msg.hub_group = True
            transfer_id = chat_msg.msg_id
            self._current_transfer_id = transfer_id
            cancel_ev = threading.Event()
            self._cancel_events[transfer_id] = cancel_ev
            try:
                if (
                    self.lan_transfer_enabled
                    and fsize >= LAN_HTTP_MIN_BYTES
                    and physical_lan_reachable()
                    and not self._peer_uses_hub_transport(peer)
                ):
                    lan_msg = self._send_file_lan_http(
                        file_path, msg_type, fname, fsize, transfer_id, link, peer, progress_callback,
                    )
                    if lan_msg:
                        self._sent_messages[chat_msg.msg_id] = chat_msg
                        return chat_msg

                packet = RNS.Packet(link, chat_msg.to_json().encode("utf-8"))
                packet.send()

                resource_holder = {"resource": None}

                def wrapped_progress(resource):
                    if cancel_ev.is_set() or transfer_id in self._cancelled_transfers:
                        try:
                            if hasattr(resource, "cancel"):
                                resource.cancel()
                            elif hasattr(resource, "close"):
                                resource.close()
                        except Exception:
                            pass
                        return
                    if progress_callback:
                        progress_callback(resource)
                    try:
                        pct = int(resource.get_progress() * 100)
                        transferred = int(float(resource.get_progress()) * fsize) if fsize else 0
                        speed = self._calc_transfer_speed(transfer_id, transferred)
                        self._emit_progress(
                            fname, pct, fsize, speed=speed,
                            direction="send", transfer_id=transfer_id,
                        )
                    except Exception:
                        pass

                f = open(file_path, "rb")
                self._file_handles[transfer_id] = f
                ext = os.path.splitext(file_path)[1].lower()
                xfer_link = link or self._outgoing_link(peer)
                xfer_iface = self._link_attached_interface(xfer_link)
                xfer_fam = interface_family(xfer_iface)
                fast_path = xfer_fam in ("tcp", "lan", "udp")
                serial_path = is_serial_interface(xfer_iface)
                compress = (
                    not serial_path
                    and msg_type not in (MESSAGE_TYPE_IMAGE, MESSAGE_TYPE_VIDEO)
                    and fsize > 65536
                    and ext not in _NO_COMPRESS_SUFFIXES
                    and not fast_path
                )
                timeout_s = None
                if serial_path:
                    from chatx5.core.serial_transfer import (
                        serial_baud_from_interface,
                        serial_transfer_timeout_s,
                    )
                    timeout_s = serial_transfer_timeout_s(
                        fsize, serial_baud_from_interface(xfer_iface),
                    )
                resource = RNS.Resource(
                    f, link,
                    callback=self._resource_send_callback(fname, transfer_id, fsize),
                    progress_callback=wrapped_progress,
                    auto_compress=compress,
                    timeout=timeout_s,
                )
                tune_outgoing_resource(resource, xfer_iface)
                resource_holder["resource"] = resource
                self._active_resources[transfer_id] = resource
                mode = "serial (window=2)" if serial_path else (xfer_fam or "unknown")
                print(f"[messaging] Sent file: {fname} ({fsize} bytes) via {mode}")
                self._sent_messages[chat_msg.msg_id] = chat_msg
                return chat_msg
            except Exception as e:
                print(f"[messaging] File send failed: {e}")
                self._emit_progress(fname, 0, fsize, status="failed", direction="send", transfer_id=transfer_id)
                self._cleanup_transfer(transfer_id)
                return False

    def _resource_send_callback(self, fname, transfer_id=None, fsize=0):
        def callback(resource):
            was_cancelled = (
                self.shutdown_requested or transfer_id in self._cancelled_transfers
            )
            self._cleanup_transfer(transfer_id)
            if was_cancelled:
                self._cancelled_transfers.discard(transfer_id)
                print(f"[messaging] File transfer cancelled: {fname}")
                self._emit_progress(
                    fname, 0, fsize, status="cancelled", direction="send",
                    transfer_id=transfer_id,
                )
                if self.on_transfer_revoked and transfer_id:
                    try:
                        self.on_transfer_revoked(transfer_id, fname)
                    except Exception:
                        pass
                if self._current_transfer_id == transfer_id:
                    self._current_transfer_id = None
                return
            print(f"[messaging] File transfer complete: {fname}")
            status = "complete"
            try:
                if resource.status != RNS.Resource.COMPLETE:
                    status = "failed"
            except Exception:
                pass
            pct = 100 if status == "complete" else 0
            self._emit_progress(fname, pct, fsize, status=status, direction="send", transfer_id=transfer_id)
            if self._current_transfer_id == transfer_id:
                self._current_transfer_id = None
        return callback
