"""RNS interface presets and default lists."""

import copy

INTERFACE_PRESETS = {
    "udp_lan": {
        "label": "UDP LAN",
        "type": "UDPInterface",
        "defaults": {
            "enabled": True,
            "listen_ip": "0.0.0.0",
            "listen_port": 4242,
            "forward_ip": "255.255.255.255",
            "forward_port": 4242,
            "ifac_size": 16,
        },
    },
    "tcp_lan": {
        "label": "TCP LAN",
        "type": "TCPServerInterface",
        "defaults": {
            "enabled": True,
            "listen_ip": "0.0.0.0",
            "listen_port": 4242,
            "ifac_size": 16,
        },
    },
    "tcp_client": {
        "label": "TCP Client",
        "type": "TCPClientInterface",
        "defaults": {
            "enabled": True,
            "target_host": "127.0.0.1",
            "target_port": 4242,
            "ifac_size": 16,
        },
    },
    "tcp_server": {
        "label": "TCP Hub Server",
        "type": "TCPServerInterface",
        "defaults": {
            "enabled": True,
            "listen_ip": "0.0.0.0",
            "listen_port": 4242,
            "ifac_size": 16,
        },
    },
    "serial": {
        "label": "Serial",
        "type": "SerialInterface",
        "defaults": {
            "enabled": False,
            "port": "",
            "speed": 57600,
            "ifac_size": 16,
        },
    },
}

SERIAL_DEFAULT_BAUD = 57600
_serial_hot_add_callback = None
_last_serial_unavail_log = 0.0

SERIAL_BAUD_RATES = [
    1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600,
]

SERIAL_ACCESS_GROUPS = ("dialout", "uucp")

SERIAL_PERMISSION_HINT = (
    "Serial port access denied for this chatx5 process. "
    "Ubuntu: sudo usermod -aG dialout $USER — then fully log out of Ubuntu and log back in "
    "(a new terminal is not enough). Stop chatx5, start it again, then refresh serial ports."
)

ANDROID_SERIAL_PERMISSION_HINT = (
    "USB serial permission required. Plug in your USB adapter (OTG cable), tap Refresh devices, "
    "then tap Grant USB access when prompted. Restart the app after applying serial settings."
)

DEFAULT_INTERFACE_LIST = [
    {
        "id": "tcp-client",
        "preset": "tcp_client",
        "name": "TCP Client",
        "type": "TCPClientInterface",
        "enabled": True,
        "target_host": "127.0.0.1",
        "target_port": 4242,
        "ifac_size": 16,
    },
]

ANDROID_DEFAULT_INTERFACE_LIST = [
    {
        "id": "udp-lan",
        "preset": "udp_lan",
        "name": "UDP Interface",
        "type": "UDPInterface",
        "enabled": True,
        "listen_ip": "0.0.0.0",
        "listen_port": 4242,
        "forward_ip": "255.255.255.255",
        "forward_port": 4242,
        "ifac_size": 16,
    },
]


def standalone_needs_udp(interfaces, hub_role="off"):
    """True when only a loopback TCP client is configured with no hub — LAN cannot work."""
    return android_standalone_needs_udp(interfaces, hub_role)


def default_interface_list():
    """Fresh installs get UDP LAN so discovery works without manual TCP hub setup."""
    try:
        from chatx5.utils.platform import is_android
        if is_android():
            return copy.deepcopy(ANDROID_DEFAULT_INTERFACE_LIST)
    except Exception:
        pass
    return copy.deepcopy(ANDROID_DEFAULT_INTERFACE_LIST)


def android_standalone_needs_udp(interfaces, hub_role="off"):
    """True when Android has only a loopback TCP client and no hub — cannot work standalone."""
    from chatx5.core.rns_interfaces.iface_list import normalize_interface_list
    if hub_role and hub_role != "off":
        return False
    items = normalize_interface_list(interfaces)
    if not items:
        return True
    has_udp = any(i.get("type") == "UDPInterface" for i in items)
    if has_udp:
        return False
    has_tcp_lan = any(i.get("preset") == "tcp_lan" for i in items)
    if has_tcp_lan:
        return False
    if len(items) != 1:
        return False
    only = items[0]
    if only.get("type") != "TCPClientInterface":
        return False
    host = (only.get("target_host") or "").strip().lower()
    return host in ("127.0.0.1", "localhost", "")


