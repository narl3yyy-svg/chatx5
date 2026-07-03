"""Message queue persistence, drain, and retry."""

import json
import os
import threading
import time
import uuid

import RNS

from chatx5.core.messaging.constants import (
    QUEUE_DRAIN_DELAY_S,
    QUEUE_RECEIPT_TIMEOUT_S,
)
from chatx5.core.messaging.peers import is_hub_peer_hash


class QueueMixin:
    """Queued message store and per-peer drain."""

    def _load_queue(self):
        try:
            with open(self.queue_file) as f:
                return json.load(f)
        except Exception:
            return []

    def _save_queue(self):
        try:
            with open(self.queue_file, "w") as f:
                json.dump(self.message_queue, f, indent=2)
        except Exception:
            pass

    def enqueue(self, msg_type, content, target_hash=None, file_name=None, file_size=None, file_path=None, msg_id=None):
        msg_id = msg_id or str(uuid.uuid4())[:12]
        for entry in self.message_queue:
            if entry.get("msg_id") == msg_id:
                print(f"[queue] Already queued {msg_type} ({msg_id[:8]})")
                return
        entry = {
            "type": msg_type,
            "content": content,
            "target_hash": target_hash,
            "file_name": file_name,
            "file_size": file_size,
            "file_path": file_path,
            "msg_id": msg_id,
            "timestamp": time.time(),
        }
        self.message_queue.append(entry)
        self._save_queue()
        print(f"[queue] Enqueued {msg_type} for target {target_hash[:16] if target_hash else 'any (next peer)'}")

    def _queue_matches_target(self, entry, target_hash):
        tgt = entry.get("target_hash")
        if not tgt:
            return not target_hash
        if not target_hash:
            return False
        if is_hub_peer_hash(tgt) != is_hub_peer_hash(target_hash):
            return False
        if self.hashes_equivalent(tgt, target_hash):
            return True
        return self._peers_share_contact(tgt, target_hash)

    def _remove_queue_entry(self, msg_id):
        if not msg_id:
            return False
        before = len(self.message_queue)
        self.message_queue = [
            e for e in self.message_queue if e.get("msg_id") != msg_id
        ]
        if len(self.message_queue) < before:
            self._save_queue()
            print(f"[queue] Confirmed delivery for {msg_id[:8]}")
            return True
        return False

    def _queue_send_link(self, peer_hash, link_hint=None, prefer_transport=None):
        peer = self.dest_hash_for(peer_hash)
        if not peer or peer == "unknown":
            return None
        transport = self._normalize_transport(prefer_transport) if prefer_transport else None
        if not transport and self._session_transport and self._session_peer_hash:
            if (
                self.hashes_equivalent(peer, self._session_peer_hash)
                or self._peers_share_contact(peer, self._session_peer_hash)
            ):
                transport = self._session_transport
        if transport:
            preferred = self._link_for_peer(peer, transport=transport)
            if preferred and self._link_interface_healthy(preferred) and self._link_matches_peer(preferred, peer):
                if self._link_acceptable_for_peer(preferred, peer):
                    return preferred
        if link_hint and self._link_matches_peer(link_hint, peer):
            if self._link_acceptable_for_peer(link_hint, peer):
                return link_hint
        best = self._best_outgoing_link(peer)
        if best and self._link_acceptable_for_peer(best, peer):
            return best
        hinted = link_hint if (
            link_hint
            and self._link_matches_peer(link_hint, peer)
            and self._link_acceptable_for_peer(link_hint, peer)
        ) else None
        if hinted:
            return hinted
        fallback = self._link_for_peer(peer)
        if fallback and self._link_acceptable_for_peer(fallback, peer):
            return fallback
        return None

    def _schedule_queue_drain(self, peer_hash, link=None, include_files=True, delay=None):
        peer = self.dest_hash_for(peer_hash)
        if not peer or peer == "unknown" or is_hub_peer_hash(peer):
            return
        if self.is_user_disconnected(peer):
            return
        wait = QUEUE_DRAIN_DELAY_S if delay is None else delay

        def run():
            with self._queue_drain_lock:
                self._queue_drain_timers.pop(peer, None)
            try:
                if not self.running or self.is_user_disconnected(peer):
                    return
                self._drain_queue_for_peer(peer, link_hint=link, include_files=include_files)
            except Exception as e:
                print(f"[queue] Scheduled drain error: {e}")

        with self._queue_drain_lock:
            existing = self._queue_drain_timers.pop(peer, None)
            if existing:
                existing.cancel()
            timer = threading.Timer(wait, run)
            timer.daemon = True
            self._queue_drain_timers[peer] = timer
            timer.start()

    def _drain_queue_for_peer(self, peer_hash, link_hint=None, include_files=True):
        peer = self.dest_hash_for(peer_hash)
        if not peer or peer == "unknown" or is_hub_peer_hash(peer):
            return 0
        if not self._peer_link_active(peer):
            return 0
        send_link = self._queue_send_link(peer, link_hint=link_hint)
        if not send_link:
            return 0
        self._consolidate_peer_links(peer, keep_link=send_link)
        return self.drain_queue(send_link, peer, include_files=include_files)

    def drain_queue(self, link, target_hash, include_files=True):
        peer = self.dest_hash_for(target_hash)
        if not peer or is_hub_peer_hash(peer):
            return 0
        send_link = self._queue_send_link(peer, link_hint=link)
        if not send_link:
            return 0
        remaining = []
        sent = 0
        confirmed_ids = set()
        now = time.time()
        for entry in self.message_queue:
            if not self._queue_matches_target(entry, peer):
                remaining.append(entry)
                continue
            try:
                if entry["type"] in ("text", "emoji"):
                    sent_at = entry.get("_queue_sent_at")
                    if sent_at and (now - sent_at) < QUEUE_RECEIPT_TIMEOUT_S:
                        remaining.append(entry)
                        continue
                    if sent_at:
                        entry.pop("_queue_sent_at", None)
                    msg_id = entry.get("msg_id")
                    sent_msg = []

                    def on_receipt(status, receipt, mid=msg_id, qentry=entry):
                        if status not in ("received", "read"):
                            return
                        confirmed_ids.add(mid)
                        self._remove_queue_entry(mid)
                        if self.on_queue_sent and sent_msg:
                            try:
                                self.on_queue_sent(sent_msg[0], peer, qentry)
                            except Exception as e:
                                print(f"[queue] on_queue_sent error: {e}")

                    result = self.send_message(
                        entry["content"],
                        msg_id=msg_id,
                        target_peer=peer,
                        link=send_link,
                        receipt_callback=on_receipt,
                    )
                    if result:
                        sent_msg.append(result)
                        entry["_queue_sent_at"] = time.time()
                        sent += 1
                        if msg_id not in confirmed_ids:
                            remaining.append(entry)
                    else:
                        remaining.append(entry)
                elif entry["type"] in ("file", "image", "video", "voice"):
                    if not include_files:
                        remaining.append(entry)
                        continue
                    fp = entry.get("file_path") or entry.get("content")
                    if fp and os.path.exists(fp):
                        result = self.send_file(
                            fp,
                            entry["type"],
                            transfer_id=entry.get("msg_id"),
                            target_peer=peer,
                            link=send_link,
                        )
                        if result:
                            sent += 1
                            if self.on_queue_sent:
                                try:
                                    self.on_queue_sent(result, peer, entry)
                                except Exception as e:
                                    print(f"[queue] on_queue_sent error: {e}")
                        else:
                            remaining.append(entry)
                    else:
                        print(f"[queue] File no longer exists: {fp}")
            except Exception as e:
                print(f"[queue] Failed to send: {e}")
                remaining.append(entry)
        if sent:
            print(
                f"[queue] Drained {sent} queued item(s) for {peer[:16]}... "
                f"(awaiting receipt)"
            )
        self.message_queue = remaining
        self._save_queue()
        return sent

    def clear_queue(self, target_hash=None):
        if not target_hash:
            self.message_queue = []
        else:
            self.message_queue = [
                e for e in self.message_queue
                if not self._queue_matches_target(e, target_hash)
            ]
        self._save_queue()

    def retry_queue(self):
        if not self.message_queue:
            return 0
        targets = set()
        for entry in self.message_queue:
            tgt = entry.get("target_hash")
            if tgt:
                targets.add(self.dest_hash_for(tgt))
        if not targets:
            if self.active_peer_hash:
                targets.add(self.dest_hash_for(self.active_peer_hash))
        sent = 0
        for peer in targets:
            if not self._peer_link_active(peer):
                continue
            link = self._queue_send_link(peer)
            if not link:
                continue
            try:
                if getattr(link, "status", None) != RNS.Link.ACTIVE:
                    continue
            except Exception:
                pass
            sent += self.drain_queue(link, peer, include_files=True)
        role, hub_hash = self._load_hub_settings()
        if role != "off":
            sent += self.drain_hub_group_queue(
                hub_server_hash=hub_hash,
                hub_server_mode=(role == "server"),
            )
        return sent

    def queue_size(self):
        return len(self.message_queue)

    def queue_size_for(self, target_hash=None):
        if not target_hash:
            return len(self.message_queue)
        return sum(
            1 for entry in self.message_queue
            if self._queue_matches_target(entry, target_hash)
        )

    def prune_stale_queue(self, sent_msg_ids=None):
        """Drop queue rows already marked sent in chat history."""
        sent = set(sent_msg_ids or [])
        if not sent:
            return 0
        before = len(self.message_queue)
        self.message_queue = [
            e for e in self.message_queue
            if e.get("msg_id") not in sent
        ]
        if len(self.message_queue) != before:
            self._save_queue()
        return before - len(self.message_queue)
