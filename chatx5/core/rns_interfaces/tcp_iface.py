"""Runtime RNS TCP interface management."""
import os

from chatx5.core.rns_interfaces.iface_list import (
    hub_tcp_client_active,
    normalize_interface_list,
)
from chatx5.core.rns_interfaces.runtime_common import _register_hot_rns_interface
from chatx5.core.rns_interfaces.serial_runtime import (
    _last_serial_unavail_log,
)
from chatx5.core.rns_interfaces.settings import (
    configured_tcp_lan_enabled,
    configured_tcp_lan_listen,
)


def _tcp_server_listen_ip(iface):
    return (getattr(iface, "listen_ip", None) or "0.0.0.0").strip() or "0.0.0.0"


def _tcp_server_listen_port(iface):
    port = getattr(iface, "listen_port", None) or getattr(iface, "port", None)
    return int(port or 4242)


def find_tcp_server_interface(listen_ip="0.0.0.0", listen_port=4242):
    """Return TCPServerInterface matching listen_ip:port, if present."""
    listen_ip = (listen_ip or "0.0.0.0").strip() or "0.0.0.0"
    listen_port = int(listen_port or 4242)
    try:
        import RNS
        for iface in getattr(RNS.Transport, "interfaces", []) or []:
            if type(iface).__name__ != "TCPServerInterface":
                continue
            if (
                _tcp_server_listen_ip(iface) == listen_ip
                and _tcp_server_listen_port(iface) == listen_port
            ):
                return iface
    except Exception:
        pass
    return None


def tcp_server_interfaces_online(listen_port=None):
    """All online TCPServerInterface rows, optionally filtered by port."""
    out = []
    try:
        import RNS
        for iface in getattr(RNS.Transport, "interfaces", []) or []:
            if type(iface).__name__ != "TCPServerInterface":
                continue
            if listen_port is not None:
                if _tcp_server_listen_port(iface) != int(listen_port):
                    continue
            if getattr(iface, "online", False):
                out.append(iface)
    except Exception:
        pass
    return out


def tcp_server_interface_online(listen_port=None):
    """Return an online TCPServerInterface, optionally matching listen_port."""
    rows = tcp_server_interfaces_online(listen_port)
    return rows[0] if rows else None


def normalize_hub_listen_interfaces(settings=None, raw=None):
    """Normalize hub server bind addresses from settings (default all interfaces)."""
    if raw is None and settings:
        raw = settings.get("hub_listen_interfaces")
    if not raw:
        return ["0.0.0.0"]
    if isinstance(raw, str):
        raw = [part.strip() for part in raw.split(",") if part.strip()]
    ips = []
    for item in raw or []:
        ip = (item or "").strip()
        if not ip:
            continue
        if ip == "0.0.0.0":
            return ["0.0.0.0"]
        if ip not in ips:
            ips.append(ip)
    return ips or ["0.0.0.0"]


def remove_tcp_hub_listeners(listen_port, keep_ips=None):
    """Remove hub TCP listeners on listen_port except addresses in keep_ips."""
    keep = set(normalize_hub_listen_interfaces(raw=keep_ips or ["0.0.0.0"]))
    if "0.0.0.0" in keep:
        keep = {"0.0.0.0"}
    removed = 0
    try:
        import RNS
        for iface in list(getattr(RNS.Transport, "interfaces", []) or []):
            if type(iface).__name__ != "TCPServerInterface":
                continue
            lip = _tcp_server_listen_ip(iface)
            port = _tcp_server_listen_port(iface)
            if port != int(listen_port or 4242):
                continue
            name = (getattr(iface, "name", None) or "").strip()
            if name and not name.startswith("TCP Hub"):
                continue
            if lip in keep:
                continue
            try:
                RNS.Transport.remove_interface(iface)
                removed += 1
                print(f"[hub] Removed TCP hub listener {lip}:{port}")
            except Exception as exc:
                print(f"[hub] Could not remove TCP hub listener {lip}:{port}: {exc}")
    except Exception:
        pass
    return removed


def remove_tcp_listeners_on_port(listen_port, log_tag="hub"):
    """Remove all TCPServerInterface bindings on listen_port (hub vs tcp_lan conflicts)."""
    listen_port = int(listen_port or 4242)
    removed = 0
    try:
        import RNS
        for iface in list(getattr(RNS.Transport, "interfaces", []) or []):
            if type(iface).__name__ != "TCPServerInterface":
                continue
            if _tcp_server_listen_port(iface) != listen_port:
                continue
            lip = _tcp_server_listen_ip(iface)
            try:
                RNS.Transport.remove_interface(iface)
                removed += 1
                print(f"[{log_tag}] Removed TCP listener {lip}:{listen_port}")
            except Exception as exc:
                print(f"[{log_tag}] Could not remove TCP listener {lip}:{listen_port}: {exc}")
    except Exception:
        pass
    return removed


def tcp_client_interface_online():
    """Return an online TCPClientInterface if any."""
    try:
        import RNS
        for iface in getattr(RNS.Transport, "interfaces", []) or []:
            if type(iface).__name__ == "TCPClientInterface" and getattr(iface, "online", False):
                return iface
    except Exception:
        pass
    return None


