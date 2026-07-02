"""Auto-extracted from web/server.py — DiscoveryRoutes layer."""

import asyncio
import base64
import json
import mimetypes
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zipfile
from pathlib import Path
from urllib.parse import quote, unquote

from aiohttp import web
import RNS

from chatx5._version import __version__ as APP_VERSION
from chatx5.core.contacts import (
    contact_connect_meta,
    contact_has_hash,
    delete_contact as delete_saved_contact,
    find_contact_by_hash,
    list_contacts,
    save_contact,
)
from chatx5.core.discovery import PeerDiscovery
from chatx5.core.lan_beacon import LanBeacon, BEACON_PORT
from chatx5.core.messaging import HUB_GROUP_PEER, MessagingBackend, is_hub_peer_hash
from chatx5.core.messaging.constants import MESSAGE_TYPE_SHARE_BROWSE
from chatx5.core.rns_interfaces import (
    INTERFACE_PRESETS,
    SERIAL_BAUD_RATES,
    SERIAL_DEFAULT_BAUD,
    ANDROID_SERIAL_PERMISSION_HINT,
    SERIAL_PERMISSION_HINT,
    add_interface,
    configured_serial_enabled,
    configured_serial_port,
    configured_tcp_lan_enabled,
    configured_udp_lan_enabled,
    delete_interface,
    dedupe_serial_interfaces,
    ensure_runtime_serial,
    ensure_runtime_tcp_lan_server,
    lan_discovery_configured,
    lan_transport_hub_policy,
    list_serial_ports,
    normalize_interface_list,
    prune_dead_serial_interfaces,
    remove_serial_interfaces,
    render_rns_config,
    serial_permission_hint_for_process,
    serial_port_status,
    serial_runtime_active,
    tcp_client_target_warning,
    update_interface,
    user_has_serial_group_access,
)
from chatx5.core.voice import VoiceRecorder, VoicePlayer
from chatx5.utils.debug_log import (
    debug_log_path,
    debug_log_tail,
    export_debug_logs,
    list_debug_log_files,
)
from chatx5.utils.file_serve import stream_file_response
from chatx5.utils.helpers import (
    format_speed,
    media_type_for_filename,
    safe_basename,
    safe_path_under,
    safe_rel_path_under,
)
from chatx5.utils.android_notify import show_message_notification
from chatx5.utils.platform import (
    apply_lan_interface_preference,
    desktop_lan_status,
    effective_display_name,
    enumerate_lan_interfaces,
    host_platform,
    invalidate_desktop_interface_cache,
    is_android,
    lan_connected,
    lan_ip as platform_lan_ip,
    list_network_interfaces,
    parse_lan_interface_value,
    patch_embedded_signals,
    physical_lan_reachable,
    set_lan_interface_preference,
)
from chatx5.core.lan_rns import (
    lan_ip_reachable,
    patch_udp_interface_unicast,
    serial_interface_online as rns_serial_online,
)
from chatx5.web.rns_utils import (
    CONFIG_DIR,
    DATA_DIR,
    NETWORK_STATS_AUTO_RESET_SEC,
    SESSION_SYSTEM_LINK_CLOSED_TTL,
    SETTINGS_FILE,
    detect_lan_ip,
    ensure_rns_ports_free,
    shutdown_rns_stack,
    stop_stale_chatx5_servers,
)



class DiscoveryRoutesMixin:
    async def handle_discover(self, request):
        peers = self._scoped_peers()
        if self.active_peer and not any(p.get("hash", "").replace(":", "") == self.active_peer for p in peers):
            peers.append({
                "hash": self.active_peer,
                "name": self.active_peer[:8],
                "app": "chatx5",
                "connected": True,
            })
        return web.json_response({"peers": peers})

    async def handle_discover_refresh(self, request):
        """Re-announce, purge stale discovery rows, and return an authoritative peer list."""
        ok, err = await self._wait_for_rns()
        if not ok:
            return web.json_response({"error": err or "not ready"}, status=400)
        if self.discovery:
            self.discovery.purge_misclassified_serial()
            self.discovery.purge_ipless_non_serial()
            self.discovery.purge_stale_probes()
        settings = self.load_settings()
        configured = settings.get("rns_interfaces")
        try:
            if self.messaging:
                if lan_discovery_configured(configured) and lan_ip_reachable():
                    await asyncio.to_thread(
                        self.messaging._silent_announce, also_serial=False,
                    )
                if configured_serial_enabled(configured) and serial_interface_online():
                    await asyncio.to_thread(
                        self.messaging._burst_serial_announce, 1, force=True,
                    )
            if self.lan_beacon and lan_ip_reachable():
                await asyncio.to_thread(self.lan_beacon.send, 1, is_android())
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=500)
        peers = await self._broadcast_peers(authoritative=True)
        print(f"[discovery] Manual refresh — {len(peers)} peer(s)")
        return web.json_response({"peers": peers, "count": len(peers)})

