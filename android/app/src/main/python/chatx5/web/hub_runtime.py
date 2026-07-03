"""Hub TCP relay runtime: settings apply, hot-add, and in-scope host resolution."""

import asyncio
import json
import threading

from aiohttp import web

from chatx5.core.rns_interfaces import (
    add_interface,
    hub_tcp_client_active,
    normalize_interface_list,
)


class HubRuntimeMixin:
    """Hub server/client settings and runtime TCP interface management."""

    @staticmethod
    def _is_tcp_server_iface(iface):
        return (
            iface.get("type") == "TCPServerInterface"
            or iface.get("preset") in ("tcp_server", "tcp_lan")
        )

    @staticmethod
    def _is_tcp_client_iface(iface):
        return (
            iface.get("type") == "TCPClientInterface"
            or iface.get("preset") == "tcp_client"
        )

    def _resolve_hub_host_in_scope(self, hub_host, settings=None):
        """Pick a hub host IP on the pinned LAN when the saved host is on another subnet."""
        from chatx5.utils.lan_scope import peer_in_scope
        from chatx5.utils.platform import parse_lan_interface_value

        host = (hub_host or "").strip()
        if not host:
            return host
        settings = settings or self.load_settings()
        pinned = (settings.get("lan_interface") or "").strip()
        scope = ""
        if pinned:
            _, ip = parse_lan_interface_value(pinned)
            scope = (ip or "").strip()
        if not scope:
            scope = self._discovery_scope_ip() or ""
        if scope and peer_in_scope(host, scope):
            return host
        if not scope or not self.discovery:
            return host
        saved_hash = (settings.get("hub_server_hash") or "").strip().replace(":", "")
        candidates = []
        for peer in self._scoped_peers():
            ip = (peer.get("ip") or "").strip()
            if not ip or not peer_in_scope(ip, scope):
                continue
            ph = (peer.get("hash") or "").replace(":", "")
            ih = (peer.get("identity_hash") or "").replace(":", "")
            if saved_hash and saved_hash in (ph, ih):
                return ip
            candidates.append((peer.get("last_seen") or 0, ip, peer.get("name") or ""))
        if not candidates:
            return host
        for _seen, ip, _name in sorted(candidates, reverse=True):
            try:
                from chatx5.core.http_peer import peer_get_with_fallback

                scheme = "https" if getattr(self, "use_tls", False) else "http"
                raw, _used = peer_get_with_fallback(
                    ip, int(settings.get("http_port") or self.port or 8742),
                    "/api/network-status",
                    primary_scheme=scheme,
                    timeout=2.0,
                )
                data = json.loads(raw.decode("utf-8"))
                if (data.get("hub_role") or "").strip().lower() == "server":
                    return ip
            except Exception:
                continue
        if len(candidates) == 1:
            return candidates[0][1]
        return host

    def _ensure_hub_host_in_scope(self, settings, persist=True):
        """Resolve hub_host to an in-scope peer IP and optionally persist it."""
        settings = dict(settings or {})
        if (settings.get("hub_role") or "off") != "client":
            return settings
        host = (settings.get("hub_host") or "").strip()
        if not host:
            return settings
        if not getattr(self, "discovery", None) or not getattr(self, "config_dir", None):
            return settings
        resolved = self._resolve_hub_host_in_scope(host, settings)
        if not resolved or resolved == host:
            return settings
        settings["hub_host"] = resolved
        if persist:
            self.save_settings(settings)
            print(f"[hub] Updated hub host {host} → {resolved} (pinned LAN scope)")
            try:
                threading.Timer(
                    0.3,
                    lambda: self._apply_hub_runtime(dict(settings)),
                ).start()
            except Exception:
                pass
        return settings

    def _apply_hub_settings(self, settings):
        settings = self._ensure_hub_host_in_scope(
            settings, persist=bool(getattr(self, "discovery", None)),
        )
        hub_role = settings.get("hub_role", "off")
        hub_host = (settings.get("hub_host") or "").strip()
        hub_port = int(settings.get("hub_port") or 4242)
        interfaces = normalize_interface_list(settings.get("rns_interfaces"))
        if hub_role == "server":
            server = None
            for iface in interfaces:
                if self._is_tcp_server_iface(iface):
                    server = iface
                    break
            if not server:
                interfaces = add_interface(interfaces, "tcp_server")
                interfaces = normalize_interface_list(interfaces)
                server = next(
                    i for i in interfaces if self._is_tcp_server_iface(i)
                )
            from chatx5.core.rns_interfaces import normalize_hub_listen_interfaces

            server["enabled"] = True
            server["type"] = "TCPServerInterface"
            listen_ips = normalize_hub_listen_interfaces(settings)
            server["listen_ip"] = listen_ips[0]
            server["listen_port"] = hub_port
            settings["hub_listen_interfaces"] = listen_ips
            for iface in interfaces:
                if iface is server:
                    continue
                if self._is_tcp_client_iface(iface):
                    iface["enabled"] = False
                if iface.get("preset") == "tcp_lan":
                    iface["enabled"] = False
        elif hub_role == "client":
            if not hub_host:
                settings["rns_interfaces"] = normalize_interface_list(interfaces)
                return settings
            for iface in interfaces:
                if iface.get("preset") == "tcp_server":
                    iface["enabled"] = False
            hub_tcp_on = hub_tcp_client_active(settings)
            updated = False
            for iface in interfaces:
                if iface.get("preset") != "tcp_client":
                    continue
                iface["target_host"] = hub_host
                iface["target_port"] = hub_port
                iface["type"] = "TCPClientInterface"
                iface["enabled"] = hub_tcp_on
                updated = True
                break
            if hub_tcp_on and not updated:
                interfaces = add_interface(interfaces, "tcp_client")
                interfaces = normalize_interface_list(interfaces)
                client = next(
                    i for i in interfaces if i.get("preset") == "tcp_client"
                )
                client["target_host"] = hub_host
                client["target_port"] = hub_port
                client["enabled"] = True
        else:
            for iface in interfaces:
                if iface.get("preset") in ("tcp_client", "tcp_server"):
                    iface["enabled"] = False
        settings["rns_interfaces"] = normalize_interface_list(interfaces)
        return settings

    def _apply_hub_runtime(self, settings=None):
        """Hot-apply hub interfaces on a running RNS instance (Android/desktop)."""
        settings = settings or self.load_settings()
        hub_role = settings.get("hub_role", "off")
        # This can be invoked from a background threading.Timer (see
        # _ensure_hub_host_in_scope) that may fire while the server is still
        # initializing or already torn down, so read messaging defensively.
        messaging = getattr(self, "messaging", None)
        try:
            from chatx5.core.rns_interfaces import (
                ensure_runtime_tcp_client,
                ensure_runtime_tcp_hub,
                remove_tcp_client_interfaces,
                remove_tcp_client_to_host,
                tcp_client_interface_online,
            )
            if hub_role == "server":
                remove_tcp_client_interfaces()
                iface = ensure_runtime_tcp_hub(settings, self.config_dir)
                if iface and messaging:
                    messaging._silent_announce()
                    messaging._schedule_hub_queue_drain()
                from chatx5.core.rns_interfaces import (
                    normalize_hub_listen_interfaces,
                    tcp_server_interfaces_online,
                )

                hub_port = int(settings.get("hub_port") or 4242)
                listen_ips = normalize_hub_listen_interfaces(settings)
                online_rows = tcp_server_interfaces_online(hub_port)
                if online_rows:
                    bound = ", ".join(
                        f"{getattr(i, 'listen_ip', '0.0.0.0')}:{hub_port}"
                        for i in online_rows
                    )
                    print(f"[hub] TCP hub server listening on {bound}")
                    if messaging:
                        messaging._schedule_hub_link_ensure(delay=1.0)
                else:
                    want = ", ".join(f"{ip}:{hub_port}" for ip in listen_ips)
                    print(
                        f"[hub] TCP hub server not online yet ({want}) "
                        f"— check hub role and restart"
                    )
            elif hub_role == "client":
                settings = self._ensure_hub_host_in_scope(settings, persist=True)
                host = (settings.get("hub_host") or "").strip()
                port = int(settings.get("hub_port") or 4242)
                if hub_tcp_client_active(settings):
                    iface = ensure_runtime_tcp_client(settings, self.config_dir)
                    if iface and messaging:
                        messaging._silent_announce()
                    online = tcp_client_interface_online()
                    if online:
                        print(f"[hub] TCP hub client connected to {host}:{port}")
                        if messaging:
                            messaging._schedule_hub_link_ensure(delay=1.0)
                            messaging._schedule_hub_queue_drain()
                    elif host:
                        print(f"[hub] TCP hub client connecting to {host}:{port}...")
                        if messaging:
                            messaging._schedule_hub_link_ensure(delay=4.0)
                elif host:
                    remove_tcp_client_to_host(host, port)
                    pinned = (settings.get("lan_interface") or "").strip()
                    if pinned:
                        print(
                            f"[hub] Hub TCP client paused — {host} is not on your "
                            f"pinned LAN ({pinned}); P2P on this subnet continues"
                        )
                    else:
                        print(f"[hub] Hub TCP client disabled for {host}:{port}")
            else:
                remove_targets = set()
                host = (settings.get("hub_host") or "").strip()
                if host:
                    remove_targets.add((host, int(settings.get("hub_port") or 4242)))
                for iface in normalize_interface_list(settings.get("rns_interfaces")):
                    if iface.get("preset") != "tcp_client":
                        continue
                    th = (iface.get("target_host") or "").strip()
                    tp = int(iface.get("target_port") or 4242)
                    if th:
                        remove_targets.add((th, tp))
                for th, tp in remove_targets:
                    remove_tcp_client_to_host(th, tp)
        except Exception as exc:
            print(f"[hub] Runtime hub apply failed: {exc}")

    def _schedule_hub_bootstrap_retries(self):
        """Retry hub TCP client bring-up after discovery populates in-scope hub host."""
        settings = self.load_settings()
        if settings.get("hub_role") != "client":
            return

        def attempt(label):
            if self._shutting_down:
                return
            try:
                self._apply_hub_runtime(self.load_settings())
            except Exception as exc:
                print(f"[hub] Bootstrap retry ({label}) failed: {exc}")

        for delay, label in ((3.0, "3s"), (8.0, "8s"), (20.0, "20s")):
            timer = threading.Timer(delay, attempt, args=(label,))
            timer.daemon = True
            timer.start()

    async def handle_hub_ensure(self, request):
        """Ensure hub TCP RNS link is up (group chat reconnect after P2P switch)."""
        ok, err = await self._wait_for_rns()
        if not ok:
            return web.json_response({"error": err or "not ready"}, status=400)
        settings = self.load_settings()
        if settings.get("hub_role", "off") == "off":
            return web.json_response({"error": "hub disabled"}, status=400)

        def _run():
            linked = False
            clients = 0
            if self.messaging:
                linked = bool(self.messaging.ensure_hub_link(background=False))
                clients = len(self.messaging._hub_tcp_linked_peers())
                self.messaging._schedule_hub_queue_drain(delay=0.3)
            return linked, clients

        linked, clients = await asyncio.to_thread(_run)
        return web.json_response({
            "status": "ok",
            "hub_group_linked": linked or clients > 0,
            "hub_clients_linked": clients,
        })

    def _maybe_update_hub_server_hash(self, peer_hash, link=None):
        settings = self.load_settings()
        if settings.get("hub_role") != "client":
            return
        if self.messaging and link and not self.messaging._link_is_hub_tcp(link):
            return
        clean = self._peer_dest_hash(peer_hash)
        if not clean or self._is_self_hash(clean):
            return
        if settings.get("hub_server_hash") != clean:
            settings["hub_server_hash"] = clean
            self.save_settings(settings)
            print(f"[hub] Recorded hub server hash {clean[:16]}...")