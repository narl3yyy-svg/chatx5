"""Settings-backed interface configuration helpers."""
import os

from chatx5.core.rns_interfaces.serial_ports import serial_runtime_active


def load_settings_interfaces(config_dir=None):
    """Load rns_interfaces from settings.json (best-effort)."""
    try:
        import json

        from chatx5.utils.helpers import get_config_dir

        root = config_dir or get_config_dir()
        path = os.path.join(root, "settings.json")
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data.get("rns_interfaces")
    except Exception:
        return None


def configured_udp_lan_enabled(interfaces=None, config_dir=None):
    """True when UDP LAN preset is present and enabled in settings."""
    from chatx5.core.rns_interfaces.iface_list import normalize_interface_list
    items = normalize_interface_list(interfaces or load_settings_interfaces(config_dir))
    return any(
        i.get("type") == "UDPInterface" and i.get("enabled", True)
        for i in items
    )


def configured_tcp_lan_enabled(interfaces=None, config_dir=None):
    """True when TCP LAN preset is present and enabled in settings."""
    from chatx5.core.rns_interfaces.iface_list import normalize_interface_list
    items = normalize_interface_list(interfaces or load_settings_interfaces(config_dir))
    return any(
        i.get("preset") == "tcp_lan" and i.get("enabled", True)
        for i in items
    )


def configured_tcp_lan_listen(interfaces=None, config_dir=None):
    """Return (listen_ip, listen_port, ifac_size) for the enabled TCP LAN preset."""
    from chatx5.core.rns_interfaces.iface_list import normalize_interface_list
    listen_ip = "0.0.0.0"
    listen_port = 4242
    ifac_size = 16
    for iface in normalize_interface_list(interfaces or load_settings_interfaces(config_dir)):
        if iface.get("preset") != "tcp_lan" or not iface.get("enabled", True):
            continue
        listen_ip = (iface.get("listen_ip") or listen_ip).strip() or "0.0.0.0"
        listen_port = int(iface.get("listen_port") or listen_port)
        ifac_size = int(iface.get("ifac_size") or ifac_size)
        break
    return listen_ip, listen_port, ifac_size


def configured_serial_enabled(interfaces=None, config_dir=None):
    """True when serial is enabled in settings (not user-disabled)."""
    from chatx5.core.rns_interfaces.iface_list import normalize_interface_list
    items = normalize_interface_list(interfaces or load_settings_interfaces(config_dir))
    for iface in items:
        if iface.get("preset") != "serial" and iface.get("type") != "SerialInterface":
            continue
        if serial_runtime_active(iface):
            return True
    return False


def lan_discovery_configured(interfaces=None, config_dir=None):
    """True when LAN discovery/beacon should be active (UDP LAN or TCP LAN)."""
    return (
        configured_udp_lan_enabled(interfaces, config_dir)
        or configured_tcp_lan_enabled(interfaces, config_dir)
    )
