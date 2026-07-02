"""Inbound RNS link and packet callbacks for MessagingBackend."""

import json
import time

from chatx5.core.lan_rns import interface_family
from chatx5.core.messaging.constants import (
    MESSAGE_TYPE_EMOJI,
    MESSAGE_TYPE_FILE,
    MESSAGE_TYPE_IMAGE,
    MESSAGE_TYPE_LAN_HTTP,
    MESSAGE_TYPE_LONGTEXT,
    MESSAGE_TYPE_TEXT,
    MESSAGE_TYPE_TRANSFER_CANCEL,
    MESSAGE_TYPE_VIDEO,
    MESSAGE_TYPE_VOICE,
)
from chatx5.core.messaging.models import ChatMessage
from chatx5.core.messaging.peers import is_hub_peer_hash
from chatx5.utils.platform import physical_lan_reachable


class InboundCallbacksMixin:
    def _link_callback(self, link):
        peer_hash = self._resolve_incoming_link_peer(link, self._peer_destination_hash(link))
        if is_hub_peer_hash(peer_hash):
            identity_peer = self.dest_hash_for(self._peer_hash_from_link_identity(link))
            peer_hash = identity_peer if identity_peer and not is_hub_peer_hash(identity_peer) else "unknown"
        self._cache_link_peer(link, peer_hash)
        if is_hub_peer_hash(peer_hash):
            try:
                link.teardown()
            except Exception:
                pass
            print("[messaging] Rejected inbound link — hub group is not a real peer")
            return

        if not self._peer_allowed_by_scope(peer_hash, link=link):
            try:
                link.teardown()
            except Exception:
                pass
            print(
                f"[messaging] Rejected inbound link from {peer_hash[:16]}... "
                "(outside LAN scope)"
            )
            return

        if self._inbound_link_is_hub_tcp(link):
            if not peer_hash or peer_hash == "unknown":
                resolved = self._resolve_remote_peer(link, fallback=peer_hash)
                if resolved and resolved != "unknown":
                    peer_hash = resolved
                    self._cache_link_peer(link, peer_hash)
            print(
                f"[messaging] Incoming hub TCP link: {link.link_id.hex()[:12]} "
                f"({peer_hash[:16] if peer_hash and peer_hash != 'unknown' else 'unknown'}...)"
            )
            self._setup_link(link)
            if peer_hash and peer_hash != "unknown":
                self._register_peer_link(link, peer_hash, transport="tcp")
                role, _ = self._load_hub_settings()
                if role == "server":
                    n = len(self._hub_tcp_linked_peers())
                    print(f"[hub] Hub server: {n} TCP client(s) linked")
                self._notify_link_established(
                    link, peer_hash, promote_active=False, background=True,
                )
                self._schedule_hub_queue_drain()
            return

        incoming_fam = interface_family(self._link_attached_interface(link))
        expected = self._peer_expected_transport_families(peer_hash)
        if expected and incoming_fam != "serial":
            if incoming_fam in ("udp", "lan", "tcp") and not (expected & {"udp", "lan", "tcp"}):
                canon = self.dest_hash_for(peer_hash)
                if canon and canon != "unknown":
                    from chatx5.core.lan_rns import (
                        prune_lan_path_for_peer,
                        seed_serial_path_for_peer,
                    )
                    prune_lan_path_for_peer(canon)
                    seed_serial_path_for_peer(canon)
                try:
                    link.teardown()
                except Exception:
                    pass
                print(
                    f"[messaging] Rejected LAN inbound from {peer_hash[:16]}... "
                    "(serial peer)"
                )
                return
        if incoming_fam == "serial" and peer_hash and peer_hash != "unknown":
            canon = self.dest_hash_for(peer_hash)
            if canon and canon != "unknown":
                prune_lan_path_for_peer(canon)

        if self.active_link and self.active_peer_hash and not is_hub_peer_hash(self.active_peer_hash):
            same_peer = (
                self.hashes_equivalent(peer_hash, self.active_peer_hash)
                or (peer_hash == "unknown" and self._incoming_matches_active_session(link))
            )
            if same_peer:
                if peer_hash == "unknown":
                    peer_hash = self.dest_hash_for(self.active_peer_hash)
                    self._cache_link_peer(link, peer_hash)
                if link.link_id == self.active_link.link_id:
                    print(f"[messaging] Ignoring duplicate incoming link from {peer_hash[:16]}...")
                    try:
                        link.teardown()
                    except Exception:
                        pass
                    return
                if self._has_active_transfer():
                    print(f"[messaging] Keeping current link during active transfer ({peer_hash[:16]}...)")
                    try:
                        link.teardown()
                    except Exception:
                        pass
                    return
                old_score = self._link_path_score(self.active_link)
                new_score = self._link_path_score(link)
                old_healthy = self._link_interface_healthy(self.active_link)
                incoming_fam = interface_family(self._link_attached_interface(link))
                old_fam = interface_family(self._link_attached_interface(self.active_link))
                peer_expected = self._peer_expected_transport_families(peer_hash)
                prefer_serial = (
                    incoming_fam == "serial"
                    and peer_expected == {"serial"}
                ) or (
                    incoming_fam == "serial"
                    and not peer_expected
                    and (
                        self._failover_in_progress
                        or not physical_lan_reachable()
                        or self._peer_has_path_on_family(peer_hash, "serial")
                        or (old_fam in ("udp", "lan") and not old_healthy)
                    )
                )
                if (
                    prefer_serial
                    or new_score > old_score + 8
                    or (not old_healthy and new_score >= old_score)
                ):
                    self._handoff_to_link(link, peer_hash)
                else:
                    print(f"[messaging] Keeping current link (better path than incoming {peer_hash[:16]}...)")
                    try:
                        link.teardown()
                    except Exception:
                        pass
                return

        existing = self._link_for_peer(peer_hash)
        if existing and existing.link_id != link.link_id:
            print(
                f"[messaging] Replacing stale link from {peer_hash[:16]}... "
                f"({existing.link_id.hex()[:12]} -> {link.link_id.hex()[:12]})"
            )
            try:
                existing.teardown()
            except Exception:
                pass

        if not peer_hash or peer_hash == "unknown":
            resolved = self._peer_hash_from_link_identity(link)
            if resolved and resolved != "unknown":
                peer_hash = resolved
                self._cache_link_peer(link, peer_hash)
        print(f"[messaging] Incoming link established: {link.link_id.hex()[:12]} ({peer_hash[:16]}...)")
        self._last_handoff = False
        self._setup_link(link)
        passive_only = self.is_user_disconnected(peer_hash)
        if passive_only:
            promote = False
        else:
            promote = (
                not self.active_link
                or self.hashes_equivalent(peer_hash, self.active_peer_hash)
            )
        self._notify_link_established(
            link, peer_hash,
            promote_active=promote,
            background=not promote,
            passive=passive_only,
        )
        if not passive_only:
            self._schedule_queue_drain(peer_hash, link=link)

    def _link_closed(self, link):
        def callback(link):
            remote_hash = self.dest_hash_for(self._peer_for_link(link))
            if link.link_id in self.links:
                del self.links[link.link_id]
            self._link_peer_hashes.pop(link.link_id, None)
            if remote_hash and remote_hash != "unknown":
                peer = self.dest_hash_for(remote_hash)
                remaining = self._other_active_links_for_peer(peer, except_link=link)
                if remaining:
                    self.peer_links[peer] = remaining[0]
                    if (
                        self.active_link
                        and self.active_link.link_id == link.link_id
                        and not self._link_handoff
                    ):
                        self._notify_link_established(
                            remaining[0], peer,
                            promote_active=True, background=False,
                        )
                else:
                    self._unlink_peer(peer)
            if not self._link_handoff:
                xfer_peer = self.dest_hash_for(
                    self._session_peer_hash or self.active_peer_hash or remote_hash or ""
                )
                alt_link = self._link_for_peer(xfer_peer) if xfer_peer else None
                if (
                    self._has_active_transfer()
                    and alt_link
                    and alt_link.link_id != link.link_id
                ):
                    self._migrate_pending_files(link.link_id, alt_link.link_id)
                else:
                    self._flush_pending_files_failed(link.link_id)
            closing_active = self.active_link and self.active_link.link_id == link.link_id
            if closing_active and not self._link_handoff:
                lost_peer = self.dest_hash_for(self.active_peer_hash)
                if (
                    self.active_peer_hash
                    and lost_peer
                    and not self.is_user_disconnected(lost_peer)
                ):
                    self._session_peer_hash = self.active_peer_hash
                self.active_link = None
                self.active_peer_hash = None
                if lost_peer and not self.is_user_disconnected(lost_peer):
                    self._last_link_lost_at = time.time()
                session_peer = self.dest_hash_for(self._session_peer_hash or "")
                if session_peer and not self.is_user_disconnected(session_peer):
                    next_link = self._link_for_peer(session_peer)
                    if next_link and next_link.link_id != link.link_id:
                        self.active_link = next_link
                        self.active_peer_hash = session_peer
                        self._send_link = next_link
            if self._send_link and self._send_link.link_id == link.link_id:
                self._send_link = self.active_link
            if self.on_link_closed and not self._link_handoff:
                try:
                    self.on_link_closed(remote_hash, handoff=closing_active and bool(self.active_link))
                except TypeError:
                    try:
                        self.on_link_closed(remote_hash)
                    except Exception as e:
                        print(f"[messaging] on_link_closed error: {e}")
                except Exception as e:
                    print(f"[messaging] on_link_closed error: {e}")
        return callback

    def _packet_callback(self, link):
        def callback(message, packet):
            try:
                chat_msg = ChatMessage.from_json(message.decode("utf-8"))
                remote_hash = self.dest_hash_for(self._peer_for_link(link))
                if remote_hash and not self._peer_allowed_by_scope(remote_hash, link=link):
                    if chat_msg.msg_type not in ("__receipt", "__read_receipt"):
                        print(
                            f"[messaging] Dropped {chat_msg.msg_type} from "
                            f"{remote_hash[:16]}... (outside LAN scope)"
                        )
                    return

                if chat_msg.msg_type == "__receipt":
                    try:
                        receipt = json.loads(chat_msg.content)
                        msg_id = receipt.get("msg_id")
                        status = receipt.get("status", "received")
                        self._pending_sends.pop(msg_id, None)
                        self._remove_queue_entry(msg_id)
                        cb = self._receipt_callbacks.pop(msg_id, None)
                        if cb:
                            cb(status, receipt)
                        print(f"[receipt] Received {status} for msg {msg_id[:8]} from {remote_hash[:16]}")
                    except Exception as e:
                        print(f"[receipt] Error: {e}")
                    return

                if chat_msg.msg_type == "__read_receipt":
                    try:
                        receipt = json.loads(chat_msg.content)
                        msg_id = receipt.get("msg_id")
                        cb = self._receipt_callbacks.pop(msg_id, None)
                        if cb:
                            cb("read", receipt)
                        print(f"[receipt] Read receipt for msg {msg_id[:8]} from {remote_hash[:16]}")
                    except Exception as e:
                        print(f"[receipt] Read receipt error: {e}")
                    return

                if chat_msg.msg_type == MESSAGE_TYPE_LAN_HTTP:
                    self._handle_lan_http_offer(chat_msg, remote_hash)
                    return

                if chat_msg.msg_type == MESSAGE_TYPE_TRANSFER_CANCEL:
                    try:
                        payload = json.loads(chat_msg.content or "{}")
                    except Exception:
                        payload = {}
                    tid = payload.get("transfer_id") or payload.get("msg_id") or chat_msg.msg_id
                    fname = payload.get("file_name") or ""
                    if tid:
                        self._cancelled_transfers.add(tid)
                    self._cancel_incoming_resources(link, transfer_id=tid, file_name=fname)
                    is_sender = (
                        tid in self._active_resources
                        or tid == self._current_transfer_id
                        or tid in self._sent_messages
                    )
                    if is_sender:
                        self.cancel_transfer(
                            transfer_id=tid, file_name=fname, notify_peer=False,
                        )
                        if self.on_transfer_revoked and tid:
                            try:
                                self.on_transfer_revoked(tid, fname)
                            except Exception:
                                pass
                    return

                if not self._hub_message_acceptable(chat_msg, link):
                    print("[hub] Ignored group message (hub transport only)")
                    return

                chat_msg.sender = remote_hash
                print(f"[messaging] Received {chat_msg.msg_type} from {remote_hash[:16]}...")

                if chat_msg.msg_type in (MESSAGE_TYPE_FILE, MESSAGE_TYPE_IMAGE, MESSAGE_TYPE_VIDEO, MESSAGE_TYPE_VOICE, MESSAGE_TYPE_LONGTEXT):
                    with self._pending_lock:
                        queue = self._pending_files.setdefault(link.link_id, [])
                        queue.append(chat_msg)
                    print(f"[messaging] Waiting for resource data for {chat_msg.file_name}...")
                    self._emit_progress(
                        chat_msg.file_name or "file",
                        0,
                        total_size=chat_msg.file_size or 0,
                        direction="receive",
                        transfer_id=chat_msg.msg_id,
                        status="active",
                    )
                    self._start_receive_progress_watch(link, chat_msg)
                elif self.on_message:
                    self.on_message(chat_msg, remote_hash)

                if chat_msg.msg_type in (MESSAGE_TYPE_TEXT, MESSAGE_TYPE_EMOJI):
                    self._send_receipt(link, chat_msg.msg_id, "received")
            except Exception as e:
                print(f"[messaging] Packet callback error: {e}")
                if self.on_message:
                    self.on_message(
                        ChatMessage("system", f"Failed to parse message: {e}"),
                        None
                    )
        return callback

