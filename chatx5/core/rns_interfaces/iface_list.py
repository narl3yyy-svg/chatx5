"""Interface list CRUD and LAN transport policy."""
import copy
import uuid

from chatx5.core.rns_interfaces.presets import (
    INTERFACE_PRESETS,
    SERIAL_DEFAULT_BAUD,
    default_interface_list,
)
from chatx5.core.rns_interfaces.serial_ports import (
    _sync_serial_enabled,
    list_serial_ports,
)


def _new_id():
    return uuid.uuid4().hex[:8]


def normalize_interface_list(items):
    if not items:
        return default_interface_list()
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        preset = item.get("preset") or "udp_lan"
        base = copy.deepcopy(INTERFACE_PRESETS.get(preset, INTERFACE_PRESETS["udp_lan"])["defaults"])
        merged = {**base, **item}
        merged.setdefault("id", _new_id())
        merged.setdefault("preset", preset)
        merged.setdefault("name", INTERFACE_PRESETS.get(preset, {}).get("label", merged.get("type", "Interface")))
        merged["type"] = INTERFACE_PRESETS.get(preset, {}).get("type", merged.get("type", "UDPInterface"))
        out.append(_sync_serial_enabled(merged))
    if not out:
        return default_interface_list()
    seen_serial_ports = set()
    seen_udp = False
    seen_tcp_lan = False
    deduped = []
    for item in out:
        itype = item.get("type")
        preset = item.get("preset")
        if itype == "SerialInterface":
            port = (item.get("port") or "").strip()
            if port and port in seen_serial_ports:
                continue
            if port:
                seen_serial_ports.add(port)
        elif itype == "UDPInterface" or preset == "udp_lan":
            if seen_udp:
                continue
            seen_udp = True
        elif preset == "tcp_lan" or (
            itype == "TCPServerInterface" and preset not in ("tcp_server",)
        ):
            if seen_tcp_lan:
                continue
            seen_tcp_lan = True
        deduped.append(item)
    return deduped


def _pick_default_serial_port():
    ports = list_serial_ports()
    if not ports:
        return ""
    for port in ports:
        if port.get("accessible"):
            return port.get("device") or ""
    return ports[0].get("device") or ""


def add_interface(items, preset_key):
    preset = INTERFACE_PRESETS.get(preset_key)
    if not preset:
        raise ValueError(f"Unknown preset: {preset_key}")
    items = normalize_interface_list(items)
    if preset_key == "udp_lan":
        if any(
            i.get("preset") == "udp_lan" or i.get("type") == "UDPInterface"
            for i in items
        ):
            return items
    if preset_key == "tcp_lan":
        if any(i.get("preset") == "tcp_lan" for i in items):
            return items
    entry = {
        "id": _new_id(),
        "preset": preset_key,
        "name": f"{preset['label']} {_new_id()}",
        **copy.deepcopy(preset["defaults"]),
    }
    if preset_key == "serial":
        entry["port"] = _pick_default_serial_port()
        entry["speed"] = SERIAL_DEFAULT_BAUD
        entry = _sync_serial_enabled(entry)
    items.append(entry)
    return items


def set_primary_lan_transport(interfaces, preset_key):
    """Replace UDP/TCP LAN presets with a single chosen LAN transport."""
    if preset_key not in ("udp_lan", "tcp_lan"):
        return normalize_interface_list(interfaces)
    items = normalize_interface_list(interfaces)
    kept = []
    for iface in items:
        preset = iface.get("preset")
        itype = iface.get("type")
        if preset in ("udp_lan", "tcp_lan"):
            continue
        if itype == "UDPInterface":
            continue
        if preset == "tcp_server" or (itype == "TCPServerInterface" and preset == "tcp_server"):
            kept.append(iface)
            continue
        if itype == "TCPServerInterface" and preset != "tcp_server":
            continue
        kept.append(iface)
    if any(i.get("preset") == preset_key for i in kept):
        return kept
    preset = INTERFACE_PRESETS.get(preset_key)
    if not preset:
        return kept
    entry = {
        "id": _new_id(),
        "preset": preset_key,
        "name": f"{preset['label']} {_new_id()}",
        **copy.deepcopy(preset["defaults"]),
    }
    # Avoid add_interface([]) — normalize_interface_list([]) re-inserts UDP LAN.
    return normalize_interface_list(kept + [entry] if kept else [entry])


def lan_transport_hub_policy(hub_role, preset_key):
    """Whether Primary LAN transport can switch to TCP LAN while hub mode is on."""
    hub_role = (hub_role or "off").strip().lower()
    preset_key = (preset_key or "").strip()
    if preset_key != "tcp_lan":
        return {"allowed": True, "warning": ""}
    if hub_role == "server":
        return {
            "allowed": False,
            "warning": (
                "Hub server already uses TCP port 4242 for group-chat relay. "
                "LAN peers still connect via UDP discovery and links. "
                "Set hub mode to Off to use TCP LAN for peer traffic."
            ),
        }
    if hub_role == "client":
        return {
            "allowed": True,
            "warning": (
                "Hub client: TCP LAN applies to local 1:1 peers only. "
                "Group chat still uses the TCP link to your hub host. "
                "Restart chatx5 after changing."
            ),
        }
    return {"allowed": True, "warning": ""}


