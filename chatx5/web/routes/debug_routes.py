"""Auto-extracted from web/server.py — DebugRoutes layer."""

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



class DebugRoutesMixin:
    async def handle_debug(self, request):
        peers = self._scoped_peers()
        settings = self.load_settings()
        received_dir = settings.get("received_dir", os.path.join(self.config_dir, "received"))
        payload = {
            "identity_hash": self.identity_mgr.get_hex_hash() if self.identity_mgr else None,
            "ws_clients": self._ws_client_count(),
            "discovered_peers": peers,
            "discovery_running": self.discovery.running if self.discovery else False,
            "discovery_active": bool(self.discovery and self.discovery.accept_peers),
            "lan_beacon_port": BEACON_PORT,
            "lan_beacon_running": bool(self.lan_beacon and self.lan_beacon.running),
            "lan_beacon_targets": self.lan_beacon.last_send_targets if self.lan_beacon else [],
            "lan_beacon_sent": self.lan_beacon.packets_sent if self.lan_beacon else 0,
            "lan_beacon_received": self.lan_beacon.packets_received if self.lan_beacon else 0,
            "active_peer": self.active_peer,
            "message_count": len(self.message_history),
            "loop_running": self._loop is not None and self._loop.is_running(),
            "rns_interfaces": len(RNS.Transport.interfaces) if hasattr(RNS.Transport, 'interfaces') else "unknown",
            "received_files_dir": received_dir,
            "settings": settings,
        }
        if is_android():
            payload["debug_log_path"] = debug_log_path()
            payload["debug_log_files"] = list_debug_log_files()
            tail = debug_log_tail()
            if tail:
                payload["debug_log_tail"] = tail
        return web.json_response(payload)

    async def handle_debug_export(self, request):
        if not is_android():
            return web.json_response(
                {"error": "Debug log export is for Android debug builds"},
                status=400,
            )
        try:
            data = await request.json()
            dest = (data.get("path") or "").strip()
            if not dest:
                return web.json_response({"error": "path required"}, status=400)
            copied, err = await asyncio.to_thread(export_debug_logs, dest)
            if err and copied == 0:
                return web.json_response({"error": err}, status=400)
            return web.json_response({
                "status": "ok",
                "copied": copied,
                "path": dest,
                "warning": err,
            })
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=500)

