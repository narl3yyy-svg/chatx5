"""RNS announce loops, LAN/serial path refresh, and transport readiness."""

import json
import time

import RNS

from chatx5.core.lan_rns import (
    build_announce_packet,
    lan_ip_reachable,
    lan_mesh_has_peer,
    online_interfaces,
    suppress_offline_lan_transports,
    udp_interface_online,
    unicast_announce_packet,
)
from chatx5.core.messaging.constants import (
    APP_NAME,
    SERIAL_ANNOUNCE_BURST_COUNT,
    SERIAL_ANNOUNCE_BURST_INTERVAL_S,
)
from chatx5.core.rns_interfaces import (
    configured_serial_enabled,
    configured_tcp_lan_enabled,
    configured_udp_lan_enabled,
    dedupe_serial_interfaces,
    ensure_runtime_tcp_lan_server,
    ensure_tcp_client_to_peer,
    lan_discovery_configured,
    load_settings_interfaces,
    prune_dead_serial_interfaces,
    tcp_client_interface_online,
    tcp_server_interface_online,
)
from chatx5.core.serial_transfer import is_serial_interface
from chatx5.utils.platform import is_android, physical_lan_reachable


def _serial_iface_online():
    """Delegate through backend so tests can patch messaging.backend.serial_interface_online."""
    from chatx5.core.messaging import backend as bm

    return bm.serial_interface_online()


