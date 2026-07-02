"""Auto-extracted from web/server.py — StaticRoutes layer."""

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



class StaticRoutesMixin:
    def _static_dir(self):
        candidates = []
        if getattr(sys, "frozen", False):
            meipass = getattr(sys, "_MEIPASS", None)
            if meipass:
                candidates.append(Path(meipass) / "chatx5" / "web" / "static")
        candidates.extend([
            Path(__file__).parent / "static",
            Path.cwd() / "chatx5" / "web" / "static",
            Path.cwd() / "static",
        ])
        if is_android():
            candidates.append(Path(__file__).resolve().parent / "static")
        for p in candidates:
            if p.exists() and (p / "index.html").exists():
                return p
        return candidates[0]

    async def handle_index(self, request):
        static_dir = self._static_dir()
        index_path = static_dir / "index.html"
        if not index_path.exists():
            return web.Response(text="Frontend not found", status=500)
        resp = web.FileResponse(index_path)
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        resp.headers["Pragma"] = "no-cache"
        return resp

    async def handle_static(self, request):
        static_dir = self._static_dir()
        filepath = static_dir / request.match_info["filename"]
        if not filepath.exists() or not filepath.is_file():
            return web.Response(text="Not found", status=404)
        ct, _ = mimetypes.guess_type(str(filepath))
        resp = web.FileResponse(filepath)
        if ct:
            resp.headers['Content-Type'] = ct
        return resp

