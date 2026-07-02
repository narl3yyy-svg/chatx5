"""Auto-extracted from web/server.py — IdentityRoutes layer."""

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



class IdentityRoutesMixin:
    async def handle_identity(self, request):
        if not self.identity_mgr.identity:
            try:
                self.identity_mgr.load_or_create()
            except Exception:
                pass
        from chatx5.core.discovery import normalize_hash
        from chatx5.core.peer_identity import connect_hash_for_manager

        connect = ""
        if self.messaging and self.messaging.my_dest_hash:
            connect = normalize_hash(self.messaging.my_dest_hash)
        elif self.messaging and self.messaging.destination:
            connect = normalize_hash(RNS.hexrep(self.messaging.destination.hash))
        else:
            connect = connect_hash_for_manager(
                self.identity_mgr,
                getattr(self.messaging, "destination", None) if self.messaging else None,
            )
        if not connect:
            connect = normalize_hash(self.destination_hash or "")
        if not connect:
            connect = self.identity_mgr.get_connect_hash()
        identity_raw = normalize_hash(self.identity_mgr.get_hex_hash() if self.identity_mgr else "")
        contacts = list_contacts(self.config_dir)
        discovered = self._scoped_peers()
        link_active = bool(self.messaging and self.messaging.active_link)
        connected = self.active_peer if link_active and self.active_peer else None
        linked_peers = self.messaging.linked_peers() if self.messaging else []
        settings = self.load_settings()
        id_payload = self.identity_mgr.identity_payload() if hasattr(self.identity_mgr, "identity_payload") else {}
        serial_connect = ""
        if self.messaging and getattr(self.messaging, "my_dest_hash_serial", None):
            serial_connect = normalize_hash(self.messaging.my_dest_hash_serial)
        elif id_payload.get("serial"):
            serial_connect = normalize_hash(id_payload["serial"].get("connect_hash") or "")
        configured = settings.get("rns_interfaces")
        serial_port, _ = configured_serial_port(configured) if configured else ("", 0)
        serial_configured = bool(
            configured and configured_serial_enabled(configured)
        )
        serial_in_rns = bool(serial_port and serial_interface_online(serial_port))
        serial_active = serial_configured or serial_in_rns
        return web.json_response({
            "hash": connect,
            "connect_hash": connect,
            "identity_hash": identity_raw,
            "lan": id_payload.get("lan") or {"connect_hash": connect, "identity_hash": identity_raw},
            "serial": id_payload.get("serial") or (
                {"connect_hash": serial_connect, "identity_hash": self.identity_mgr.get_hex_hash("serial")}
                if serial_connect or self.identity_mgr.get_hex_hash("serial") else None
            ),
            "name": settings.get("name", ""),
            "connected": connected,
            "linked_peers": linked_peers,
            "contacts": contacts,
            "discovered": discovered,
            "platform": self._platform_name(),
            "app_version": APP_VERSION,
            "rns_ready": bool(self.messaging and self.messaging.destination),
            "rns_error": self.rns_init_error,
            "debug_log_path": debug_log_path() if is_android() else None,
            "serial_active": serial_active,
            "serial_configured": serial_configured,
            "serial_in_rns": serial_in_rns,
        })

