"""Discovery scope, peer callbacks, and LAN scope change handling."""

import asyncio
import sys

import chatx5.core.discovery as discovery_core
from chatx5.core.contacts import (
    find_contact_by_hash,
    list_contacts,
    migrate_contact_by_ip,
    migrate_contact_hash,
    sync_contact_from_discovery,
    update_contact_endpoint,
    update_contact_transport_hash,
)
from chatx5.core.discovery import normalize_hash, register_identity_from_peer
from chatx5.core.lan_rns import (
    clear_all_lan_paths,
    interface_family,
    peer_path_on_family,
    prune_known_udp_peer_ips,
)
from chatx5.core.messaging import is_hub_peer_hash
from chatx5.core.rns_interfaces import lan_discovery_configured
from chatx5.utils.lan_scope import peer_in_scope
from chatx5.utils.platform import (
    apply_lan_interface_preference,
    discovery_scope_ip,
    enumerate_lan_interfaces,
    invalidate_desktop_interface_cache,
    parse_lan_interface_value,
)


class DiscoveryBridgeMixin:
    """Peer discovery integration: scope, callbacks, and contact sync."""

    def _primary_lan_ip(self):
        from chatx5.web.server import detect_lan_ip
        return detect_lan_ip()

    def _discovery_scope_ip(self):
        settings = self.load_settings()
        if not lan_discovery_configured(settings.get("rns_interfaces")):
            return None
        pinned = (settings.get("lan_interface") or "").strip()
        if pinned:
            name, ip = parse_lan_interface_value(pinned)
            if ip:
                return ip
            for entry in enumerate_lan_interfaces():
                if entry.get("name") == (name or pinned):
                    entry_ip = entry.get("ip")
                    if entry_ip and entry_ip != "disconnected":
                        return entry_ip
        if settings.get("hub_role", "off") != "off":
            return None
        return self._primary_lan_ip() or discovery_scope_ip()

    def _peer_in_discovery_scope(self, peer_hash, link=None):
        from chatx5.core.lan_rns import interface_family as _iface_family

        target = normalize_hash(peer_hash or "")
        if (
            target
            and self.messaging
            and self.messaging._peer_hash_is_serial_endpoint(target)
            and discovery_core.serial_discovery_active()
        ):
            return True

        if link and self.messaging:
            iface = self.messaging._link_attached_interface(link)
            if _iface_family(iface) == "serial":
                return True
            if self.messaging._inbound_link_is_hub_tcp(link):
                return True
            if not iface and self.messaging._serial_inbound_scope_ok(peer_hash, link):
                return True
            if iface and not self.messaging._link_acceptable_for_peer(link, peer_hash):
                return False
        if is_hub_peer_hash(peer_hash):
            return True
        scope = self._discovery_scope_ip()
        if not scope:
            return True
        target = normalize_hash(peer_hash or "")
        if not target:
            return False
        if discovery_core.serial_discovery_active() and getattr(self, "discovery", None):
            for peer in self.discovery.peers.values():
                ph = normalize_hash(peer.get("hash"))
                ih = normalize_hash(peer.get("identity_hash"))
                if target in (ph, ih) and (peer.get("via") or "").strip() == "serial":
                    return True
        if (
            discovery_core.serial_discovery_active()
            and self.messaging
            and peer_path_on_family(target, "serial") is not None
        ):
            return True
        peer_ip = ""
        peer_via = ""
        meta = self._discovery_peer_for_connect(None, target)
        if meta:
            peer_ip = (meta.get("ip") or "").strip()
            peer_via = (meta.get("via") or "").strip()
        if getattr(self, "discovery", None):
            serial_match = None
            other_match = None
            for peer in self.discovery.peers.values():
                ph = normalize_hash(peer.get("hash"))
                ih = normalize_hash(peer.get("identity_hash"))
                if target not in (ph, ih):
                    continue
                via = (peer.get("via") or "").strip()
                if via == "serial":
                    serial_match = peer
                else:
                    other_match = other_match or peer
            chosen = serial_match or other_match
            if chosen:
                peer_ip = (chosen.get("ip") or "").strip()
                peer_via = (chosen.get("via") or "").strip()
        if peer_via == "serial":
            if discovery_core.serial_discovery_active():
                return True
            if peer_ip and peer_in_scope(peer_ip, scope):
                return True
            return False
        if discovery_core.serial_discovery_active() and self.messaging and target:
            link_for_peer = self.messaging._link_for_peer(target)
            if link_for_peer and interface_family(
                self.messaging._link_attached_interface(link_for_peer)
            ) == "serial":
                return True
        if not peer_ip:
            return discovery_core.serial_discovery_active()
        return peer_in_scope(peer_ip, scope)

    def _maybe_apply_live_scope_change(self):
        """Detect OS/pinned LAN scope drift while the server stays up."""
        scope = self._discovery_scope_ip()
        prev = self._live_scope_ip
        self._live_scope_ip = scope
        if prev is None or scope == prev:
            return False
        print(
            f"[network] Live LAN scope drift {prev or '?'} -> {scope or '?'}"
            " — refreshing discovery and paths"
        )
        self._apply_lan_scope_change()
        return True

    def _apply_lan_scope_change(self):
        """Drop links/paths/peers when the user changes LAN IPv4 scope."""
        scope = self._discovery_scope_ip()
        self._live_scope_ip = scope
        prune_known_udp_peer_ips(scope)
        invalidate_desktop_interface_cache(use_powershell=sys.platform == "win32")
        apply_lan_interface_preference(self.config_dir)
        if self.messaging:
            self.messaging.disconnect_all_peers(clear_session=False)
        clear_all_lan_paths()
        if self.discovery:
            removed = self.discovery.refresh_paths_for_scope(scope)
            if removed:
                print(f"[network] Refreshed discovery — removed {removed} stale path(s)")
        if self.lan_beacon:
            self.lan_beacon.ip = self._primary_lan_ip()
        if self.messaging:
            try:
                self.messaging.announce()
            except Exception:
                pass
        self.active_peer = None
        print(
            "[network] LAN scope changed — links cleared"
            + (f" (scope={scope})" if scope else "")
        )

    def _scoped_peers(self):
        if not self.discovery:
            return []
        self.discovery.purge_misclassified_serial()
        self.discovery.purge_ipless_non_serial()
        return self.discovery.get_peers(scope_ip=self._discovery_scope_ip())

    def _sync_discovery_local_hashes(self):
        """Teach discovery to ignore our own LAN + serial hashes (USB loopback)."""
        if not self.discovery:
            return
        hashes = []
        if self.messaging:
            hashes.append(getattr(self.messaging, "my_dest_hash", None))
            hashes.append(getattr(self.messaging, "my_dest_hash_serial", None))
        if self.identity_mgr:
            hashes.append(self.identity_mgr.get_hex_hash("lan"))
            hashes.append(self.identity_mgr.get_hex_hash("serial"))
            hashes.append(self.identity_mgr.get_connect_hash("lan"))
            hashes.append(self.identity_mgr.get_connect_hash("serial"))
        hashes.append(self._clean_hash(self.destination_hash))
        self.discovery.set_local_hashes(*hashes)

    def _on_peer_evicted(self, removed_hashes, new_peer=None):
        if not removed_hashes:
            return
        self._supersede_peer_hashes(removed_hashes, new_peer)

    def _supersede_peer_hashes(self, removed_hashes, new_peer=None):
        removed_clean = []
        for raw in removed_hashes:
            clean = self._peer_dest_hash(raw)
            if clean:
                removed_clean.append(clean)
        if not removed_clean:
            return

        replacement = None
        if new_peer:
            replacement = self._peer_dest_hash(new_peer.get("hash"))
            ip = new_peer.get("ip")
            if ip:
                migrate_contact_by_ip(
                    self.config_dir,
                    ip,
                    replacement,
                    name=new_peer.get("name"),
                    port=new_peer.get("port"),
                    identity_hash=new_peer.get("identity_hash"),
                )

        skip_path_purge = False
        if self.messaging and getattr(self.messaging, "_connect_in_progress", False):
            session = self.messaging.dest_hash_for(
                self.messaging._session_peer_hash
                or self.messaging.active_peer_hash
                or ""
            )
            if session:
                for old_hash in removed_clean:
                    if self._peers_equivalent(old_hash, session):
                        skip_path_purge = True
                        break
                    if new_peer and new_peer.get("identity_hash"):
                        if self._peers_equivalent(old_hash, new_peer.get("identity_hash")):
                            skip_path_purge = True
                            break
        if not skip_path_purge:
            try:
                from chatx5.core.peer_identity import purge_rns_paths_for_hashes
                purge_rns_paths_for_hashes(removed_clean)
            except Exception:
                pass

        for old in removed_clean:
            same_peer = bool(
                replacement
                and (
                    self._peers_equivalent(old, replacement)
                    or (
                        new_peer
                        and new_peer.get("identity_hash")
                        and self._peers_equivalent(old, new_peer.get("identity_hash"))
                    )
                )
            )
            still_linked = bool(
                self.messaging
                and (
                    self.messaging._peer_link_active(old)
                    or (replacement and self.messaging._peer_link_active(replacement))
                )
            )
            if self.messaging:
                if replacement:
                    self.messaging.register_peer_mapping(
                        replacement,
                        (new_peer or {}).get("identity_hash"),
                    )
                    self.messaging.register_peer_mapping(
                        old,
                        (new_peer or {}).get("identity_hash") or replacement,
                    )
                if still_linked and replacement:
                    canon = replacement
                    if self.messaging.active_peer_hash and self._peers_equivalent(
                        self.messaging.active_peer_hash, old
                    ):
                        self.messaging.active_peer_hash = canon
                    if self.messaging._session_peer_hash and self._peers_equivalent(
                        self.messaging._session_peer_hash, old
                    ):
                        self.messaging._session_peer_hash = canon
                    link = (
                        self.messaging._link_for_peer(replacement)
                        or self.messaging._link_for_peer(old)
                        or self.messaging.active_link
                    )
                    if link:
                        self.messaging._register_peer_link(link, canon)
                        self.messaging._cache_link_peer(link, canon)
                elif not same_peer:
                    self.messaging.disconnect_peer(old, transport="lan")
                    self.messaging.disconnect_peer(old, transport="serial")
                    self.messaging.clear_queue(old)
            new_via = ((new_peer or {}).get("via") or "").strip().lower()
            contact = find_contact_by_hash(self.config_dir, old)
            if same_peer and replacement:
                migrate_contact_hash(
                    self.config_dir,
                    old,
                    replacement,
                    name=(new_peer or {}).get("name"),
                    ip=(new_peer or {}).get("ip"),
                    port=(new_peer or {}).get("port"),
                    identity_hash=(new_peer or {}).get("identity_hash"),
                    via=new_via or None,
                )
            elif contact and replacement:
                update_contact_transport_hash(
                    self.config_dir,
                    old,
                    replacement,
                    via=new_via or None,
                    name=(new_peer or {}).get("name"),
                    ip=(new_peer or {}).get("ip"),
                    port=(new_peer or {}).get("port"),
                    identity_hash=(new_peer or {}).get("identity_hash"),
                )
                self._schedule_contacts_broadcast()
            elif contact:
                self._schedule_contacts_broadcast()
            else:
                self._clear_history_for_peer(old)
                self._clear_queue_for_peer(old)
            if self.active_peer and self._peers_equivalent(self.active_peer, old):
                self.active_peer = replacement
            if self._ui_state.get("viewing_peer") and self._peers_equivalent(
                self._ui_state.get("viewing_peer"), old
            ):
                self._ui_state["viewing_peer"] = replacement

        if self.websockets and self._loop:
            asyncio.run_coroutine_threadsafe(
                self._broadcast({
                    "type": "peer_superseded",
                    "data": {
                        "removed": removed_clean,
                        "replacement": replacement,
                        "replacement_peer": new_peer,
                    },
                }),
                self._loop,
            )
            peers = self._scoped_peers()
            asyncio.run_coroutine_threadsafe(
                self._broadcast({"type": "peers", "data": peers}),
                self._loop,
            )
        print(
            f"[discovery] Superseded {len(removed_clean)} stale peer hash(es)"
            + (f" -> {replacement[:16]}..." if replacement else "")
        )

    def _on_peer_discovered(self, peer):
        if not self.messaging:
            return
        register_identity_from_peer(peer)
        dest = self.messaging._hash_from_peer_info(peer) or self._peer_dest_hash(peer.get("hash"))
        if dest and dest != peer.get("hash"):
            peer = dict(peer)
            peer["hash"] = dest
        if peer.get("identity_hash"):
            self.messaging.register_peer_mapping(dest, peer.get("identity_hash"))
        contacts_dirty = False
        peer_record = dict(peer)
        peer_record["hash"] = dest
        synced = sync_contact_from_discovery(
            self.config_dir,
            peer_record,
            peers_equivalent=self._peers_equivalent,
            local_scope_ip=self._discovery_scope_ip(),
        )
        if synced:
            contacts_dirty = True
        elif peer.get("ip") and any(
            (c.get("ip") or "").strip() == peer.get("ip")
            for c in list_contacts(self.config_dir)
        ):
            migrate_contact_by_ip(
                self.config_dir,
                peer.get("ip"),
                dest,
                name=peer.get("name"),
                port=peer.get("port"),
                identity_hash=peer.get("identity_hash"),
            )
            contacts_dirty = True
        contact_updated = update_contact_endpoint(
            self.config_dir,
            dest,
            ip=peer.get("ip"),
            port=peer.get("port"),
            identity_hash=peer.get("identity_hash"),
            peers_equivalent=self._peers_equivalent,
            name=peer.get("name"),
            local_scope_ip=self._discovery_scope_ip(),
        )
        if contact_updated:
            contacts_dirty = True
        if contacts_dirty:
            self._schedule_contacts_broadcast()
        if self.discovery and self.websockets and self._loop:
            asyncio.run_coroutine_threadsafe(
                self._broadcast_peers(authoritative=True),
                self._loop,
            )

    def _register_link_peer_in_discovery(self, peer_hash, peer_ip=None, link=None):
        if not self.discovery or not peer_hash:
            return
        name = self._peer_display_name(peer_hash) or peer_hash[:8]
        settings = self.load_settings()
        via = "tcp_hub" if settings.get("hub_role", "off") != "off" else "link"
        if link and self.messaging:
            if interface_family(self.messaging._link_attached_interface(link)) == "serial":
                via = "serial"
        self.discovery.register_link_peer(
            peer_hash, name=name, via=via, ip=peer_ip,
        )