"""Hub group-chat TCP relay helpers."""

import json
import os
import threading
import time
from urllib import request as urlrequest

from chatx5.core.discovery import normalize_hash
from chatx5.core.lan_rns import request_paths_for_hash
from chatx5.core.messaging.peers import is_hub_peer_hash


class HubMixin:
    """Hub server/client link establishment and settings helpers."""

    def _persist_hub_server_hash(self, hub_hash):
        hub_hash = normalize_hash(hub_hash or "")
        if len(hub_hash) != 32 or not self.config_dir:
            return
        try:
            path = os.path.join(self.config_dir, "settings.json")
            with open(path, encoding="utf-8") as fh:
                settings = json.load(fh)
            if settings.get("hub_server_hash") == hub_hash:
                return
            settings["hub_server_hash"] = hub_hash
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(settings, fh, indent=2)
            print(f"[hub] Saved hub server hash {hub_hash[:16]}...")
        except Exception as exc:
            print(f"[hub] Could not save hub server hash: {exc}")

    def _fetch_hub_server_hash_from_peer(self, hub_host, http_port=None):
        hub_host = (hub_host or "").strip()
        if not hub_host:
            return ""
        port = int(http_port or getattr(self, "http_port", None) or 8742)
        url = f"http://{hub_host}:{port}/api/network-status"
        try:
            req = urlrequest.Request(url, method="GET")
            with urlrequest.urlopen(req, timeout=4) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            hub_hash = normalize_hash(data.get("hub_server_hash") or "")
            if len(hub_hash) == 32:
                return hub_hash
        except Exception as exc:
            print(f"[hub] Fetch hub server hash from {hub_host}:{port} failed: {exc}")
        return ""

    def _hub_tcp_transport_online(self):
        from chatx5.core.rns_interfaces import (
            hub_tcp_client_active,
            load_settings_interfaces,
            tcp_client_interface_online,
            tcp_server_interface_online,
        )

        role, _ = self._load_hub_settings()
        if role == "off":
            return False
        try:
            import json as _json
            path = os.path.join(self.config_dir, "settings.json")
            with open(path, encoding="utf-8") as fh:
                settings = _json.load(fh)
        except Exception:
            settings = {}
        hub_port = int(settings.get("hub_port") or 4242)
        if role == "server":
            return tcp_server_interface_online(hub_port) is not None
        if role == "client":
            if not hub_tcp_client_active(settings):
                return False
            return tcp_client_interface_online() is not None
        return False

    def ensure_hub_link(self, background=True):
        """Ensure an RNS link exists over the hub TCP transport (client or server)."""
        if self._interrupted():
            return False
        role, hub_hash = self._load_hub_settings()
        if role == "off":
            return False
        if not self._hub_tcp_transport_online():
            if role == "client":
                hub_host, _ = self._hub_endpoint_from_settings()
                if hub_host:
                    print(f"[hub] TCP transport to {hub_host}:4242 not online yet")
            return False
        if role == "server":
            peers = self._hub_tcp_linked_peers()
            if peers:
                return True
            print("[hub] Hub server waiting for client TCP link(s)...")
            return False
        hub_host, _ = self._hub_endpoint_from_settings()
        if not hub_hash and hub_host:
            hub_hash = self._fetch_hub_server_hash_from_peer(
                hub_host, getattr(self, "http_port", 8742),
            )
            if hub_hash:
                self._persist_hub_server_hash(hub_hash)
        if not hub_hash:
            print("[hub] Hub server identity unknown — ensure hub server is running with --share")
            return False
        peer = self.dest_hash_for(hub_hash)
        if not peer or peer == "unknown":
            return False
        if self._peer_link_active(peer):
            return True
        if self._connect_in_progress:
            return False
        request_paths_for_hash(peer, family="tcp")
        print(f"[hub] Opening hub link to {peer[:16]}... (TCP)")
        return self.connect_to(
            peer,
            peer_ip=None,
            user_initiated=not background,
            respond_to_wake=background,
        )

    def _schedule_hub_link_ensure(self, delay=2.0):
        role, _ = self._load_hub_settings()
        if role == "off":
            return

        def run():
            try:
                if self.running:
                    self.ensure_hub_link(background=True)
                    self._schedule_hub_queue_drain()
            except Exception as exc:
                print(f"[hub] Link ensure error: {exc}")

        timer = threading.Timer(delay, run)
        timer.daemon = True
        timer.start()