def hot_add_tcp_server_interface(
    listen_ip="0.0.0.0", listen_port=4242, ifac_size=16, name=None, log_tag="hub",
    replace_existing=False,
):
    """Attach TCPServerInterface when hub server or TCP LAN is enabled after RNS started."""
    listen_ip = (listen_ip or "0.0.0.0").strip() or "0.0.0.0"
    listen_port = int(listen_port or 4242)
    try:
        import RNS
        from RNS.Interfaces.TCPInterface import TCPServerInterface
    except Exception as exc:
        print(f"[{log_tag}] TCP server hot-add unavailable: {exc}")
        return None

    existing = find_tcp_server_interface(listen_ip, listen_port)
    if existing and getattr(existing, "online", False) and not replace_existing:
        return existing

    if existing and replace_existing:
        try:
            RNS.Transport.remove_interface(existing)
        except Exception:
            pass

    iface_name = name or (
        f"TCP Hub {listen_ip}:{listen_port}"
        if log_tag == "hub"
        else f"TCP LAN {listen_port}"
    )
    try:
        iface = TCPServerInterface(RNS.Transport, {
            "name": iface_name,
            "listen_ip": listen_ip,
            "listen_port": listen_port,
            "ifac_size": ifac_size,
        })
        _register_hot_rns_interface(iface, ifac_size=ifac_size)
        print(f"[{log_tag}] Hot-added TCP server on {listen_ip}:{listen_port}")
        return iface
    except Exception as exc:
        print(f"[{log_tag}] TCP server hot-add failed for {listen_ip}:{listen_port}: {exc}")
        return None


def remove_tcp_client_interfaces():
    """Remove TCPClientInterface(s) from the running transport."""
    try:
        import RNS
    except Exception:
        return 0
    removed = 0
    for iface in list(getattr(RNS.Transport, "interfaces", []) or []):
        if type(iface).__name__ != "TCPClientInterface":
            continue
        try:
            RNS.Transport.remove_interface(iface)
            removed += 1
            print(f"[hub] Removed RNS TCPClientInterface {getattr(iface, 'name', iface)}")
        except Exception as exc:
            print(f"[hub] Could not remove TCPClientInterface: {exc}")
    return removed


def hot_add_tcp_client_interface(target_host, target_port=4242, ifac_size=16, log_tag="hub"):
    """Attach TCPClientInterface for hub client or TCP LAN peer dial."""
    target_host = (target_host or "").strip()
    target_port = int(target_port or 4242)
    if not target_host:
        return None
    try:
        import RNS
        from RNS.Interfaces.TCPInterface import TCPClientInterface
    except Exception as exc:
        print(f"[{log_tag}] TCP client hot-add unavailable: {exc}")
        return None

    for iface in list(getattr(RNS.Transport, "interfaces", []) or []):
        if type(iface).__name__ != "TCPClientInterface":
            continue
        host = (getattr(iface, "target_host", None) or "").strip()
        port = int(
            getattr(iface, "target_port", None)
            or getattr(iface, "port", None)
            or 4242
        )
        if host == target_host and port == target_port:
            return iface

    name = f"TCP Client {target_host}:{target_port}"
    try:
        iface = TCPClientInterface(RNS.Transport, {
            "name": name,
            "target_host": target_host,
            "target_port": target_port,
            "ifac_size": ifac_size,
        })
        _register_hot_rns_interface(iface, ifac_size=ifac_size)
        print(f"[{log_tag}] Hot-added TCP client to {target_host}:{target_port}")
        return iface
    except Exception as exc:
        print(f"[{log_tag}] TCP client hot-add failed for {target_host}:{target_port}: {exc}")
        return None


def ensure_runtime_tcp_client(settings=None, config_dir=None):
    """Dial TCP hub server when hub_role is client (runtime hot-add)."""
    if not settings:
        try:
            import json

            from chatx5.utils.helpers import get_config_dir
            path = os.path.join(config_dir or get_config_dir(), "settings.json")
            with open(path, encoding="utf-8") as fh:
                settings = json.load(fh)
        except Exception:
            return None
    if not hub_tcp_client_active(settings):
        return None
    host = (settings.get("hub_host") or "").strip()
    if not host:
        return None
    try:
        import RNS
        if RNS.Reticulum.get_instance() is None:
            return None
    except Exception:
        return None
    port = int(settings.get("hub_port") or 4242)
    ifac_size = 16
    for iface in normalize_interface_list(settings.get("rns_interfaces")):
        if iface.get("type") != "TCPClientInterface":
            continue
        if not iface.get("enabled", True):
            continue
        ifac_size = int(iface.get("ifac_size") or ifac_size)
        break
    return hot_add_tcp_client_interface(
        target_host=host, target_port=port, ifac_size=ifac_size,
    )


