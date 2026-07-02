import threading, RNS, json, time, os, tempfile, uuid
from contextlib import contextmanager


@contextmanager
def _null_context():
    yield
from urllib import request as urlrequest

from chatx5.utils.helpers import format_speed
from chatx5.core.discovery import (
    normalize_hash,
    message_dest_hash_for_identity,
    register_identity_from_peer,
)
from chatx5.core.lan_rns import (
    build_announce_packet,
    clear_paths_except_families,
    clear_paths_on_family,
    clear_peer_path,
    clear_peer_path_unless_family,
    detach_unhealthy_interfaces,
    ensure_serial_path_pinned,
    pin_serial_path,
    serial_path_is_pinned,
    unpin_serial_path,
    interface_family,
    interface_is_healthy,
    lan_ip_reachable,
    lan_mesh_has_peer,
    online_interfaces,
    peer_path_entry,
    peer_path_on_family,
    reinforce_serial_peer_path,
    restore_serial_path_from_announce,
    prune_bridged_lan_paths,
    prune_lan_path_for_peer,
    prune_stale_lan_paths,
    request_path_for_hash,
    request_paths_for_hash,
    scrub_peer_path,
    serial_interface_online,
    suppress_offline_lan_transports,
    udp_interface_online,
    register_udp_peer_ip,
    unicast_announce_packet,
    wait_for_peer_path,
    wait_for_peer_path_families,
)
from chatx5.utils.platform import is_android, lan_ip, physical_lan_reachable
from chatx5.core.lan_transfer import register_offer, remove_offer
from chatx5.core.serial_transfer import (
    boost_serial_establishment_timeout,
    is_serial_interface,
    tune_incoming_resource,
    tune_outgoing_resource,
    tune_serial_link,
)
from chatx5.core.rns_interfaces import (
    configured_serial_enabled,
    configured_tcp_lan_enabled,
    configured_udp_lan_enabled,
    ensure_runtime_serial,
    ensure_runtime_tcp_lan_server,
    ensure_tcp_client_to_peer,
    lan_discovery_configured,
    load_settings_interfaces,
    dedupe_serial_interfaces,
    prune_dead_serial_interfaces,
    tcp_client_interface_online,
    tcp_server_interface_online,
)
from chatx5.core.messaging.constants import (
    ANDROID_IDENTITY_WAIT_TIMEOUT_S,
    ANDROID_INITIATOR_INBOUND_WAIT_S,
    ANDROID_LINK_CONNECT_TIMEOUT_S,
    ANDROID_REVERSE_CONNECT_WAIT_S,
    APP_NAME,
    DUAL_PATH_DISCONNECTED_COOLDOWN_S,
    DUAL_PATH_FAILOVER_COOLDOWN_S,
    DUAL_PATH_RECONNECT_MIN_IDLE_S,
    FAILOVER_CONNECT_TIMEOUT_S,
    HUB_GROUP_PEER,
    HTTP_WAKE_TIMEOUT_S,
    IDENTITY_WAIT_TIMEOUT_S,
    INITIATOR_INBOUND_WAIT_S,
    LAN_HTTP_CHUNK,
    LAN_HTTP_MIN_BYTES,
    LINK_CONNECT_POLL_S,
    LINK_CONNECT_TIMEOUT_S,
    LINK_FAILOVER_GRACE_S,
    LINK_STALE_FAILOVER_IDLE_S,
    MAX_CONCURRENT_RECEIVES,
    MESSAGE_TYPE_EMOJI,
    MESSAGE_TYPE_FILE,
    MESSAGE_TYPE_IMAGE,
    MESSAGE_TYPE_LAN_HTTP,
    MESSAGE_TYPE_LONGTEXT,
    MESSAGE_TYPE_TEXT,
    MESSAGE_TYPE_TRANSFER_CANCEL,
    MESSAGE_TYPE_VIDEO,
    MESSAGE_TYPE_VOICE,
    PEER_LAN_UNREACHABLE_TTL_S,
    QUEUE_DRAIN_DELAY_S,
    QUEUE_RECEIPT_TIMEOUT_S,
    QUEUE_RETRY_INTERVAL_S,
    QUICK_OUTBOUND_TIMEOUT_S,
    RECEIPT_FAILOVER_MIN_PENDING,
    RECEIPT_FAILOVER_TIMEOUT_S,
    REVERSE_CONNECT_WAIT_S,
    SERIAL_ANNOUNCE_BURST_COUNT,
    SERIAL_ANNOUNCE_BURST_INTERVAL_S,
    SERIAL_CONNECT_PRIME_INTERVAL_S,
    SERIAL_IDENTITY_WAIT_TIMEOUT_S,
    SERIAL_INBOUND_FIRST_WAIT_S,
    SERIAL_INBOUND_WAIT_S,
    SERIAL_LINK_CONNECT_TIMEOUT_S,
    SERIAL_PATH_PRIME_TIMEOUT_S,
    SERIAL_SPEED_MARGIN,
    SESSION_RECONNECT_MIN_IDLE_S,
    _NO_COMPRESS_SUFFIXES,
)
from chatx5.core.messaging.models import ChatMessage
from chatx5.core.messaging.peers import is_hub_peer_hash
from chatx5.core.messaging.links import PeerLinkMixin
from chatx5.core.messaging.connect import ConnectMixin
from chatx5.core.messaging.hub import HubMixin
from chatx5.core.messaging.queue import QueueMixin
from chatx5.core.messaging.transfer import TransferMixin


