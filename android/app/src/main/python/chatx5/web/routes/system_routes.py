"""Auto-extracted from web/server.py — SystemRoutes layer."""

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



class SystemRoutesMixin:
    def _spawn_unix_server_restart(self):
        """Re-exec via restart-server.sh so dialout/uucp (sg) is preserved on Linux."""
        sys.stdout.flush()
        root = os.environ.get("CHATX5_ROOT") or os.getcwd()
        extra = list(sys.argv[1:]) or ["--share"]
        env = os.environ.copy()
        env["CHATX5_ROOT"] = root
        env["PYTHONPATH"] = root
        env["PYTHON"] = sys.executable
        wrapper = os.path.join(root, "scripts", "restart-server.sh")
        if os.path.isfile(wrapper):
            cmd = ["bash", wrapper, str(os.getpid()), root, *extra]
        else:
            cmd = ["bash", os.path.join(root, "run.sh"), "web", *extra]
        subprocess.Popen(
            cmd,
            cwd=root,
            env=env,
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        os._exit(0)

    async def handle_restart(self, request):
        if is_android():
            settings = self.load_settings()
            self._write_rns_config(settings)
            await asyncio.to_thread(self._apply_hub_runtime, settings)
            return web.json_response({
                "status": "restarting",
                "android": True,
                "rns_reloaded": True,
            })
        if not getattr(sys, "frozen", False):
            try:
                await self._reload_server_runtime()
                print("[restart] Reloaded network stack in-process")
                return web.json_response({
                    "status": "ok",
                    "restarting": True,
                    "reloaded": True,
                    "message": "Network stack reloaded — refresh the page",
                })
            except Exception as e:
                if sys.platform != "win32":
                    print(f"[restart] In-process reload failed ({e}) — spawning new process")
                    asyncio.get_event_loop().call_later(0.8, self._spawn_unix_server_restart)
                    return web.json_response({"status": "restarting"})
                return web.json_response({"error": str(e)}, status=400)
        if getattr(sys, "frozen", False) and sys.platform == "win32":
            exe = sys.executable
            cwd = os.path.dirname(os.path.abspath(exe))

            def _win_restart():
                sys.stdout.flush()
                stop_stale_chatx5_servers(exclude_pid=os.getpid())
                flags = (
                    getattr(subprocess, "DETACHED_PROCESS", 0)
                    | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                )
                subprocess.Popen(
                    [exe],
                    cwd=cwd,
                    close_fds=True,
                    creationflags=flags,
                )
                os._exit(0)

            print(f"[restart] Spawning new process: {exe}")
            asyncio.get_event_loop().call_later(0.5, _win_restart)
            return web.json_response({"status": "restarting"})
        def _source_restart():
            sys.stdout.flush()
            stop_stale_chatx5_servers(exclude_pid=os.getpid())
            root = os.environ.get("CHATX5_ROOT") or os.getcwd()
            extra = [a for a in sys.argv[1:] if a.startswith("-")]
            if sys.platform == "win32":
                run_bat = os.path.join(root, "run.bat")
                cmd = ["cmd.exe", "/c", run_bat, "web"] + (extra or ["--share"])
                flags = (
                    getattr(subprocess, "DETACHED_PROCESS", 0)
                    | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                )
                subprocess.Popen(cmd, cwd=root, creationflags=flags)
            else:
                args = [sys.executable, "-m", "chatx5.web.server", *sys.argv[1:]]
                env = os.environ.copy()
                env["CHATX5_ROOT"] = root
                env["PYTHONPATH"] = root
                subprocess.Popen(args, cwd=root, env=env, start_new_session=True)
            os._exit(0)

        print("[restart] Spawning new server process")
        asyncio.get_event_loop().call_later(0.8, _source_restart)
        return web.json_response({"status": "restarting"})

    async def handle_temperature(self, request):
        try:
            detail = await asyncio.to_thread(get_cpu_temperature_detail)
        except Exception:
            detail = {"avg_celsius": None, "approx": False}
        return web.json_response(detail)

    async def handle_cpu(self, request):
        pct = await asyncio.to_thread(get_cpu_percent)
        if pct is not None:
            return web.json_response({"cpu_percent": pct})
        return web.json_response({"cpu_percent": None})

    async def handle_brand_logo_get(self, request):
        path = self._brand_logo_path()
        if not os.path.isfile(path):
            raise web.HTTPNotFound()
        return web.FileResponse(path)

    async def handle_brand_logo_upload(self, request):
        try:
            reader = await request.multipart()
            field = await reader.next()
            if not field or field.name != "logo":
                return web.json_response({"error": "missing logo field"}, status=400)
            data = await field.read()
            if not data or len(data) > 2 * 1024 * 1024:
                return web.json_response({"error": "invalid image"}, status=400)
            os.makedirs(self.config_dir, exist_ok=True)
            with open(self._brand_logo_path(), "wb") as f:
                f.write(data)
            return web.json_response({"status": "ok"})
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=400)

    async def handle_brand_logo_delete(self, request):
        try:
            os.remove(self._brand_logo_path())
        except FileNotFoundError:
            pass
        except OSError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        return web.json_response({"status": "ok"})

    async def handle_release_notes(self, request):
        from chatx5.release_notes import all_release_notes, CURRENT_VERSION
        return web.json_response({
            "current_version": CURRENT_VERSION,
            "releases": all_release_notes(),
        })

    async def handle_health(self, request):
        status = "ok" if not self.rns_init_error else "rns_error"
        return web.json_response({
            "status": status,
            "rns_ready": self.messaging is not None,
            "rns_error": self.rns_init_error,
        })

