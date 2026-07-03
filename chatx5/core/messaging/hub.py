"""Hub group-chat TCP relay: settings, links, send/relay, queue drain."""

import json
import os
import threading
import time

import RNS

from chatx5.core.discovery import normalize_hash
from chatx5.core.lan_rns import interface_family, request_paths_for_hash
from chatx5.core.messaging.constants import (
    HUB_GROUP_PEER,
    MESSAGE_TYPE_FILE,
    MESSAGE_TYPE_IMAGE,
    MESSAGE_TYPE_SHARE_BROWSE,
    MESSAGE_TYPE_TEXT,
    MESSAGE_TYPE_VIDEO,
    MESSAGE_TYPE_VOICE,
    QUEUE_DRAIN_DELAY_S,
)
from chatx5.core.messaging.models import ChatMessage
from chatx5.core.messaging.peers import is_hub_peer_hash


class HubMixin:
    """Hub server/client settings, link establishment, and group messaging."""

    def _load_hub_settings(self):
        try:
            from chatx5.utils.helpers import get_config_dir

            path = os.path.join(self.config_dir or get_config_dir(), "settings.json")
            with open(path, encoding="utf-8") as fh:
                settings = json.load(fh)
            return (
                settings.get("hub_role") or "off",
                (settings.get("hub_server_hash") or "").strip(),
            )
        except Exception:
            return "off", ""

    def _hub_endpoint_from_settings(self):
        try:
            from chatx5.utils.helpers import get_config_dir

            path = os.path.join(self.config_dir or get_config_dir(), "settings.json")
            with open(path, encoding="utf-8") as fh:
                settings = json.load(fh)
            return (
                (settings.get("hub_host") or "").strip(),
                int(settings.get("hub_port") or 4242),
            )
        except Exception:
            return "", 4242

    def _link_is_hub_transport(self, iface, role=None, hub_host=None, hub_port=None):
        if iface is None or interface_family(iface) != "tcp":
            return False
        if role is None:
            role, _ = self._load_hub_settings()
        if role == "off":
            return False
        if hub_host is None or hub_port is None:
            hub_host, hub_port = self._hub_endpoint_from_settings()
        if role == "client":
            target = (getattr(iface, "target_host", None) or "").strip()
            port = int(
                getattr(iface, "target_port", None)
                or getattr(iface, "port", None)
                or 4242
            )
            return bool(hub_host) and target == hub_host and port == hub_port
        if role == "server":
            if type(iface).__name__ != "TCPServerInterface":
                return False
            port = int(
                getattr(iface, "listen_port", None)
                or getattr(iface, "port", None)
                or 4242
            )
            return port == int(hub_port or 4242)
        return False

    def _link_registered_as_hub_tcp(self, link):
        """True when link was registered under a peer:tcp key (hub relay session)."""
        if not link:
            return False
        for key, mapped in self.peer_links.items():
            if mapped is not link:
                continue
            text = str(key)
            if ":" in text and text.rsplit(":", 1)[-1] == "tcp":
                return True
        return False

    def _link_is_hub_tcp(self, link):
        """True when a link uses the hub TCP transport (client dial or server listener)."""
        if not link or not self._hub_transport_active():
            return False
        # Inbound hub relay links may report a UDP/serial attached interface;
        # trust explicit :tcp registration from _register_peer_link.
        if self._link_registered_as_hub_tcp(link):
            return True
        role, _ = self._load_hub_settings()
        hub_host, hub_port = self._hub_endpoint_from_settings()
        iface = self._link_attached_interface(link)
        if iface:
            if self._link_is_hub_transport(
                iface, role=role, hub_host=hub_host, hub_port=hub_port,
            ):
                return True
            fam = interface_family(iface)
            if fam in ("udp", "serial"):
                return False
            if fam == "tcp":
                if role == "server":
                    if type(iface).__name__ == "TCPClientInterface":
                        target = (getattr(iface, "target_host", None) or "").strip()
                        return not target
                    return True
                if role == "client":
                    if type(iface).__name__ == "TCPServerInterface":
                        return False
                    target = (getattr(iface, "target_host", None) or "").strip()
                    if target:
                        return self._link_is_hub_transport(
                            iface, role=role, hub_host=hub_host, hub_port=hub_port,
                        )
                    return True
        return self._inbound_link_is_hub_tcp(link)

    def _peer_uses_hub_transport(self, peer_hash):
        if is_hub_peer_hash(peer_hash):
            return True
        role, hub_server_hash = self._load_hub_settings()
        if role == "off":
            return False
        peer = normalize_hash(self.dest_hash_for(peer_hash) or peer_hash or "")
        if len(peer) != 32:
            return False
        hub_hex = normalize_hash(hub_server_hash or "")
        if hub_hex and self.hashes_equivalent(peer, hub_hex):
            return True
        return False

    def _hub_transport_active(self):
        role, _ = self._load_hub_settings()
        return role != "off"

    def _hub_link_for_peer(self, peer_hash):
        """Active RNS link over hub TCP for a peer (not LAN/UDP P2P)."""
        peer = self.dest_hash_for(peer_hash)
        if not peer or peer == "unknown":
            return None
        for _key, link in list(self.peer_links.items()):
            if not link or not self._link_matches_peer(link, peer):
                continue
            if not self._link_is_hub_tcp(link):
                continue
            try:
                if getattr(link, "status", None) == RNS.Link.CLOSED:
                    continue
            except Exception:
                pass
            return link
        for link in list(self.links.values()):
            if not link or not self._link_matches_peer(link, peer):
                continue
            if not self._link_is_hub_tcp(link):
                continue
            try:
                if getattr(link, "status", None) == RNS.Link.CLOSED:
                    continue
            except Exception:
                pass
            self._register_peer_link(link, peer, transport="tcp")
            return link
        return None

    def _hub_tcp_linked_peers(self):
        """Peers on hub TCP transport only (not TCP LAN P2P dials)."""
        role, _ = self._load_hub_settings()
        if role == "off":
            return []
        out = []
        seen = set()
        for key, link in list(self.peer_links.items()):
            peer = self._peer_from_link_key(key)
            if not peer or is_hub_peer_hash(peer) or peer in seen:
                continue
            if not link or not self._link_is_hub_tcp(link):
                continue
            seen.add(peer)
            out.append(peer)
        for link in list(self.links.values()):
            peer = self._peer_hash_from_link_identity(link)
            if not peer or peer == "unknown" or is_hub_peer_hash(peer) or peer in seen:
                continue
            if not self._link_is_hub_tcp(link):
                continue
            seen.add(peer)
            out.append(peer)
        return out

    def _hub_message_receivable(self, chat_msg, link=None):
        """Inbound hub group messages may arrive on any transport; UI still shows them."""
        if not getattr(chat_msg, "hub_group", False):
            return True
        role, _ = self._load_hub_settings()
        return role != "off"

    def _hub_message_acceptable(self, chat_msg, link):
        if not getattr(chat_msg, "hub_group", False):
            return True
        role, _ = self._load_hub_settings()
        if role == "off":
            return False
        return self._link_is_hub_tcp(link)

    def _hub_send_targets(self, hub_server_hash=None, hub_server_mode=False):
        tcp_peers = self._hub_tcp_linked_peers()
        if hub_server_mode:
            return tcp_peers
        if hub_server_hash:
            peer = self.dest_hash_for(hub_server_hash)
            if peer and peer != "unknown":
                # Match by hash equivalence, not exact string: the linked peer
                # may be keyed under its message-dest hash while hub_server_hash
                # is the server's identity/announce hash (the same peer). Exact
                # `in` left the client's queued group messages stuck with "no
                # active link".
                match = next(
                    (p for p in tcp_peers if self.hashes_equivalent(p, peer)),
                    None,
                )
                if match:
                    return [match]
            if tcp_peers:
                return [tcp_peers[0]]
            return []
        return tcp_peers[:1]

    def _persist_hub_server_hash(self, hub_hash):
        hub_hash = normalize_hash(hub_hash or "")
        if len(hub_hash) != 32 or not self.config_dir:
            return
        try:
            path = os.path.join(self.config_dir, "settings.json")
            with open(path, encoding="utf-8") as fh:
                settings = json.load(fh)
            if settings.get("hub_server_hash") == hub_hash:
                return
            settings["hub_server_hash"] = hub_hash
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(settings, fh, indent=2)
            print(f"[hub] Saved hub server hash {hub_hash[:16]}...")
        except Exception as exc:
            print(f"[hub] Could not save hub server hash: {exc}")

    def _register_hub_server_identity(self, data, hub_host=""):
        """Register hub server RNS identity from network-status or beacon fields."""
        if not data:
            return ""
        from chatx5.core.discovery import register_identity_from_peer
        from chatx5.core.peer_identity import register_beacon_identity

        identity_hex = normalize_hash(
            data.get("identity_hash") or data.get("identity") or ""
        )
        pubkey = (
            (data.get("identity_pubkey") or data.get("pubkey") or "").strip()
        )
        hub_hash = normalize_hash(
            data.get("hub_server_hash")
            or data.get("destination_hash")
            or data.get("hash")
            or ""
        )
        if not identity_hex or not pubkey:
            return hub_hash if len(hub_hash) == 32 else ""
        peer_record = {
            "hash": hub_hash or identity_hex,
            "identity_hash": identity_hex,
            "pubkey": pubkey,
        }
        if hub_host:
            peer_record["ip"] = hub_host.strip()
        registered = (
            register_beacon_identity(peer_record)
            or register_identity_from_peer(peer_record)
            or ""
        )
        if registered:
            dest = normalize_hash(registered)
            ident_hex = identity_hex
            if dest and ident_hex:
                self.register_peer_mapping(dest, ident_hex)
            now = time.time()
            last = float(getattr(self, "_last_hub_identity_log", 0) or 0)
            if now - last >= 30.0:
                self._last_hub_identity_log = now
                print(
                    f"[hub] Registered hub server identity from {hub_host or 'peer'}: "
                    f"{dest[:16] if dest else identity_hex[:16]}..."
                )
            return dest or hub_hash
        return hub_hash if len(hub_hash) == 32 else ""

    def _fetch_hub_server_hash_from_peer(self, hub_host, http_port=None):
        hub_host = (hub_host or "").strip()
        if not hub_host:
            return ""
        port = int(http_port or getattr(self, "http_port", None) or 8742)
        scheme = getattr(self, "http_scheme", "http") or "http"
        try:
            from chatx5.core.http_peer import peer_get_with_fallback

            raw, _used = peer_get_with_fallback(
                hub_host, port, "/api/network-status",
                primary_scheme=scheme, timeout=4.0,
            )
            data = json.loads(raw.decode("utf-8"))
            if (data.get("hub_role") or "").strip().lower() != "server":
                print(
                    f"[hub] {hub_host}:{port} is not a hub server "
                    f"(hub_role={data.get('hub_role')!r})"
                )
                return ""
            registered = self._register_hub_server_identity(data, hub_host=hub_host)
            hub_hash = normalize_hash(
                registered
                or data.get("hub_server_hash")
                or data.get("destination_hash")
                or ""
            )
            if len(hub_hash) == 32:
                return hub_hash
        except Exception as exc:
            print(f"[hub] Fetch hub server hash from {hub_host}:{port} failed: {exc}")
        return ""

    def _learn_hub_server_from_link(self, link):
        """Persist hub server hash and identity from an established hub TCP link."""
        if not link:
            return ""
        role, configured_hash = self._load_hub_settings()
        if role != "client":
            return ""
        peer = self._peer_hash_from_link_identity(link)
        if not peer or peer == "unknown" or self._is_self_hash(peer):
            return ""
        hub_host, _ = self._hub_endpoint_from_settings()
        configured = normalize_hash(configured_hash or "")
        if configured and not self.hashes_equivalent(peer, self.dest_hash_for(configured)):
            return peer
        try:
            ident = link.get_remote_identity()
            if ident and getattr(ident, "hash", None):
                ident_hex = normalize_hash(RNS.hexrep(ident.hash))
                dest = self._dest_hash_from_identity(ident)
                if dest and ident_hex:
                    self.register_peer_mapping(dest, ident_hex)
                if dest:
                    peer = dest
        except Exception:
            pass
        self._persist_hub_server_hash(peer)
        return peer

    def _finalize_hub_tcp_inbound(self, link, initial_peer="unknown"):
        """Register hub TCP links once the remote identity is available."""
        peer = initial_peer
        if not peer or peer == "unknown":
            peer = self._peer_hash_from_link_identity(link)
        if not peer or peer == "unknown":
            for _ in range(8):
                time.sleep(0.25)
                peer = self._peer_hash_from_link_identity(link)
                if peer and peer != "unknown":
                    break
        if not peer or peer == "unknown":
            resolved = self._resolve_remote_peer(link, fallback=peer)
            if resolved and resolved != "unknown":
                peer = self.dest_hash_for(resolved)
        if not peer or peer == "unknown":
            return ""
        self._cache_link_peer(link, peer)
        self._register_peer_link(link, peer, transport="tcp")
        self._consolidate_peer_links(peer, keep_link=link, transport="tcp")
        role, _ = self._load_hub_settings()
        if role == "client":
            self._learn_hub_server_from_link(link)
        if role == "server":
            n = len(self._hub_tcp_linked_peers())
            print(f"[hub] Hub server: {n} TCP client(s) linked")
        self._notify_link_established(
            link, peer, promote_active=False, background=True,
        )
        self._schedule_hub_queue_drain()
        return peer

    def _inbound_link_is_hub_tcp(self, link):
        """True when an inbound link arrived on the hub TCP relay (iface may be unset)."""
        if not link or not self._hub_transport_active():
            return False
        role, _ = self._load_hub_settings()
        if role == "server":
            return True
        if role == "client":
            return self._hub_tcp_transport_online()
        return False

    def _hub_tcp_transport_online(self):
        from chatx5.core.rns_interfaces import (
            hub_tcp_client_active,
            tcp_client_interface_online,
            tcp_server_interface_online,
        )

        role, _ = self._load_hub_settings()
        if role == "off":
            return False
        try:
            path = os.path.join(self.config_dir, "settings.json")
            with open(path, encoding="utf-8") as fh:
                settings = json.load(fh)
        except Exception:
            settings = {}
        hub_port = int(settings.get("hub_port") or 4242)
        if role == "server":
            return tcp_server_interface_online(hub_port) is not None
        if role == "client":
            if not hub_tcp_client_active(settings):
                return False
            return tcp_client_interface_online() is not None
        return False

    def ensure_hub_link(self, background=True):
        """Ensure an RNS link exists over the hub TCP transport (client or server)."""
        if self._interrupted():
            return False
        role, hub_hash = self._load_hub_settings()
        if role == "off":
            return False
        if not self._hub_tcp_transport_online():
            if role == "client":
                hub_host, _ = self._hub_endpoint_from_settings()
                if hub_host:
                    print(f"[hub] TCP transport to {hub_host}:4242 not online yet")
            return False
        if role == "server":
            peers = self._hub_tcp_linked_peers()
            if peers:
                last_n = int(getattr(self, "_last_hub_client_count", -1))
                if len(peers) != last_n:
                    self._last_hub_client_count = len(peers)
                    print(f"[hub] Hub server: {len(peers)} TCP client(s) linked")
                return True
            now = time.time()
            last = float(getattr(self, "_last_hub_wait_log", 0) or 0)
            if now - last >= 15.0:
                self._last_hub_wait_log = now
                print("[hub] Hub server waiting for client TCP link(s)...")
            return False
        hub_host, _ = self._hub_endpoint_from_settings()
        if hub_host:
            fetched = self._fetch_hub_server_hash_from_peer(
                hub_host, getattr(self, "http_port", 8742),
            )
            if fetched:
                hub_hash = fetched
                self._persist_hub_server_hash(hub_hash)
        if not hub_hash:
            print("[hub] Hub server identity unknown — ensure hub server is running with --share")
            return False
        peer = self.dest_hash_for(hub_hash)
        if not peer or peer == "unknown":
            print(f"[hub] Hub server hash {hub_hash[:16]}... not mapped to a destination")
            return False
        existing = self._hub_link_for_peer(peer)
        if existing:
            return True
        now = time.time()
        last = float(getattr(self, "_last_hub_open_attempt", 0) or 0)
        hub_queue_pending = any(
            is_hub_peer_hash(e.get("target_hash")) for e in self.message_queue
        )
        throttle_s = 2.0 if hub_queue_pending else 4.0
        if now - last < throttle_s:
            existing = self._hub_link_for_peer(peer)
            if existing or hub_queue_pending:
                self._schedule_hub_queue_drain(delay=0.1)
            return bool(existing)
        self._last_hub_open_attempt = now
        if self._connect_in_progress:
            return False
        if not self._identity_for_hash(peer) and hub_host:
            print(
                f"[hub] Hub server identity not cached yet — "
                f"retrying fetch from {hub_host}"
            )
            refetched = self._fetch_hub_server_hash_from_peer(
                hub_host, getattr(self, "http_port", 8742),
            )
            if refetched:
                peer = self.dest_hash_for(refetched) or peer
        from chatx5.core.lan_rns import clear_peer_path_unless_family, prune_lan_path_for_peer

        prune_lan_path_for_peer(peer)
        clear_peer_path_unless_family(peer, "tcp")
        request_paths_for_hash(peer, family="tcp")
        print(f"[hub] Opening hub link to {peer[:16]}... (TCP)")
        return self.connect_to(
            peer,
            peer_ip=hub_host or None,
            user_initiated=not background,
            respond_to_wake=background,
            prefer_transport="tcp",
        )

    def send_hub_message(self, text, receipt_callback=None, msg_id=None,
                         hub_server_hash=None, hub_server_mode=False,
                         msg_type=None):
        role, _ = self._load_hub_settings()
        if role == "off":
            return False
        if not self._hub_tcp_linked_peers():
            self.ensure_hub_link(background=(role == "server"))
        wire_type = msg_type or MESSAGE_TYPE_TEXT
        sender_hex = normalize_hash(self.my_dest_hash or "")
        msg = ChatMessage(
            wire_type, text, msg_id=msg_id,
            sender=sender_hex if len(sender_hex) == 32 else None,
        )
        msg.hub_group = True
        data = msg.to_json().encode("utf-8")
        targets = self._hub_send_targets(
            hub_server_hash=hub_server_hash,
            hub_server_mode=hub_server_mode,
        )
        sent = False
        for peer in targets:
            if not peer or is_hub_peer_hash(peer):
                continue
            link = self._hub_link_for_peer(peer)
            if not link:
                continue
            try:
                mtu = getattr(link, "mtu", 500)
                if len(data) > mtu - 50:
                    if not self._send_long_text(msg, text, data, receipt_callback, link):
                        print(f"[hub] send failed to {peer[:16]}: long text transfer failed")
                        continue
                else:
                    packet = RNS.Packet(link, data)
                    packet.send()
                sent = True
            except Exception as e:
                print(f"[hub] send failed to {peer[:16]}: {e}")
        if not sent:
            print("[hub] send_hub_message: no active link")
            self._schedule_hub_queue_drain(delay=0.15)
            self.ensure_hub_link(background=(role == "server"))
            return False
        preview = text[:50] if wire_type == MESSAGE_TYPE_TEXT else wire_type
        print(f"[hub] Sent group message: {preview}...")
        self._sent_messages[msg.msg_id] = msg
        self._pending_sends[msg.msg_id] = time.time()
        if receipt_callback:
            self._receipt_callbacks[msg.msg_id] = receipt_callback
            self._fire_sent_receipt(msg.msg_id, receipt_callback)
        return msg

    _HUB_FILE_TYPES = (
        MESSAGE_TYPE_FILE,
        MESSAGE_TYPE_IMAGE,
        MESSAGE_TYPE_VIDEO,
        MESSAGE_TYPE_VOICE,
    )

    def send_hub_file(self, file_path, msg_type=MESSAGE_TYPE_FILE, progress_callback=None,
                      transfer_id=None, hub_server_hash=None, hub_server_mode=False):
        """Send a file over hub TCP link(s) for group chat."""
        role, _ = self._load_hub_settings()
        if role == "off":
            return False
        if not self._hub_tcp_transport_online():
            self.ensure_hub_link(background=(role == "server"))
        if not self._hub_tcp_linked_peers():
            self.ensure_hub_link(background=True)
        targets = self._hub_send_targets(
            hub_server_hash=hub_server_hash,
            hub_server_mode=hub_server_mode,
        )
        if hub_server_mode and not targets:
            targets = self._hub_tcp_linked_peers()
        last_result = False
        sent_any = False
        for peer in targets:
            if not peer or is_hub_peer_hash(peer):
                continue
            link = self._hub_link_for_peer(peer)
            if not link:
                continue
            last_result = self.send_file(
                file_path,
                msg_type,
                progress_callback=progress_callback,
                transfer_id=transfer_id,
                target_peer=peer,
                link=link,
                hub_group=True,
            )
            if last_result:
                sent_any = True
        if not sent_any:
            print("[hub] send_hub_file: no active hub link")
            self._schedule_hub_queue_drain(delay=0.15)
            self.ensure_hub_link(background=(role == "server"))
            return False
        return last_result

    def relay_hub_file(self, chat_msg, sender_hash, file_path):
        """Hub server re-sends a received group file to other linked clients."""
        if not file_path or not os.path.isfile(file_path):
            return 0
        msg_type = chat_msg.msg_type
        if msg_type not in self._HUB_FILE_TYPES:
            return 0
        role, _ = self._load_hub_settings()
        if role != "server":
            return 0
        wire_sender = normalize_hash(getattr(chat_msg, "sender", None) or "")
        if len(wire_sender) != 32 and sender_hash:
            wire_sender = normalize_hash(sender_hash)
        if len(wire_sender) == 32:
            chat_msg.sender = wire_sender
        relayed = 0
        for peer in self._hub_tcp_linked_peers():
            if is_hub_peer_hash(peer) or self.hashes_equivalent(peer, sender_hash):
                continue
            link = self._hub_link_for_peer(peer)
            if not link:
                continue
            try:
                result = self.send_file(
                    file_path,
                    msg_type,
                    transfer_id=chat_msg.msg_id,
                    target_peer=peer,
                    link=link,
                    hub_group=True,
                    hub_sender=wire_sender if len(wire_sender) == 32 else None,
                )
                if result:
                    relayed += 1
            except Exception as exc:
                print(f"[hub] relay file failed to {peer[:16]}: {exc}")
        if relayed:
            print(f"[hub] Relayed group file {chat_msg.file_name} to {relayed} peer(s)")
        return relayed

    def relay_hub_message(self, chat_msg, sender_hash):
        if not getattr(chat_msg, "hub_group", False):
            return
        wire_sender = normalize_hash(getattr(chat_msg, "sender", None) or "")
        if len(wire_sender) != 32 and sender_hash:
            chat_msg.sender = normalize_hash(sender_hash)
        data = chat_msg.to_json().encode("utf-8")
        for peer in self._hub_tcp_linked_peers():
            if is_hub_peer_hash(peer) or self.hashes_equivalent(peer, sender_hash):
                continue
            link = self._hub_link_for_peer(peer)
            if not link:
                continue
            try:
                RNS.Packet(link, data).send()
            except Exception as e:
                print(f"[hub] relay failed to {peer[:16]}: {e}")

    def drain_hub_group_queue(self, hub_server_hash=None, hub_server_mode=False):
        if not any(is_hub_peer_hash(e.get("target_hash")) for e in self.message_queue):
            return 0
        if not self._hub_tcp_transport_online():
            return 0
        if hub_server_mode:
            if not self._hub_tcp_linked_peers():
                self.ensure_hub_link(background=True)
        else:
            self.ensure_hub_link(background=True)
        targets = self._hub_send_targets(hub_server_hash, hub_server_mode)
        if not targets or not any(self._hub_link_for_peer(t) for t in targets):
            return 0
        remaining = []
        sent = 0
        for entry in self.message_queue:
            if not is_hub_peer_hash(entry.get("target_hash")):
                remaining.append(entry)
                continue
            entry_type = entry.get("type")
            file_types = (
                "file", "image", "video", "voice",
                MESSAGE_TYPE_FILE, MESSAGE_TYPE_IMAGE, MESSAGE_TYPE_VIDEO, MESSAGE_TYPE_VOICE,
            )
            if entry_type not in ("text", "emoji", MESSAGE_TYPE_SHARE_BROWSE) and entry_type not in file_types:
                remaining.append(entry)
                continue
            msg_id = entry.get("msg_id")
            if entry_type in file_types:
                fp = entry.get("file_path") or entry.get("content")
                if not fp or not os.path.isfile(fp):
                    print(f"[queue] Hub file no longer exists: {fp}")
                    remaining.append(entry)
                    continue
                result = self.send_hub_file(
                    fp,
                    entry_type if entry_type in file_types else MESSAGE_TYPE_FILE,
                    transfer_id=msg_id,
                    hub_server_hash=hub_server_hash,
                    hub_server_mode=hub_server_mode,
                )
            else:
                result = self.send_hub_message(
                    entry["content"],
                    msg_id=msg_id,
                    hub_server_hash=hub_server_hash,
                    hub_server_mode=hub_server_mode,
                    msg_type=entry_type if entry_type != "emoji" else MESSAGE_TYPE_TEXT,
                )
            if result:
                sent += 1
                if self.on_queue_sent:
                    try:
                        self.on_queue_sent(result, HUB_GROUP_PEER, entry)
                    except Exception as e:
                        print(f"[queue] on_queue_sent error: {e}")
            else:
                remaining.append(entry)
        if sent:
            print(f"[queue] Drained {sent} hub group item(s)")
        self.message_queue = remaining
        self._save_queue()
        return sent

    def _schedule_hub_link_ensure(self, delay=0.5):
        role, _ = self._load_hub_settings()
        if role == "off":
            return

        def run():
            try:
                if self.running:
                    self.ensure_hub_link(background=True)
                    self._schedule_hub_queue_drain()
            except Exception as exc:
                print(f"[hub] Link ensure error: {exc}")

        timer = threading.Timer(delay, run)
        timer.daemon = True
        timer.start()

    def _schedule_hub_queue_drain(self, delay=None):
        role, hub_hash = self._load_hub_settings()
        if role == "off":
            return
        wait = QUEUE_DRAIN_DELAY_S if delay is None else delay

        def run():
            try:
                if not self.running:
                    return
                role_now, hub_hash_now = self._load_hub_settings()
                if role_now == "off":
                    return
                self.drain_hub_group_queue(
                    hub_server_hash=hub_hash_now,
                    hub_server_mode=(role_now == "server"),
                )
            except Exception as e:
                print(f"[queue] Hub drain error: {e}")

        timer = threading.Timer(wait, run)
        timer.daemon = True
        timer.start()