import json
import os
import threading
import time

import RNS

from chatx5.core.discovery import (
    message_dest_hash_for_identity,
    normalize_hash,
)
from chatx5.core.lan_rns import (
    clear_paths_on_family,
    interface_family,
    interface_is_healthy,
    lan_mesh_has_peer,
    online_interfaces,
    peer_path_entry,
    peer_path_on_family,
    register_udp_peer_ip,
    request_paths_for_hash,
    scrub_peer_path,
    serial_interface_online,
    suppress_offline_lan_transports,  # noqa: F401 — backend patch surface (see below)
    unpin_serial_path,
)
from chatx5.core.messaging.announce import AnnounceMixin
from chatx5.core.messaging.connect import ConnectMixin
from chatx5.core.messaging.constants import (
    APP_NAME,
    INITIATOR_INBOUND_WAIT_S,
    MESSAGE_TYPE_TEXT,
    QUEUE_RETRY_INTERVAL_S,
    SERIAL_INBOUND_FIRST_WAIT_S,
)
from chatx5.core.messaging.failover import FailoverMixin
from chatx5.core.messaging.hub import HubMixin
from chatx5.core.messaging.inbound_callbacks import InboundCallbacksMixin
from chatx5.core.messaging.links import PeerLinkMixin
from chatx5.core.messaging.models import ChatMessage
from chatx5.core.messaging.peers import is_hub_peer_hash
from chatx5.core.messaging.queue import QueueMixin
from chatx5.core.messaging.transfer import TransferMixin
from chatx5.core.rns_interfaces import (
    configured_serial_enabled,
    configured_tcp_lan_enabled,
    configured_udp_lan_enabled,  # noqa: F401 — backend patch surface (see below)
    dedupe_serial_interfaces,  # noqa: F401 — backend patch surface (see below)
    lan_discovery_configured,
    load_settings_interfaces,
    prune_dead_serial_interfaces,  # noqa: F401 — backend patch surface (see below)
    tcp_client_interface_online,
    tcp_server_interface_online,
)
from chatx5.core.serial_transfer import (
    is_serial_interface,
)
from chatx5.utils.platform import is_android, physical_lan_reachable

# Backend patch surface: the failover/connect/announce mixins delegate a few
# transport predicates through this module (chatx5.core.messaging.backend.<name>)
# so the test-suite can patch one place and have the stub apply everywhere. The
# names flagged above are imported here only to expose them for that delegation
# and are not referenced directly in this file.


class MessagingBackend(
    PeerLinkMixin,
    AnnounceMixin,
    ConnectMixin,
    FailoverMixin,
    HubMixin,
    QueueMixin,
    TransferMixin,
    InboundCallbacksMixin,
):
    """Orchestrator for encrypted peer messaging over Reticulum.

    Combines the transport, connection, failover, hub-relay, queue, and
    transfer mixins into a single backend that owns identities, destinations,
    active links, and the send path. Import path is unchanged:
    ``from chatx5.core.messaging import MessagingBackend``.
    """
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
        self.on_after_serial_announce = None
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

    def stop(self):
        self.running = False
        self.shutdown_requested = True
        self.cancel_all_transfers()
        for link_id, link in self.links.items():
            try:
                link.teardown()
            except Exception:
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
        except Exception:
            pass

    def send_read_receipt(self, link, msg_id):
        try:
            receipt = json.dumps({"msg_id": msg_id})
            msg = ChatMessage("__read_receipt", receipt)
            packet = RNS.Packet(link, msg.to_json().encode("utf-8"))
            packet.send()
        except Exception:
            pass

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

    def send_chat_message(self, chat_msg, receipt_callback=None, target_peer=None,
                          link=None, prefer_transport=None):
        """Send a pre-built ChatMessage (any type) to a peer."""
        peer = self.dest_hash_for(
            target_peer or self.active_peer_hash or self._session_peer_hash or ""
        )
        if not peer or peer == "unknown":
            print("[messaging] send_chat_message: no target peer")
            return False
        transport = self._normalize_transport(prefer_transport) if prefer_transport else None
        if not self._peer_link_active(peer, transport=transport):
            print(f"[messaging] send_chat_message: no active link to {peer[:16]}")
            return False
        link = self._queue_send_link(peer, link_hint=link, prefer_transport=transport)
        if not link or not self._link_matches_peer(link, peer):
            print(f"[messaging] send_chat_message: no transport-safe link to {peer[:16]}")
            return False
        data = chat_msg.to_json().encode("utf-8")
        mtu = getattr(link, "mtu", 500)
        try:
            if len(data) > mtu - 50 and chat_msg.msg_type == MESSAGE_TYPE_TEXT:
                return self._send_long_text(
                    chat_msg, chat_msg.content, data, receipt_callback, link,
                )
            import RNS
            RNS.Packet(link, data).send()
            print(f"[messaging] Sent {chat_msg.msg_type} message")
            self._sent_messages[chat_msg.msg_id] = chat_msg
            self._pending_sends[chat_msg.msg_id] = time.time()
            if receipt_callback:
                self._receipt_callbacks[chat_msg.msg_id] = receipt_callback
            return chat_msg
        except Exception as e:
            print(f"[messaging] Send failed: {e}")
            return False

