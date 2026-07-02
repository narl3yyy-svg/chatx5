"""Auto-extracted from web/server.py — SettingsStore layer."""

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
    _pick_directory_tkinter,
    detect_lan_ip,
    ensure_rns_ports_free,
    shutdown_rns_stack,
    stop_stale_chatx5_servers,
)



class SettingsStoreMixin:
    def load_settings(self):
        defaults = {
            "name": "",
            "history_retention": "never",
            "received_dir": os.path.join(self.config_dir, "received"),
            "network_stats_auto_reset": True,
            "network_stats_reset_at": 0,
            "lan_interface": "",
            "rns_interfaces": normalize_interface_list(None),
            "hub_role": "off",
            "hub_host": "",
            "hub_port": 4242,
            "hub_server_hash": "",
            "auto_announce": False,
            "probe_interval_s": 30,
            "lan_announce_interval_s": 0,
            "serial_announce_interval_s": 0,
            "lan_probe_interval_s": 30,
            "serial_probe_interval_s": 30,
            "brand_title": "",
            "setup_complete": False,
            "last_release_notes_seen": "",
            "max_peer_links": 0,
        }
        try:
            with open(SETTINGS_FILE) as f:
                s = json.load(f)
                for key, val in defaults.items():
                    s.setdefault(key, val)
                if s.get("auto_announce") and not s.get("lan_announce_interval_s"):
                    s["lan_announce_interval_s"] = 30
                    s["serial_announce_interval_s"] = 30
                if "lan_probe_interval_s" not in s and "probe_interval_s" in s:
                    s["lan_probe_interval_s"] = s["probe_interval_s"]
                    s["serial_probe_interval_s"] = s["probe_interval_s"]
                s.pop("auto_interface_enabled", None)
                needs_udp = standalone_needs_udp(
                    s.get("rns_interfaces"), s.get("hub_role", "off")
                )
                if needs_udp:
                    s["rns_interfaces"] = normalize_interface_list(None)
                    self.save_settings(s)
                repaired = normalize_interface_list(s.get("rns_interfaces"))
                if repaired != s.get("rns_interfaces"):
                    s["rns_interfaces"] = repaired
                    self.save_settings(s)
                    self._write_rns_config(s)
                apply_lan_interface_preference(self.config_dir)
                return s
        except:
            apply_lan_interface_preference(self.config_dir)
            return dict(defaults)

    def save_settings(self, settings):
        os.makedirs(self.config_dir, exist_ok=True)
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)

    def _settings_api_payload(self, settings):
        from chatx5.release_notes import release_notes_payload
        payload = dict(settings)
        payload["app_version"] = APP_VERSION
        payload.update(release_notes_payload())
        hub_role = settings.get("hub_role", "off")
        payload["lan_transport_hub_tcp"] = lan_transport_hub_policy(hub_role, "tcp_lan")
        return payload

    def _abs_path_hint(self):
        if sys.platform == "win32":
            return "C:\\Users\\you\\Downloads"
        return "/home/user/Downloads"

    def _normalize_received_dir(self, raw):
        path = (raw or "").strip()
        if not path:
            return None, "Path is empty"
        path = os.path.expanduser(path)
        if sys.platform == "win32":
            if re.match(r"^[A-Za-z]:[^\\/]", path):
                path = path[:2] + "\\" + path[2:]
            path = os.path.normpath(path.replace("/", "\\"))
        else:
            path = os.path.normpath(path)
        if not os.path.isabs(path):
            for base in (self.config_dir, os.path.expanduser("~"), os.getcwd()):
                if not base:
                    continue
                candidate = os.path.normpath(os.path.join(base, path))
                if os.path.isdir(candidate):
                    path = candidate
                    break
            else:
                hint = self._abs_path_hint()
                return None, f"Path must be absolute (e.g. {hint})"
        if is_android() and path.startswith("/storage/"):
            try:
                os.makedirs(path, exist_ok=True)
            except OSError as e:
                return None, f"Cannot use folder: {e}"
            if os.path.isdir(path):
                return path, None
            return None, "Path is not a directory"
        try:
            os.makedirs(path, exist_ok=True)
        except OSError as e:
            return None, f"Cannot create directory: {e}"
        if not os.path.isdir(path):
            return None, "Path is not a directory"
        return path, None

    def _apply_received_dir(self, settings):
        received_dir = settings.get("received_dir")
        if not received_dir:
            return
        path, err = self._normalize_received_dir(received_dir)
        if err:
            return
        settings["received_dir"] = path
        if self.messaging:
            self.messaging.receive_dir = path

    def _pick_directory_start(self):
        settings = self.load_settings()
        start = settings.get("received_dir", os.path.join(self.config_dir, "received"))
        start = os.path.expanduser(start)
        if not os.path.isdir(start):
            start = os.path.expanduser("~")
        return start

    def _pick_directory_subprocess(self):
        """Run folder picker in a child process (keeps asyncio responsive on Windows)."""
        start = self._pick_directory_start()
        root = os.environ.get("CHATX5_ROOT") or os.getcwd()
        script = os.path.join(root, "scripts", "pick-folder.py")
        if not os.path.isfile(script):
            return self._pick_directory_native()
        try:
            flags = _win_subprocess_flags()
            if sys.platform == "win32":
                flags = 0
            result = subprocess.run(
                [sys.executable, script, start],
                capture_output=True,
                text=True,
                timeout=300,
                cwd=root,
                creationflags=flags,
            )
            picked = (result.stdout or "").strip()
            if result.returncode == 0 and picked:
                return os.path.normpath(picked)
        except Exception as exc:
            print(f"[browse] Folder picker subprocess failed: {exc}")
        return None

    def _pick_directory_native(self):
        if is_android():
            return None
        start = self._pick_directory_start()

        if sys.platform in ("win32", "darwin"):
            try:
                from chatx5.utils.folder_picker import pick_folder
                picked = pick_folder(start)
                if picked:
                    return os.path.normpath(picked)
            except Exception:
                pass
        if sys.platform == "darwin":
            picked = _pick_directory_tkinter(start)
            if picked:
                return os.path.normpath(picked)
        if sys.platform == "win32":
            picked = _pick_directory_tkinter(start)
            if picked:
                return os.path.normpath(picked)

        commands = []
        if shutil.which("zenity"):
            commands.append(["zenity", "--file-selection", "--directory", f"--filename={start}/"])
        if shutil.which("kdialog"):
            commands.append(["kdialog", "--getexistingdirectory", start])
        if shutil.which("yad"):
            commands.append(["yad", "--file", "--directory", f"--filename={start}"])

        for cmd in commands:
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
                if result.returncode == 0:
                    picked = result.stdout.strip()
                    if picked:
                        return os.path.normpath(picked)
            except Exception:
                continue
        return None

    async def handle_settings_get(self, request):
        settings = self._apply_hub_settings(self.load_settings())
        return web.json_response(self._settings_api_payload(settings))

    async def handle_browse_dir(self, request):
        try:
            if request.method == "POST":
                data = await request.json()
                picked = (data.get("path") or "").strip()
                if not picked:
                    return web.json_response({"error": "path required"}, status=400)
                path, err = self._normalize_received_dir(picked)
                if err:
                    return web.json_response({"error": err}, status=400)
                return web.json_response({"path": path})

            if is_android():
                settings = self.load_settings()
                return web.json_response({
                    "platform": "android",
                    "options": android_storage_dirs(),
                    "current": settings.get("received_dir", os.path.join(self.config_dir, "received")),
                })

            if sys.platform == "win32":
                picked = await asyncio.to_thread(self._pick_directory_subprocess)
            else:
                picked = await asyncio.to_thread(self._pick_directory_native)
            if not picked:
                return web.json_response({
                    "error": "cancelled",
                    "platform": self._platform_name(),
                }, status=400)
            path, err = self._normalize_received_dir(picked)
            if err:
                return web.json_response({"error": err}, status=400)
            return web.json_response({"path": path, "platform": self._platform_name()})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def handle_settings_post(self, request):
        try:
            data = await request.json()
            settings = self.load_settings()
            if "name" in data:
                settings["name"] = data["name"].strip()[:50]
            if "history_retention" in data:
                valid = ["1d", "1w", "1m", "6m", "12m", "never", "on_restart", "on_close"]
                if data["history_retention"] in valid:
                    settings["history_retention"] = data["history_retention"]
            if "received_dir" in data:
                raw_dir = (data.get("received_dir") or "").strip()
                if not raw_dir:
                    settings["received_dir"] = os.path.join(self.config_dir, "received")
                else:
                    path, err = self._normalize_received_dir(raw_dir)
                    if err:
                        return web.json_response({"error": err}, status=400)
                    settings["received_dir"] = path
            if "network_stats_auto_reset" in data:
                settings["network_stats_auto_reset"] = bool(data["network_stats_auto_reset"])
            if "hub_role" in data:
                role = (data.get("hub_role") or "off").strip().lower()
                if role in ("off", "server", "client"):
                    settings["hub_role"] = role
            if "hub_host" in data:
                settings["hub_host"] = (data.get("hub_host") or "").strip()
            if "hub_port" in data and data.get("hub_port") is not None:
                try:
                    settings["hub_port"] = int(data["hub_port"])
                except (TypeError, ValueError):
                    pass
            if "hub_server_hash" in data:
                settings["hub_server_hash"] = (data.get("hub_server_hash") or "").strip()
            config_dirty = False
            if "lan_transport" in data:
                preset = (data.get("lan_transport") or "").strip()
                if preset in ("udp_lan", "tcp_lan"):
                    policy = lan_transport_hub_policy(
                        settings.get("hub_role", "off"), preset
                    )
                    if not policy.get("allowed", True):
                        return web.json_response(
                            {"error": policy.get("warning") or "TCP LAN unavailable"},
                            status=400,
                        )
                    settings["rns_interfaces"] = set_primary_lan_transport(
                        settings.get("rns_interfaces"), preset
                    )
                    config_dirty = True
            lan_scope_changed = False
            if "lan_interface" in data:
                settings["lan_interface"] = (data.get("lan_interface") or "").strip()
                set_lan_interface_preference(settings["lan_interface"])
                config_dirty = True
                lan_scope_changed = True
            if "lan_interface" in data and not (data.get("lan_interface") or "").strip():
                return web.json_response(
                    {"error": "LAN IPv4 interface is required — pick an address from the list"},
                    status=400,
                )
            from chatx5.core.peer_probe import (
                clamp_announce_interval,
                clamp_probe_interval,
                clamp_serial_probe_interval,
            )
            for key in ("lan_probe_interval_s", "serial_probe_interval_s", "probe_interval_s"):
                if key in data:
                    if key == "serial_probe_interval_s":
                        val = clamp_serial_probe_interval(data.get(key))
                    elif key == "probe_interval_s":
                        val = clamp_probe_interval(data.get(key))
                    else:
                        val = clamp_probe_interval(data.get(key))
                    if key == "probe_interval_s":
                        settings["probe_interval_s"] = val
                        settings["lan_probe_interval_s"] = val
                        settings["serial_probe_interval_s"] = clamp_serial_probe_interval(val)
                    else:
                        settings[key] = val
            settings.pop("lan_probe_packet_bytes", None)
            if "brand_title" in data:
                settings["brand_title"] = str(data.get("brand_title") or "").strip()[:18]
            for key in ("lan_announce_interval_s", "serial_announce_interval_s"):
                if key in data:
                    settings[key] = clamp_announce_interval(data.get(key))
            if "auto_announce" in data:
                settings["auto_announce"] = bool(data["auto_announce"])
            if "setup_complete" in data:
                settings["setup_complete"] = bool(data["setup_complete"])
            if "last_release_notes_seen" in data:
                settings["last_release_notes_seen"] = (
                    str(data.get("last_release_notes_seen") or "").strip()
                )
            if "max_peer_links" in data:
                try:
                    limit = int(data.get("max_peer_links") or 0)
                except (TypeError, ValueError):
                    limit = 0
                settings["max_peer_links"] = max(0, min(64, limit))
            hub_changed = any(
                k in data for k in ("hub_role", "hub_host", "hub_port")
            )
            if settings.get("hub_role") == "client" and not (settings.get("hub_host") or "").strip():
                return web.json_response(
                    {"error": "Hub host IP is required for client mode"},
                    status=400,
                )
            settings = self._apply_hub_settings(settings)
            self.save_settings(settings)
            if "max_peer_links" in data and self.messaging:
                self.messaging.max_peer_links = int(settings.get("max_peer_links") or 0)
            setup_fast = bool(data.get("setup_complete"))
            if setup_fast:
                if self.messaging:
                    self.messaging.display_name = effective_display_name(settings)
                    if "max_peer_links" in data and settings.get("max_peer_links", 0) > 0:
                        self.messaging._enforce_max_peer_links()
                if lan_scope_changed:
                    async def _safe_lan_scope():
                        try:
                            await asyncio.to_thread(self._apply_lan_scope_change)
                        except Exception as exc:
                            print(f"[network] LAN scope apply warning: {exc}")
                    asyncio.create_task(_safe_lan_scope())
                if config_dirty or hub_changed:
                    asyncio.create_task(
                        asyncio.to_thread(self._write_rns_config, settings)
                    )
                if hub_changed:
                    asyncio.create_task(self._apply_hub_runtime(settings))
                if "auto_announce" in data:
                    self._apply_auto_announce_settings(settings)
                if any(
                    k in data
                    for k in (
                        "probe_interval_s",
                        "lan_probe_interval_s",
                        "serial_probe_interval_s",
                    )
                ):
                    self._apply_probe_interval_settings(settings)
                    if self.discovery:
                        self.discovery.reset_probe_timers()
                    self._schedule_peers_broadcast()
                return web.json_response({
                    "status": "ok",
                    "settings": self._settings_api_payload(settings),
                })
            if lan_scope_changed:
                try:
                    await asyncio.to_thread(self._apply_lan_scope_change)
                except Exception as exc:
                    print(f"[network] LAN scope apply warning: {exc}")
            if config_dirty or hub_changed:
                await asyncio.to_thread(self._write_rns_config, settings)
            if hub_changed:
                await asyncio.to_thread(self._apply_hub_runtime, settings)
            if "auto_announce" in data:
                self._apply_auto_announce_settings(settings)
            if any(
                k in data
                for k in (
                    "probe_interval_s",
                    "lan_probe_interval_s",
                    "serial_probe_interval_s",
                )
            ):
                self._apply_probe_interval_settings(settings)
                if self.discovery:
                    self.discovery.reset_probe_timers()
                self._schedule_peers_broadcast()
            if self.messaging:
                self.messaging.display_name = effective_display_name(settings)
                if "max_peer_links" in data:
                    self.messaging.max_peer_links = int(settings.get("max_peer_links") or 0)
                    if settings.get("max_peer_links", 0) > 0:
                        await asyncio.to_thread(self.messaging._enforce_max_peer_links)
            if lan_scope_changed and self.websockets and self._loop:
                await self._broadcast({
                    "type": "link_closed",
                    "data": {
                        "peer": self.active_peer,
                        "reason": "lan_scope_changed",
                        "linked_peers": (
                            self.messaging.linked_peers() if self.messaging else []
                        ),
                    },
                })
                await self._broadcast_peers(authoritative=True)
            self._apply_received_dir(settings)
            self._apply_retention()
            self._save_history()
            return web.json_response({
                "status": "ok",
                "settings": self._settings_api_payload(settings),
            })
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def _reload_identity_runtime(self, old_dest_hash="", old_identity_hash="", role="lan"):
        from chatx5.core.discovery import normalize_hash

        role = (role or "lan").strip().lower()
        old_dest = normalize_hash(old_dest_hash)
        old_ident = normalize_hash(old_identity_hash or old_dest_hash)
        my_dest_clean = ""
        my_ident_clean = ""

        ident = self.identity_mgr.get_identity(role) if self.identity_mgr else self.identity
        if self.messaging and ident:
            dest = await asyncio.to_thread(self.messaging.rebind_identity, ident, role)
            my_hash = RNS.hexrep(dest.hash)
            my_dest_clean = my_hash.replace(":", "")
            if role == "serial":
                self.messaging.my_dest_hash_serial = my_dest_clean
            else:
                self.messaging.my_dest_hash = my_dest_clean
                self.destination_hash = my_hash
                self.identity = ident
        elif self.identity_mgr:
            my_ident_clean = (self.identity_mgr.get_hex_hash(role) or "").replace(":", "")

        if self.identity_mgr:
            my_ident_clean = (self.identity_mgr.get_hex_hash(role) or "").replace(":", "")

        if self.discovery and role == "lan":
            self.discovery.purge_hashes({old_dest, old_ident, my_dest_clean, my_ident_clean})
            self.discovery.clear_peers()
            self.discovery.accept_peers = True

        if self.lan_beacon and my_dest_clean and role == "lan":
            self.lan_beacon.dest_hash = my_dest_clean
            self.lan_beacon.identity_hash = my_ident_clean
            try:
                self.lan_beacon.identity_pubkey = (
                    ident.get_public_key() if ident else None
                )
            except Exception:
                self.lan_beacon.identity_pubkey = None
            self.lan_beacon.display_name = effective_display_name(self.load_settings())

        self.active_peer = None
        if self.messaging:
            self.messaging.display_name = effective_display_name(self.load_settings())

        if self.websockets and self._loop:
            await self._broadcast({
                "type": "identity_changed",
                "data": {
                    "hash": my_dest_clean or my_ident_clean,
                    "identity_hash": my_ident_clean,
                    "old_hash": old_dest,
                    "old_identity_hash": old_ident,
                },
            })
            if self.messaging:
                await self._perform_announce()
            peers = self._scoped_peers()
            await self._broadcast({"type": "peers", "data": peers})

        self._sync_discovery_local_hashes()
        print(
            f"[identity] Live identity update: {old_dest[:16] or old_ident[:16]}... "
            f"-> {(my_dest_clean or my_ident_clean)[:16]}..."
        )

    async def handle_regenerate_identity(self, request):
        try:
            from chatx5.core.discovery import normalize_hash

            role = "lan"
            try:
                if request.can_read_body:
                    data = await request.json()
                    role = (data.get("role") or "lan").strip().lower()
            except Exception:
                pass
            old_dest = normalize_hash(self.destination_hash or "")
            if role == "serial" and self.messaging:
                old_dest = normalize_hash(getattr(self.messaging, "my_dest_hash_serial", "") or "")
            old_ident = normalize_hash(self.identity_mgr.get_hex_hash(role) if self.identity_mgr else "")
            self.identity = self.identity_mgr.regenerate(role)
            if role == "lan":
                self.identity = self.identity_mgr.identity_lan
            await self._reload_identity_runtime(old_dest, old_ident, role=role)
            new_dest = normalize_hash(self.destination_hash or "")
            new_ident = normalize_hash(self.identity_mgr.get_hex_hash())
            return web.json_response({
                "status": "ok",
                "old_hash": old_dest or old_ident,
                "new_hash": new_dest or new_ident,
                "identity_hash": new_ident,
                "live": bool(self.messaging),
            })
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def _reload_server_runtime(self):
        settings = self.load_settings()
        await asyncio.to_thread(self._write_rns_config, settings)
        await asyncio.to_thread(self._apply_hub_runtime, settings)
        self._apply_auto_announce_settings(settings)
        if self.messaging:
            self.messaging.display_name = effective_display_name(settings)
        if self.lan_beacon:
            self.lan_beacon.display_name = effective_display_name(settings)