class AnnounceMixin:
    """LAN/serial RNS announces and periodic path refresh."""

    def announce(self, also_serial=True):
        self._announce(also_serial=also_serial)

    def _serial_mode_active(self):
        return (
            configured_serial_enabled(load_settings_interfaces(self.config_dir))
            and not lan_discovery_configured(load_settings_interfaces(self.config_dir))
        )

    def _announce_payload(self, include_lan_ip=True):
        payload = {
            "app": APP_NAME,
            "name": self.display_name or "",
        }
        if include_lan_ip and lan_discovery_configured(load_settings_interfaces(self.config_dir)):
            try:
                from chatx5.utils.platform import discovery_scope_ip

                ip = (discovery_scope_ip() or "").strip()
                if ip:
                    payload["ip"] = ip
            except Exception:
                pass
        return json.dumps(payload).encode("utf-8")

    def _peer_lan_ip_usable(self, peer_ip):
        """False when peer IPv4 is outside our pinned LAN scope (use serial instead)."""
        host = (peer_ip or "").strip()
        if not host:
            return False
        try:
            from chatx5.utils.lan_scope import peer_in_scope
            from chatx5.utils.platform import discovery_scope_ip

            scope = (discovery_scope_ip() or "").strip()
            if not scope:
                return True
            return peer_in_scope(host, scope)
        except Exception:
            return True

    def _announce_on_interface(self, iface, app_data=None):
        if is_serial_interface(iface) and not self.ensure_serial_runtime():
            return False
        dest = self._destination_for_interface(iface)
        if not dest or not iface:
            return False
        data = app_data if app_data is not None else self._announce_payload()
        if is_serial_interface(iface):
            try:
                payload = json.loads(data.decode("utf-8"))
                payload.pop("ip", None)
                data = json.dumps(payload).encode("utf-8")
            except Exception:
                pass
        dest.announce(app_data=data, attached_interface=iface)
        if is_serial_interface(iface):
            try:
                if self.identity_serial:
                    self.identity_serial.announce(attached_interface=iface)
            except Exception:
                pass
        else:
            try:
                if self.identity:
                    self.identity.announce(attached_interface=iface)
            except Exception:
                pass
        return True

    def _fallback_announce(self, announce_data):
        """Last-resort announce — never fan out LAN IP on USB when serial is up."""
        if self._serial_transport_ready():
            self._burst_serial_announce(count=1)
            return
        self.destination.announce(app_data=announce_data)
        try:
            RNS.Transport.identity.announce()
        except Exception:
            pass

    def _burst_serial_announce(self, count=None, interval=None, force=False):
        """Send RNS announces on serial only (default: one packet)."""
        if not force and (
            self._connect_in_progress
            or self._failover_in_progress
            or self._has_active_transfer()
        ):
            return 0
        if not self._serial_transport_ready():
            return 0
        if not self.ensure_serial_runtime():
            return 0
        suppress_offline_lan_transports()
        dedupe_serial_interfaces()
        prune_dead_serial_interfaces()
        iface = _serial_iface_online()
        if not iface:
            return 0
        burst = count or SERIAL_ANNOUNCE_BURST_COUNT
        gap = interval if interval is not None else SERIAL_ANNOUNCE_BURST_INTERVAL_S
        announce_data = self._announce_payload(include_lan_ip=False)
        for attempt in range(burst):
            self._announce_on_interface(iface, app_data=announce_data)
            if attempt < burst - 1 and gap > 0:
                time.sleep(gap)
        port = getattr(iface, "port", "?")
        if burst <= 1:
            print(f"[serial] RNS announce on {port}")
        else:
            print(f"[serial] Burst {burst} RNS announce(s) on {port}")
        cb = getattr(self, "on_after_serial_announce", None)
        if cb and burst > 0:
            try:
                cb()
            except Exception as exc:
                print(f"[serial] after-serial-announce callback failed: {exc}")
        return burst

    def _silent_announce(self, peer_ip=None, also_serial=None):
        """RNS path refresh only — no subnet beacon probe."""
        if also_serial is None:
            also_serial = not self._failover_in_progress
        if not self.destination:
            return
        announce_data = self._announce_payload()
        interfaces = load_settings_interfaces(self.config_dir)
        tcp_lan = configured_tcp_lan_enabled(interfaces)
        udp_lan = configured_udp_lan_enabled(interfaces)
        if not physical_lan_reachable():
            suppress_offline_lan_transports()
            if self._serial_transport_ready():
                self._burst_serial_announce(count=1)
            return
        prune_dead_serial_interfaces()
        hub_role, _ = self._load_hub_settings()
        use_tcp_lan = tcp_lan and hub_role != "server"
        if use_tcp_lan:
            ensure_runtime_tcp_lan_server(config_dir=self.config_dir)
            if peer_ip:
                ensure_tcp_client_to_peer(peer_ip, config_dir=self.config_dir)
            tcp_iface = tcp_server_interface_online() or tcp_client_interface_online()
            if tcp_iface:
                self._announce_on_interface(tcp_iface, app_data=announce_data)
            elif self._serial_transport_ready():
                self._burst_serial_announce(count=1)
                return
            else:
                self._fallback_announce(announce_data)
        elif udp_lan:
            udp_iface = udp_interface_online()
            if udp_iface:
                self._announce_on_interface(udp_iface, app_data=announce_data)
            elif self._serial_transport_ready():
                self._burst_serial_announce(count=1)
                return
            else:
                self._fallback_announce(announce_data)
        elif self._serial_transport_ready():
            self._burst_serial_announce(count=1)
            return
        else:
            self._fallback_announce(announce_data)
        if peer_ip and udp_lan:
            packet = build_announce_packet(self.destination, announce_data)
            unicast_announce_packet(packet, peer_ip=peer_ip, subnet_probe=False)

    def _announce(self, peer_ip=None, unicast_subnet=None, also_serial=True):
        if not self.destination:
            return
        announce_data = self._announce_payload()
        if not physical_lan_reachable() and self._serial_transport_ready():
            if also_serial:
                self._burst_serial_announce(count=1)
            return
        prune_dead_serial_interfaces()
        interfaces = load_settings_interfaces(self.config_dir)
        tcp_lan = configured_tcp_lan_enabled(interfaces)
        udp_lan = configured_udp_lan_enabled(interfaces)
        hub_role, _ = self._load_hub_settings()
        use_tcp_lan = tcp_lan and hub_role != "server"
        if use_tcp_lan:
            ensure_runtime_tcp_lan_server(config_dir=self.config_dir)
            tcp_iface = tcp_server_interface_online() or tcp_client_interface_online()
            if tcp_iface:
                self._announce_on_interface(tcp_iface, app_data=announce_data)
            else:
                self._fallback_announce(announce_data)
        elif udp_lan:
            udp_iface = udp_interface_online()
            if udp_iface:
                self._announce_on_interface(udp_iface, app_data=announce_data)
            else:
                self._fallback_announce(announce_data)
        elif self._serial_transport_ready():
            self._burst_serial_announce(count=1)
        else:
            self._fallback_announce(announce_data)
        if unicast_subnet is None:
            unicast_subnet = True
        lan_ok = (
            lan_ip_reachable()
            and lan_discovery_configured(load_settings_interfaces(self.config_dir))
        )
        if udp_lan and (peer_ip or (unicast_subnet and lan_ok)):
            packet = build_announce_packet(self.destination, announce_data)
            sent = unicast_announce_packet(
                packet,
                peer_ip=peer_ip,
                subnet_probe=unicast_subnet and lan_ok,
            )
            if sent:
                hint = f" + {sent} unicast" if sent else ""
                print(f"[messaging] Announced on LAN (name={self.display_name or 'none'}{hint})")
                if also_serial and self._serial_transport_ready() and configured_serial_enabled(interfaces):
                    self._burst_serial_announce(count=1)
                return
        if (
            also_serial
            and self._serial_transport_ready()
            and configured_serial_enabled(interfaces)
            and lan_ok
        ):
            self._burst_serial_announce(count=1)
        if lan_ok:
            print(f"[messaging] Announced on LAN (name={self.display_name or 'none'})")
        else:
            print("[messaging] Announced on RNS (serial/other — LAN disconnected)")

    def _lan_transport_ready(self):
        interfaces = load_settings_interfaces(self.config_dir)
        if not lan_discovery_configured(interfaces):
            return False
        udp_lan = configured_udp_lan_enabled(interfaces)
        tcp_lan = configured_tcp_lan_enabled(interfaces)
        if is_android():
            if tcp_lan and not udp_lan:
                return (
                    tcp_server_interface_online() is not None
                    or lan_mesh_has_peer()
                    or bool(online_interfaces(family="tcp"))
                )
            return lan_mesh_has_peer() or bool(online_interfaces(family="udp"))
        if not physical_lan_reachable():
            return lan_mesh_has_peer()
        if tcp_lan and not udp_lan:
            return (
                lan_mesh_has_peer()
                or tcp_server_interface_online() is not None
                or bool(online_interfaces(family="tcp"))
            )
        return lan_mesh_has_peer() or bool(online_interfaces(family="udp"))

    def _serial_transport_ready(self):
        return _serial_iface_online() is not None

    def _should_periodic_announce(self):
        """True when periodic LAN/serial RNS refresh may run."""
        if not self.auto_announce:
            return False
        if (
            self._connect_in_progress
            or self._failover_in_progress
            or self._has_active_transfer()
        ):
            return False
        interfaces = load_settings_interfaces(self.config_dir)
        return (
            lan_discovery_configured(interfaces)
            or (
                configured_serial_enabled(interfaces)
                and self._serial_transport_ready()
            )
        )

    def _announce_loop(self):
        lan_tick = 0
        serial_tick = 0
        while self.running:
            time.sleep(1)
            if not self.running:
                return
            if self._has_active_transfer() or not self._should_periodic_announce():
                continue
            interfaces = load_settings_interfaces(self.config_dir)
            prune_dead_serial_interfaces()
            lan_iv = max(0, int(self.lan_announce_interval_s or 0))
            ser_iv = max(0, int(self.serial_announce_interval_s or 0))
            if lan_iv <= 0 and ser_iv <= 0 and not self.auto_announce:
                continue
            if lan_iv <= 0 and self.auto_announce:
                lan_iv = self.announce_interval
            if ser_iv <= 0 and self.auto_announce:
                ser_iv = self.announce_interval
            lan_tick += 1
            serial_tick += 1
            if lan_iv > 0 and lan_tick >= lan_iv and lan_discovery_configured(interfaces):
                lan_tick = 0
                self._silent_announce(also_serial=False)
            if (
                ser_iv > 0
                and serial_tick >= ser_iv
                and configured_serial_enabled(interfaces)
                and self._serial_transport_ready()
            ):
                serial_tick = 0
                self._burst_serial_announce(count=1)