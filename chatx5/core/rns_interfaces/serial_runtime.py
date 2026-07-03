"""Runtime RNS SerialInterface management."""

from chatx5.core.rns_interfaces.presets import SERIAL_DEFAULT_BAUD
from chatx5.core.rns_interfaces.runtime_common import (
    _register_hot_rns_interface,
)
from chatx5.core.rns_interfaces.serial_ports import (
    serial_port_accessible,
)

_serial_hot_add_callback = None
_last_serial_unavail_log = 0.0

def find_serial_interface(port):
    """Return the first RNS SerialInterface bound to port, if any."""
    port = (port or "").strip()
    if not port:
        return None
    try:
        import RNS
        for iface in getattr(RNS.Transport, "interfaces", []) or []:
            if type(iface).__name__ != "SerialInterface":
                continue
            if getattr(iface, "port", None) == port:
                return iface
    except Exception:
        pass
    return None


def serial_interface_is_ready(iface):
    """True when a SerialInterface has finished opening and is usable."""
    if not iface or not hasattr(iface, "mode"):
        return False
    if not getattr(iface, "online", False):
        return False
    serial = getattr(iface, "serial", None)
    if serial is None:
        return False
    try:
        return bool(serial.is_open)
    except Exception:
        return False


def _serial_interface_score(iface):
    score = 0
    if hasattr(iface, "mode"):
        score += 1
    if getattr(iface, "online", False):
        score += 2
    serial = getattr(iface, "serial", None)
    if serial is not None:
        try:
            if serial.is_open:
                score += 4
        except Exception:
            pass
    return score


def _stop_serial_reconnect(iface):
    iface.online = False
    try:
        iface.reconnect_port = lambda: None
    except Exception:
        pass
    serial = getattr(iface, "serial", None)
    if serial is not None:
        try:
            if getattr(serial, "is_open", False):
                serial.close()
        except Exception:
            pass

def dedupe_serial_interfaces(port=None):
    """Keep one SerialInterface per USB port — duplicates break the link."""
    try:
        import RNS
    except Exception:
        return 0
    keepers = {}
    removed = 0
    for iface in list(getattr(RNS.Transport, "interfaces", []) or []):
        if type(iface).__name__ != "SerialInterface":
            continue
        p = getattr(iface, "port", None)
        if port and p != port:
            continue
        if not p:
            continue
        prev = keepers.get(p)
        if prev is None:
            keepers[p] = iface
            continue
        if _serial_interface_score(iface) > _serial_interface_score(prev):
            keepers[p] = iface
            drop = prev
        else:
            drop = iface
        _stop_serial_reconnect(drop)
        try:
            RNS.Transport.remove_interface(drop)
            removed += 1
            print(f"[serial] Removed duplicate SerialInterface on {p}")
        except Exception:
            pass
        if drop is prev:
            keepers[p] = iface
    return removed


def remove_serial_interfaces(port=None):
    """Remove SerialInterface(s) from the running transport (stops reconnect spam)."""
    try:
        import RNS
    except Exception:
        return 0
    removed = 0
    for iface in list(getattr(RNS.Transport, "interfaces", []) or []):
        if type(iface).__name__ != "SerialInterface":
            continue
        if port and getattr(iface, "port", None) != port:
            continue
        _stop_serial_reconnect(iface)
        try:
            RNS.Transport.remove_interface(iface)
            removed += 1
            print(f"[serial] Removed RNS SerialInterface {getattr(iface, 'name', iface)}")
        except Exception as exc:
            print(f"[serial] Could not remove SerialInterface: {exc}")
    return removed


def prune_dead_serial_interfaces():
    """Drop broken or offline serial interfaces so announces/paths use LAN only."""
    try:
        import RNS
    except Exception:
        return 0
    removed = 0
    for iface in list(getattr(RNS.Transport, "interfaces", []) or []):
        if type(iface).__name__ != "SerialInterface":
            continue
        port = getattr(iface, "port", None)
        broken = not hasattr(iface, "mode")
        unplugged = bool(port) and not serial_port_accessible(port)
        if broken or unplugged:
            _stop_serial_reconnect(iface)
            try:
                RNS.Transport.remove_interface(iface)
                removed += 1
            except Exception:
                pass
    if removed:
        print(f"[serial] Pruned {removed} dead SerialInterface(s)")
    return removed


def register_serial_hot_add_callback(callback):
    """Register callback invoked when a new SerialInterface is hot-added at runtime."""
    global _serial_hot_add_callback
    _serial_hot_add_callback = callback


def _notify_serial_hot_add(iface):
    cb = _serial_hot_add_callback
    if not cb or not iface:
        return
    try:
        cb(iface)
    except Exception as exc:
        print(f"[serial] Hot-add callback error: {exc}")


def hot_add_serial_interface(port, speed=SERIAL_DEFAULT_BAUD, ifac_size=16):
    """Attach SerialInterface to a running RNS instance when USB is plugged in later."""
    import chatx5.core.rns_interfaces as ri

    port = (port or "").strip()
    if not port or not ri.serial_port_accessible(port):
        return None
    try:
        from chatx5.utils.platform import is_android
        if is_android():
            from chatx5.core.android_serial import ensure_android_serial_patch
            ensure_android_serial_patch()
    except Exception:
        pass
    try:
        import RNS
        from RNS.Interfaces.SerialInterface import SerialInterface
    except Exception as exc:
        print(f"[serial] Hot-add unavailable: {exc}")
        return None

    ri.dedupe_serial_interfaces(port)
    existing = ri.find_serial_interface(port)
    if existing:
        return existing

    name = f"Serial {port}"
    try:
        iface = SerialInterface(RNS.Transport, {
            "name": name,
            "port": port,
            "speed": int(speed),
            "ifac_size": ifac_size,
        })
        _register_hot_rns_interface(iface, ifac_size=ifac_size)
        ri.dedupe_serial_interfaces(port)
        print(f"[serial] Hot-added RNS SerialInterface on {port}")
        _notify_serial_hot_add(iface)
        return iface
    except Exception as exc:
        print(f"[serial] Hot-add failed for {port}: {exc}")
        return None

