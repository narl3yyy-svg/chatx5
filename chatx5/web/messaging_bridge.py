"""Auto-extracted from web/server.py — MessagingBridge layer."""

import asyncio
import json
import os
import time
from urllib.parse import quote

import RNS

from chatx5.core.contacts import (
    contact_has_hash,
    find_contact_by_hash,
    list_contacts,
)
from chatx5.core.discovery import normalize_hash
from chatx5.core.messaging import HUB_GROUP_PEER, is_hub_peer_hash
from chatx5.utils.android_notify import show_message_notification
from chatx5.utils.helpers import (
    format_speed,
)
from chatx5.utils.platform import (
    is_android,
)


class MessagingBridgeMixin:
    def _peer_endpoint_for_transfer(self, peer_hash):
        from chatx5.core.discovery import normalize_hash

        target = normalize_hash(peer_hash or "")
        if not target:
            return None
        for peer in self._scoped_peers():
            ph = normalize_hash(peer.get("hash") or "")
            ih = normalize_hash(peer.get("identity_hash") or "")
            if target in (ph, ih):
                ip = (peer.get("ip") or "").strip()
                if ip:
                    return ip, int(peer.get("port") or self.port)
        for contact in list_contacts(self.config_dir):
            ph = normalize_hash(contact.get("hash") or "")
            if ph == target and contact.get("ip"):
                return str(contact["ip"]).strip(), int(contact.get("port") or self.port)
        return None

    def _resolve_incoming_peer(self, ident_hex=None, computed_dest=None, fallback=None, link=None):
        from chatx5.core.discovery import normalize_hash

        if computed_dest and not self._is_self_hash(computed_dest):
            return self._peer_dest_hash(computed_dest)

        clean_fallback = normalize_hash(fallback)
        if clean_fallback and not self._is_self_hash(clean_fallback):
            return self._peer_dest_hash(clean_fallback)

        if ident_hex and not self._is_self_hash(ident_hex) and self.discovery:
            for p in self.discovery.get_peers():
                ph = normalize_hash(p.get("hash"))
                ih = normalize_hash(p.get("identity_hash"))
                if ident_hex == ih or ident_hex == ph:
                    return ph or ident_hex

        if self.messaging and ident_hex and not self._is_self_hash(ident_hex):
            mapped = self.messaging.dest_hash_for(ident_hex)
            if mapped and len(mapped) == 32 and not self._is_self_hash(mapped):
                return mapped

        session_peer = self._session_chat_peer()
        if (
            session_peer
            and not self._is_self_hash(session_peer)
            and not is_hub_peer_hash(session_peer)
        ):
            if not ident_hex or self.messaging and self.messaging.hashes_equivalent(
                ident_hex, session_peer
            ):
                return session_peer

        if ident_hex and not self._is_self_hash(ident_hex):
            if computed_dest and not self._is_self_hash(computed_dest):
                return self._peer_dest_hash(computed_dest)
            if self.messaging:
                canon = self.messaging.canonical_connect_hash(ident_hex, link=link)
                if canon:
                    return canon
        return ""

    def _resolve_peer_hash(self, peer_hash):
        from chatx5.core.discovery import message_dest_hash_for_identity, normalize_hash
        clean = normalize_hash(peer_hash)
        if not clean:
            return clean
        if self.messaging:
            mapped = self.messaging.dest_hash_for(clean)
            if mapped and len(mapped) == 32 and not self._is_self_hash(mapped):
                return mapped
            ident = self.messaging._identity_for_hash(clean)
            if ident:
                dest = message_dest_hash_for_identity(ident)
                if dest:
                    self.messaging.register_peer_mapping(
                        dest, normalize_hash(RNS.hexrep(ident.hash))
                    )
                    return dest
        if self.discovery:
            for p in self.discovery.get_peers():
                ph = normalize_hash(p.get("hash"))
                ih = normalize_hash(p.get("identity_hash"))
                if clean == ph or clean == ih:
                    if p.get("via") == "rns" and ph:
                        return ph
                    if self.messaging:
                        ident = self.messaging._identity_for_hash(ih or ph)
                        if ident:
                            dest = message_dest_hash_for_identity(ident)
                            if dest:
                                return dest
                    return ph or clean
        return clean

    def _received_dir(self):
        settings = self.load_settings()
        return os.path.normpath(settings.get("received_dir", os.path.join(self.config_dir, "received")))

    def _sent_dir(self):
        return os.path.normpath(os.path.join(self.config_dir, "sent"))

    def _encode_file_rel(self, rel):
        return "/".join(quote(part, safe="") for part in rel.replace("\\", "/").split("/"))

    def _file_url(self, filepath):
        if not filepath:
            return ""
        full = os.path.normpath(filepath)
        if not os.path.isfile(full):
            return ""
        received = self._received_dir()
        sent = self._sent_dir()
        if full.startswith(received + os.sep) or full == received:
            rel = os.path.relpath(full, received)
            return "/api/file/received/" + self._encode_file_rel(rel)
        if full.startswith(sent + os.sep) or full == sent:
            rel = os.path.relpath(full, sent)
            return "/api/file/sent/" + self._encode_file_rel(rel)
        default_received = os.path.normpath(os.path.join(self.config_dir, "received"))
        if full.startswith(default_received + os.sep):
            rel = os.path.relpath(full, default_received)
            return "/api/file/received/" + self._encode_file_rel(rel)
        return ""

    def _coerce_share_browse_message(self, chat_msg):
        """Hub group shares were sometimes sent as JSON text — normalize for the UI."""
        from chatx5.core.messaging.constants import MESSAGE_TYPE_SHARE_BROWSE

        if chat_msg.msg_type == MESSAGE_TYPE_SHARE_BROWSE:
            return chat_msg
        if chat_msg.msg_type != "text":
            return chat_msg
        try:
            parsed = json.loads(chat_msg.content or "")
        except Exception:
            return chat_msg
        if not isinstance(parsed, dict):
            return chat_msg
        if not parsed.get("session_id") or not parsed.get("token"):
            return chat_msg
        chat_msg.msg_type = MESSAGE_TYPE_SHARE_BROWSE
        return chat_msg

    def _on_message(self, chat_msg, sender_hash, link=None):
        chat_msg = self._coerce_share_browse_message(chat_msg)
        hub_group = bool(getattr(chat_msg, "hub_group", False))
        if not hub_group and chat_msg.msg_type == "share_browse":
            try:
                offer = json.loads(chat_msg.content or "")
                hub_group = bool(isinstance(offer, dict) and offer.get("hub_group"))
            except Exception:
                pass
        if hub_group:
            settings = self.load_settings()
            if settings.get("hub_role", "off") == "off":
                if self.debug:
                    print("[hub] Dropped group message (hub disabled)")
                return
            chat_peer = HUB_GROUP_PEER
            wire_sender = normalize_hash(getattr(chat_msg, "sender", None) or "")
            if len(wire_sender) == 32:
                if self.messaging:
                    sender = (
                        self.messaging.canonical_connect_hash(wire_sender)
                        or self._peer_dest_hash(wire_sender)
                    )
                else:
                    sender = self._peer_dest_hash(wire_sender)
            elif sender_hash and sender_hash != "system":
                if self.messaging:
                    sender = (
                        self.messaging.canonical_connect_hash(sender_hash)
                        or self._peer_dest_hash(sender_hash)
                    )
                else:
                    sender = self._peer_dest_hash(sender_hash)
            else:
                sender = "system"
        elif (
            sender_hash
            and sender_hash != "system"
            and not is_hub_peer_hash(sender_hash)
            and not self._peer_in_discovery_scope(sender_hash)
            and not self._sender_has_serial_path(sender_hash)
        ):
            if self.debug:
                print(
                    f"[network] Dropped message from {sender_hash[:16]}... "
                    "(outside LAN scope)"
                )
            return
        elif sender_hash and sender_hash != "system":
            if self.messaging:
                chat_peer = (
                    self.messaging.canonical_connect_hash(sender_hash, link=link)
                    or self._peer_dest_hash(sender_hash)
                )
            else:
                chat_peer = self._peer_dest_hash(sender_hash)
            sender = chat_peer
        else:
            chat_peer = self._peer_dest_hash(self.active_peer)
            sender = "system"
        msg_via = None
        if link and self.messaging:
            try:
                msg_via = self.messaging._transport_from_link(link)
            except Exception:
                msg_via = None
        entry = self._enrich_message({
            "type": chat_msg.msg_type,
            "content": chat_msg.content,
            "sender": sender,
            "peer": chat_peer,
            "chat_peer": chat_peer,
            "timestamp": chat_msg.timestamp,
            "file_name": chat_msg.file_name,
            "file_size": chat_msg.file_size,
            "msg_id": chat_msg.msg_id,
            "hub_group": hub_group,
            "via": msg_via,
            "status": "received" if sender_hash and sender_hash != "system" else "",
        }, outgoing=False)
        if self._is_session_system_message(chat_msg.content or ""):
            return
        self.message_history.append(entry)
        self._save_history()
        if self.debug:
            print(f"[chat] recv type={entry['type']} peer={entry.get('chat_peer', '')[:16]} msg_id={entry.get('msg_id', '')[:8]}")
        if self.websockets and self._loop:
            asyncio.run_coroutine_threadsafe(
                self._broadcast({"type": "message", "data": entry}),
                self._loop
            )
        settings = self.load_settings()
        if hub_group and settings.get("hub_role") == "server" and self.messaging and sender_hash:
            if chat_msg.msg_type in ("file", "image", "video", "voice"):
                import threading

                fp = chat_msg.content
                threading.Thread(
                    target=self.messaging.relay_hub_file,
                    args=(chat_msg, sender_hash, fp),
                    daemon=True,
                    name=f"hub-relay-file-{chat_msg.msg_id[:8]}",
                ).start()
            else:
                self.messaging.relay_hub_message(chat_msg, sender_hash)
        notify_peer = HUB_GROUP_PEER if hub_group else chat_peer
        if sender_hash and sender_hash != "system" and self._should_android_notify(notify_peer, entry):
            preview = self._notification_preview(entry)
            if hub_group:
                name = "Group chat"
            else:
                name = self._contact_name_for(chat_peer) or chat_peer[:8]
            show_message_notification(name, preview, notify_peer)

    def _resolve_send_target(self, peer_hash, prefer_via=None):
        """Map UI/contact hash to the transport-specific connect hash for sends."""
        clean = self._peer_dest_hash(peer_hash)
        if not clean or clean == "unknown":
            return ""
        via = (prefer_via or self._ui_state.get("viewing_via") or "").strip() or None
        transport_hash = self._contact_hash_for_transport(clean, via)
        if transport_hash:
            clean = transport_hash
        peer_ip = None
        if self.messaging:
            meta = self._discovery_peer_for_connect(None, clean, via=via)
            if meta:
                peer_ip = meta.get("ip")
        return self._resolve_current_peer_hash(clean, peer_ip, prefer_via=via)

    def _queue_target_hash(self):
        viewing = self._ui_state.get("viewing_peer")
        if viewing:
            return self._resolve_send_target(
                viewing, prefer_via=self._ui_state.get("viewing_via"),
            )
        return (
            self._session_chat_peer()
            or self._peer_dest_hash(self.active_peer)
            or getattr(self.messaging, "_session_peer_hash", None)
        )

    def _is_saved_contact(self, peer_hash):
        return find_contact_by_hash(self.config_dir, peer_hash) is not None

    def _clear_queue_for_peer(self, peer_hash):
        if not self.messaging:
            return 0
        before = self.messaging.queue_size()
        self.messaging.clear_queue(self._peer_dest_hash(peer_hash))
        return before - self.messaging.queue_size()

    def _purge_ephemeral_peer(self, peer_hash):
        peer = self._peer_dest_hash(peer_hash)
        if not peer or self._is_saved_contact(peer):
            return 0
        removed = self._clear_history_for_peer(peer)
        self._clear_queue_for_peer(peer)
        return removed

    def _enable_discovery(self, clear=False):
        if self.discovery:
            self.discovery.enable_discovery(clear=clear)

    def _on_beacon_periodic(self):
        if self.messaging and not self.messaging.shutdown_requested:
            try:
                self.messaging._silent_announce(also_serial=False)
            except Exception:
                pass

    def _apply_auto_announce_settings(self, settings):
        enabled = bool(settings.get("auto_announce", False))
        if self.messaging:
            self.messaging.auto_announce = enabled
        if enabled and self.discovery:
            self.discovery.enable_discovery(clear=False)
        if self.lan_beacon:
            self.lan_beacon.set_periodic(
                enabled,
                on_periodic=self._on_beacon_periodic if enabled else None,
            )

    def _contact_name_for(self, peer_hash):
        from chatx5.core.contacts import (
            _contact_hashes,
            _is_corrupt_contact_name,
            _sanitize_contact_name,
        )
        contact = find_contact_by_hash(self.config_dir, peer_hash)
        if contact:
            hashes = _contact_hashes(contact)
            name = _sanitize_contact_name(contact.get("name"), hashes)
            if name and not _is_corrupt_contact_name(name, hashes):
                return name
        for row in list_contacts(self.config_dir):
            if self._peers_equivalent(row.get("hash"), peer_hash):
                hashes = _contact_hashes(row)
                name = _sanitize_contact_name(row.get("name"), hashes)
                if name and not _is_corrupt_contact_name(name, hashes):
                    return name
        if self.discovery:
            for peer in self.discovery.get_peers():
                if (
                    self._peers_equivalent(peer.get("hash"), peer_hash)
                    or self._peers_equivalent(peer.get("identity_hash"), peer_hash)
                ):
                    name = _sanitize_contact_name(peer.get("name"))
                    if name and name != peer_hash[:8]:
                        return name
        settings = self.load_settings()
        my_hash = self._my_sender_hash()
        if settings.get("name") and self._peers_equivalent(peer_hash, my_hash):
            return settings.get("name")
        return ""

    def _peer_display_name(self, peer_hash):
        if not peer_hash or peer_hash == "system":
            return ""
        name = self._contact_name_for(peer_hash)
        if name:
            return name
        clean = self._peer_dest_hash(peer_hash)
        return clean[:8] if clean else ""

    def _notification_preview(self, entry):
        msg_type = entry.get("type", "text")
        if msg_type in ("text", "emoji"):
            return (entry.get("content") or "New message")[:120]
        return entry.get("file_name") or msg_type or "New message"

    def _should_android_notify(self, peer_hash, entry):
        if not is_android() or entry.get("type") == "system":
            return False
        vp = self._ui_state.get("viewing_peer")
        hidden = self._ui_state.get("hidden", True)
        if vp and not hidden:
            if is_hub_peer_hash(peer_hash) and is_hub_peer_hash(vp):
                return False
            if not is_hub_peer_hash(peer_hash) and self._peers_equivalent(vp, peer_hash):
                return False
        return True

    def _on_queue_sent(self, chat_msg, target_hash, queue_entry):
        my_hash = self._my_sender_hash()
        chat_peer = self._peer_dest_hash(target_hash) if target_hash else (
            self._session_chat_peer() or self._peer_dest_hash(self.active_peer)
        )
        msg_id = chat_msg.msg_id or queue_entry.get("msg_id")
        file_name = chat_msg.file_name or queue_entry.get("file_name")
        file_size = chat_msg.file_size or queue_entry.get("file_size")
        updated = False
        for item in self.message_history:
            if item.get("msg_id") == msg_id:
                item["status"] = "sent"
                item["timestamp"] = chat_msg.timestamp
                if file_name:
                    item["file_name"] = file_name
                if file_size:
                    item["file_size"] = file_size
                updated = True
                break
        if not updated:
            entry = self._enrich_message({
                "type": chat_msg.msg_type,
                "content": chat_msg.content,
                "sender": my_hash,
                "peer": chat_peer,
                "chat_peer": chat_peer,
                "timestamp": chat_msg.timestamp,
                "msg_id": msg_id,
                "file_name": file_name,
                "file_size": file_size,
                "status": "sent",
            }, outgoing=True)
            self.message_history.append(entry)
        else:
            entry = next(i for i in self.message_history if i.get("msg_id") == msg_id)
        self._save_history()
        if self.websockets and self._loop:
            asyncio.run_coroutine_threadsafe(
                self._broadcast({"type": "message", "data": entry}),
                self._loop
            )
            asyncio.run_coroutine_threadsafe(
                self._broadcast({
                    "type": "queue_cleared",
                    "data": {"count": self.messaging.queue_size() if self.messaging else 0},
                }),
                self._loop,
            )

    def _current_peer_for_ip(self, ip):
        if not ip or not self.discovery:
            return None
        best = None
        for peer in self.discovery.get_peers():
            if peer.get("ip") != ip:
                continue
            if not best or peer.get("last_seen", 0) >= best.get("last_seen", 0):
                best = peer
        return best

    def _peer_is_current(self, peer_hash):
        clean = self._peer_dest_hash(peer_hash)
        if not clean:
            return False
        if contact_has_hash(self.config_dir, clean):
            return True
        scope_ip = self._discovery_scope_ip()
        if (
            scope_ip
            and not is_hub_peer_hash(clean)
            and not self._peer_in_discovery_scope(clean)
        ):
            return False
        if self.messaging:
            if self.messaging._peer_link_active(clean):
                return True
            if self.messaging.active_peer_hash and self._peers_equivalent(
                clean, self.messaging.active_peer_hash
            ):
                return True
            for linked in self.messaging.linked_peers():
                if self._peers_equivalent(clean, linked):
                    return True
        if self.discovery:
            return self.discovery.peer_is_current(clean, scope_ip=scope_ip)
        return False

    def _peer_matches_transport(self, peer, prefer_via):
        if not prefer_via:
            return True
        requested = (prefer_via or "").strip().lower()
        pvia = (peer.get("via") or "").strip().lower()
        if requested == "serial":
            return pvia == "serial"
        return pvia in ("rns", "beacon", "lan", "")

    def _contact_hash_for_transport(self, peer_hash, prefer_via=None):

        contact = find_contact_by_hash(self.config_dir, self._peer_dest_hash(peer_hash))
        if not contact:
            return None
        via = (prefer_via or "").strip().lower()
        if via == "serial":
            serial = (contact.get("serial_hash") or "").replace(":", "")
            return serial or None
        if via in ("lan", "rns", "beacon", "udp", "tcp"):
            lan = (contact.get("lan_hash") or contact.get("hash") or "").replace(":", "")
            return lan or None
        return None

    def _resolve_current_peer_hash(self, peer_hash, peer_ip=None, prefer_via=None):
        clean = self._peer_dest_hash(peer_hash)
        transport_hash = self._contact_hash_for_transport(clean, prefer_via)
        if transport_hash:
            clean = transport_hash
        if self._peer_is_current(clean):
            return clean
        if peer_ip:
            current = self._current_peer_for_ip(peer_ip)
            if current and self._peer_matches_transport(current, prefer_via):
                return self._peer_dest_hash(current.get("hash"))
        if self.discovery:
            for peer in self._scoped_peers():
                if not self._peer_matches_transport(peer, prefer_via):
                    continue
                if self._peers_equivalent(peer.get("hash"), clean):
                    return self._peer_dest_hash(peer.get("hash"))
                if peer.get("identity_hash") and self._peers_equivalent(
                    peer.get("identity_hash"), clean
                ):
                    return self._peer_dest_hash(peer.get("hash"))
        return clean

    def _on_transfer_revoked(self, transfer_id, file_name=None):
        if self._loop:
            asyncio.run_coroutine_threadsafe(
                self._remove_history_message(transfer_id),
                self._loop,
            )

    def _on_link_closed(self, peer_hash, handoff=False):
        if handoff or getattr(self.messaging, "_failover_in_progress", False):
            return
        peer = self._peer_dest_hash(peer_hash)
        still_linked = bool(self.messaging and peer and self.messaging._peer_link_active(peer))
        removed = 0
        if (
            peer
            and self.active_peer
            and self._peers_equivalent(peer, self.active_peer)
            and not still_linked
        ):
            self.active_peer = (
                self.messaging.active_peer_hash if self.messaging else None
            )
        if self.websockets and self._loop:
            if removed:
                asyncio.run_coroutine_threadsafe(
                    self._broadcast({
                        "type": "peer_history_cleared",
                        "data": {"peer": peer, "removed": removed},
                    }),
                    self._loop,
                )
            if peer and self.discovery and self.discovery.clear_peer_rtt(peer):
                self._schedule_peers_broadcast()
            asyncio.run_coroutine_threadsafe(
                self._broadcast({
                    "type": "link_closed",
                    "data": {
                        "peer": peer,
                        "linked_peers": (
                            self.messaging.linked_peers() if self.messaging else []
                        ),
                    },
                }),
                self._loop
            )

    def _on_link_established(self, peer_hash, link, background=False, promote_active=True,
                             passive=False):
        if self.messaging and link:
            resolved = self.messaging.canonical_connect_hash(peer_hash, link=link)
        else:
            resolved = self._peer_dest_hash(peer_hash)
        if (not resolved or self._is_self_hash(resolved)) and self.discovery:
            fixed = self._resolve_incoming_peer(link=link)
            if fixed and not self._is_self_hash(fixed):
                resolved = fixed
        elif not resolved:
            resolved = self._peer_dest_hash(peer_hash)
        hub_tcp_link = bool(
            self.messaging and link and self.messaging._link_is_hub_tcp(link)
        )
        if (
            resolved
            and not is_hub_peer_hash(resolved)
            and not hub_tcp_link
            and not self._peer_in_discovery_scope(resolved, link=link)
        ):
            if self.messaging and link:
                try:
                    link.teardown()
                except Exception:
                    pass
            print(
                f"[connect] Rejected link from {resolved[:16]}... (outside LAN scope)"
            )
            return
        peer_ip = ""
        meta = self._discovery_peer_for_connect(None, resolved)
        if meta:
            peer_ip = (meta.get("ip") or "").strip()
        self._register_link_peer_in_discovery(
            resolved, peer_ip=peer_ip or None, link=link,
        )
        link_rtt = None
        link_quality = None
        if self.discovery and self.messaging:
            from chatx5.core.peer_probe import link_rtt_ms
            link_via = None
            if link:
                try:
                    link_via = self.messaging._transport_from_link(link)
                except Exception:
                    link_via = None
            from chatx5.core.peer_probe import serial_link_quality_percent

            link_rtt = link_rtt_ms(self.messaging, resolved, transport=link_via)
            if link_via == "serial" and link_rtt is not None:
                link_quality = serial_link_quality_percent(link_rtt)
            if link_rtt is not None:
                self.discovery.update_peer_probe(
                    resolved, rtt_ms=link_rtt, ok=True, via=link_via,
                )
                self._schedule_peers_broadcast()
            elif self.discovery.clear_peer_rtt(resolved, via=link_via):
                self._schedule_peers_broadcast()
        self._maybe_update_hub_server_hash(resolved, link=link)
        if hub_tcp_link and self.messaging:
            self.messaging._schedule_hub_queue_drain(delay=0.5)
            self.messaging._schedule_hub_link_ensure(delay=1.0)
        user_disconnected = bool(
            self.messaging and self.messaging.is_user_disconnected(resolved)
        )
        if passive or user_disconnected:
            promote_active = False
            background = True
        if promote_active and not passive:
            self.active_peer = resolved
        self._prune_stale_session_system_messages()
        path_switch = bool(getattr(self.messaging, "_last_handoff", False))
        label = "passive" if passive else ("background" if background else "active")
        print(f"[connect] Link with {resolved[:16]}... ({label})")
        link_via = None
        if self.messaging and link:
            try:
                link_via = self.messaging._transport_from_link(link)
            except Exception:
                link_via = None
        if self.websockets and self._loop:
            asyncio.run_coroutine_threadsafe(
                self._broadcast({
                    "type": "link_established",
                    "data": {
                        "hash": resolved,
                        "aliases": self._peer_alias_list(resolved),
                        "via": link_via,
                        "path_switch": path_switch,
                        "background": background,
                        "promote_active": promote_active,
                        "passive": passive,
                        "user_disconnected": user_disconnected,
                        "rtt_ms": link_rtt,
                        "link_quality_pct": link_quality,
                        "linked_peers": (
                            self.messaging.linked_peers() if self.messaging else []
                        ),
                    },
                }),
                self._loop
            )

    def _on_transfer_progress(self, data):
        status = data.get("status", "active")
        transfer_id = data.get("transfer_id")
        if (
            status == "active"
            and transfer_id
            and self.messaging
            and transfer_id in getattr(self.messaging, "_cancelled_transfers", set())
        ):
            return
        if status in ("complete", "cancelled", "failed"):
            self._progress_last.pop(data.get("transfer_id") or data.get("file_name"), None)
        else:
            key = data.get("transfer_id") or data.get("file_name") or "default"
            now = time.time()
            last = self._progress_last.get(key, {})
            pct = data.get("progress", 0)
            if last and (now - last.get("ts", 0)) < (self._progress_throttle_ms / 1000.0):
                if abs(pct - last.get("pct", -1)) < 1:
                    return
            self._progress_last[key] = {"ts": now, "pct": pct}
        if self.websockets and self._loop:
            asyncio.run_coroutine_threadsafe(
                self._broadcast({"type": "progress", "data": data}),
                self._loop
            )

    def _make_progress_callback(self, fname, total_size, transfer_id=None):
        start = time.time()
        def callback(resource):
            try:
                progress = resource.get_progress()
                pct = int(progress * 100)
                elapsed = time.time() - start
                bytes_xfer = progress * total_size
                speed = bytes_xfer / elapsed if elapsed > 0 else 0
                speed_str = format_speed(speed)
                self._on_transfer_progress({
                    "file_name": fname,
                    "progress": pct,
                    "size": total_size,
                    "speed": speed_str,
                    "direction": "send",
                    "status": "active",
                    "transfer_id": transfer_id,
                })
            except Exception:
                pass
        return callback

