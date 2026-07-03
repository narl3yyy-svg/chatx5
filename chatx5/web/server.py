"""chatx5 web server — thin orchestrator wiring HTTP, WebSocket, and RNS layers."""

import asyncio
import os
import signal
import sys
import threading
import time

from aiohttp import web

from chatx5._version import __version__ as APP_VERSION
from chatx5.core.identity import IdentityManager
from chatx5.core.lan_rns import serial_interface_online
from chatx5.core.messaging import HUB_GROUP_PEER, is_hub_peer_hash
from chatx5.core.rns_interfaces import lan_discovery_configured
from chatx5.utils.platform import (
    enumerate_lan_interfaces,
    invalidate_desktop_interface_cache,
    is_android,
)
from chatx5.web.background_tasks import BackgroundTasksMixin
from chatx5.web.discovery_bridge import DiscoveryBridgeMixin
from chatx5.web.history_store import HistoryStoreMixin
from chatx5.web.hub_runtime import HubRuntimeMixin
from chatx5.web.messaging_bridge import MessagingBridgeMixin
from chatx5.web.peer_connect import PeerConnectMixin
from chatx5.web.rns_lifecycle import RNSLifecycleMixin
from chatx5.web.rns_utils import (
    CONFIG_DIR,
    DATA_DIR,
    NETWORK_STATS_AUTO_RESET_SEC,
    SESSION_SYSTEM_LINK_CLOSED_TTL,
    SETTINGS_FILE,
    build_android_rns_config,
    build_desktop_rns_config,
    detect_lan_ip,
    ensure_rns_ports_free,
    shutdown_rns_stack,
    stop_stale_chatx5_servers,
)
from chatx5.web.routes import register_routes
from chatx5.web.routes.contacts_routes import ContactRoutesMixin
from chatx5.web.routes.debug_routes import DebugRoutesMixin
from chatx5.web.routes.discovery_routes import DiscoveryRoutesMixin
from chatx5.web.routes.identity_routes import IdentityRoutesMixin
from chatx5.web.routes.queue_routes import QueueRoutesMixin
from chatx5.web.routes.static_routes import StaticRoutesMixin
from chatx5.web.routes.system_routes import SystemRoutesMixin
from chatx5.web.routes.transfers_routes import TransferRoutesMixin
from chatx5.web.settings_store import SettingsStoreMixin
from chatx5.web.share_browser import ShareBrowserMixin
from chatx5.web.ws import WebSocketMixin

# Re-export for backward-compatible imports and test patches.
__all__ = [
    "APP_VERSION",
    "ChatWebServer",
    "CONFIG_DIR",
    "DATA_DIR",
    "SETTINGS_FILE",
    "NETWORK_STATS_AUTO_RESET_SEC",
    "SESSION_SYSTEM_LINK_CLOSED_TTL",
    "build_android_rns_config",
    "build_desktop_rns_config",
    "detect_lan_ip",
    "ensure_rns_ports_free",
    "lan_discovery_configured",
    "main",
    "serial_interface_online",
    "shutdown_rns_stack",
    "stop_stale_chatx5_servers",
]