def hub_tcp_client_active(settings):
    """Whether the hub-client TCP dial should be enabled in config/runtime."""
    settings = settings or {}
    if (settings.get("hub_role") or "off") != "client":
        return False
    host = (settings.get("hub_host") or "").strip()
    if not host:
        return False
    from chatx5.utils.lan_scope import same_lan_scope
    from chatx5.utils.platform import parse_lan_interface_value

    pinned = (settings.get("lan_interface") or "").strip()
    if pinned:
        _, ip = parse_lan_interface_value(pinned)
        scope = (ip or "").strip()
        if scope and not same_lan_scope(host, scope):
            return False
    return True


def remove_tcp_client_to_host(target_host, target_port=4242):
    """Remove hub (or other) TCP client dials matching host:port; keep TCP LAN peer dials."""
    target_host = (target_host or "").strip()
    target_port = int(target_port or 4242)
    if not target_host:
        return 0
    try:
        import RNS
    except Exception:
        return 0
    removed = 0
    for iface in list(getattr(RNS.Transport, "interfaces", []) or []):
        if type(iface).__name__ != "TCPClientInterface":
            continue
        host = (getattr(iface, "target_host", None) or "").strip()
        port = int(
            getattr(iface, "target_port", None)
            or getattr(iface, "port", None)
            or 4242
        )
        if host != target_host or port != target_port:
            continue
        try:
            RNS.Transport.remove_interface(iface)
            removed += 1
            print(f"[hub] Removed RNS TCP client to {host}:{port}")
        except Exception as exc:
            print(f"[hub] Could not remove TCP client to {host}:{port}: {exc}")
    return removed


def delete_interface(items, iface_id):
    items = normalize_interface_list(items)
    return [i for i in items if i.get("id") != iface_id]


def tcp_client_target_is_local(target_host):
    host = (target_host or "").strip().lower()
    if not host or host in ("127.0.0.1", "localhost", "0.0.0.0"):
        return True
    try:
        from chatx5.utils.platform import local_ipv4_addresses
        return host in {ip.lower() for ip in local_ipv4_addresses()}
    except Exception:
        return False


def tcp_client_target_warning(target_host):
    if not tcp_client_target_is_local(target_host):
        return None
    return (
        "TCP Client target is this machine. For LAN peers, use TCP Hub Server here "
        "and TCP Client on the remote device (or use UDP LAN / TCP LAN for subnet peers)."
    )


def update_interface(items, iface_id, updates):
    items = normalize_interface_list(items)
    if not iface_id:
        raise ValueError("id required")
    found = False
    out = []
    for item in items:
        if item.get("id") != iface_id:
            out.append(item)
            continue
        found = True
        updated = {**item}
        preset = updated.get("preset") or ""
        itype = updated.get("type", "")
        if "enabled" in updates:
            updated["enabled"] = bool(updates["enabled"])
            if preset == "serial" or itype == "SerialInterface":
                updated["user_disabled"] = not bool(updates["enabled"])
        if preset == "serial" or itype == "SerialInterface":
            if "port" in updates:
                updated["port"] = str(updates["port"] or "").strip()
            if "speed" in updates and updates["speed"] is not None:
                updated["speed"] = int(updates["speed"])
            updated = _sync_serial_enabled(updated)
        elif preset in ("tcp_client", "tcp_server", "tcp_lan") or itype in ("TCPClientInterface", "TCPServerInterface"):
            if "target_host" in updates and updates["target_host"]:
                updated["target_host"] = str(updates["target_host"]).strip()
            if "target_port" in updates and updates["target_port"] is not None:
                updated["target_port"] = int(updates["target_port"])
            if "listen_ip" in updates and updates["listen_ip"]:
                updated["listen_ip"] = str(updates["listen_ip"]).strip()
            if "listen_port" in updates and updates["listen_port"] is not None:
                updated["listen_port"] = int(updates["listen_port"])
        elif preset == "udp_lan" or itype == "UDPInterface":
            for key in ("listen_ip", "listen_port", "forward_ip", "forward_port"):
                if key in updates and updates[key] is not None:
                    updated[key] = updates[key]
        out.append(updated)
    if not found:
        raise ValueError(f"Interface not found: {iface_id}")
    return out


def configured_serial_port(settings_interfaces=None):
    for iface in normalize_interface_list(settings_interfaces):
        if iface.get("type") != "SerialInterface":
            continue
        port = (iface.get("port") or "").strip()
        if port:
            return port, int(iface.get("speed") or SERIAL_DEFAULT_BAUD)
    return "", SERIAL_DEFAULT_BAUD

