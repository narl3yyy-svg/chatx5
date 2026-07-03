"""RNS config file rendering."""
import sys

from chatx5.core.rns_interfaces.iface_list import normalize_interface_list
from chatx5.core.rns_interfaces.presets import SERIAL_DEFAULT_BAUD
from chatx5.core.rns_interfaces.settings import configured_tcp_lan_enabled
from chatx5.core.rns_interfaces.tcp_iface import normalize_hub_listen_interfaces


def render_rns_config(
    interfaces, broadcast_ip=None, android=False, log=print, auto_interface_enabled=True,
    hub_listen_ips=None, hub_port=None,
):
    import chatx5.core.rns_interfaces as ri

    normalized = normalize_interface_list(interfaces)
    has_tcp_server = any(
        i.get("type") == "TCPServerInterface" and i.get("enabled", True)
        for i in normalized
    )
    has_serial = any(
        i.get("type") == "SerialInterface" and i.get("enabled", True)
        for i in normalized
    )
    has_udp = any(
        i.get("type") == "UDPInterface" and i.get("enabled", True)
        for i in normalized
    )
    has_tcp_lan = configured_tcp_lan_enabled(normalized)
    # Android needs transport enabled for serial/UDP/TCP LAN path discovery.
    enable_transport = "Yes" if (
        not android or has_tcp_server or has_serial or has_udp or has_tcp_lan
    ) else "No"
    lines = [
        "[reticulum]",
        f"enable_transport = {enable_transport}",
        "share_instance = No",
        "link_mtu_discovery = Yes",
        "",
        "[logging]",
        "loglevel = 3" if not android else "loglevel = 4",
        "",
        "[interfaces]",
    ]
    skipped_serial = []
    seen_serial_ports = set()
    seen_udp = False
    seen_tcp_lan = False
    seen_tcp_hub = False
    hub_ips = normalize_hub_listen_interfaces(raw=hub_listen_ips) if hub_listen_ips else []
    hub_listen_port = int(hub_port or 4242)
    for iface in normalized:
        itype = iface.get("type", "")
        if itype == "SerialInterface":
            port = (iface.get("port") or "").strip()
            if port:
                if port in seen_serial_ports:
                    continue
                seen_serial_ports.add(port)
            if android:
                port, reason = ri.serial_skip_reason(iface.get("port"))
                skipped_serial.append((iface.get("name") or "Serial", port, "hot-add on Android"))
                continue
            if ri.serial_runtime_active(iface):
                name = iface.get("name") or iface.get("type", "Serial")
                lines.append(f"  [[{name}]]")
                lines.append("    type = SerialInterface")
                lines.append("    enabled = Yes")
                lines.append(f"    port = {iface.get('port', '/dev/ttyUSB0')}")
                lines.append(f"    speed = {iface.get('speed', SERIAL_DEFAULT_BAUD)}")
                if iface.get("ifac_size"):
                    lines.append(f"    ifac_size = {iface.get('ifac_size')}")
                lines.append("")
                continue
            port, reason = ri.serial_skip_reason(iface.get("port"))
            skipped_serial.append((iface.get("name") or "Serial", port, reason))
            continue
        elif not iface.get("enabled", True):
            continue
        preset = iface.get("preset")
        if itype == "UDPInterface" or preset == "udp_lan":
            if seen_udp:
                continue
            seen_udp = True
        elif preset == "tcp_server" or (
            itype == "TCPServerInterface" and preset == "tcp_server"
        ):
            if seen_tcp_hub:
                continue
            seen_tcp_hub = True
            if hub_ips:
                for lip in hub_ips:
                    lines.append(f"  [[TCP Hub {lip}:{hub_listen_port}]]")
                    lines.append("    type = TCPServerInterface")
                    lines.append("    enabled = Yes")
                    lines.append(f"    listen_ip = {lip}")
                    lines.append(f"    listen_port = {hub_listen_port}")
                    if iface.get("ifac_size"):
                        lines.append(f"    ifac_size = {iface.get('ifac_size')}")
                    lines.append("")
                continue
        elif preset == "tcp_lan" or (
            itype == "TCPServerInterface" and preset not in ("tcp_server",)
        ):
            if seen_tcp_lan:
                continue
            seen_tcp_lan = True
        name = iface.get("name") or iface.get("type", "Interface")
        lines.append(f"  [[{name}]]")
        lines.append(f"    type = {iface.get('type', 'UDPInterface')}")
        lines.append("    enabled = Yes")
        if itype == "UDPInterface":
            listen_ip = iface.get("listen_ip", "0.0.0.0")
            forward_ip = iface.get("forward_ip") or broadcast_ip or "255.255.255.255"
            lines.append(f"    listen_ip = {listen_ip}")
            lines.append(f"    listen_port = {iface.get('listen_port', 4242)}")
            lines.append(f"    forward_ip = {forward_ip}")
            lines.append(f"    forward_port = {iface.get('forward_port', 4242)}")
            if iface.get("ifac_size"):
                lines.append(f"    ifac_size = {iface.get('ifac_size')}")
        elif itype in ("TCPClientInterface", "TCPServerInterface"):
            if itype == "TCPServerInterface":
                lines.append(f"    listen_ip = {iface.get('listen_ip', '0.0.0.0')}")
                lines.append(f"    listen_port = {iface.get('listen_port', 4242)}")
            else:
                lines.append(f"    target_host = {iface.get('target_host', '127.0.0.1')}")
                lines.append(f"    target_port = {iface.get('target_port', 4242)}")
            if iface.get("ifac_size"):
                lines.append(f"    ifac_size = {iface.get('ifac_size')}")
        elif itype == "SerialInterface":
            lines.append(f"    port = {iface.get('port', '/dev/ttyUSB0')}")
            lines.append(f"    speed = {iface.get('speed', SERIAL_DEFAULT_BAUD)}")
            if iface.get("ifac_size"):
                lines.append(f"    ifac_size = {iface.get('ifac_size')}")
        lines.append("")
    # AutoInterface also binds UDP 4242 — never combine with explicit LAN presets.
    has_udp_lan = any(
        i.get("type") == "UDPInterface" and i.get("enabled", True)
        for i in normalized
    )
    has_lan_preset = any(
        i.get("preset") in ("udp_lan", "tcp_lan") for i in normalized
    )
    if (
        auto_interface_enabled
        and not has_udp_lan
        and not has_tcp_lan
        and not has_lan_preset
        and not android
        and sys.platform != "win32"
    ):
        lines.extend([
            "  [[Default Interface]]",
            "    type = AutoInterface",
            "    enabled = Yes",
            "",
        ])
    for name, port, reason in skipped_serial:
        if log:
            log(f"[config] Serial '{name}' skipped — {port}: {reason}")
    return "\n".join(lines).rstrip() + "\n"
