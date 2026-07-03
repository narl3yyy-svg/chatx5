"""Auto-extracted from web/server.py — RNSLifecycle layer."""

import asyncio
import base64
import os
import sys
import threading
import time

import RNS
from aiohttp import web

from chatx5._version import __version__ as APP_VERSION
from chatx5.core.discovery import PeerDiscovery
from chatx5.core.lan_beacon import BEACON_PORT, LanBeacon
from chatx5.core.lan_rns import (
    lan_ip_reachable,
    patch_udp_interface_unicast,
    serial_interface_online,
)
from chatx5.core.messaging import MessagingBackend
from chatx5.core.rns_interfaces import (
    ANDROID_SERIAL_PERMISSION_HINT,
    INTERFACE_PRESETS,
    SERIAL_BAUD_RATES,
    SERIAL_DEFAULT_BAUD,
    SERIAL_PERMISSION_HINT,
    add_interface,
    configured_serial_enabled,
    configured_serial_port,
    configured_tcp_lan_enabled,
    configured_udp_lan_enabled,
    dedupe_serial_interfaces,
    delete_interface,
    ensure_runtime_serial,
    ensure_runtime_tcp_lan_server,
    lan_discovery_configured,
    lan_transport_hub_policy,
    list_serial_ports,
    normalize_interface_list,
    remove_serial_interfaces,
    render_rns_config,
    serial_permission_hint_for_process,
    serial_port_accessible,
    serial_port_status,
    serial_runtime_active,
    summarize_rns_interfaces,
    tcp_client_target_warning,
    update_interface,
    user_has_serial_group_access,
)
from chatx5.core.voice import VoiceRecorder
from chatx5.utils.debug_log import (
    debug_log_path,
)
from chatx5.utils.platform import (
    apply_lan_interface_preference,
    desktop_lan_status,
    effective_display_name,
    get_lan_interface_preference,
    host_platform,
    is_android,
    lan_broadcast,
    lan_connected,
    list_network_interfaces,
    patch_embedded_signals,
)
from chatx5.web.rns_utils import (
    NETWORK_STATS_AUTO_RESET_SEC,
    _port_holder_pids,
    _rns_startup_failure,
    detect_lan_ip,
    ensure_rns_ports_free,
    stop_stale_chatx5_servers,
)


