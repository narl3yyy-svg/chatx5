#!/usr/bin/env python3
"""One-shot extractor: split chatx5/web/server.py into structural modules."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "chatx5" / "web" / "server.py"

# Methods grouped by destination mixin (order preserved within each group).
GROUPS: list[tuple[str, str, list[str]]] = [
    (
        "chatx5/web/rns_utils.py",
        "MODULE",
        [],  # filled from pre-class lines
    ),
    (
        "chatx5/web/history_store.py",
        "HistoryStoreMixin",
        [
            "_remove_history_message",
            "_enrich_message",
            "_is_session_system_message",
            "_prune_stale_session_system_messages",
            "_session_peer_at",
            "_history_for_peer",
            "_history_file",
            "_history_peer",
            "_should_persist_history",
            "_persisted_history_entries",
            "_load_history",
            "_save_history",
            "_prune_ephemeral_history_disk",
            "_apply_retention",
            "_history_peer_aliases",
            "_history_matches_peer",
            "_clear_history_for_peer",
            "handle_history_clear",
            "handle_delete_message",
            "handle_history",
            "_history_maintenance_loop",
        ],
    ),
    (
        "chatx5/web/settings_store.py",
        "SettingsStoreMixin",
        [
            "load_settings",
            "save_settings",
            "_settings_api_payload",
            "_abs_path_hint",
            "_normalize_received_dir",
            "_apply_received_dir",
            "_pick_directory_start",
            "_pick_directory_subprocess",
            "_pick_directory_native",
            "handle_settings_get",
            "handle_browse_dir",
            "handle_settings_post",
            "_reload_identity_runtime",
            "handle_regenerate_identity",
            "_reload_server_runtime",
        ],
    ),
    (
        "chatx5/web/messaging_bridge.py",
        "MessagingBridgeMixin",
        [
            "_peer_endpoint_for_transfer",
            "_resolve_incoming_peer",
            "_resolve_peer_hash",
            "_received_dir",
            "_sent_dir",
            "_encode_file_rel",
            "_file_url",
            "_on_message",
            "_queue_target_hash",
            "_is_saved_contact",
            "_clear_queue_for_peer",
            "_purge_ephemeral_peer",
            "_enable_discovery",
            "_on_beacon_periodic",
            "_apply_auto_announce_settings",
            "_contact_name_for",
            "_peer_display_name",
            "_notification_preview",
            "_should_android_notify",
            "_on_queue_sent",
            "_current_peer_for_ip",
            "_peer_is_current",
            "_peer_matches_transport",
            "_contact_hash_for_transport",
            "_resolve_current_peer_hash",
            "_on_transfer_revoked",
            "_on_link_closed",
            "_on_link_established",
            "_on_transfer_progress",
            "_make_progress_callback",
        ],
    ),
    (
        "chatx5/web/peer_connect.py",
        "PeerConnectMixin",
        [
            "_discovery_peer_for_connect",
            "_resolve_connect_target",
            "handle_connect",
            "_reverse_connect_task",
            "handle_request_connect",
            "_peer_in_discovery",
            "_peer_connect_meta",
            "_resolve_peer_connect_ip",
            "_resume_session_task",
            "_link_failover_loop",
        ],
    ),
    (
        "chatx5/web/rns_lifecycle.py",
        "RNSLifecycleMixin",
        [
            "_interfaces_for_api",
            "_write_rns_config",
            "_log_serial_diagnostics",
            "start_rns",
            "_schedule_android_lan_announce_retries",
            "_beacon_payload",
            "_platform_name",
            "_reset_network_state",
            "_maybe_auto_reset_network_stats",
            "handle_network_reset",
            "handle_network_repair",
            "_disable_rns_serial_interfaces",
            "_serial_watchdog_loop",
            "handle_rns_interfaces_get",
            "handle_rns_interfaces_add",
            "handle_rns_interfaces_delete",
            "handle_serial_ports_get",
            "handle_serial_usb_permission",
            "handle_rns_interfaces_update",
            "handle_network",
            "handle_interfaces_get",
            "handle_network_status",
            "handle_path_wake",
            "handle_lan_transfer",
            "_perform_announce",
            "handle_announce",
            "handle_disconnect",
            "_init_rns_background",
            "_embedded_init_rns",
            "_prepare_listen_ports",
        ],
    ),
    (
        "chatx5/web/background_tasks.py",
        "BackgroundTasksMixin",
        [
            "_probe_interval_s",
            "_apply_probe_interval_settings",
            "_probe_discovered_peers",
            "_peer_probe_loop",
            "_discovery_broadcaster",
            "_queue_retry_loop",
        ],
    ),
    (
        "chatx5/web/routes/static_routes.py",
        "StaticRoutesMixin",
        ["_static_dir", "handle_index", "handle_static"],
    ),
    (
        "chatx5/web/routes/identity_routes.py",
        "IdentityRoutesMixin",
        ["handle_identity"],
    ),
    (
        "chatx5/web/routes/contacts_routes.py",
        "ContactRoutesMixin",
        ["handle_add_contact", "handle_delete_contact"],
    ),
    (
        "chatx5/web/routes/discovery_routes.py",
        "DiscoveryRoutesMixin",
        ["handle_discover", "handle_discover_refresh"],
    ),
    (
        "chatx5/web/routes/transfers_routes.py",
        "TransferRoutesMixin",
        [
            "handle_file_upload",
            "handle_folder_upload",
            "handle_transfer_cancel",
            "handle_voice_upload",
            "handle_play_voice",
            "handle_serve_file",
        ],
    ),
    (
        "chatx5/web/routes/queue_routes.py",
        "QueueRoutesMixin",
        ["handle_queue", "handle_queue_clear"],
    ),
    (
        "chatx5/web/routes/debug_routes.py",
        "DebugRoutesMixin",
        ["handle_debug", "handle_debug_export"],
    ),
    (
        "chatx5/web/routes/system_routes.py",
        "SystemRoutesMixin",
        [
            "_spawn_unix_server_restart",
            "handle_restart",
            "handle_temperature",
            "handle_cpu",
            "handle_brand_logo_get",
            "handle_brand_logo_upload",
            "handle_brand_logo_delete",
            "handle_release_notes",
            "handle_health",
        ],
    ),
]

# Core methods that stay in server.py
CORE_METHODS = {
    "__init__",
    "_clean_hash",
    "_run_blocking",
    "_on_shutdown",
    "_teardown_network_stack",
    "_on_cleanup",
    "_wait_for_rns",
    "_reset_connection_state",
    "_peer_dest_hash",
    "_my_sender_hash",
    "_is_self_hash",
    "_peers_equivalent",
    "_peer_alias_list",
    "_session_chat_peer",
    "_sender_has_serial_path",
    "_interfaces_for_picker",
    "_brand_logo_path",
    "_on_startup",
    "run_embedded",
    "run",
}

# WebSocket methods live in ws/manager.py
WS_METHODS = {
    "_prune_websockets",
    "_ws_client_count",
    "_broadcast_peers",
    "_schedule_peers_broadcast",
    "_broadcast",
    "_schedule_contacts_broadcast",
    "_send_peers_to",
    "handle_websocket",
    "_handle_ws_message",
}

COMMON_HEADER = '''"""Auto-extracted from web/server.py — {doc}."""

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
    serial_interface_online,
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

'''


def parse_methods(lines: list[str], class_start: int) -> dict[str, tuple[int, int]]:
    """Return method_name -> (start_idx, end_idx_exclusive) 0-based line indices."""
    method_starts: list[tuple[int, str]] = []
    for i in range(class_start, len(lines)):
        m = re.match(r"    (async )?def (\w+)", lines[i])
        if m:
            method_starts.append((i, m.group(2)))

    ranges: dict[str, tuple[int, int]] = {}
    for idx, (start, name) in enumerate(method_starts):
        end = method_starts[idx + 1][0] if idx + 1 < len(method_starts) else len(lines)
        # trim trailing blank lines before next def/main
        while end > start and lines[end - 1].strip() == "":
            end -= 1
        ranges[name] = (start, end)
    return ranges


def extract_pre_class(lines: list[str], class_start: int) -> str:
    # Keep imports in server.py; rns_utils gets module-level helpers only
    chunk = []
    in_imports = True
    for i in range(class_start):
        line = lines[i]
        if line.startswith("from chatx5.web.") or line.startswith("from chatx5._version"):
            continue
        if in_imports and (line.startswith("import ") or line.startswith("from ")):
            continue
        if line.strip() == "" and not chunk:
            continue
        in_imports = False
        chunk.append(line)
    return "".join(chunk)


def write_rns_utils(pre_class: str):
    header = '''"""RNS startup utilities, port management, and shared web constants."""

import glob as _glob
import os
import re
import signal
import socket
import subprocess
import sys
import time

from chatx5.utils.helpers import get_config_dir, get_data_dir
from chatx5.utils.platform import is_android, lan_ip as platform_lan_ip

'''
    path = ROOT / "chatx5" / "web" / "rns_utils.py"
    path.write_text(header + pre_class)
    print(f"Wrote {path} ({path.read_text().count(chr(10))+1} lines)")


def write_mixin(rel_path: str, class_name: str, method_blocks: list[str], doc: str):
    if class_name == "MODULE":
        return
    body = COMMON_HEADER.format(doc=doc) + f"\n\nclass {class_name}:\n"
    if not method_blocks:
        body += "    pass\n"
    else:
        for block in method_blocks:
            body += block + "\n"
    path = ROOT / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    print(f"Wrote {path} ({body.count(chr(10))+1} lines)")


def main():
    text = SERVER.read_text()
    lines = text.splitlines(keepends=True)
    class_start = next(i for i, l in enumerate(lines) if l.startswith("class ChatWebServer"))
    ranges = parse_methods(lines, class_start)

    pre_class = extract_pre_class(lines, class_start)
    write_rns_utils(pre_class)

    assigned = set()
    for rel_path, class_name, method_names in GROUPS[1:]:
        blocks = []
        for name in method_names:
            if name not in ranges:
                raise SystemExit(f"Missing method {name} for {rel_path}")
            start, end = ranges[name]
            blocks.append("".join(lines[start:end]))
            assigned.add(name)
        doc = class_name.replace("Mixin", " layer")
        write_mixin(rel_path, class_name, blocks, doc)

    all_methods = set(ranges) - CORE_METHODS - WS_METHODS
    unassigned = all_methods - assigned
    if unassigned:
        raise SystemExit(f"Unassigned methods: {sorted(unassigned)}")

    print("Extraction complete.")


if __name__ == "__main__":
    main()