def ensure_runtime_tcp_lan_server(settings=None, config_dir=None):
    """Start TCP LAN listener when tcp_lan preset is enabled (not hub mode)."""
    if not settings:
        try:
            import json

            from chatx5.utils.helpers import get_config_dir
            path = os.path.join(config_dir or get_config_dir(), "settings.json")
            with open(path, encoding="utf-8") as fh:
                settings = json.load(fh)
        except Exception:
            return None
    hub_role = settings.get("hub_role") or "off"
    if hub_role == "server":
        return None
    if not configured_tcp_lan_enabled(settings.get("rns_interfaces")):
        return None
    try:
        import RNS
        if RNS.Reticulum.get_instance() is None:
            return None
    except Exception:
        return None
    listen_ip, listen_port, ifac_size = configured_tcp_lan_listen(
        settings.get("rns_interfaces"), config_dir,
    )
    return hot_add_tcp_server_interface(
        listen_ip=listen_ip,
        listen_port=listen_port,
        ifac_size=ifac_size,
        name=f"TCP LAN {listen_port}",
        log_tag="tcp-lan",
    )


def ensure_tcp_client_to_peer(peer_ip, port=None, settings=None, config_dir=None):
    """Dial a discovered peer over TCP LAN (runtime hot-add)."""
    peer_ip = (peer_ip or "").strip()
    if not peer_ip:
        return None
    if not settings:
        try:
            import json

            from chatx5.utils.helpers import get_config_dir
            path = os.path.join(config_dir or get_config_dir(), "settings.json")
            with open(path, encoding="utf-8") as fh:
                settings = json.load(fh)
        except Exception:
            return None
    if (settings.get("hub_role") or "off") == "server":
        return None
    if not configured_tcp_lan_enabled(settings.get("rns_interfaces")):
        return None
    try:
        import RNS
        if RNS.Reticulum.get_instance() is None:
            return None
    except Exception:
        return None
    if port is None:
        _, port, ifac_size = configured_tcp_lan_listen(
            settings.get("rns_interfaces"), config_dir,
        )
    else:
        _, _, ifac_size = configured_tcp_lan_listen(
            settings.get("rns_interfaces"), config_dir,
        )
    return hot_add_tcp_client_interface(
        target_host=peer_ip,
        target_port=int(port or 4242),
        ifac_size=ifac_size,
        log_tag="tcp-lan",
    )


def ensure_runtime_tcp_hub(settings=None, config_dir=None):
    """Start TCP hub listener(s) when hub_role is server (runtime hot-add)."""
    if not settings:
        try:
            import json

            from chatx5.utils.helpers import get_config_dir
            path = os.path.join(config_dir or get_config_dir(), "settings.json")
            with open(path, encoding="utf-8") as fh:
                settings = json.load(fh)
        except Exception:
            return None
    if (settings.get("hub_role") or "off") != "server":
        return None
    try:
        import RNS
        if RNS.Reticulum.get_instance() is None:
            return None
    except Exception:
        return None
    listen_ips = normalize_hub_listen_interfaces(settings)
    listen_port = int(settings.get("hub_port") or 4242)
    ifac_size = 16
    for iface in normalize_interface_list(settings.get("rns_interfaces")):
        if iface.get("type") != "TCPServerInterface":
            continue
        if not iface.get("enabled", True):
            continue
        listen_port = int(iface.get("listen_port") or listen_port)
        ifac_size = int(iface.get("ifac_size") or ifac_size)
        break
    if "0.0.0.0" in listen_ips:
        remove_tcp_listeners_on_port(listen_port, log_tag="hub")
        listen_ips = ["0.0.0.0"]
    else:
        remove_tcp_listeners_on_port(listen_port, log_tag="hub")
    first = None
    for lip in listen_ips:
        iface = hot_add_tcp_server_interface(
            listen_ip=lip, listen_port=listen_port, ifac_size=ifac_size,
        )
        if iface and first is None:
            first = iface
    return first


def ensure_runtime_serial(settings_interfaces=None):
    import chatx5.core.rns_interfaces as ri

    if not ri.configured_serial_enabled(settings_interfaces):
        return None
    port, speed = ri.configured_serial_port(settings_interfaces)
    if not port:
        return None
    if not ri.serial_port_accessible(port):
        ri.remove_serial_interfaces(port)
        return None
    existing = ri.find_serial_interface(port)
    if existing:
        if ri.serial_interface_is_ready(existing):
            return existing
        for _ in range(20):
            ri.time.sleep(0.1)
            existing = ri.find_serial_interface(port)
            if existing and ri.serial_interface_is_ready(existing):
                return existing
        if ri.find_serial_interface(port):
            return ri.find_serial_interface(port)
    if ri.serial_port_accessible(port):
        added = ri.hot_add_serial_interface(port, speed=speed)
        if added:
            return added
        print(f"[serial] Hot-add skipped for {port} — interface already loaded or port busy")
        return ri.find_serial_interface(port)
    global _last_serial_unavail_log
    now = ri.time.time()
    if now - _last_serial_unavail_log >= 30.0:
        status = ri.serial_port_status(port)
        print(f"[serial] Runtime serial unavailable on {port} ({status})")
        _last_serial_unavail_log = now
    return None