class RNSLifecycleMixin:
    def _interfaces_for_api(self, interfaces):
        rows = []
        for iface in normalize_interface_list(interfaces):
            row = dict(iface)
            if iface.get("preset") == "serial" or iface.get("type") == "SerialInterface":
                row["port_status"] = serial_port_status(iface.get("port"))
                row["port_accessible"] = serial_port_accessible(iface.get("port"))
                row["serial_active"] = serial_runtime_active(iface)
            rows.append(row)
        return rows

    def _status_peers_display(self, peers):
        """Dedupe discovered peers for the live status panel."""
        from chatx5.core.discovery import normalize_hash

        rows = []
        seen = set()
        for peer in peers or []:
            peer = dict(peer or {})
            via = (peer.get("via") or peer.get("transport") or "lan").strip().lower()
            if via == "serial":
                via = "serial"
            else:
                via = "lan"
            ident = normalize_hash(peer.get("identity_hash") or "")
            name = (peer.get("name") or "").strip().lower()
            ip = (peer.get("ip") or "").strip()
            h = normalize_hash(peer.get("hash") or "")
            key = f"ident:{ident}" if ident else f"{via}:{name}:{ip}" if name and ip else f"{via}:{h}"
            if key in seen:
                continue
            seen.add(key)
            rows.append({
                "name": peer.get("name") or (h[:8] if h else "peer"),
                "hash": h or peer.get("hash"),
                "via": via,
                "ip": peer.get("ip"),
                "rtt_ms": peer.get("rtt_ms"),
                "rtt_avg_ms": peer.get("rtt_avg_ms"),
                "connected": bool(peer.get("connected")),
            })
        rows.sort(
            key=lambda row: (
                0 if row.get("via") == "lan" else 1,
                (row.get("name") or "").lower(),
            )
        )
        return rows

    def _write_rns_config(self, settings=None):
        settings = settings or self.load_settings()
        rns_config_path = os.path.join(self.config_dir, "config")
        os.makedirs(self.config_dir, exist_ok=True)
        bcast = lan_broadcast()
        interfaces = normalize_interface_list(settings.get("rns_interfaces"))
        settings["rns_interfaces"] = interfaces
        self.save_settings(settings)
        for iface in interfaces:
            if iface.get("type") == "UDPInterface" and bcast:
                iface["forward_ip"] = bcast
        config_text = render_rns_config(
            interfaces,
            broadcast_ip=bcast,
            android=is_android(),
            auto_interface_enabled=settings.get("auto_interface_enabled", True),
        )
        with open(rns_config_path, "w") as f:
            f.write(config_text)
        print(f"[config] Wrote RNS config at {rns_config_path} (broadcast={bcast})")
        return rns_config_path

    def _log_serial_diagnostics(self):
        settings = self.load_settings()
        if not configured_serial_enabled(settings.get("rns_interfaces")):
            if sys.platform == "win32":
                return
        try:
            import grp
            names = sorted(grp.getgrgid(g).gr_name for g in os.getgroups())
        except Exception:
            names = []
        print(f"[serial] process groups: {', '.join(names) or '(none)'}")
        print(f"[serial] dialout/uucp access: {user_has_serial_group_access()}")
        for p in list_serial_ports():
            print(f"[serial] {p.get('device')}: {p.get('status')}")
        for iface in normalize_interface_list(self.load_settings().get("rns_interfaces")):
            if iface.get("preset") == "serial" or iface.get("type") == "SerialInterface":
                port = iface.get("port") or "(none)"
                active = serial_runtime_active(iface)
                print(
                    f"[serial] configured port={port} enabled={iface.get('enabled')} "
                    f"active={active}"
                )

    def start_rns(self):
        try:
            if RNS.Reticulum.get_instance() is not None and self.messaging and self.messaging.destination:
                return RNS.hexrep(self.messaging.destination.hash)
        except Exception:
            pass
        if is_android():
            try:
                from chatx5.android_usb.bootstrap import bootstrap as bootstrap_android_usb
                bootstrap_android_usb()
            except Exception as e:
                print(f"[serial] Android USB bootstrap failed: {e}")
        # v0.3.90+ starts RNS on a worker thread on desktop; RNS registers SIGINT handlers.
        patch_embedded_signals()
        settings = self._apply_hub_settings(self.load_settings())
        self._write_rns_config(settings)
        self._log_serial_diagnostics()

        if not ensure_rns_ports_free(force=self.force):
            msg = "UDP port 4242 is already in use"
            if self.embedded:
                raise RuntimeError(msg)
            _rns_startup_failure(msg)

        if self.debug and not (self.embedded or is_android()):
            loglevel = getattr(RNS, "LOG_EXTREME", RNS.LOG_DEBUG)
            print("[startup] Debug logging enabled (RNS extreme + chatx5 trace)")
        elif self.debug or self.verbose:
            loglevel = RNS.LOG_DEBUG
            print("[startup] Verbose logging enabled (RNS debug)")
        else:
            loglevel = RNS.LOG_NOTICE
        if getattr(sys, "frozen", False):
            from chatx5.utils.rns_frozen import ensure_rns_interfaces
            ensure_rns_interfaces()
        def _start_reticulum():
            from chatx5.core.rns_tuning import apply_chatx5_rns_tuning
            apply_chatx5_rns_tuning()
            return RNS.Reticulum(self.config_dir, loglevel=loglevel)

        try:
            _start_reticulum()
        except (OSError, Exception) as e:
            err = str(e)
            if "reinitialise" in err and self.messaging and self.messaging.destination:
                print("[RNS] Already running - reusing existing instance")
                return RNS.hexrep(self.messaging.destination.hash)
            print(f"[RNS] Startup error: {e}")
            if is_android():
                raise RuntimeError(f"RNS failed to start: {e}") from e
            if any(
                token in err.lower()
                for token in ("address already in use", "errno 48", "errno 10048", "eaddrinuse")
            ):
                print("[RNS] Duplicate interface or port conflict — repairing config...")
                settings = self.load_settings()
                settings["rns_interfaces"] = normalize_interface_list(
                    settings.get("rns_interfaces")
                )
                self._write_rns_config(settings)
            print("[RNS] Retrying after stopping stale instances...")
            stop_stale_chatx5_servers(exclude_pid=os.getpid())
            time.sleep(1)
            if not ensure_rns_ports_free(force=True):
                msg = "UDP port 4242 is already in use — close other chatx5 windows"
                if self.embedded:
                    raise RuntimeError(msg)
                _rns_startup_failure(msg)
            try:
                _start_reticulum()
            except Exception as retry_exc:
                if self.embedded:
                    raise RuntimeError(f"RNS init failed: {retry_exc}") from retry_exc
                _rns_startup_failure(f"RNS init failed: {retry_exc}")
        settings = self.load_settings()
        apply_lan_interface_preference(self.config_dir)
        interfaces = settings.get("rns_interfaces")
        if configured_udp_lan_enabled(interfaces):
            patch_udp_interface_unicast()
        elif configured_tcp_lan_enabled(interfaces):
            print("[network] TCP LAN mode — beacon discovery active, direct TCP dial on connect")
        else:
            print("[network] LAN transport not configured — skipping beacon/unicast helpers")
        serial_enabled = configured_serial_enabled(interfaces)
        self.identity = self.identity_mgr.load_or_create(serial_enabled=serial_enabled)
        my_ip = detect_lan_ip()
        if my_ip and lan_discovery_configured(interfaces):
            print(f"[network] Detected LAN IP: {my_ip}")
        elif configured_serial_enabled(interfaces) and not lan_discovery_configured(interfaces):
            print("[network] Serial-only transport — LAN IP detection skipped")
        received_dir = settings.get("received_dir", os.path.join(self.config_dir, "received"))
        from chatx5.core.peer_probe import clamp_announce_interval
        lan_ann = clamp_announce_interval(settings.get("lan_announce_interval_s", 0))
        ser_ann = clamp_announce_interval(settings.get("serial_announce_interval_s", 0))
        if settings.get("auto_announce") and lan_ann == 0 and ser_ann == 0:
            lan_ann = ser_ann = 30
        auto_announce = lan_ann > 0 or ser_ann > 0 or bool(settings.get("auto_announce", False))
        self.messaging = MessagingBackend(
            self.identity_mgr.identity_lan, self.config_dir,
            on_message=self._on_message,
            on_progress=self._on_transfer_progress,
            on_link_established=self._on_link_established,
            on_link_closed=self._on_link_closed,
            on_queue_sent=self._on_queue_sent,
            on_transfer_revoked=self._on_transfer_revoked,
            display_name=effective_display_name(settings),
            auto_announce=auto_announce,
            receive_dir=received_dir,
            peer_resolver=self._resolve_incoming_peer,
            http_port=self.port,
            http_scheme="https" if getattr(self, "use_tls", False) else "http",
            lan_transfer_enabled=(
                self.host in ("0.0.0.0", "::")
                and not bool(settings.get("wan_secure_mode"))
            ),
            peer_endpoint_resolver=self._peer_endpoint_for_transfer,
            peer_scope_checker=self._peer_in_discovery_scope,
            peer_transport_resolver=lambda h, via=None: self._discovery_peer_for_connect(
                None, h, via=via,
            ),
            identity_serial=self.identity_mgr.identity_serial,
            dual_identity_mode=True,
        )
        self.messaging.lan_announce_interval_s = lan_ann
        self.messaging.serial_announce_interval_s = ser_ann
        self.messaging.max_peer_links = int(settings.get("max_peer_links") or 0)
        self.messaging.on_after_serial_announce = self._after_serial_announce_beacon
        self.voice_recorder = VoiceRecorder(self.config_dir)
        dest = self.messaging.start()
        sent_ids = [
            m.get("msg_id") for m in self.message_history
            if m.get("msg_id") and m.get("status") == "sent"
        ]
        pruned = self.messaging.prune_stale_queue(sent_ids)
        if pruned:
            print(f"[queue] Pruned {pruned} stale item(s) already marked sent")

        my_hash = RNS.hexrep(dest.hash)
        my_dest_clean = my_hash.replace(":", "")
        self.messaging.my_dest_hash = my_dest_clean
        self.destination_hash = my_hash
        self.discovery = PeerDiscovery(
            on_peer_seen=self._on_peer_discovered,
            on_peer_evicted=self._on_peer_evicted,
        )
        self.discovery.start()
        self._sync_discovery_local_hashes()
        if lan_discovery_configured(interfaces) or configured_serial_enabled(interfaces):
            self.discovery.enable_discovery(clear=False)
            if configured_serial_enabled(interfaces) and not lan_discovery_configured(interfaces):
                print("[discovery] Serial discovery active — listening for USB peers")
        identity_pubkey = None
        if self.identity:
            try:
                identity_pubkey = self.identity.get_public_key()
            except Exception:
                identity_pubkey = None
        if lan_discovery_configured(interfaces):
            serial_hash, serial_ident, serial_pubkey = self._serial_beacon_fields()
            self.lan_beacon = LanBeacon(
                self.discovery,
                my_dest_clean,
                display_name=effective_display_name(settings),
                ip=my_ip,
                port=self.port,
                periodic=auto_announce,
                identity_hash=self.identity_mgr.get_hex_hash(),
                identity_pubkey=identity_pubkey,
                serial_hash=serial_hash,
                serial_identity_hash=serial_ident,
                serial_identity_pubkey=serial_pubkey,
                on_periodic=self._on_beacon_periodic if auto_announce else None,
            )
            self.lan_beacon.start()
            self._apply_probe_interval_settings(settings)
        else:
            self.lan_beacon = None
            print("[network] Serial/other-only mode — LAN beacon disabled")
        if self.messaging:
            self._apply_probe_interval_settings(settings)
        if auto_announce:
            print("[network] Auto-announce on — periodic LAN + serial discovery every 30s")
        else:
            print("[network] Auto-announce off — tap Announce to discover peers")

        serial_hot = None
        for attempt in range(3):
            serial_hot = ensure_runtime_serial(settings.get("rns_interfaces"))
            if serial_hot:
                break
            if attempt < 2:
                time.sleep(0.5)
        dedupe_serial_interfaces()
        if serial_hot:
            print(f"[serial] Runtime serial interface active on {getattr(serial_hot, 'port', '?')}")
            if self.messaging:
                self.messaging.ensure_serial_runtime()
            self._sync_beacon_serial_fields()
        elif configured_serial_port(settings.get("rns_interfaces"))[0]:
            print("[serial] Warning: serial port configured but RNS SerialInterface is not active")

        try:
            from chatx5.core.lan_rns import prune_stale_lan_paths
            prune_stale_lan_paths()
            if configured_serial_enabled(interfaces) and not lan_discovery_configured(interfaces):
                print("[network] Serial-only — tap Announce to broadcast on USB")
            else:
                self.messaging._silent_announce(also_serial=False)
            if not auto_announce:
                print("[network] Startup announce queued (tap Announce for more)")

            def _deferred_startup_announce():
                try:
                    if (
                        lan_discovery_configured(interfaces)
                        and lan_ip_reachable()
                        and self.lan_beacon
                    ):
                        self.lan_beacon.send(1, subnet_probe=False)
                        if not auto_announce:
                            print("[network] Startup announce sent once (tap Announce for more)")
                except Exception as exc:
                    print(f"[network] Startup announce failed: {exc}")

            threading.Thread(
                target=_deferred_startup_announce,
                name="chatx5-startup-announce",
                daemon=True,
            ).start()
        except Exception as exc:
            print(f"[network] Startup announce failed: {exc}")

        if configured_tcp_lan_enabled(interfaces) and settings.get("hub_role", "off") != "server":
            tcp_srv = ensure_runtime_tcp_lan_server(settings, self.config_dir)
            if tcp_srv:
                print(f"[tcp-lan] TCP LAN server listening on 0.0.0.0:{getattr(tcp_srv, 'listen_port', 4242)}")
        self._apply_hub_runtime(settings)
        if settings.get("hub_role") == "client":
            self._schedule_hub_bootstrap_retries()
        if is_android() and lan_discovery_configured(interfaces):
            self._schedule_android_lan_announce_retries()
        if settings.get("hub_role") == "server":
            hub_hash = my_dest_clean
            if settings.get("hub_server_hash") != hub_hash:
                settings["hub_server_hash"] = hub_hash
                self.save_settings(settings)

        self._live_scope_ip = self._discovery_scope_ip()
        return my_hash

    def _schedule_android_lan_announce_retries(self):
        """Wi-Fi may come up after WebView loads — retry beacon/RNS announce."""
        if not is_android():
            return

        def attempt(label):
            if self._shutting_down or not self.messaging:
                return
            settings = self.load_settings()
            if not lan_discovery_configured(settings.get("rns_interfaces")):
                return
            if not lan_ip_reachable():
                return
            try:
                if self.discovery:
                    self.discovery.enable_discovery(clear=False)
                self.messaging._silent_announce()
                if self.lan_beacon:
                    self.lan_beacon.send(2, True)
                print(f"[network] Android LAN announce retry ({label})")
            except Exception as exc:
                print(f"[network] Android announce retry failed ({label}): {exc}")

        for delay, label in ((2.0, "2s"), (5.0, "5s"), (12.0, "12s")):
            timer = threading.Timer(delay, attempt, args=(label,))
            timer.daemon = True
            timer.start()

    def _serial_beacon_fields(self):
        """USB serial connect hash + identity for LAN beacon payloads (dual-transport)."""
        serial_hash = ""
        serial_ident = ""
        serial_pubkey = None
        if self.messaging and getattr(self.messaging, "my_dest_hash_serial", None):
            serial_hash = self._clean_hash(self.messaging.my_dest_hash_serial)
        elif self.identity_mgr:
            serial_hash = self._clean_hash(self.identity_mgr.get_connect_hash("serial"))
        if self.identity_mgr and self.identity_mgr.identity_serial:
            serial_ident = self._clean_hash(self.identity_mgr.get_hex_hash("serial"))
            try:
                serial_pubkey = self.identity_mgr.identity_serial.get_public_key()
            except Exception:
                serial_pubkey = None
        return serial_hash, serial_ident, serial_pubkey

    def _sync_beacon_serial_fields(self):
        """Refresh USB endpoint fields on the LAN beacon after serial comes online."""
        if not self.lan_beacon:
            return
        serial_hash, serial_ident, serial_pubkey = self._serial_beacon_fields()
        self.lan_beacon.serial_hash = serial_hash
        self.lan_beacon.serial_identity_hash = serial_ident
        self.lan_beacon.serial_identity_pubkey = serial_pubkey

    def _after_serial_announce_beacon(self):
        """After every USB RNS announce, blast LAN beacon with serial_hash (dual-transport)."""
        self._sync_beacon_serial_fields()
        if not self.lan_beacon:
            return
        if not lan_ip_reachable():
            return
        sent = self.lan_beacon.send(1, is_android())
        if sent:
            print(
                f"[beacon] Companion LAN beacon after serial announce "
                f"({sent} packet(s), includes serial_hash)"
            )

    def _beacon_payload(self):
        from chatx5.core.peer_identity import connect_hash_for_manager

        dest = ""
        if self.messaging and self.messaging.my_dest_hash:
            dest = self._clean_hash(self.messaging.my_dest_hash)
        elif self.messaging and self.messaging.destination:
            dest = self._clean_hash(RNS.hexrep(self.messaging.destination.hash))
        if not dest:
            dest = connect_hash_for_manager(
                self.identity_mgr,
                getattr(self.messaging, "destination", None) if self.messaging else None,
            )
        if not dest:
            dest = self._clean_hash(self.destination_hash or "")
        if not dest and self.identity_mgr:
            dest = self.identity_mgr.get_connect_hash()
        ident = self._clean_hash(self.identity_mgr.get_hex_hash() if self.identity_mgr else "")
        payload = {
            "app": "chatx5",
            "v": 1,
            "hash": dest,
            "name": self.load_settings().get("name", ""),
            "ip": detect_lan_ip() or "",
            "port": self.port,
        }
        if ident and ident != dest:
            payload["identity_hash"] = ident
        if self.identity:
            try:
                import base64
                payload["pubkey"] = base64.b64encode(
                    self.identity.get_public_key()
                ).decode("ascii")
            except Exception:
                pass
        serial_hash, serial_ident, serial_pubkey = self._serial_beacon_fields()
        if serial_hash and len(serial_hash) == 32:
            payload["serial_hash"] = serial_hash
        if serial_ident and len(serial_ident) == 32:
            payload["serial_identity_hash"] = serial_ident
        if serial_pubkey:
            try:
                import base64
                payload["serial_pubkey"] = base64.b64encode(serial_pubkey).decode("ascii")
            except Exception:
                pass
        return payload

    def _platform_name(self):
        if self.embedded and not is_android():
            return host_platform()
        return host_platform()

    def _reset_network_state(self, update_settings=True):
        if self.messaging:
            self.messaging.disconnect_all_peers(clear_session=True)
        self.active_peer = None
        if self.discovery:
            self.discovery.clear_peers()
            self.discovery.accept_peers = True
        if self.lan_beacon:
            self.lan_beacon.reset_stats()
        if update_settings:
            settings = self.load_settings()
            settings["network_stats_reset_at"] = time.time()
            self.save_settings(settings)

    def _maybe_auto_reset_network_stats(self):
        settings = self.load_settings()
        if not settings.get("network_stats_auto_reset", True):
            return
        last = float(settings.get("network_stats_reset_at") or 0)
        if last and (time.time() - last) < NETWORK_STATS_AUTO_RESET_SEC:
            return
        if self.lan_beacon:
            self.lan_beacon.reset_stats()
        settings["network_stats_reset_at"] = time.time()
        self.save_settings(settings)
        print("[network] Auto-reset beacon counters (weekly)")

    async def handle_network_reset(self, request):
        self._reset_network_state(update_settings=True)
        await self._broadcast({"type": "peers", "data": []})
        await self._broadcast({"type": "link_closed", "data": {"linked_peers": []}})
        await self._broadcast({"type": "network_reset", "data": {}})
        beacon = self.lan_beacon.status() if self.lan_beacon else None
        return web.json_response({
            "status": "ok",
            "beacon": beacon,
            "discovery_active": bool(self.discovery and self.discovery.accept_peers),
        })

    async def handle_network_repair(self, request):
        """Dedupe duplicate UDP/TCP LAN interfaces and rewrite RNS config."""
        try:
            settings = self.load_settings()
            raw = settings.get("rns_interfaces") or []
            before = len(raw)
            settings["rns_interfaces"] = normalize_interface_list(raw)
            after = len(settings["rns_interfaces"])
            self.save_settings(settings)
            self._write_rns_config(settings)
            return web.json_response({
                "status": "ok",
                "removed": max(0, before - after),
                "interfaces": self._interfaces_for_api(settings["rns_interfaces"]),
                "message": "Repaired LAN interfaces — restart chatx5 to apply.",
            })
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    def _disable_rns_serial_interfaces(self):
        try:
            from chatx5.core.lan_rns import clear_paths_on_family

            settings = self.load_settings()
            port, _ = configured_serial_port(settings.get("rns_interfaces"))
            n = remove_serial_interfaces(port or None)
            if n:
                print(f"[serial] Removed {n} SerialInterface(s) after port unplug")
            clear_paths_on_family("serial")
            if self.discovery:
                purged = self.discovery.purge_offline_serial_peers()
                mis = self.discovery.purge_misclassified_serial()
                if purged or mis:
                    print(
                        f"[serial] Cleared {purged + mis} USB peer(s) after unplug"
                    )
            if self.messaging:
                self.messaging.on_serial_transport_detached()
            self._write_rns_config(settings)
        except Exception as e:
            print(f"[serial] Could not remove runtime serial interface: {e}")

    async def _serial_watchdog_loop(self):
        serial_detach_sent = False
        serial_was_online = False
        while True:
            await asyncio.sleep(5)
            if self._shutting_down:
                return
            settings = self.load_settings()
            interfaces = normalize_interface_list(settings.get("rns_interfaces"))
            port, _ = configured_serial_port(interfaces)
            if not port:
                serial_was_online = False
                continue
            if serial_port_status(port) == "missing":
                serial_was_online = False
                if not serial_detach_sent:
                    self._disable_rns_serial_interfaces()
                    serial_detach_sent = True
            else:
                serial_detach_sent = False
                was_online = serial_was_online
                iface = await self._run_blocking(ensure_runtime_serial, interfaces)
                serial_was_online = iface is not None
                if serial_was_online and not was_online:
                    self._enable_discovery(clear=False)
                    if configured_serial_enabled(interfaces):
                        await self._run_blocking(
                            lambda: self.identity_mgr.load_or_create(serial_enabled=True),
                        )
                    if self.messaging:
                        await self._run_blocking(self.messaging.ensure_serial_runtime)
                        await self._run_blocking(
                            self.messaging.on_serial_transport_attached, iface,
                        )
                        self._sync_discovery_local_hashes()
                        if self.discovery:
                            self.discovery.reset_probe_timers()
                    if self._loop and not self._shutting_down:
                        peers = self._scoped_peers()
                        asyncio.run_coroutine_threadsafe(
                            self._broadcast({"type": "peers", "data": peers}),
                            self._loop,
                        )

    async def handle_rns_interfaces_get(self, request):
        settings = self.load_settings()
        interfaces = normalize_interface_list(settings.get("rns_interfaces"))
        return web.json_response({
            "interfaces": self._interfaces_for_api(interfaces),
            "presets": {k: v["label"] for k, v in INTERFACE_PRESETS.items()},
            "restart_required": True,
        })

    async def handle_rns_interfaces_add(self, request):
        try:
            data = await request.json()
            preset = (data.get("preset") or "udp_lan").strip()
            settings = self.load_settings()
            policy = lan_transport_hub_policy(settings.get("hub_role", "off"), preset)
            if not policy.get("allowed", True):
                return web.json_response(
                    {"error": policy.get("warning") or "TCP LAN unavailable"},
                    status=400,
                )
            settings["rns_interfaces"] = add_interface(settings.get("rns_interfaces"), preset)
            self.save_settings(settings)
            self._write_rns_config(settings)
            return web.json_response({
                "status": "ok",
                "interfaces": self._interfaces_for_api(settings["rns_interfaces"]),
                "message": "Interface added. Restart chatx5 to apply.",
            })
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def handle_rns_interfaces_delete(self, request):
        try:
            data = await request.json()
            iface_id = (data.get("id") or "").strip()
            if not iface_id:
                return web.json_response({"error": "id required"}, status=400)
            settings = self.load_settings()
            settings["rns_interfaces"] = delete_interface(settings.get("rns_interfaces"), iface_id)
            self.save_settings(settings)
            self._write_rns_config(settings)
            return web.json_response({
                "status": "ok",
                "interfaces": settings["rns_interfaces"],
                "message": "Interface removed. Restart chatx5 to apply.",
            })
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def handle_serial_ports_get(self, request):
        ports = await asyncio.to_thread(list_serial_ports)
        android = is_android()
        has_groups = None if android else user_has_serial_group_access()
        denied = [p for p in ports if p.get("status") == "permission_denied"]
        hint = serial_permission_hint_for_process() if denied else (
            ANDROID_SERIAL_PERMISSION_HINT if android else SERIAL_PERMISSION_HINT
        )
        return web.json_response({
            "ports": ports,
            "baud_rates": SERIAL_BAUD_RATES,
            "default_baud": SERIAL_DEFAULT_BAUD,
            "permission_hint": hint,
            "has_group_access": has_groups,
            "process_needs_restart": bool(denied and has_groups) if not android else False,
            "platform": "android" if android else "desktop",
            "can_request_usb_permission": android,
            "count": len(ports),
            "ready_count": sum(1 for p in ports if p.get("status") == "ok"),
        })

    async def handle_serial_usb_permission(self, request):
        if not is_android():
            return web.json_response({"error": "USB permission API is Android-only"}, status=400)
        try:
            data = await request.json()
            device = (data.get("device") or data.get("port") or "").strip()
            if not device:
                return web.json_response({"error": "device required"}, status=400)
            from usb4a import usb
            dev = usb.get_usb_device(device)
            if not dev:
                return web.json_response({"error": "device not found"}, status=404)
            if usb.has_usb_permission(dev):
                return web.json_response({"status": "ok", "granted": True})
            usb.request_usb_permission(dev)
            return web.json_response({"status": "ok", "granted": False, "requested": True})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def handle_rns_interfaces_update(self, request):
        try:
            data = await request.json()
            iface_id = (data.get("id") or "").strip()
            if not iface_id:
                return web.json_response({"error": "id required"}, status=400)
            settings = self.load_settings()
            settings["rns_interfaces"] = update_interface(
                settings.get("rns_interfaces"),
                iface_id,
                data,
            )
            self.save_settings(settings)
            self._write_rns_config(settings)
            serial_hot = await self._run_blocking(
                ensure_runtime_serial, settings.get("rns_interfaces")
            )
            if configured_serial_enabled(settings.get("rns_interfaces")):
                await self._run_blocking(
                    lambda: self.identity_mgr.load_or_create(serial_enabled=True),
                )
            if serial_hot and self.messaging:
                from chatx5.core.lan_rns import prune_stale_lan_paths
                await self._run_blocking(prune_stale_lan_paths)
                await self._run_blocking(self.messaging.ensure_serial_runtime)
                await self._run_blocking(
                    self.messaging.on_serial_transport_attached, serial_hot,
                )
                peer = (
                    self.messaging.active_peer_hash
                    or getattr(self.messaging, "_session_peer_hash", None)
                )
                if peer:
                    await self._run_blocking(
                        self.messaging._prime_serial_path, peer, 12.0
                    )
            if serial_hot:
                msg = "Serial interface attached to RNS (no restart needed)."
            elif is_android():
                msg = "Settings saved. Select a USB port and grant access if needed."
            else:
                msg = "Interface updated."
            warning = None
            for iface in settings.get("rns_interfaces") or []:
                if iface.get("type") == "TCPClientInterface":
                    warning = tcp_client_target_warning(iface.get("target_host"))
                    if warning:
                        break
            return web.json_response({
                "status": "ok",
                "interfaces": self._interfaces_for_api(settings["rns_interfaces"]),
                "serial_hot_added": bool(serial_hot),
                "message": msg,
                "warning": warning,
            })
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def handle_network(self, request):
        """Alias for network-status — used by setup wizard and settings."""
        return await self.handle_network_status(request)

    async def handle_interfaces_get(self, request):
        refresh = request.query.get("refresh", "").lower() in ("1", "true", "yes")
        try:
            ifaces = await asyncio.to_thread(
                lambda: self._interfaces_for_picker(refresh=refresh)
            )
            return web.json_response({"interfaces": ifaces})
        except Exception as exc:
            print(f"[network] Interface rescan failed: {exc}")
            return web.json_response({"interfaces": [], "error": str(exc)}, status=500)

    async def handle_network_status(self, request):
        try:
            settings = self.load_settings()
            await self._run_blocking(
                ensure_runtime_serial, settings.get("rns_interfaces")
            )
        except Exception:
            pass
        rns_interfaces = []
        rns_interfaces_summary = []
        try:
            raw_ifaces = getattr(RNS.Transport, "interfaces", []) or []
            for iface in raw_ifaces:
                rns_interfaces.append({
                    "type": type(iface).__name__,
                    "online": bool(getattr(iface, "online", False)),
                    "name": str(
                        getattr(iface, "name", "")
                        or getattr(iface, "interface_name", "")
                    ),
                })
            settings_early = self.load_settings()
            rns_interfaces_summary = summarize_rns_interfaces(
                raw_ifaces,
                hub_role=settings_early.get("hub_role", "off"),
                hub_port=int(settings_early.get("hub_port") or 4242),
            )
        except Exception:
            pass
        peers = self._scoped_peers()
        peers_display = self._status_peers_display(peers)
        linked_peers = self.messaging.linked_peers() if self.messaging else []
        link_active = False
        active_peer = None
        link_rns_interface = None
        if self.messaging:
            if self.messaging.active_link:
                try:
                    healthy = self.messaging._link_interface_healthy(
                        self.messaging.active_link
                    )
                    link_active = (
                        healthy
                        and self.messaging.active_link.status == RNS.Link.ACTIVE
                    )
                except Exception:
                    link_active = False
                if link_active:
                    active_peer = self.active_peer or self.messaging.active_peer_hash
                    try:
                        iface = self.messaging._link_attached_interface(
                            self.messaging.active_link
                        )
                        if iface:
                            link_rns_interface = type(iface).__name__
                    except Exception:
                        pass
            if not link_active:
                session_peer = getattr(self.messaging, "_session_peer_hash", None)
                session_transport = getattr(self.messaging, "_session_transport", None)
                if session_peer:
                    session_dest = self.messaging.dest_hash_for(session_peer)
                    if self.messaging._peer_link_active(
                        session_dest, transport=session_transport,
                    ):
                        link = self.messaging._link_for_peer(
                            session_dest, transport=session_transport,
                        )
                        if link and self.messaging._link_interface_healthy(link):
                            link_active = True
                            active_peer = (
                                self.active_peer
                                or session_dest
                                or self.messaging.active_peer_hash
                            )
                            try:
                                iface = self.messaging._link_attached_interface(link)
                                if iface:
                                    link_rns_interface = type(iface).__name__
                            except Exception:
                                pass
            if not link_active:
                for entry in linked_peers:
                    peer = self.messaging._peer_from_link_key(entry)
                    transport = None
                    text = str(entry or "")
                    if ":" in text:
                        transport = text.rsplit(":", 1)[-1]
                    if not self.messaging._peer_link_active(
                        peer, transport=transport,
                    ):
                        continue
                    link = self.messaging._link_for_peer(peer, transport=transport)
                    if link and self.messaging._link_interface_healthy(link):
                        link_active = True
                        active_peer = peer
                        try:
                            iface = self.messaging._link_attached_interface(link)
                            if iface:
                                link_rns_interface = type(iface).__name__
                        except Exception:
                            pass
                        break
        port, _ = configured_serial_port(self.load_settings().get("rns_interfaces"))
        settings = self.load_settings()
        configured = settings.get("rns_interfaces")
        from chatx5.core.rns_interfaces import (
            tcp_client_interface_online,
            tcp_server_interface_online,
        )
        hub_role = settings.get("hub_role", "off")
        hub_port = int(settings.get("hub_port") or 4242)
        tcp_hub_online = bool(
            hub_role == "server" and tcp_server_interface_online(hub_port)
        )
        tcp_client_online = bool(
            hub_role == "client" and tcp_client_interface_online()
        )
        hub_clients = 0
        hub_group_linked = False
        if hub_role != "off" and self.messaging:
            hub_clients = len(self.messaging._hub_tcp_linked_peers())
            if hub_role == "server":
                hub_group_linked = hub_clients > 0 or tcp_hub_online
            else:
                from chatx5.core.discovery import normalize_hash

                hub_hex = normalize_hash(settings.get("hub_server_hash") or "")
                if hub_hex:
                    dest = self.messaging.dest_hash_for(hub_hex)
                    if dest and dest != "unknown":
                        hub_group_linked = bool(
                            self.messaging._hub_link_for_peer(dest)
                        )
                if not hub_group_linked:
                    hub_group_linked = bool(self.messaging._hub_tcp_linked_peers())
        lan_discovery = lan_discovery_configured(configured)
        refresh_ifaces = request.query.get("refresh", "").lower() in ("1", "true", "yes")
        if lan_discovery and sys.platform in ("win32", "darwin"):
            lan_snap = await asyncio.to_thread(desktop_lan_status)
            lan_up = lan_snap["lan_connected"]
            lan_ip_value = lan_snap["lan_ip"] if lan_up else None
            bcast_value = lan_snap["broadcast"] if lan_up else None
        else:
            lan_up = lan_connected() if lan_discovery else False
            lan_ip_value = detect_lan_ip() if lan_up else None
            bcast_value = lan_broadcast() if lan_up else None
        avail_ifaces = await asyncio.to_thread(
            lambda: self._interfaces_for_picker(refresh=refresh_ifaces)
        )
        identity_hash = ""
        identity_pubkey = ""
        destination_hash = ""
        if getattr(self, "identity_mgr", None):
            identity_hash = (self.identity_mgr.get_hex_hash() or "").replace(":", "")
        if self.messaging and getattr(self.messaging, "my_dest_hash", None):
            destination_hash = (self.messaging.my_dest_hash or "").replace(":", "")
        if getattr(self, "identity", None):
            try:
                identity_pubkey = base64.b64encode(
                    self.identity.get_public_key()
                ).decode("ascii")
            except Exception:
                identity_pubkey = ""
        return web.json_response({
            "platform": self._platform_name(),
            "embedded": self.embedded,
            "app_version": APP_VERSION,
            "http_bind": f"{self.host}:{self.port}",
            "http_webview": f"127.0.0.1:{self.port}" if self.embedded else None,
            "discovery_active": bool(self.discovery and self.discovery.accept_peers),
            "rns_udp_port": 4242,
            "beacon_udp_port": BEACON_PORT,
            "lan_connected": lan_up,
            "lan_discovery_configured": lan_discovery,
            "serial_only_mode": (
                configured_serial_enabled(configured) and not lan_discovery
            ),
            "lan_ip": lan_ip_value if lan_discovery else (
                "not configured" if not lan_discovery else None
            ),
            "broadcast": bcast_value if lan_up else (
                "not configured" if not lan_discovery else None
            ),
            "interfaces": list_network_interfaces(),
            "available_interfaces": avail_ifaces,
            "lan_interface": get_lan_interface_preference() or "",
            "rns_ready": bool(self.messaging and self.messaging.destination),
            "rns_error": self.rns_init_error,
            "rns_interfaces": rns_interfaces,
            "rns_interfaces_summary": rns_interfaces_summary,
            "rns_interface_count": len(rns_interfaces),
            "configured_interfaces": self._interfaces_for_api(
                self.load_settings().get("rns_interfaces")
            ),
            "serial_group_access": (
                None if is_android() else user_has_serial_group_access()
            ),
            "usb_serial_ready": (
                sum(1 for p in list_serial_ports() if p.get("status") == "ok")
                if is_android() else None
            ),
            "beacon": self.lan_beacon.status() if self.lan_beacon else None,
            "discovered_peers": peers,
            "discovered_peers_display": peers_display,
            "discovered_count": len(peers),
            "discovered_display_count": len(peers_display),
            "ws_clients": self._ws_client_count(),
            "link_active": link_active,
            "linked_peers": linked_peers,
            "active_peer": active_peer,
            "link_rns_interface": link_rns_interface,
            "serial_configured_port": port or None,
            "serial_in_rns": bool(port and serial_interface_online(port)),
            "session_peer": (
                getattr(self.messaging, "_session_peer_hash", None)
                if self.messaging else None
            ),
            "queue_size": self.messaging.queue_size() if self.messaging else 0,
            "debug_log_path": debug_log_path() if is_android() else None,
            "hub_role": hub_role,
            "hub_host": settings.get("hub_host") or "",
            "hub_port": hub_port,
            "hub_server_hash": settings.get("hub_server_hash") or "",
            "destination_hash": destination_hash,
            "identity_hash": identity_hash,
            "identity_pubkey": identity_pubkey,
            "tcp_hub_online": tcp_hub_online,
            "tcp_client_online": tcp_client_online,
            "hub_group_linked": hub_group_linked,
            "hub_clients_linked": hub_clients,
        })

    async def handle_path_wake(self, request):
        """Silent RNS path refresh for connect wake - no discovery or beacon."""
        ok, err = await self._wait_for_rns()
        if not ok:
            return web.json_response({"error": err or "not ready"}, status=400)
        try:
            await asyncio.to_thread(self.messaging._silent_announce)
            return web.json_response({"status": "ok"})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def handle_lan_transfer(self, request):
        from chatx5.core.lan_transfer import peek_offer, pop_offer, set_offer_progress

        transfer_id = request.match_info.get("transfer_id", "")
        token = request.query.get("token", "")
        offer = peek_offer(transfer_id, token)
        if not offer:
            return web.Response(status=404, text="offer not found")
        path = offer.get("path")
        if not path or not os.path.isfile(path):
            pop_offer(transfer_id, token)
            return web.Response(status=404, text="file missing")

        total = os.path.getsize(path)
        range_hdr = request.headers.get("Range", "")
        start = 0
        if range_hdr.startswith("bytes="):
            part = range_hdr.split("=", 1)[1].split("-", 1)[0]
            try:
                start = max(0, int(part))
            except ValueError:
                start = 0

        resp = web.StreamResponse(status=206 if start else 200)
        resp.headers["Content-Type"] = "application/octet-stream"
        resp.headers["Accept-Ranges"] = "bytes"
        resp.headers["Content-Length"] = str(max(0, total - start))
        if start:
            resp.headers["Content-Range"] = f"bytes {start}-{total - 1}/{total}"
        await resp.prepare(request)

        sent = start
        try:
            with open(path, "rb") as src:
                if start:
                    src.seek(start)
                while True:
                    chunk = src.read(256 * 1024)
                    if not chunk:
                        break
                    await resp.write(chunk)
                    sent += len(chunk)
                    set_offer_progress(transfer_id, sent)
        except Exception:
            pop_offer(transfer_id, token)
            raise
        await resp.write_eof()
        pop_offer(transfer_id, token)
        return resp

    ANNOUNCE_DEBOUNCE_SEC = 0.4

    async def _perform_announce(self, transport=None):
        ok, err = await self._wait_for_rns()
        if not ok:
            return {"ok": False, "error": err or "not ready"}

        transport = (transport or "all").strip().lower()
        now = time.time()
        debounced = False
        beacon_sent = 0
        serial_sent = 0
        serial_port = ""
        try:
            with self._announce_lock:
                if now - self._last_announce_at < self.ANNOUNCE_DEBOUNCE_SEC:
                    debounced = True
                else:
                    self._last_announce_at = now
                    self._enable_discovery(clear=False)
                    settings = self.load_settings()
                    configured = settings.get("rns_interfaces")
                    do_lan = transport in ("lan", "all")
                    do_serial = transport in ("serial", "usb", "all")
                    if do_serial and configured_serial_enabled(configured):
                        self._sync_beacon_serial_fields()
                        serial_sent = await asyncio.to_thread(
                            self.messaging._burst_serial_announce, 1, force=True,
                        )
                        if serial_sent:
                            self._sync_discovery_local_hashes()
                            print("[network] Serial RNS announce (+ LAN beacon with serial_hash)")
                        serial_port, _ = configured_serial_port(configured)
                    if do_lan and lan_discovery_configured(configured):
                        await asyncio.to_thread(
                            self.messaging._silent_announce, also_serial=False,
                        )
                        if lan_ip_reachable() and self.lan_beacon:
                            beacon_sent = await asyncio.to_thread(
                                self.lan_beacon.send, 1, is_android(),
                            )
                            print("[network] LAN RNS announce + beacon")
                        else:
                            print("[network] LAN RNS announce (beacon skipped — LAN down)")
                    elif do_lan:
                        print("[network] LAN transport not configured")
        except Exception as e:
            return {"ok": False, "error": str(e)}

        peers = await self._broadcast_peers(authoritative=True)
        do_lan = transport in ("lan", "all")
        do_serial = transport in ("serial", "usb", "all")
        companion_beacon = 0
        if self.lan_beacon and serial_sent and do_serial:
            companion_beacon = self.lan_beacon.last_announce_sent
        beacon_fired = bool(beacon_sent) and do_lan
        return {
            "ok": True,
            "debounced": debounced,
            "transport": transport,
            "broadcast": lan_broadcast() if do_lan else None,
            "serial_port": serial_port if do_serial else None,
            "serial_announced": bool(serial_sent),
            "companion_beacon_sent": companion_beacon if do_serial else 0,
            "lan_announced": do_lan,
            "beacon_port": BEACON_PORT if beacon_fired else None,
            "beacon_sent": beacon_sent if beacon_fired else 0,
            "beacon_session_total": (
                self.lan_beacon.packets_sent if self.lan_beacon else 0
            ),
            "lan_ip": detect_lan_ip() if do_lan else None,
            "discovered_count": len(peers),
        }

    async def handle_announce(self, request):
        transport = None
        try:
            if request.can_read_body:
                data = await request.json()
                transport = (data.get("transport") or "").strip().lower() or None
        except Exception:
            pass
        result = await self._perform_announce(transport=transport)
        if not result.get("ok"):
            return web.json_response(
                {"error": result.get("error") or "not ready"}, status=400
            )
        return web.json_response({
            "status": "ok",
            "debounced": result.get("debounced", False),
            "transport": result.get("transport"),
            "broadcast": result.get("broadcast"),
            "serial_port": result.get("serial_port"),
            "serial_announced": result.get("serial_announced", False),
            "companion_beacon_sent": result.get("companion_beacon_sent", 0),
            "lan_announced": result.get("lan_announced", False),
            "beacon_port": result.get("beacon_port"),
            "beacon_sent": result.get("beacon_sent", 0),
            "beacon_session_total": result.get("beacon_session_total", 0),
            "lan_ip": result.get("lan_ip"),
            "discovered_count": result.get("discovered_count", 0),
        })

    async def handle_disconnect(self, request):
        peer = ""
        via = ""
        if request.can_read_body:
            try:
                data = await request.json()
                peer = (data.get("peer") or "").strip()
                via = (data.get("via") or "").strip().lower()
            except Exception:
                pass
        if not peer:
            peer = request.query.get("peer", "").strip()
        if not via:
            via = request.query.get("via", "").strip().lower()
        if not peer:
            peer = self._ui_state.get("viewing_peer") or self.active_peer or ""
        peer = self._peer_dest_hash(peer)
        if via not in ("serial", "lan"):
            via = None
        if self.messaging and peer:
            self.messaging.disconnect_peer(peer, user_initiated=True, transport=via)
        elif self.messaging:
            self.messaging.disconnect_all_peers(clear_session=True)
        if self.active_peer and peer and self._peers_equivalent(self.active_peer, peer):
            remaining = self.messaging.linked_peers() if self.messaging else []
            if not remaining:
                self.active_peer = None
        await self._broadcast({
            "type": "link_closed",
            "data": {
                "peer": peer,
                "via": via,
                "linked_peers": (
                    self.messaging.linked_peers() if self.messaging else []
                ),
            },
        })
        return web.json_response({
            "status": "ok",
            "linked_peers": (
                self.messaging.linked_peers() if self.messaging else []
            ),
        })

    async def _init_rns_background(self):
        try:
            my_hash = await asyncio.to_thread(self.start_rns)
            print(f"[startup] RNS ready, identity: {my_hash}")
            await self._broadcast({"type": "rns_ready", "data": {"hash": my_hash}})
        except (SystemExit, RuntimeError) as e:
            self.rns_init_error = str(e) or "RNS startup failed"
            print(f"[startup] RNS init failed: {self.rns_init_error}")
            await self._broadcast({
                "type": "info",
                "data": f"Network stack failed: {self.rns_init_error}",
            })
        except Exception:
            import traceback
            self.rns_init_error = traceback.format_exc()
            print(f"[startup] RNS init failed:\n{self.rns_init_error}")

    async def _embedded_init_rns(self, app):
        """Start Reticulum after the HTTP server is already listening."""
        try:
            my_hash = await asyncio.to_thread(self.start_rns)
            print(f"[embedded] RNS ready, identity: {my_hash}")
        except Exception:
            import traceback
            self.rns_init_error = traceback.format_exc()
            print(f"[embedded] RNS init failed:\n{self.rns_init_error}")

    def _prepare_listen_ports(self):
        """Stop stale chatx5 instances before binding HTTP/RNS ports."""
        if is_android():
            return
        http_holders = [
            p for p in _port_holder_pids(self.port, udp=False)
            if p != os.getpid()
        ]
        rns_holders = [
            p for p in _port_holder_pids(4242, udp=True)
            if p != os.getpid()
        ]
        if not (http_holders or rns_holders or self.force):
            return
        stop_stale_chatx5_servers(exclude_pid=os.getpid())
        deadline = time.time() + 6.0
        while time.time() < deadline:
            busy = any(
                p != os.getpid()
                for p in _port_holder_pids(self.port, udp=False)
            ) or any(
                p != os.getpid()
                for p in _port_holder_pids(4242, udp=True)
            )
            if not busy:
                break
            time.sleep(0.25)