class ChatWebServer(
    WebSocketMixin,
    StaticRoutesMixin,
    IdentityRoutesMixin,
    ContactRoutesMixin,
    DiscoveryRoutesMixin,
    TransferRoutesMixin,
    QueueRoutesMixin,
    DebugRoutesMixin,
    SystemRoutesMixin,
    BackgroundTasksMixin,
    RNSLifecycleMixin,
    PeerConnectMixin,
    MessagingBridgeMixin,
    SettingsStoreMixin,
    HistoryStoreMixin,
    HubRuntimeMixin,
    DiscoveryBridgeMixin,
    ShareBrowserMixin,
):
    def __init__(self, host="127.0.0.1", port=8742, verbose=False, debug=False, force=False,
                 embedded=False, use_tls=False, cert_path=None, key_path=None):
        self.host = host
        self.port = port
        self.verbose = verbose
        self.debug = debug
        self.force = force
        self.embedded = embedded
        self.use_tls = bool(use_tls)
        self.cert_path = cert_path
        self.key_path = key_path
        self._ssl_context = None
        self.config_dir = CONFIG_DIR
        self.data_dir = DATA_DIR
        os.makedirs(self.config_dir, exist_ok=True)
        os.makedirs(self.data_dir, exist_ok=True)
        os.makedirs(os.path.join(self.config_dir, "received"), exist_ok=True)
        os.makedirs(os.path.join(self.config_dir, "sent"), exist_ok=True)
        os.makedirs(os.path.join(self.config_dir, "contacts"), exist_ok=True)

        self.identity_mgr = IdentityManager(self.config_dir)
        self.identity = None
        self.messaging = None
        self.voice_recorder = None

        self.websockets = set()
        self.message_history = self._load_history()
        self._prune_ephemeral_history_disk()

        self.active_peer = None
        self.destination_hash = None
        self.discovery = None
        self.lan_beacon = None
        self._loop = None
        self.rns_init_error = None
        self._announce_lock = threading.Lock()
        self._last_announce_at = 0.0
        self._reverse_connect_last = {}
        self._session_resume_last = 0.0
        self._shutting_down = False
        self._failover_task = None
        self._background_tasks = []
        self._progress_last = {}
        self._progress_throttle_ms = 250
        self._ui_state = {"viewing_peer": None, "hidden": False}
        self._live_scope_ip = None
        self._init_share_sessions()

    @staticmethod

    def _clean_hash(h):
        return (h or "").replace("<", "").replace(">", "").replace(":", "").strip()

    async def _run_blocking(self, fn, *args):
        if self._shutting_down:
            return None
        try:
            return await asyncio.to_thread(fn, *args)
        except asyncio.CancelledError:
            if self._shutting_down:
                return None
            raise

    async def _on_shutdown(self, app):
        self._shutting_down = True
        if self.messaging:
            self.messaging.shutdown_requested = True
            self.messaging.running = False
            self.messaging.cancel_all_transfers()
        for task in list(self._background_tasks):
            task.cancel()

    def _teardown_network_stack(self):
        if self.lan_beacon:
            try:
                self.lan_beacon.stop()
            except Exception:
                pass
            self.lan_beacon = None
        if self.discovery:
            try:
                self.discovery.stop()
            except Exception:
                pass
        if self.messaging:
            self.messaging.shutdown_requested = True
            self.messaging.running = False
            try:
                self.messaging._teardown_active_link()
                self.messaging.stop()
            except Exception:
                pass
        shutdown_rns_stack()

    async def _on_cleanup(self, app):
        self._shutting_down = True
        try:
            await asyncio.to_thread(self._teardown_network_stack)
        except Exception:
            self._teardown_network_stack()
        for ws in list(self.websockets):
            try:
                await ws.close()
            except Exception:
                pass
        self.websockets.clear()
        print("[shutdown] Server stopped — ports released")

    async def _wait_for_rns(self, timeout=90.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.rns_init_error:
                return False, "Network error: " + self.rns_init_error.splitlines()[-1]
            if self.messaging and self.messaging.destination:
                return True, None
            await asyncio.sleep(0.5)
        if self.embedded and not self.rns_init_error:
            return False, "Network stack still starting - wait a few seconds and try again"
        return False, "not ready"

    def _reset_connection_state(self):
        """Clear peer session on server start - UI reconnects explicitly."""
        if self.messaging and self.messaging.active_link:
            try:
                self.messaging.active_link.teardown()
            except Exception:
                pass
            self.messaging.active_link = None
        self.active_peer = None

    def _peer_dest_hash(self, any_hash):
        if any_hash in (HUB_GROUP_PEER, "__hub_group__"):
            return HUB_GROUP_PEER
        if self.messaging:
            return self.messaging.dest_hash_for(any_hash)
        return self._clean_hash(any_hash).lower()

    def _my_sender_hash(self):
        from chatx5.core.discovery import normalize_hash
        if self.messaging and self.messaging.my_dest_hash:
            return normalize_hash(self.messaging.my_dest_hash)
        if self.identity_mgr:
            connect = self.identity_mgr.get_connect_hash()
            if connect:
                return connect
        return normalize_hash(self._clean_hash(self.destination_hash or ""))

    def _is_self_hash(self, h):
        from chatx5.core.discovery import normalize_hash
        clean = normalize_hash(h)
        if not clean:
            return False
        my_connect = normalize_hash(
            (self.messaging.my_dest_hash if self.messaging else None)
            or (self.identity_mgr.get_connect_hash() if self.identity_mgr else "")
            or self._clean_hash(self.destination_hash or "")
        )
        my_ident = normalize_hash(self.identity_mgr.get_hex_hash() if self.identity_mgr else "")
        return clean in (my_connect, my_ident)

    def _peers_equivalent(self, hash_a, hash_b):
        if self.messaging:
            return self.messaging.hashes_equivalent(hash_a, hash_b)
        from chatx5.core.discovery import normalize_hash
        return normalize_hash(hash_a) == normalize_hash(hash_b)

    def _peer_alias_list(self, peer_hash):
        if self.messaging:
            return self.messaging.peer_aliases_for(peer_hash)
        clean = self._peer_dest_hash(peer_hash)
        return [clean] if clean else []

    def _session_chat_peer(self, sender_hash=None):
        viewing = self._ui_state.get("viewing_peer")
        if viewing and not is_hub_peer_hash(viewing):
            resolved = self._peer_dest_hash(viewing)
            if resolved and resolved != "unknown" and not is_hub_peer_hash(resolved):
                return resolved
        if self.messaging and self.messaging.active_peer_hash:
            if not is_hub_peer_hash(self.messaging.active_peer_hash):
                resolved = self._peer_dest_hash(self.messaging.active_peer_hash)
                if resolved and resolved != "unknown" and not is_hub_peer_hash(resolved):
                    return resolved
        if self.active_peer and not is_hub_peer_hash(self.active_peer):
            resolved = self._peer_dest_hash(self.active_peer)
            if resolved and resolved != "unknown" and not is_hub_peer_hash(resolved):
                return resolved
        if sender_hash and not is_hub_peer_hash(sender_hash):
            return self._peer_dest_hash(sender_hash)
        return ""

    def _sender_has_serial_path(self, sender_hash):
        if not self.messaging or not sender_hash:
            return False
        from chatx5.core.lan_rns import peer_path_on_family
        sender = self.messaging.dest_hash_for(sender_hash)
        return bool(sender and peer_path_on_family(sender, "serial"))

    def _interfaces_for_picker(self, refresh=False):
        """All local NICs/IPv4 addresses for setup/settings dropdowns (unfiltered)."""
        if refresh:
            invalidate_desktop_interface_cache(use_powershell=sys.platform == "win32")
        seen = set()
        entries = []
        for entry in enumerate_lan_interfaces():
            name = entry.get("name")
            ip = entry.get("ip") or "disconnected"
            if not name:
                continue
            key = (name, ip)
            if key in seen:
                continue
            seen.add(key)
            entries.append(entry)
        if is_android():
            ip = detect_lan_ip()
            if ip and not any(e.get("ip") == ip for e in entries):
                parts = ip.split(".")
                subnet = (
                    f"{parts[0]}.{parts[1]}.{parts[2]}.255"
                    if len(parts) == 4 else None
                )
                entries.append({
                    "name": "active",
                    "kind": "wifi",
                    "ip": ip,
                    "broadcast": subnet,
                    "subnet_broadcast": subnet,
                    "up": True,
                })
        entries.sort(key=lambda e: (e.get("name") or "", e.get("ip") or ""))
        return entries

    def _brand_logo_path(self):
        return os.path.join(self.config_dir, "brand_logo.png")

    async def _on_startup(self, app):
        self._loop = asyncio.get_running_loop()
        self._reset_connection_state()
        self._maybe_auto_reset_network_stats()
        print(f"[startup] Event loop captured: {self._loop}")
        for coro in (
            self._discovery_broadcaster(),
            self._peer_probe_loop(),
            self._history_maintenance_loop(),
            self._serial_watchdog_loop(),
            self._queue_retry_loop(),
        ):
            task = asyncio.create_task(coro)
            self._background_tasks.append(task)
        if not self.embedded and not is_android():
            task = asyncio.create_task(self._init_rns_background())
            self._background_tasks.append(task)
        self._prune_stale_session_system_messages()
        retention = self.load_settings().get("history_retention", "never")
        if retention == "on_restart":
            self.message_history = []
            self._save_history()
            print("[history] Cleared on restart")

    def run_embedded(self):
        """Blocking server loop for embedded hosts (Android/Chaquopy)."""
        app = web.Application()
        register_routes(self, app)

        async def _embedded_startup(app):
            self._loop = asyncio.get_running_loop()
            self._reset_connection_state()
            self._maybe_auto_reset_network_stats()
            for coro in (
                self._discovery_broadcaster(),
                self._embedded_init_rns(app),
                self._queue_retry_loop(),
                self._link_failover_loop(),
            ):
                task = asyncio.create_task(coro)
                self._background_tasks.append(task)
            retention = self.load_settings().get("history_retention", "never")
            if retention == "on_restart":
                self.message_history = []
                self._save_history()

        app.on_startup.append(_embedded_startup)
        app.on_shutdown.append(self._on_shutdown)
        app.on_cleanup.append(self._on_cleanup)
        print(f"[embedded] starting http://{self.host}:{self.port}")

        async def _serve():
            runner = web.AppRunner(app, access_log=None)
            await runner.setup()
            site = web.TCPSite(runner, self.host, self.port, reuse_address=True)
            await site.start()
            while True:
                await asyncio.sleep(3600)

        asyncio.run(_serve())

    def run(self):
        from aiohttp.web_runner import AppRunner, GracefulExit, TCPSite

        self._prepare_listen_ports()

        app = web.Application()
        register_routes(self, app)
        app.on_startup.append(self._on_startup)
        app.on_shutdown.append(self._on_shutdown)
        app.on_cleanup.append(self._on_cleanup)

        if self.use_tls:
            from chatx5.utils.tls import build_ssl_context, ensure_self_signed_cert

            cert = self.cert_path
            key = self.key_path
            if not cert or not key:
                cert, key = ensure_self_signed_cert(self.config_dir)
            self._ssl_context = build_ssl_context(cert, key)
        scheme = "https" if self._ssl_context else "http"
        print(f"chatx5 web server v{APP_VERSION}")
        print(f"Web interface: {scheme}://{self.host}:{self.port}")
        if self._ssl_context:
            print("[startup] HTTPS listening (self-signed cert — trust in browser for LAN access)")
        else:
            print("[startup] HTTP listening — RNS/network stack starting in background")
        print("Press Ctrl+C to stop")

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        runner = AppRunner(app, access_log=None)
        stopping = False
        self._loop = loop

        async def _start():
            await runner.setup()
            site = TCPSite(
                runner, self.host, self.port,
                reuse_address=True,
                ssl_context=self._ssl_context,
            )
            await site.start()

        def _stop_loop(signum=None, frame=None):
            nonlocal stopping
            if stopping:
                try:
                    self._teardown_network_stack()
                except Exception:
                    pass
                os._exit(130 if signum == signal.SIGINT else 0)
            stopping = True
            self._shutting_down = True
            if self.messaging:
                self.messaging.shutdown_requested = True
            try:
                self._teardown_network_stack()
            except Exception:
                pass
            loop.call_soon_threadsafe(loop.stop)

        if sys.platform != "win32":
            try:
                loop.add_signal_handler(signal.SIGINT, lambda: _stop_loop(signal.SIGINT))
                loop.add_signal_handler(signal.SIGTERM, lambda: _stop_loop(signal.SIGTERM))
            except (NotImplementedError, RuntimeError, ValueError):
                signal.signal(signal.SIGINT, _stop_loop)
                signal.signal(signal.SIGTERM, _stop_loop)
            try:
                signal.signal(signal.SIGTSTP, lambda s, f: print(
                    "\n[shutdown] Ctrl+Z suspends the server — use Ctrl+C to stop",
                    flush=True,
                ))
            except (AttributeError, ValueError, OSError):
                pass

        try:
            loop.run_until_complete(_start())
            try:
                loop.run_forever()
            except KeyboardInterrupt:
                stopping = True
        except (GracefulExit, KeyboardInterrupt):
            stopping = True
        finally:
            if not stopping:
                stopping = True
            self._shutting_down = True
            try:
                loop.run_until_complete(runner.cleanup())
            except Exception:
                try:
                    self._teardown_network_stack()
                except Exception:
                    pass
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            loop.close()


def main():
    import argparse
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(line_buffering=True)
            sys.stderr.reconfigure(line_buffering=True)
        except Exception:
            pass
    parser = argparse.ArgumentParser(description="chatx5 web server")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address")
    parser.add_argument("--port", type=int, default=8742, help="Port")
    parser.add_argument("--share", action="store_true", help="Listen on 0.0.0.0 (accessible on LAN)")
    parser.add_argument("--tls", action="store_true",
                        help="Serve web UI over HTTPS (auto self-signed cert if --cert/--key omitted)")
    parser.add_argument("--no-tls", action="store_true",
                        help="With --share, keep plain HTTP even if OpenSSL is available")
    parser.add_argument("--cert", default=None, help="TLS certificate PEM (with --tls)")
    parser.add_argument("--key", default=None, help="TLS private key PEM (with --tls)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show RNS debug logs")
    parser.add_argument("--debug", "-d", action="store_true",
                        help="Extreme RNS logging + chatx5 trace logs (very noisy)")
    parser.add_argument("--force", "-f", action="store_true",
                        help="Stop any existing chatx5 server before starting")
    args = parser.parse_args()
    host = "0.0.0.0" if args.share else args.host
    use_tls = bool(args.tls)
    if args.share and not args.no_tls and not use_tls:
        try:
            from chatx5.utils.tls import ensure_self_signed_cert
            ensure_self_signed_cert(CONFIG_DIR)
            use_tls = True
        except Exception as exc:
            print(f"[startup] HTTPS unavailable ({exc}) — using HTTP")
    server = ChatWebServer(
        host=host, port=args.port, verbose=args.verbose, debug=args.debug, force=args.force,
        use_tls=use_tls, cert_path=args.cert, key_path=args.key,
    )
    try:
        server.run()
    except KeyboardInterrupt:
        pass
    raise SystemExit(0)


if __name__ == "__main__":
    main()