class MessagingBackend(PeerLinkMixin, ConnectMixin, HubMixin, QueueMixin, TransferMixin):
    def __init__(self, identity, config_dir, on_message=None, on_file=None,
                 on_progress=None, on_link_established=None, on_link_closed=None,
                 display_name="", auto_announce=False,
                 receive_dir=None, peer_resolver=None, on_queue_sent=None,
                 on_transfer_revoked=None,
                 http_port=8742, lan_transfer_enabled=False,
                 peer_endpoint_resolver=None, peer_scope_checker=None,
                 peer_transport_resolver=None, identity_serial=None,
                 dual_identity_mode=True):
        self.identity = identity
        self.identity_serial = identity_serial
        self.dual_identity_mode = bool(dual_identity_mode)
        self.config_dir = config_dir
        self.receive_dir = receive_dir or os.path.join(config_dir, "received")
        self.on_message = on_message
        self.on_file = on_file
        self.on_progress = on_progress
        self.on_link_established = on_link_established
        self.on_link_closed = on_link_closed
        self.on_queue_sent = on_queue_sent
        self.on_transfer_revoked = on_transfer_revoked
        self.display_name = display_name
        self.auto_announce = auto_announce
        self.announce_interval = 30
        self.destination = None
        self.destination_serial = None
        self.my_dest_hash_serial = None
        self.lan_announce_interval_s = 0
        self.serial_announce_interval_s = 0
        self.links = {}
        self.active_link = None
        self.active_peer_hash = None
        self.running = False
        self.shutdown_requested = False
        self._announce_thread = None
        self._pending_files = {}
        self._pending_lock = threading.Lock()
        self.queue_file = os.path.join(config_dir, "queue.json")
        self.message_queue = self._load_queue()
        self._file_send_lock = threading.Lock()
        self._connect_lock = threading.Lock()
        self._sent_messages = {}
        self._receipt_callbacks = {}
        self._active_resources = {}
        self._cancel_events = {}
        self._file_handles = {}
        self._cancelled_transfers = set()
        self._current_transfer_id = None
        self._progress_last = {}
        self._progress_throttle_s = 0.25
        self._transfer_bytes_state = {}
        self.my_dest_hash = None
        self.identity_to_dest = {}
        self.dest_to_identity = {}
        self._send_link = None
        self.peer_resolver = peer_resolver
        self.http_port = int(http_port or 8742)
        self.lan_transfer_enabled = bool(lan_transfer_enabled)
        self.peer_endpoint_resolver = peer_endpoint_resolver
        self.peer_scope_checker = peer_scope_checker
        self.peer_transport_resolver = peer_transport_resolver
        self._link_peer_hashes = {}
        self._link_handoff = False
        self._last_handoff = False
        self._failover_last_attempt = 0
        self._failover_cooldown_s = 20
        self._failover_in_progress = False
        self._last_link_established_at = 0
        self._last_link_lost_at = 0
        self._session_peer_hash = None
        self._pending_sends = {}
        self._longtext_temp_paths = {}
        self._queue_retry_thread = None
        self._queue_drain_timers = {}
        self._queue_drain_lock = threading.Lock()
        self.peer_links = {}
        self._session_transport = None
        self._connect_user_initiated = False
        self._connect_background = False
        self._connect_in_progress = False
        self._peer_lan_unreachable = {}
        self._user_disconnected = set()
        self._transport_reconnect_pending = False
        self.max_peer_links = 0
        self._link_connect_order = {}

    def _is_self_hash(self, h):
        clean = normalize_hash(h)
        if not clean:
            return False
        if self.my_dest_hash and clean == normalize_hash(self.my_dest_hash):
            return True
        if self.my_dest_hash_serial and clean == normalize_hash(self.my_dest_hash_serial):
            return True
        try:
            if self.identity and clean == normalize_hash(RNS.hexrep(self.identity.hash)):
                return True
            if self.identity_serial and clean == normalize_hash(RNS.hexrep(self.identity_serial.hash)):
                return True
        except Exception:
            pass
        return False

    def _destination_for_interface(self, iface):
        if iface and is_serial_interface(iface):
            return self.destination_serial
        return self.destination

    def ensure_serial_runtime(self):
        """Create serial identity + inbound destination when USB comes online."""
        if self.destination_serial and self.identity_serial:
            return True
        try:
            from chatx5.core.identity import IdentityManager
            mgr = IdentityManager(self.config_dir)
            mgr.load_or_create(serial_enabled=True)
            if mgr.identity_serial:
                self.identity_serial = mgr.identity_serial
        except Exception as e:
            print(f"[identity] Serial runtime setup failed: {e}")
            return False
        if not self.identity_serial:
            return False
        if not self.destination_serial:
            self.destination_serial = self._setup_inbound_destination(
                self.identity_serial, "destination_serial",
            )
            self.my_dest_hash_serial = normalize_hash(
                RNS.hexrep(self.destination_serial.hash),
            )
            print(f"[identity] Serial endpoint {self.my_dest_hash_serial[:16]}...")
        return bool(self.destination_serial)

    def _local_connect_hash_for_interface(self, iface):
        if iface and is_serial_interface(iface) and self.my_dest_hash_serial:
            return self.my_dest_hash_serial
        return self.my_dest_hash

    def _cache_link_peer(self, link, peer_hash):
        if not link or not peer_hash or peer_hash == "unknown":
            return
        canon = self.canonical_connect_hash(peer_hash, link=link)
        if canon and not self._is_self_hash(canon):
            self._link_peer_hashes[link.link_id] = canon




    def _hub_tcp_linked_peers(self):
        """Peers on hub TCP transport only (not TCP LAN P2P dials)."""
        role, _ = self._load_hub_settings()
        if role == "off":
            return []
        hub_host, hub_port = self._hub_endpoint_from_settings()
        out = []
        seen = set()
        for key, link in list(self.peer_links.items()):
            peer = self._peer_from_link_key(key)
            if not peer or is_hub_peer_hash(peer) or peer in seen:
                continue
            if not link:
                continue
            if self._link_is_hub_transport(
                self._link_attached_interface(link),
                role=role,
                hub_host=hub_host,
                hub_port=hub_port,
            ):
                seen.add(peer)
                out.append(peer)
        return out

    def _hub_message_acceptable(self, chat_msg, link):
        if not getattr(chat_msg, "hub_group", False):
            return True
        role, _ = self._load_hub_settings()
        if role == "off":
            return False
        hub_host, hub_port = self._hub_endpoint_from_settings()
        return self._link_is_hub_transport(
            self._link_attached_interface(link),
            role=role,
            hub_host=hub_host,
            hub_port=hub_port,
        )

    def disconnect_peer(self, peer_hash, user_initiated=False, transport=None):
        peer = self.dest_hash_for(peer_hash)
        transport = self._normalize_transport(transport) if transport else None
        if user_initiated and peer and not transport:
            self.mark_user_disconnected(peer)
            self.clear_session_peer()
            self._transport_reconnect_pending = False
            self._last_link_lost_at = 0
        closed = 0
        for link in list(self.links.values()):
            resolved = self._peer_hash_from_link_identity(link)
            if not resolved:
                cached = self._link_peer_hashes.get(link.link_id)
                resolved = self.dest_hash_for(cached) if cached else ""
            if peer and resolved and not self.hashes_equivalent(resolved, peer):
                continue
            if peer and not resolved:
                continue
            if transport and not self._link_transport_matches(link, transport):
                continue
            try:
                link.teardown()
                closed += 1
            except Exception:
                pass
        if peer:
            self._unlink_peer(peer, transport=transport)
        if user_initiated and peer:
            active_matches = (
                self.active_link
                and (
                    not transport
                    or self._link_transport_matches(self.active_link, transport)
                )
            )
            if active_matches and self.active_peer_hash and self.hashes_equivalent(
                self.active_peer_hash, peer
            ):
                self.active_link = None
                self.active_peer_hash = None
                self._send_link = None
            if not transport:
                self.clear_session_peer()
                self._transport_reconnect_pending = False
                self._last_link_lost_at = 0
                self.mark_user_disconnected(peer)
            elif not self._other_active_links_for_peer(peer):
                self.clear_session_peer()
                self._transport_reconnect_pending = False
                self._last_link_lost_at = 0
                self.mark_user_disconnected(peer)
        return closed > 0

    def mark_user_disconnected(self, peer_hash):
        peer = self.dest_hash_for(peer_hash)
        if peer and peer != "unknown":
            self._user_disconnected.add(peer)

    def clear_user_disconnected(self, peer_hash):
        peer = self.dest_hash_for(peer_hash)
        if not peer:
            return
        self._user_disconnected = {
            h for h in self._user_disconnected
            if not self.hashes_equivalent(h, peer)
        }

    def is_user_disconnected(self, peer_hash):
        peer = self.dest_hash_for(peer_hash)
        if not peer:
            return False
        return any(
            self.hashes_equivalent(peer, blocked)
            for blocked in self._user_disconnected
        )

    def disconnect_all_peers(self, clear_session=True):
        """Tear down every open RNS link (network reset / full disconnect)."""
        self._link_handoff = True
        try:
            seen = set()
            for link in list(self.links.values()):
                lid = getattr(link, "link_id", None)
                if lid and lid in seen:
                    continue
                if lid:
                    seen.add(lid)
                try:
                    link.teardown()
                except Exception:
                    pass
            self.peer_links.clear()
            self._link_peer_hashes.clear()
            self.active_link = None
            self.active_peer_hash = None
            self._send_link = None
            if clear_session:
                self.clear_session_peer()
        finally:
            self._link_handoff = False

    def _peer_for_link(self, link, fallback=None):
        identity_peer = self._peer_hash_from_link_identity(link)
        if identity_peer and identity_peer != "unknown" and not self._is_self_hash(identity_peer):
            self._cache_link_peer(link, identity_peer)
            return identity_peer
        cached = self._link_peer_hashes.get(link.link_id) if link else None
        if cached and not self._is_self_hash(cached):
            canon = self.canonical_connect_hash(cached, link=link)
            if canon:
                return canon
        resolved = self._resolve_remote_peer(link, fallback=fallback)
        if resolved and resolved != "unknown" and not self._is_self_hash(resolved):
            resolved = self.canonical_connect_hash(resolved, link=link)
            if resolved:
                self._cache_link_peer(link, resolved)
                return resolved
        if fallback and not self._is_self_hash(fallback):
            mapped = self.canonical_connect_hash(fallback, link=link)
            if mapped:
                self._cache_link_peer(link, mapped)
                return mapped
        if cached and not self._is_self_hash(cached):
            canon = self.canonical_connect_hash(cached, link=link)
            if canon:
                return canon
        return "unknown"

    def register_peer_mapping(self, dest_hash, identity_hash=None):
        dest = normalize_hash(dest_hash)
        if not dest:
            return
        if identity_hash:
            ident = normalize_hash(identity_hash)
            if ident and ident != dest:
                self.identity_to_dest[ident] = dest
                self.dest_to_identity[dest] = ident

    def dest_hash_for(self, any_hash):
        clean = normalize_hash(any_hash)
        if not clean:
            return ""
        if clean in self.dest_to_identity:
            return clean
        mapped = self.identity_to_dest.get(clean)
        if mapped:
            return mapped
        return clean

    def canonical_connect_hash(self, any_hash, link=None):
        """Resolve identity or alias hashes to the message destination (connect) hash."""
        clean = normalize_hash(any_hash)
        if not clean or clean == "unknown" or self._is_self_hash(clean):
            if link:
                from_link = self._peer_hash_from_link_identity(link)
                if from_link and from_link != "unknown" and not self._is_self_hash(from_link):
                    return from_link
            return ""
        mapped = self.dest_hash_for(clean)
        if mapped in self.dest_to_identity:
            return mapped
        ident = self._identity_for_hash(clean)
        if ident:
            dest = self._dest_hash_from_identity(ident)
            if dest and not self._is_self_hash(dest):
                return dest
        if link:
            from_link = self._peer_hash_from_link_identity(link)
            if from_link and from_link != "unknown" and not self._is_self_hash(from_link):
                return from_link
        if mapped and len(mapped) == 32:
            return mapped
        return ""

    def hashes_equivalent(self, hash_a, hash_b):
        a = self.dest_hash_for(hash_a)
        b = self.dest_hash_for(hash_b)
        if a and b and a == b:
            return True
        if not a or not b:
            return False
        for key in (hash_a, hash_b):
            clean = normalize_hash(key)
            if clean in self.identity_to_dest:
                other = self.dest_hash_for(hash_b if key == hash_a else hash_a)
                if other and self.identity_to_dest.get(clean) == other:
                    return True
        return False

    def peer_aliases_for(self, any_hash):
        canonical = self.dest_hash_for(any_hash)
        aliases = {canonical} if canonical else set()
        ident = self.dest_to_identity.get(canonical)
        if ident:
            aliases.add(ident)
        for ident_hex, dest in self.identity_to_dest.items():
            if dest == canonical:
                aliases.add(ident_hex)
        return sorted(h for h in aliases if h and h != "unknown")


    def _interface_healthy(self, iface):
        return interface_is_healthy(iface)

    def _interface_path_score(self, iface):
        if not self._interface_healthy(iface):
            return 0
        fam = interface_family(iface)
        if fam == "tcp":
            return 95
        if fam == "lan":
            return 100
        if fam == "serial":
            return 60 if not self._lan_transport_ready() else 25
        if fam == "udp":
            return 80
        return 50


    def _peer_has_path(self, dest_hash):
        clean = normalize_hash(dest_hash)
        if len(clean) != 32:
            return False
        scrub_peer_path(clean)
        _, path_iface = peer_path_entry(clean)
        return bool(path_iface and self._interface_healthy(path_iface))

    def _peer_has_path_on_family(self, dest_hash, family):
        clean = normalize_hash(dest_hash)
        if len(clean) != 32:
            return False
        return peer_path_on_family(clean, family) is not None

    def _peer_path_interface(self, dest_hash):
        scrub_peer_path(dest_hash)
        _, path_iface = peer_path_entry(dest_hash)
        return path_iface

    def _interfaces_equivalent(self, iface_a, iface_b):
        if iface_a is None or iface_b is None:
            return False
        if iface_a is iface_b:
            return True
        return str(iface_a) == str(iface_b)

    def _load_hub_settings(self):
        try:
            import json
            import os
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
            import json
            import os
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
            return type(iface).__name__ == "TCPServerInterface"
        return False

    def _peer_uses_hub_transport(self, peer_hash):
        """Hub TCP is for group chat and the hub server — not local LAN P2P."""
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

    def _has_online_family(self, family):
        if family == "tcp":
            if (
                configured_tcp_lan_enabled(load_settings_interfaces(self.config_dir))
                and not self._hub_transport_active()
            ):
                return (
                    tcp_server_interface_online() is not None
                    or tcp_client_interface_online() is not None
                )
            return (
                tcp_client_interface_online() is not None
                or tcp_server_interface_online() is not None
            )
        if family == "serial":
            return serial_interface_online() is not None
        if family == "udp":
            if not lan_discovery_configured(load_settings_interfaces(self.config_dir)):
                return False
            if not bool(online_interfaces(family="udp")):
                return False
            if is_android():
                return True
            return physical_lan_reachable() or lan_mesh_has_peer()
        if family == "lan":
            return lan_mesh_has_peer()
        return bool(online_interfaces(family=family))

    def _dual_path_configured(self):
        interfaces = load_settings_interfaces(self.config_dir)
        return configured_serial_enabled(interfaces) and lan_discovery_configured(interfaces)

    def _session_reconnect_min_idle(self):
        peer = self.dest_hash_for(self._session_peer_hash or self.active_peer_hash or "")
        if peer and self._peer_expected_transport_families(peer) == {"serial"}:
            return SESSION_RECONNECT_MIN_IDLE_S
        if self._dual_path_configured():
            return DUAL_PATH_RECONNECT_MIN_IDLE_S
        return SESSION_RECONNECT_MIN_IDLE_S

    def _failover_cooldown(self):
        peer = self.dest_hash_for(self._session_peer_hash or self.active_peer_hash or "")
        serial_only = bool(peer and self._peer_expected_transport_families(peer) == {"serial"})
        disconnected = (
            not self.active_link
            and bool(self.dest_hash_for(self._session_peer_hash or ""))
        )
        if serial_only:
            return self._failover_cooldown_s
        if self._dual_path_configured():
            if disconnected:
                return DUAL_PATH_DISCONNECTED_COOLDOWN_S
            return DUAL_PATH_FAILOVER_COOLDOWN_S
        if disconnected:
            return DUAL_PATH_RECONNECT_MIN_IDLE_S
        return self._failover_cooldown_s

    def _link_rtt_seconds(self, link):
        if not link:
            return None
        rtt = getattr(link, "rtt", None)
        if rtt is None:
            return None
        try:
            return float(rtt)
        except Exception:
            return None

    def _serial_faster_than_lan(self, peer):
        """True when serial is confirmed up and measurably faster than LAN/UDP."""
        if not self._serial_transport_ready():
            return False
        if not physical_lan_reachable() or not self._has_online_family("udp"):
            return True
        if not self._peer_has_path_on_family(peer, "serial"):
            return False
        serial_rtt = None
        for link in self._links_for_peer(peer):
            if interface_family(self._link_attached_interface(link)) == "serial":
                serial_rtt = self._link_rtt_seconds(link)
                if serial_rtt is not None:
                    break
        if serial_rtt is None and self.active_link:
            if interface_family(self._link_attached_interface(self.active_link)) == "serial":
                serial_rtt = self._link_rtt_seconds(self.active_link)
        if serial_rtt is None:
            return False
        lan_rtt = None
        lan_fams = ("udp", "lan", "tcp")
        for link in self._links_for_peer(peer):
            fam = interface_family(self._link_attached_interface(link))
            if fam in lan_fams:
                lan_rtt = self._link_rtt_seconds(link)
                if lan_rtt is not None:
                    break
        if lan_rtt is None and self.active_link:
            fam = interface_family(self._link_attached_interface(self.active_link))
            if fam in lan_fams:
                lan_rtt = self._link_rtt_seconds(self.active_link)
        if lan_rtt is None:
            return False
        return serial_rtt * SERIAL_SPEED_MARGIN < lan_rtt

    def _failover_families_to_try(self, peer, peer_ip=None):
        """Ordered transports to attempt when reconnecting (LAN preferred unless serial is faster)."""
        raw_session = (self._session_transport or "").strip().lower()
        session_transport = self._normalize_transport(raw_session) if raw_session else None
        if session_transport == "serial" and self._serial_transport_ready():
            return ["serial"]
        if session_transport == "lan":
            interfaces = load_settings_interfaces(self.config_dir)
            udp_lan = configured_udp_lan_enabled(interfaces)
            tcp_lan = configured_tcp_lan_enabled(interfaces)
            if tcp_lan and not udp_lan:
                return ["tcp"]
            if udp_lan:
                return ["udp", "tcp"] if tcp_lan else ["udp"]
            return ["udp", "tcp", "lan"]
        if self._hub_transport_active() and self._peer_uses_hub_transport(peer):
            return ["tcp"]
        meta = self._peer_discovery_meta(peer)
        if meta and (meta.get("via") or "").strip() == "serial":
            if self._serial_transport_ready():
                return ["serial"]
            return []
        interfaces = load_settings_interfaces(self.config_dir)
        udp_lan = configured_udp_lan_enabled(interfaces)
        tcp_lan = configured_tcp_lan_enabled(interfaces)
        peer_lan_down = bool(peer_ip and self._peer_lan_recently_unreachable(peer_ip))
        lan_up = physical_lan_reachable() and not peer_lan_down and (
            (udp_lan and self._has_online_family("udp"))
            or (tcp_lan and self._has_online_family("tcp"))
        )
        serial_up = self._serial_transport_ready()
        if tcp_lan and not udp_lan:
            if lan_up and serial_up:
                order = (
                    ("serial", "tcp") if self._serial_faster_than_lan(peer) else ("tcp", "serial")
                )
            elif lan_up:
                order = ("tcp", "serial")
            elif serial_up:
                order = ("serial", "tcp")
            else:
                order = ("tcp", "serial")
        elif lan_up and serial_up:
            if self._serial_faster_than_lan(peer):
                order = ("serial", "udp", "tcp", "lan") if tcp_lan else ("serial", "udp", "lan")
            else:
                order = ("udp", "tcp", "lan", "serial") if tcp_lan else ("udp", "lan", "serial")
        elif lan_up:
            order = ("udp", "tcp", "lan", "serial") if tcp_lan else ("udp", "lan", "serial")
        elif serial_up:
            order = ("serial", "udp", "tcp", "lan") if tcp_lan else ("serial", "udp", "lan")
        else:
            order = ("udp", "tcp", "serial", "lan") if tcp_lan else ("udp", "serial", "lan")
        expected = self._peer_expected_transport_families(peer)
        seen = set()
        out = []
        for fam in order:
            if not fam or fam in seen:
                continue
            if expected:
                if fam == "serial" and "serial" not in expected:
                    continue
                if fam in ("udp", "lan", "tcp") and not (expected & {"udp", "lan", "tcp"}):
                    continue
            seen.add(fam)
            out.append(fam)
        if expected == {"serial"} and "serial" in seen:
            return ["serial"]
        return out

    def _failover_announce(self, prefer_family, peer_ip=None):
        """Refresh RNS path on the target transport before failover reconnect."""
        if prefer_family == "tcp":
            if peer_ip:
                ensure_tcp_client_to_peer(peer_ip, config_dir=self.config_dir)
            self._silent_announce(peer_ip=peer_ip)
            return
        if prefer_family == "serial":
            if self._serial_transport_ready():
                self._burst_serial_announce(count=1, force=True)
            return
        if prefer_family in ("udp", "lan"):
            if physical_lan_reachable():
                self._silent_announce(peer_ip=peer_ip, also_serial=False)
            elif self._serial_transport_ready():
                self._burst_serial_announce(count=1, force=True)
            return
        self._silent_announce(peer_ip=peer_ip if physical_lan_reachable() else None)

    def _preferred_failover_family(self, peer, attached=None, peer_ip=None):
        if self._hub_transport_active() and self._peer_uses_hub_transport(peer):
            return "tcp"
        attached = attached or self._link_attached_interface(self.active_link)
        att_fam = interface_family(attached)
        serial_up = self._serial_transport_ready()
        physical_lan = physical_lan_reachable()
        interfaces = load_settings_interfaces(self.config_dir)
        udp_lan = configured_udp_lan_enabled(interfaces)
        tcp_lan = configured_tcp_lan_enabled(interfaces)
        udp_up = self._has_online_family("udp") if udp_lan else False
        tcp_up = self._has_online_family("tcp") if tcp_lan else False
        peer_lan_down = bool(peer_ip and self._peer_lan_recently_unreachable(peer_ip))
        path_iface = self._peer_path_interface(peer)
        path_fam = interface_family(path_iface) if path_iface else ""
        if peer_lan_down and serial_up:
            return "serial"
        if path_fam == "serial" and serial_up and not physical_lan:
            return "serial"
        if physical_lan and tcp_lan and not udp_lan and tcp_up and not peer_lan_down:
            if att_fam == "serial" and serial_up:
                if self._serial_faster_than_lan(peer) and self._peer_has_path_on_family(peer, "serial"):
                    return "serial"
                return "tcp"
            return "tcp"
        # LAN primary whenever physical ethernet/Wi-Fi is up and peer answers on LAN.
        if physical_lan and (udp_up or tcp_up) and not peer_lan_down:
            prefer = "tcp" if (tcp_lan and tcp_up and not udp_lan) else "udp"
            if att_fam == "serial" and serial_up:
                if self._serial_faster_than_lan(peer) and self._peer_has_path_on_family(peer, "serial"):
                    return "serial"
                return prefer
            if att_fam == "serial" and not serial_up:
                return prefer
            if tcp_lan and tcp_up and udp_lan and udp_up:
                return "tcp"
            return prefer
        if physical_lan and lan_mesh_has_peer() and att_fam == "serial":
            return "lan"
        if serial_up and not physical_lan:
            return "serial"
        if att_fam in ("udp", "lan", "tcp") and not physical_lan and serial_up:
            return "serial"
        if att_fam == "serial":
            if tcp_up:
                return "tcp"
            if udp_up:
                return "udp"
            if self._has_online_family("lan"):
                return "lan"
        if att_fam == "lan" and not lan_mesh_has_peer():
            if bool(online_interfaces(family="udp")):
                return "udp"
            if serial_up:
                return "serial"
        if att_fam == "udp" and not physical_lan and serial_up:
            return "serial"
        if att_fam == "udp" and lan_mesh_has_peer():
            return "lan"
        path_iface = self._peer_path_interface(peer)
        if path_iface and self._interface_healthy(path_iface):
            fam = interface_family(path_iface)
            if fam != att_fam:
                return fam
        if self._has_online_family("udp"):
            return "udp"
        if self._has_online_family("lan"):
            return "lan"
        if self._has_online_family("serial"):
            return "serial"
        return None

    def _prepare_failover_path(self, peer, prefer_family=None, peer_ip=None, peer_port=None):
        if self._interrupted():
            return False
        self._ensure_runtime_serial_transport()
        if peer_ip and self._peer_lan_recently_unreachable(peer_ip):
            peer_ip = None
            if prefer_family in ("udp", "lan"):
                prefer_family = "serial" if self._has_online_family("serial") else prefer_family
            clear_paths_on_family("udp")
        suppress_offline_lan_transports()
        dedupe_serial_interfaces()
        prune_dead_serial_interfaces()
        if not self._serial_transport_ready():
            serial_cleared = clear_paths_on_family("serial")
            if serial_cleared:
                print(f"[connect] Cleared {serial_cleared} stale serial path(s)")
        pruned = prune_stale_lan_paths()
        if pruned:
            print(f"[connect] Cleared {pruned} stale LAN path(s)")
        bridged = prune_bridged_lan_paths()
        if bridged:
            print(f"[connect] Cleared {bridged} bridged LAN path(s)")
        if prefer_family == "serial":
            keep_families = ("serial",)
        elif prefer_family == "tcp":
            keep_families = ("tcp",)
        elif prefer_family in ("lan", "udp"):
            keep_families = ("udp", "lan")
        else:
            keep_families = None
        if keep_families:
            cleared = clear_paths_except_families(keep_families)
            if cleared:
                print(f"[connect] Cleared {cleared} path(s) off {prefer_family} transport")
        detached = detach_unhealthy_interfaces()
        if detached:
            print(f"[connect] Detached {detached} offline RNS interface(s)")
        stop = self._interrupted
        physical_lan = physical_lan_reachable()
        self._failover_announce(prefer_family, peer_ip=peer_ip)
        if prefer_family == "serial":
            if not self._serial_transport_ready():
                print("[connect] Serial interface offline — skipping serial path prep")
                clear_paths_on_family("serial")
                return False
            prune_lan_path_for_peer(peer)
            clear_peer_path_unless_family(peer, "serial")
            restored = restore_serial_path_from_announce(peer)
            if not restored:
                reinforce_serial_peer_path(peer)
            path_iface = restored or wait_for_peer_path_families(
                peer, families=("serial",), timeout_s=18.0, should_stop=stop,
            )
            if not path_iface:
                self._prime_serial_path(peer, timeout_s=SERIAL_PATH_PRIME_TIMEOUT_S)
                path_iface = wait_for_peer_path_families(
                    peer, families=("serial",), timeout_s=10.0, should_stop=stop,
                )
        elif prefer_family in ("lan", "udp") and self._lan_transport_ready():
            if peer_ip and physical_lan:
                register_udp_peer_ip(peer_ip)
                self._wake_peer(
                    peer_ip, peer_port or 8742, self.my_dest_hash or "",
                )
            elif peer_ip and not physical_lan:
                peer_ip = None
            request_paths_for_hash(peer, family="udp")
            families = ("udp", "lan") if prefer_family == "lan" else (prefer_family,)
            path_iface = wait_for_peer_path_families(
                peer, families=families, timeout_s=14.0, should_stop=stop,
            )
            if not path_iface:
                self._prime_udp_path(peer, peer_ip=peer_ip, timeout_s=6.0)
                path_iface = wait_for_peer_path_families(
                    peer, families=families, timeout_s=8.0, should_stop=stop,
                )
        elif prefer_family == "tcp":
            if peer_ip and physical_lan:
                register_udp_peer_ip(peer_ip)
                self._wake_peer(
                    peer_ip, peer_port or 8742, self.my_dest_hash or "",
                )
            if peer_ip:
                ensure_tcp_client_to_peer(peer_ip, config_dir=self.config_dir)
            request_paths_for_hash(peer, family="tcp")
            path_iface = wait_for_peer_path_families(
                peer, families=("tcp",), timeout_s=14.0, should_stop=stop,
            )
            if not path_iface:
                self._prime_tcp_path(peer, peer_ip=peer_ip, timeout_s=6.0)
                path_iface = wait_for_peer_path_families(
                    peer, families=("tcp",), timeout_s=8.0, should_stop=stop,
                )
        else:
            request_paths_for_hash(peer, family=prefer_family)
            families = (prefer_family,) if prefer_family else (None,)
            wait_s = 12.0 if prefer_family in ("lan", "udp", None) else 18.0
            path_iface = wait_for_peer_path_families(
                peer, families=families, timeout_s=wait_s, should_stop=stop,
            )
        if path_iface:
            fam = interface_family(path_iface)
            print(f"[connect] Path ready on {type(path_iface).__name__} ({fam or prefer_family})")
            return True
        print(f"[connect] Waiting for path to {peer[:16]}... (no {prefer_family or 'usable'} path yet)")
        return False

    def link_needs_failover(self):
        if self.dual_identity_mode:
            return False, ""
        if not self.active_link or not self.active_peer_hash:
            return False, ""
        if self._has_active_transfer():
            return False, ""
        peer = self.dest_hash_for(self.active_peer_hash)
        if not peer or peer == "unknown":
            return False, ""

        attached = self._link_attached_interface(self.active_link)
        if self._hub_transport_active() and self._peer_uses_hub_transport(peer):
            att_fam = interface_family(attached)
            if att_fam == "tcp" and self._link_interface_healthy(self.active_link):
                return False, ""
            if self._has_online_family("tcp") and not self._link_interface_healthy(self.active_link):
                return True, "hub TCP link offline"
            if att_fam != "tcp" and self._has_online_family("tcp"):
                return True, "hub path on TCP"
            return False, ""
        in_grace = (time.time() - self._last_link_established_at) < LINK_FAILOVER_GRACE_S

        if not self._link_interface_healthy(self.active_link):
            return True, f"link interface offline ({type(attached).__name__ if attached else 'none'})"

        path_iface = self._peer_path_interface_for_peer(peer)
        att_fam = interface_family(attached)
        path_fam = interface_family(path_iface) if path_iface else ""

        if path_iface and attached and not self._interfaces_equivalent(path_iface, attached):
            if self._interface_healthy(path_iface):
                new_score = self._interface_path_score(path_iface)
                old_score = self._interface_path_score(attached)
                # UDP-LAN: ignore path-table flaps while the current link is healthy.
                if path_fam == att_fam == "udp" and self._link_interface_healthy(self.active_link):
                    pass
                elif path_fam != att_fam:
                    if not in_grace and new_score > old_score + 10:
                        return True, f"path moved to {path_fam} (link on {att_fam})"
                elif not in_grace and new_score > old_score + 25:
                    return True, f"better path on {type(path_iface).__name__}"

        if att_fam == "lan" and not lan_mesh_has_peer():
            if bool(online_interfaces(family="udp")):
                return True, "AutoInterface down, UDP available"
            if self._has_online_family("serial"):
                return True, "LAN down, serial available"

        if att_fam == "udp" and not self._lan_transport_ready():
            if self._has_online_family("serial"):
                return True, "LAN down, serial available"
            if lan_mesh_has_peer():
                return True, "UDP down, AutoInterface available"

        if att_fam == "udp" and not physical_lan_reachable() and self._has_online_family("serial"):
            if not in_grace:
                return True, "ethernet down, serial available"

        serial_only = self._peer_expected_transport_families(peer) == {"serial"}
        lan_only = bool(
            self._peer_expected_transport_families(peer)
            and "serial" not in self._peer_expected_transport_families(peer)
        )
        parallel = self._parallel_sessions_allowed()

        expected = self._peer_expected_transport_families(peer)
        if parallel and expected:
            if not self._link_interface_healthy(self.active_link):
                return True, f"link interface offline ({type(attached).__name__ if attached else 'none'})"
            if serial_only and att_fam == "serial":
                return False, ""
            if lan_only and att_fam in ("udp", "lan", "tcp"):
                return False, ""
            if serial_only and att_fam in ("udp", "lan", "tcp") and not in_grace:
                return True, "serial peer requires serial transport"
            if lan_only and att_fam == "serial" and not in_grace:
                return True, "LAN peer requires LAN transport"

        if serial_only and att_fam == "serial" and self._link_interface_healthy(self.active_link):
            return False, ""

        if (
            not parallel
            and att_fam in ("udp", "lan")
            and self._has_online_family("serial")
            and self._peer_has_path_on_family(peer, "serial")
            and not in_grace
            and not serial_only
        ):
            return True, "peer path on serial"

        if att_fam == "serial" and not self._serial_transport_ready():
            if (self._has_online_family("udp") or self._has_online_family("lan")) and physical_lan_reachable():
                if not serial_only:
                    return True, "serial offline, LAN available"

        if (
            not parallel
            and att_fam == "serial"
            and physical_lan_reachable()
            and self._has_online_family("udp")
            and not in_grace
            and not serial_only
        ):
            if self._serial_faster_than_lan(peer) and self._peer_has_path_on_family(peer, "serial"):
                return False, ""
            path_iface = self._peer_path_interface_for_peer(peer)
            if path_iface and interface_family(path_iface) == "serial":
                if self._serial_faster_than_lan(peer):
                    return False, ""
            return True, "LAN available, upgrading from serial"

        if len(self._pending_sends) >= RECEIPT_FAILOVER_MIN_PENDING:
            oldest = min(self._pending_sends.values())
            if (time.time() - oldest) > RECEIPT_FAILOVER_TIMEOUT_S:
                try:
                    if getattr(self.active_link, "status", None) == RNS.Link.STALE:
                        return True, "send receipt timeout (link stale)"
                except Exception:
                    pass
                if (time.time() - self._last_link_established_at) > LINK_FAILOVER_GRACE_S:
                    return True, "send receipt timeout (link may be dead)"

        if not self._peer_has_path(peer) and not in_grace:
            if (
                att_fam == "serial"
                and self._link_interface_healthy(self.active_link)
                and self._peer_link_active(peer)
            ):
                pass
            else:
                alt = self._preferred_failover_family(peer, attached)
                if alt and self._has_online_family(alt):
                    return True, f"path lost, trying {alt}"
                if not self._link_interface_healthy(self.active_link):
                    return True, "no path to peer (link interface dead)"

        try:
            if getattr(self.active_link, "status", None) == RNS.Link.STALE:
                inactive = self.active_link.inactive_for()
                if inactive > LINK_STALE_FAILOVER_IDLE_S:
                    return True, f"link stale ({inactive:.0f}s idle)"
        except Exception:
            pass

        return False, ""

    def session_needs_reconnect(self):
        """True when the primary session peer's RNS link is missing or unhealthy."""
        if self.dual_identity_mode:
            return False, ""
        if self._connect_in_progress:
            return False, ""
        if self._has_active_transfer():
            return False, ""
        peer = self.dest_hash_for(self._session_peer_hash or self.active_peer_hash or "")
        if not peer or peer == "unknown":
            return False, ""
        if self.is_user_disconnected(peer):
            return False, ""
        if self._peer_expected_transport_families(peer) == {"serial"}:
            if configured_serial_enabled(load_settings_interfaces(self.config_dir)):
                if serial_interface_online() is None:
                    return False, ""
        adopted = self._adopt_healthy_peer_link(peer)
        if adopted:
            if self.active_link and is_serial_interface(
                self._link_attached_interface(self.active_link)
            ):
                return False, ""
            if self.active_link and self._link_interface_healthy(self.active_link):
                needs, reason = self.link_needs_failover()
                if needs:
                    return needs, reason
                return False, ""
        if self._peer_link_active(peer):
            if self.active_link and not self._link_interface_healthy(self.active_link):
                return True, "link interface offline"
            if self.active_link and is_serial_interface(self._link_attached_interface(self.active_link)):
                return False, ""
            if self.active_link:
                needs, reason = self.link_needs_failover()
                if needs:
                    return needs, reason
            return False, ""
        healthy_links = [
            link for link in self._links_for_peer(peer)
            if self._link_interface_healthy(link)
        ]
        if healthy_links:
            self._adopt_healthy_peer_link(peer)
            return False, ""
        in_grace = (time.time() - self._last_link_established_at) < LINK_FAILOVER_GRACE_S
        if in_grace and self._links_for_peer(peer):
            return False, ""
        if self._failover_in_progress:
            return False, ""
        if self.active_link:
            return self.link_needs_failover()
        if self._last_link_lost_at and (time.time() - self._last_link_lost_at) < self._session_reconnect_min_idle():
            return False, ""
        if self._transport_reconnect_pending:
            return True, "transport available — reconnecting"
        if time.time() - self._failover_last_attempt < self._failover_cooldown():
            return False, ""
        return True, "link dropped — reconnecting"

    def clear_session_peer(self):
        self._session_peer_hash = None
        self._session_transport = None


    def _outgoing_link(self, peer_hash=None):
        if peer_hash:
            link = self._best_outgoing_link(peer_hash)
            if link:
                return link
        if self.active_peer_hash:
            link = self._best_outgoing_link(self.active_peer_hash)
            if link:
                return link
        return self._send_link or self.active_link


    def _hub_send_targets(self, hub_server_hash=None, hub_server_mode=False):
        tcp_peers = self._hub_tcp_linked_peers()
        if hub_server_mode:
            return tcp_peers
        if hub_server_hash:
            peer = self.dest_hash_for(hub_server_hash)
            if peer and peer != "unknown" and peer in tcp_peers:
                return [peer]
            return []
        return tcp_peers[:1]

    def drain_hub_group_queue(self, hub_server_hash=None, hub_server_mode=False):
        if not any(is_hub_peer_hash(e.get("target_hash")) for e in self.message_queue):
            return 0
        targets = self._hub_send_targets(hub_server_hash, hub_server_mode)
        if not targets or not any(self._peer_link_active(t) for t in targets):
            return 0
        remaining = []
        sent = 0
        for entry in self.message_queue:
            if not is_hub_peer_hash(entry.get("target_hash")):
                remaining.append(entry)
                continue
            if entry.get("type") not in ("text", "emoji"):
                remaining.append(entry)
                continue
            msg_id = entry.get("msg_id")
            result = self.send_hub_message(
                entry["content"],
                msg_id=msg_id,
                hub_server_hash=hub_server_hash,
                hub_server_mode=hub_server_mode,
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


    def _setup_inbound_destination(self, identity, attr_name):
        if not identity:
            return None
        dest = RNS.Destination(
            identity,
            RNS.Destination.IN,
            RNS.Destination.SINGLE,
            APP_NAME,
            "messages",
        )
        dest.set_proof_strategy(RNS.Destination.PROVE_ALL)
        dest.accepts_links(True)
        dest.set_link_established_callback(self._link_callback)
        setattr(self, attr_name, dest)
        dest_hex = normalize_hash(RNS.hexrep(dest.hash))
        ident_hex = normalize_hash(RNS.hexrep(identity.hash))
        self.register_peer_mapping(dest_hex, ident_hex)
        return dest

    def start(self):
        self.destination = self._setup_inbound_destination(self.identity, "destination")
        if self.identity_serial:
            self.destination_serial = self._setup_inbound_destination(
                self.identity_serial, "destination_serial",
            )
            self.my_dest_hash_serial = normalize_hash(
                RNS.hexrep(self.destination_serial.hash),
            )
            print(f"[identity] Serial endpoint {self.my_dest_hash_serial[:16]}...")

        if self.auto_announce:
            self._announce(also_serial=False)
            self._announce_thread = threading.Thread(target=self._announce_loop, daemon=True)
            self._announce_thread.start()

        self.running = True
        self._queue_retry_thread = threading.Thread(target=self._queue_retry_loop, name="chatx5-queue-retry", daemon=True)
        self._queue_retry_thread.start()
        try:
            from chatx5.core.rns_interfaces import register_serial_hot_add_callback
            register_serial_hot_add_callback(self.on_serial_transport_attached)
        except Exception:
            pass
        print(f"[messaging] Started (auto_announce={self.auto_announce})")
        return self.destination

    def on_serial_transport_attached(self, iface=None):
        """USB serial became available — announce on serial and nudge reconnect."""
        if not self.running or not self.destination:
            return
        if self._has_active_transfer():
            return
        if not self.ensure_serial_runtime():
            print("[serial] USB attached but serial identity not ready")
            return
        sent = self._burst_serial_announce(count=1, force=True)
        if sent:
            port = getattr(iface, "port", "?") if iface else "?"
            print(f"[serial] Auto-announced on serial attach ({port})")
        peer = self.dest_hash_for(self.active_peer_hash or self._session_peer_hash or "")
        if peer and not is_hub_peer_hash(peer) and not self.is_user_disconnected(peer):
            request_paths_for_hash(peer, family="serial")
            if not self._peer_link_active(peer):
                self._transport_reconnect_pending = True
                self._failover_last_attempt = 0

    def on_serial_transport_detached(self):
        """USB serial unplugged — drop serial paths and stop serial-only routing."""
        from chatx5.core.lan_rns import clear_paths_on_family, unpin_serial_path

        try:
            clear_paths_on_family("serial")
        except Exception:
            pass
        for peer_hash in list(self.peer_links.keys()):
            unpin_serial_path(peer_hash)
        self._transport_reconnect_pending = False

    def _queue_retry_loop(self):
        while self.running:
            for _ in range(QUEUE_RETRY_INTERVAL_S):
                if not self.running:
                    return
                time.sleep(1)
            if self.message_queue:
                try:
                    self.retry_queue()
                except Exception as e:
                    print(f"[queue] Retry loop error: {e}")

    def announce(self, also_serial=True):
        self._announce(also_serial=also_serial)

    def _serial_mode_active(self):
        return (
            configured_serial_enabled(load_settings_interfaces(self.config_dir))
            and not lan_discovery_configured(load_settings_interfaces(self.config_dir))
        )

    def _announce_payload(self, include_lan_ip=True):
        payload = {
            "app": APP_NAME,
            "name": self.display_name or "",
        }
        if include_lan_ip and lan_discovery_configured(load_settings_interfaces(self.config_dir)):
            try:
                from chatx5.utils.platform import discovery_scope_ip

                ip = (discovery_scope_ip() or "").strip()
                if ip:
                    payload["ip"] = ip
            except Exception:
                pass
        return json.dumps(payload).encode("utf-8")

    def _peer_lan_ip_usable(self, peer_ip):
        """False when peer IPv4 is outside our pinned LAN scope (use serial instead)."""
        host = (peer_ip or "").strip()
        if not host:
            return False
        try:
            from chatx5.utils.platform import discovery_scope_ip
            from chatx5.utils.lan_scope import peer_in_scope

            scope = (discovery_scope_ip() or "").strip()
            if not scope:
                return True
            return peer_in_scope(host, scope)
        except Exception:
            return True

    def _announce_on_interface(self, iface, app_data=None):
        if is_serial_interface(iface) and not self.ensure_serial_runtime():
            return False
        dest = self._destination_for_interface(iface)
        if not dest or not iface:
            return False
        data = app_data if app_data is not None else self._announce_payload()
        if is_serial_interface(iface):
            try:
                payload = json.loads(data.decode("utf-8"))
                payload.pop("ip", None)
                data = json.dumps(payload).encode("utf-8")
            except Exception:
                pass
        dest.announce(app_data=data, attached_interface=iface)
        if is_serial_interface(iface):
            try:
                if self.identity_serial:
                    self.identity_serial.announce(attached_interface=iface)
            except Exception:
                pass
        else:
            try:
                if self.identity:
                    self.identity.announce(attached_interface=iface)
            except Exception:
                pass
        return True

    def _fallback_announce(self, announce_data):
        """Last-resort announce — never fan out LAN IP on USB when serial is up."""
        if self._serial_transport_ready():
            self._burst_serial_announce(count=1)
            return
        self.destination.announce(app_data=announce_data)
        try:
            RNS.Transport.identity.announce()
        except Exception:
            pass

    def _burst_serial_announce(self, count=None, interval=None, force=False):
        """Send RNS announces on serial only (default: one packet)."""
        if not force and (
            self._connect_in_progress
            or self._failover_in_progress
            or self._has_active_transfer()
        ):
            return 0
        if not self._serial_transport_ready():
            return 0
        if not self.ensure_serial_runtime():
            return 0
        suppress_offline_lan_transports()
        dedupe_serial_interfaces()
        prune_dead_serial_interfaces()
        iface = serial_interface_online()
        if not iface:
            return 0
        burst = count or SERIAL_ANNOUNCE_BURST_COUNT
        gap = interval if interval is not None else SERIAL_ANNOUNCE_BURST_INTERVAL_S
        announce_data = self._announce_payload(include_lan_ip=False)
        for attempt in range(burst):
            self._announce_on_interface(iface, app_data=announce_data)
            if attempt < burst - 1 and gap > 0:
                time.sleep(gap)
        port = getattr(iface, "port", "?")
        if burst <= 1:
            print(f"[serial] RNS announce on {port}")
        else:
            print(f"[serial] Burst {burst} RNS announce(s) on {port}")
        return burst

    def _silent_announce(self, peer_ip=None, also_serial=None):
        """RNS path refresh only — no subnet beacon probe."""
        if also_serial is None:
            also_serial = not self._failover_in_progress
        if not self.destination:
            return
        announce_data = self._announce_payload()
        interfaces = load_settings_interfaces(self.config_dir)
        tcp_lan = configured_tcp_lan_enabled(interfaces)
        udp_lan = configured_udp_lan_enabled(interfaces)
        if not physical_lan_reachable():
            suppress_offline_lan_transports()
            if self._serial_transport_ready():
                self._burst_serial_announce(count=1)
            return
        prune_dead_serial_interfaces()
        hub_role, _ = self._load_hub_settings()
        use_tcp_lan = tcp_lan and hub_role != "server"
        if use_tcp_lan:
            ensure_runtime_tcp_lan_server(config_dir=self.config_dir)
            if peer_ip:
                ensure_tcp_client_to_peer(peer_ip, config_dir=self.config_dir)
            tcp_iface = tcp_server_interface_online() or tcp_client_interface_online()
            if tcp_iface:
                self._announce_on_interface(tcp_iface, app_data=announce_data)
            elif self._serial_transport_ready():
                self._burst_serial_announce(count=1)
                return
            else:
                self._fallback_announce(announce_data)
        elif udp_lan:
            udp_iface = udp_interface_online()
            if udp_iface:
                self._announce_on_interface(udp_iface, app_data=announce_data)
            elif self._serial_transport_ready():
                self._burst_serial_announce(count=1)
                return
            else:
                self._fallback_announce(announce_data)
        elif self._serial_transport_ready():
            self._burst_serial_announce(count=1)
            return
        else:
            self._fallback_announce(announce_data)
        if peer_ip and udp_lan:
            packet = build_announce_packet(self.destination, announce_data)
            unicast_announce_packet(packet, peer_ip=peer_ip, subnet_probe=False)

    def _announce(self, peer_ip=None, unicast_subnet=None, also_serial=True):
        if not self.destination:
            return
        announce_data = self._announce_payload()
        if not physical_lan_reachable() and self._serial_transport_ready():
            if also_serial:
                self._burst_serial_announce(count=1)
            return
        prune_dead_serial_interfaces()
        interfaces = load_settings_interfaces(self.config_dir)
        tcp_lan = configured_tcp_lan_enabled(interfaces)
        udp_lan = configured_udp_lan_enabled(interfaces)
        hub_role, _ = self._load_hub_settings()
        use_tcp_lan = tcp_lan and hub_role != "server"
        if use_tcp_lan:
            ensure_runtime_tcp_lan_server(config_dir=self.config_dir)
            tcp_iface = tcp_server_interface_online() or tcp_client_interface_online()
            if tcp_iface:
                self._announce_on_interface(tcp_iface, app_data=announce_data)
            else:
                self._fallback_announce(announce_data)
        elif udp_lan:
            udp_iface = udp_interface_online()
            if udp_iface:
                self._announce_on_interface(udp_iface, app_data=announce_data)
            else:
                self._fallback_announce(announce_data)
        elif self._serial_transport_ready():
            self._burst_serial_announce(count=1)
        else:
            self._fallback_announce(announce_data)
        if unicast_subnet is None:
            unicast_subnet = True
        lan_ok = (
            lan_ip_reachable()
            and lan_discovery_configured(load_settings_interfaces(self.config_dir))
        )
        if udp_lan and (peer_ip or (unicast_subnet and lan_ok)):
            packet = build_announce_packet(self.destination, announce_data)
            sent = unicast_announce_packet(
                packet,
                peer_ip=peer_ip,
                subnet_probe=unicast_subnet and lan_ok,
            )
            if sent:
                hint = f" + {sent} unicast" if sent else ""
                print(f"[messaging] Announced on LAN (name={self.display_name or 'none'}{hint})")
                if also_serial and self._serial_transport_ready() and configured_serial_enabled(interfaces):
                    self._burst_serial_announce(count=1)
                return
        if (
            also_serial
            and self._serial_transport_ready()
            and configured_serial_enabled(interfaces)
            and lan_ok
        ):
            self._burst_serial_announce(count=1)
        if lan_ok:
            print(f"[messaging] Announced on LAN (name={self.display_name or 'none'})")
        else:
            print(f"[messaging] Announced on RNS (serial/other — LAN disconnected)")

    def _lan_transport_ready(self):
        interfaces = load_settings_interfaces(self.config_dir)
        if not lan_discovery_configured(interfaces):
            return False
        udp_lan = configured_udp_lan_enabled(interfaces)
        tcp_lan = configured_tcp_lan_enabled(interfaces)
        if is_android():
            if tcp_lan and not udp_lan:
                return (
                    tcp_server_interface_online() is not None
                    or lan_mesh_has_peer()
                    or bool(online_interfaces(family="tcp"))
                )
            return lan_mesh_has_peer() or bool(online_interfaces(family="udp"))
        if not physical_lan_reachable():
            return lan_mesh_has_peer()
        if tcp_lan and not udp_lan:
            return (
                lan_mesh_has_peer()
                or tcp_server_interface_online() is not None
                or bool(online_interfaces(family="tcp"))
            )
        return lan_mesh_has_peer() or bool(online_interfaces(family="udp"))

    def _serial_transport_ready(self):
        return serial_interface_online() is not None


    def _should_periodic_announce(self):
        """True when periodic LAN/serial RNS refresh may run."""
        if not self.auto_announce:
            return False
        if (
            self._connect_in_progress
            or self._failover_in_progress
            or self._has_active_transfer()
        ):
            return False
        interfaces = load_settings_interfaces(self.config_dir)
        return (
            lan_discovery_configured(interfaces)
            or (
                configured_serial_enabled(interfaces)
                and self._serial_transport_ready()
            )
        )

    def _announce_loop(self):
        lan_tick = 0
        serial_tick = 0
        while self.running:
            time.sleep(1)
            if not self.running:
                return
            if self._has_active_transfer() or not self._should_periodic_announce():
                continue
            interfaces = load_settings_interfaces(self.config_dir)
            prune_dead_serial_interfaces()
            lan_iv = max(0, int(self.lan_announce_interval_s or 0))
            ser_iv = max(0, int(self.serial_announce_interval_s or 0))
            if lan_iv <= 0 and ser_iv <= 0 and not self.auto_announce:
                continue
            if lan_iv <= 0 and self.auto_announce:
                lan_iv = self.announce_interval
            if ser_iv <= 0 and self.auto_announce:
                ser_iv = self.announce_interval
            lan_tick += 1
            serial_tick += 1
            if lan_iv > 0 and lan_tick >= lan_iv and lan_discovery_configured(interfaces):
                lan_tick = 0
                self._silent_announce(also_serial=False)
            if (
                ser_iv > 0
                and serial_tick >= ser_iv
                and configured_serial_enabled(interfaces)
                and self._serial_transport_ready()
            ):
                serial_tick = 0
                self._burst_serial_announce(count=1)

    def stop(self):
        self.running = False
        self.shutdown_requested = True
        self.cancel_all_transfers()
        for link_id, link in self.links.items():
            try:
                link.teardown()
            except:
                pass

    def rebind_identity(self, identity, role="lan"):
        """Hot-swap LAN or serial identity without restarting the process."""
        role = (role or "lan").strip().lower()
        self.disconnect_all_peers(clear_session=True)
        self.identity_to_dest.clear()
        self.dest_to_identity.clear()
        self._link_peer_hashes.clear()
        self.peer_links.clear()
        self.links.clear()
        self.active_link = None
        self.active_peer_hash = None
        self._send_link = None
        self._session_peer_hash = None
        if role == "serial":
            self.identity_serial = identity
            self.destination_serial = self._setup_inbound_destination(
                identity, "destination_serial",
            )
            self.my_dest_hash_serial = normalize_hash(
                RNS.hexrep(self.destination_serial.hash),
            )
            dest_hex = self.my_dest_hash_serial
            try:
                self._burst_serial_announce(count=1, force=True)
            except Exception as e:
                print(f"[identity] Post-rebind serial announce failed: {e}")
        else:
            self.identity = identity
            self.destination = self._setup_inbound_destination(identity, "destination")
            dest_hex = normalize_hash(RNS.hexrep(self.destination.hash))
            self.my_dest_hash = dest_hex
            try:
                self._silent_announce(also_serial=False)
            except Exception as e:
                print(f"[identity] Post-rebind LAN announce failed: {e}")
        print(f"[identity] Rebound {role} destination to {dest_hex[:16]}...")
        return self.destination_serial if role == "serial" else self.destination

    def _dest_hash_from_identity(self, ident):
        dest = message_dest_hash_for_identity(ident)
        if dest and ident and getattr(ident, "hash", None):
            ident_hex = normalize_hash(RNS.hexrep(ident.hash))
            if ident_hex and ident_hex != dest:
                self.register_peer_mapping(dest, ident_hex)
        return dest

    def _recall_identity_bytes(self, raw):
        if not raw:
            return None
        ident = RNS.Identity.recall(raw)
        if ident is None:
            ident = RNS.Identity.recall(raw, from_identity_hash=True)
        return ident


    def _resolve_remote_peer(self, link, fallback=None):
        ident_hex = ""
        computed_dest = ""
        try:
            ident = link.get_remote_identity()
            if ident and hasattr(ident, "hash") and ident.hash:
                ident_hex = normalize_hash(RNS.hexrep(ident.hash))
                computed_dest = self._dest_hash_from_identity(ident)
                if not computed_dest:
                    pub = ident.get_public_key()
                    if pub:
                        with RNS.Identity.known_destinations_lock:
                            for dest_hash_bytes, entry in RNS.Identity.known_destinations.items():
                                if len(entry) > 2 and entry[2] == pub:
                                    computed_dest = normalize_hash(RNS.hexrep(dest_hash_bytes))
                                    self.register_peer_mapping(computed_dest, ident_hex)
                                    break
        except Exception:
            pass

        if self.peer_resolver:
            try:
                resolved = self.peer_resolver(
                    ident_hex=ident_hex,
                    computed_dest=computed_dest,
                    fallback=fallback,
                    link=link,
                )
                if resolved:
                    return self.dest_hash_for(resolved)
            except Exception as e:
                print(f"[messaging] peer_resolver error: {e}")

        if computed_dest:
            return self.dest_hash_for(computed_dest)
        if fallback:
            return self.dest_hash_for(fallback)
        if ident_hex and not self._is_self_hash(ident_hex):
            return self.dest_hash_for(ident_hex)
        return "unknown"

    def _get_remote_hash(self, link):
        return self._peer_for_link(link)

    def _peer_destination_hash(self, link, fallback=None):
        return self._peer_for_link(link, fallback=fallback)

    def _notify_link_established(self, link, peer_hash=None, promote_active=True,
                                 background=False, passive=False):
        peer = self.canonical_connect_hash(peer_hash or "", link=link)
        if (not peer or peer == "unknown") and link:
            peer = self._peer_hash_from_link_identity(link)
        if not peer or peer == "unknown":
            peer = self.canonical_connect_hash(
                self._peer_destination_hash(link, fallback=peer_hash),
                link=link,
            )
        if is_hub_peer_hash(peer):
            return
        if not peer or peer == "unknown":
            session_peer = self.dest_hash_for(self._session_peer_hash or "")
            if session_peer and not is_hub_peer_hash(session_peer):
                peer = session_peer
        if not peer or peer == "unknown":
            return
        self._register_peer_link(link, peer)
        self._last_link_established_at = time.time()
        if promote_active:
            self._consolidate_peer_links(peer, keep_link=link)
            session_peer = self.dest_hash_for(self._session_peer_hash or "")
            parallel = self._parallel_sessions_allowed()
            link_transport = self._transport_from_link(link)
            adopt_session = not parallel
            if parallel:
                adopt_session = (
                    not session_peer
                    or not self.active_link
                    or self.hashes_equivalent(peer, session_peer)
                    or link_transport == self._session_transport
                )
            old_active = self.active_peer_hash
            if adopt_session:
                self.active_link = link
                self.active_peer_hash = peer
                self._session_peer_hash = peer
                self._session_transport = link_transport
                self._send_link = link
                if not old_active or self.hashes_equivalent(peer, old_active):
                    self._pending_sends.clear()
            else:
                self._register_peer_link(link, peer)
        label = "background" if background else "active"
        print(f"[messaging] Link ready with {peer[:16]}... ({label})")
        if self.on_link_established:
            try:
                self.on_link_established(
                    peer, link,
                    background=background,
                    promote_active=promote_active,
                    passive=passive,
                )
            except TypeError:
                try:
                    self.on_link_established(
                        peer, link, background=background, promote_active=promote_active,
                    )
                except Exception as e:
                    print(f"[messaging] on_link_established error: {e}")
            except Exception as e:
                print(f"[messaging] on_link_established error: {e}")

    def _peer_endpoint(self, peer_hash):
        if self.peer_endpoint_resolver:
            try:
                endpoint = self.peer_endpoint_resolver(peer_hash)
                if endpoint:
                    ip, port = endpoint[0], endpoint[1] if len(endpoint) > 1 else self.http_port
                    if ip:
                        return str(ip).strip(), int(port or self.http_port)
            except Exception:
                pass
        return None, self.http_port

    def _setup_link(self, link):
        self.links[link.link_id] = link
        self._optimise_link_mtu(link)
        link.set_link_closed_callback(self._link_closed(link))
        link.set_packet_callback(self._packet_callback(link))
        try:
            link.set_resource_strategy(RNS.Link.ACCEPT_APP)
            link.set_resource_callback(self._resource_accept_callback(link))
            link.set_resource_concluded_callback(self._resource_concluded(link))
            if hasattr(link, "set_resource_started_callback"):
                link.set_resource_started_callback(self._resource_started_callback(link))
            print(f"[messaging] Resource strategy ACCEPT_APP for link {link.link_id.hex()[:12]}")
        except Exception as e:
            print(f"[messaging] Failed to set resource strategy: {e}")

    def _send_receipt(self, link, msg_id, status):
        try:
            receipt = json.dumps({"msg_id": msg_id, "status": status})
            msg = ChatMessage("__receipt", receipt)
            packet = RNS.Packet(link, msg.to_json().encode("utf-8"))
            packet.send()
        except:
            pass

    def send_read_receipt(self, link, msg_id):
        try:
            receipt = json.dumps({"msg_id": msg_id})
            msg = ChatMessage("__read_receipt", receipt)
            packet = RNS.Packet(link, msg.to_json().encode("utf-8"))
            packet.send()
        except:
            pass

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

        incoming_fam = interface_family(self._link_attached_interface(link))
        expected = self._peer_expected_transport_families(peer_hash)
        if expected and incoming_fam != "serial":
            if incoming_fam in ("udp", "lan", "tcp") and not (expected & {"udp", "lan", "tcp"}):
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

    def _session_occupied(self, peer_hash):
        if not self.active_link or not self.active_peer_hash:
            return False
        return not self.hashes_equivalent(peer_hash, self.active_peer_hash)

    def _teardown_active_link(self, preserve_peer=False, handoff=False, clear_session=False):
        self._link_handoff = handoff
        try:
            if self.active_link:
                try:
                    self.active_link.teardown()
                except Exception:
                    pass
            self.active_link = None
            self._send_link = None
            if not preserve_peer:
                self.active_peer_hash = None
                self._link_peer_hashes.clear()
            if clear_session:
                self.clear_session_peer()
        finally:
            if handoff:
                self._link_handoff = False

    def _drain_queue_after_reconnect(self, peer_hash=None):
        """Send queued messages once a reconnect path is live."""
        peer = self.dest_hash_for(
            peer_hash or self._session_peer_hash or self.active_peer_hash or ""
        )
        if not peer or peer == "unknown" or is_hub_peer_hash(peer):
            return
        if self.is_user_disconnected(peer) or not self._peer_link_active(peer):
            return
        link = self._queue_send_link(peer) or self._link_for_peer(peer)
        self._schedule_queue_drain(peer, link=link, include_files=True)

    def resume_session_peer(self, peer_ip=None, peer_port=None, peer_lookup=None,
                            caller_ip=None, caller_port=8742):
        """Reconnect to the saved session peer after link drop or UI resume."""
        if self.dual_identity_mode:
            return False
        peer = self.dest_hash_for(self._session_peer_hash or self.active_peer_hash or "")
        if not peer or peer == "unknown":
            return False
        if self.is_user_disconnected(peer):
            return False
        if self.active_link and self._peer_link_active(peer):
            return True
        if self._connect_in_progress:
            return False
        if self._failover_in_progress:
            return False
        print(f"[connect] Resuming session with {peer[:16]}...")
        return self.reconnect_active_peer(
            peer_ip, peer_port, peer_lookup, caller_ip, caller_port,
            reason="session resume",
        )

    def reconnect_active_peer(self, peer_ip=None, peer_port=None, peer_lookup=None,
                              caller_ip=None, caller_port=8742, reason=""):
        if self.dual_identity_mode:
            return False
        now = time.time()
        if self._connect_in_progress:
            return False
        if self._failover_in_progress:
            return False
        peer = self.dest_hash_for(self._session_peer_hash or self.active_peer_hash or "")
        if not peer or peer == "unknown":
            return False
        if self.is_user_disconnected(peer):
            return False
        if self._has_active_transfer():
            if self._peer_link_active(peer):
                return True
            return False
        if self._peer_expected_transport_families(peer) == {"serial"}:
            if configured_serial_enabled(load_settings_interfaces(self.config_dir)):
                if serial_interface_online() is None:
                    return False
        if self._peer_link_active(peer):
            link = self._link_for_peer(peer) or self.active_link
            if link and self._link_interface_healthy(link):
                if not self.active_link or getattr(self.active_link, "link_id", None) != getattr(link, "link_id", None):
                    self._adopt_healthy_peer_link(peer)
                return True
        if now - self._failover_last_attempt < self._failover_cooldown():
            return False
        if not self.active_peer_hash:
            self.active_peer_hash = peer
        if not self._session_peer_hash:
            self._session_peer_hash = peer

        self._failover_last_attempt = now
        self._failover_in_progress = True
        self._transport_reconnect_pending = False
        try:
            families = self._failover_families_to_try(peer, peer_ip=peer_ip)
            print(
                f"[connect] Failover reconnect to {peer[:16]}... ({reason}) "
                f"[{', '.join(families)}]"
            )
            self._teardown_stale_peer_links(peer, handoff=True)
            self._teardown_active_link(preserve_peer=True, handoff=True)
            pause_until = time.time() + 0.3
            while time.time() < pause_until:
                if self._interrupted():
                    return False
                time.sleep(0.05)
            for prefer in families:
                use_ip = peer_ip
                if prefer == "serial":
                    use_ip = None
                elif use_ip:
                    register_udp_peer_ip(use_ip)
                if self._interrupted():
                    return False
                if not self._prepare_failover_path(
                    peer, prefer_family=prefer, peer_ip=use_ip, peer_port=peer_port,
                ):
                    print(f"[connect] {prefer} path not ready — trying next transport")
                    continue
                if prefer == "serial" and self._serial_transport_ready():
                    inbound_wait = SERIAL_INBOUND_FIRST_WAIT_S
                    print(f"[connect] Failover waiting for serial inbound ({inbound_wait}s)...")
                    if self._wait_for_peer_link(peer, timeout_s=inbound_wait):
                        self._adopt_healthy_peer_link(peer)
                        self._drain_queue_after_reconnect(peer)
                        print("[connect] Failover complete (serial inbound)")
                        return True
                elif (
                    prefer in ("udp", "lan")
                    and use_ip
                    and self._lan_transport_ready()
                    and physical_lan_reachable()
                ):
                    inbound_wait = INITIATOR_INBOUND_WAIT_S
                    print(f"[connect] Failover waiting for inbound link on {prefer} ({inbound_wait}s)...")
                    if self._wait_for_peer_link(peer, timeout_s=inbound_wait):
                        self._adopt_healthy_peer_link(peer)
                        self._drain_queue_after_reconnect(peer)
                        print(f"[connect] Failover complete (inbound via {prefer})")
                        return True
                if self._interrupted():
                    return False
                result = self.connect_to(
                    peer,
                    use_ip,
                    peer_port,
                    peer_lookup,
                    caller_ip,
                    caller_port,
                    replace=False,
                    failover=True,
                )
                if result:
                    self._adopt_healthy_peer_link(peer)
                    self._drain_queue_after_reconnect(peer)
                    print(f"[connect] Failover complete via {prefer}")
                    return True
                if prefer == families[-1]:
                    print(f"[connect] Failover connect via {prefer} failed")
                else:
                    print(f"[connect] Failover connect via {prefer} failed — trying next transport")
            return False
        finally:
            self._failover_in_progress = False

    def _interrupted(self):
        return self.shutdown_requested or not self.running


    def send_hub_message(self, text, receipt_callback=None, msg_id=None,
                       hub_server_hash=None, hub_server_mode=False):
        role, _ = self._load_hub_settings()
        if role == "client" and not self._hub_tcp_linked_peers():
            self.ensure_hub_link(background=True)
        msg = ChatMessage(MESSAGE_TYPE_TEXT, text, msg_id=msg_id)
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
            link = self._link_for_peer(peer)
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
            return False
        print(f"[hub] Sent group message: {text[:50]}...")
        self._sent_messages[msg.msg_id] = msg
        self._pending_sends[msg.msg_id] = time.time()
        if receipt_callback:
            self._receipt_callbacks[msg.msg_id] = receipt_callback
        return msg

    def relay_hub_message(self, chat_msg, sender_hash):
        if not getattr(chat_msg, "hub_group", False):
            return
        data = chat_msg.to_json().encode("utf-8")
        for peer in self._hub_tcp_linked_peers():
            if is_hub_peer_hash(peer) or self.hashes_equivalent(peer, sender_hash):
                continue
            link = self._link_for_peer(peer)
            if not link:
                continue
            try:
                RNS.Packet(link, data).send()
            except Exception as e:
                print(f"[hub] relay failed to {peer[:16]}: {e}")

    def peer_send_ready(self, target_peer=None, prefer_transport=None):
        peer = self.dest_hash_for(
            target_peer or self.active_peer_hash or self._session_peer_hash or ""
        )
        if not peer or peer == "unknown":
            return False
        transport = self._normalize_transport(prefer_transport) if prefer_transport else None
        if not self._peer_link_active(peer, transport=transport):
            return False
        link = self._queue_send_link(peer, prefer_transport=transport)
        return bool(
            link
            and self._link_matches_peer(link, peer)
            and self._link_interface_healthy(link)
        )

    def send_message(self, text, receipt_callback=None, msg_id=None, target_peer=None,
                     link=None, prefer_transport=None):
        peer = self.dest_hash_for(
            target_peer or self.active_peer_hash or self._session_peer_hash or ""
        )
        if not peer or peer == "unknown":
            print("[messaging] send_message: no target peer")
            return False
        transport = self._normalize_transport(prefer_transport) if prefer_transport else None
        if not self._peer_link_active(peer, transport=transport):
            print(f"[messaging] send_message: no active link to {peer[:16]}")
            return False
        link = self._queue_send_link(peer, link_hint=link, prefer_transport=transport)
        if not link or not self._link_matches_peer(link, peer):
            print(f"[messaging] send_message: no transport-safe link to {peer[:16]}")
            return False
        remote = self._link_remote_peer_hash(link)
        if remote and not self.hashes_equivalent(remote, peer):
            print(
                f"[messaging] send_message: link remote {remote[:16]} "
                f"≠ target {peer[:16]} — blocked"
            )
            return False
        if not self._link_interface_healthy(link):
            alt = self._queue_send_link(peer)
            if (
                alt
                and alt.link_id != link.link_id
                and self._link_matches_peer(alt, peer)
                and self._link_interface_healthy(alt)
            ):
                link = alt
            else:
                print(f"[messaging] send_message: link transport offline for {peer[:16]}")
                return False
        msg = ChatMessage(MESSAGE_TYPE_TEXT, text, msg_id=msg_id)
        data = msg.to_json().encode("utf-8")
        mtu = getattr(link, 'mtu', 500)
        try:
            if len(data) > mtu - 50:
                return self._send_long_text(msg, text, data, receipt_callback, link)
            packet = RNS.Packet(link, data)
            packet.send()
            print(f"[messaging] Sent text message: {text[:50]}...")
            self._sent_messages[msg.msg_id] = msg
            self._pending_sends[msg.msg_id] = time.time()
            if receipt_callback:
                self._receipt_callbacks[msg.msg_id] = receipt_callback
            return msg
        except Exception as e:
            print(f"[messaging] Send failed: {e}")
            return False

