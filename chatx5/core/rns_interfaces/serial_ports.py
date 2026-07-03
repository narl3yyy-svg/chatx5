"""Serial port enumeration and accessibility."""
import glob
import os

from chatx5.core.rns_interfaces.presets import (
    ANDROID_SERIAL_PERMISSION_HINT,
    SERIAL_ACCESS_GROUPS,
    SERIAL_PERMISSION_HINT,
)


def serial_permission_hint_for_process():
    try:
        from chatx5.utils.platform import is_android
        if is_android():
            return ANDROID_SERIAL_PERMISSION_HINT
    except Exception:
        pass
    if user_has_serial_group_access():
        return (
            "Port exists but this chatx5 process still cannot open it. "
            "Stop chatx5 completely and start it again after logging out/in."
        )
    return SERIAL_PERMISSION_HINT
def user_has_serial_group_access():
    """True if the current user belongs to a group that can access serial ports."""
    try:
        import grp
        groups = set(os.getgroups())
        for name in SERIAL_ACCESS_GROUPS:
            try:
                if grp.getgrnam(name).gr_gid in groups:
                    return True
            except KeyError:
                continue
    except Exception:
        pass
    return False


def _android_serial_port_status(port):
    path = (port or "").strip()
    if not path:
        return "none"
    try:
        from usb4a import usb
        device = usb.get_usb_device(path)
        if not device:
            return "missing"
        if usb.has_usb_permission(device):
            return "ok"
        return "permission_denied"
    except Exception:
        return "missing"


def serial_port_status(port):
    """Return none, missing, permission_denied, or ok."""
    path = (port or "").strip()
    if not path:
        return "none"
    try:
        from chatx5.utils.platform import is_android
        if is_android():
            return _android_serial_port_status(path)
    except Exception:
        pass
    if not os.path.exists(path):
        return "missing"
    if not os.access(path, os.R_OK | os.W_OK):
        return "permission_denied"
    return "ok"


def serial_port_accessible(port):
    return serial_port_status(port) == "ok"


def serial_port_available(port):
    """Backward-compatible alias for serial_port_accessible."""
    return serial_port_accessible(port)


def serial_runtime_active(iface):
    """True when serial is configured (not user-disabled) and the port is accessible."""
    if iface.get("preset") != "serial" and iface.get("type") != "SerialInterface":
        return False
    if iface.get("user_disabled"):
        return False
    port = (iface.get("port") or "").strip()
    if not port:
        return False
    return serial_port_accessible(port)


def _sync_serial_enabled(iface):
    """Keep enabled in sync with port selection and live accessibility."""
    if iface.get("preset") != "serial" and iface.get("type") != "SerialInterface":
        return iface
    if iface.get("user_disabled"):
        iface["enabled"] = False
        return iface
    port = (iface.get("port") or "").strip()
    if not port:
        iface["enabled"] = False
    else:
        iface["enabled"] = serial_port_status(port) == "ok"
    return iface


def serial_skip_reason(port):
    status = serial_port_status(port)
    path = (port or "").strip() or "(none)"
    if status == "permission_denied":
        return path, "permission denied — " + SERIAL_PERMISSION_HINT
    if status == "missing":
        return path, "not connected"
    if status == "none":
        return path, "no port selected"
    return path, "inactive"

def _is_useful_serial_port(entry):
    device = entry.device or ""
    if any(
        device.startswith(prefix)
        for prefix in ("/dev/ttyUSB", "/dev/ttyACM", "/dev/ttyAMA", "/dev/rfcomm", "/dev/cu.")
    ):
        return True
    desc = (entry.description or "").strip().lower()
    hwid = (entry.hwid or "").strip().lower()
    if desc and desc not in ("n/a", "none"):
        return True
    if hwid and hwid not in ("n/a", "none"):
        return True
    if "/ttyS" in device:
        return False
    return bool(device)


def _glob_serial_devices():
    devices = set()
    for pattern in ("/dev/ttyUSB*", "/dev/ttyACM*", "/dev/ttyAMA*", "/dev/rfcomm*"):
        devices.update(glob.glob(pattern))
    return sorted(devices)


def _serial_port_entry(device, description="", hwid=""):
    status = serial_port_status(device)
    return {
        "device": device,
        "description": description or "",
        "hwid": hwid or "",
        "accessible": status == "ok",
        "status": status,
    }


def list_android_usb_serial_ports():
    """Return USB serial devices visible to the Android USB host API."""
    try:
        from usb4a import usb
    except Exception as exc:
        print(f"[serial] Android USB modules unavailable: {exc}")
        return []
    by_device = {}
    try:
        for device in usb.get_usb_device_list():
            if device is None:
                continue
            name = str(device.getDeviceName())
            vid = int(device.getVendorId())
            pid = int(device.getProductId())
            mfr = device.getManufacturerName()
            prod = device.getProductName()
            desc_parts = [p for p in (mfr, prod) if p]
            description = " ".join(desc_parts).strip() or f"USB serial {vid:04x}:{pid:04x}"
            by_device[name] = _serial_port_entry(
                name,
                description,
                f"VID:PID={vid:04x}:{pid:04x}",
            )
    except Exception as exc:
        print(f"[serial] Android USB enumeration failed: {exc}")
    return [by_device[k] for k in sorted(by_device)]


def list_serial_ports():
    """Return serial devices from pyserial and /dev/ttyUSB* /dev/ttyACM* globs."""
    try:
        from chatx5.utils.platform import is_android
        if is_android():
            return list_android_usb_serial_ports()
    except Exception:
        pass
    by_device = {}
    try:
        from serial.tools import list_ports
        for entry in sorted(list_ports.comports(), key=lambda p: p.device):
            if not _is_useful_serial_port(entry):
                continue
            by_device[entry.device] = _serial_port_entry(
                entry.device, entry.description or "", entry.hwid or ""
            )
    except Exception:
        pass
    for device in _glob_serial_devices():
        if device not in by_device:
            by_device[device] = _serial_port_entry(device)
    return [by_device[k] for k in sorted(by_device)]

