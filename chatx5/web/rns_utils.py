"""RNS startup utilities, port management, and shared web constants."""

import os
import re
import signal
import shutil
import socket
import subprocess
import sys
import time

from chatx5.utils.helpers import get_config_dir, get_data_dir
from chatx5.utils.platform import is_android, lan_ip as platform_lan_ip

if getattr(sys, "frozen", False):
    from chatx5.utils.rns_frozen import ensure_rns_interfaces
    ensure_rns_interfaces()

CONFIG_DIR = get_config_dir()
DATA_DIR = get_data_dir()
SETTINGS_FILE = os.path.join(CONFIG_DIR, "settings.json")
NETWORK_STATS_AUTO_RESET_SEC = 7 * 86400
SESSION_SYSTEM_LINK_CLOSED_TTL = 600


def build_desktop_rns_config(broadcast_ip="255.255.255.255"):
    return f"""[reticulum]
enable_transport = Yes
share_instance = No

[logging]
loglevel = 3

[interfaces]
  [[UDP Interface]]
    type = UDPInterface
    enabled = Yes
    listen_ip = 0.0.0.0
    listen_port = 4242
    forward_ip = {broadcast_ip}
    forward_port = 4242
    ifac_size = 16
"""


def build_android_rns_config(broadcast_ip="255.255.255.255"):
    return f"""[reticulum]
enable_transport = No
share_instance = No

[logging]
loglevel = 4

[interfaces]
  [[UDP Interface]]
    type = UDPInterface
    enabled = Yes
    listen_ip = 0.0.0.0
    listen_port = 4242
    forward_ip = {broadcast_ip}
    forward_port = 4242
    ifac_size = 16
"""


def _patch_rns_forward_ip(config_text, broadcast_ip):
    if not broadcast_ip:
        return config_text
    if "forward_ip" in config_text:
        return re.sub(r"forward_ip\s*=\s*[^\n]+", f"forward_ip = {broadcast_ip}", config_text)
    return config_text


def detect_lan_ip():
    return platform_lan_ip()


def cleanup_rns_stale():
    if is_android():
        return
    import glob as _glob
    for p in _glob.glob("/tmp/rns/*/socket"):
        try:
            os.unlink(p)
            print(f"[cleanup] Removed stale RNS socket: {p}")
        except OSError:
            pass
    for p in _glob.glob("/tmp/rns/*"):
        try:
            os.rmdir(p)
        except OSError:
            pass


def shutdown_rns_stack():
    """Release RNS UDP/TCP interfaces so ports 4242/8743 are not left open."""
    if is_android():
        return
    try:
        import RNS
        for iface in list(getattr(RNS.Transport, "interfaces", []) or []):
            try:
                RNS.Transport.remove_interface(iface)
            except Exception:
                pass
        inst = RNS.Reticulum.get_instance()
        if inst is not None:
            for attr in ("shutdown", "stop", "close"):
                fn = getattr(inst, attr, None)
                if callable(fn):
                    try:
                        fn()
                    except Exception:
                        pass
    except Exception:
        pass
    cleanup_rns_stale()


def _win_subprocess_flags():
    if sys.platform != "win32":
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _proc_cmdline(pid):
    if sys.platform == "win32":
        try:
            result = subprocess.run(
                [
                    "powershell.exe", "-NoProfile", "-Command",
                    f"(Get-CimInstance Win32_Process -Filter \"ProcessId={int(pid)}\").CommandLine",
                ],
                capture_output=True, text=True, timeout=5,
                creationflags=_win_subprocess_flags(),
            )
            return (result.stdout or "").strip()
        except (ValueError, subprocess.TimeoutExpired, OSError):
            return ""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return f.read().replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()
    except OSError:
        return ""


def _is_chatx5_process(pid):
    if sys.platform == "win32":
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {int(pid)}", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=5,
                creationflags=_win_subprocess_flags(),
            )
            line = (result.stdout or "").lower()
            return "chatx5" in line and "grok" not in line
        except (ValueError, subprocess.TimeoutExpired, OSError):
            return False
    cmd = _proc_cmdline(pid)
    return "chatx5" in cmd and "grok" not in cmd.lower()


def _port_holder_pids(port, udp=True):
    pids = []
    needle = f":{port}"
    if sys.platform == "win32":
        try:
            result = subprocess.run(
                ["netstat", "-ano"],
                capture_output=True, text=True, timeout=5,
                creationflags=_win_subprocess_flags(),
            )
            proto = "UDP" if udp else "TCP"
            for line in result.stdout.splitlines():
                upper = line.upper()
                if proto not in upper or needle not in line:
                    continue
                parts = line.split()
                if not parts:
                    continue
                try:
                    pid = int(parts[-1])
                except ValueError:
                    continue
                if pid > 0:
                    pids.append(pid)
        except (subprocess.TimeoutExpired, OSError):
            pass
        return list(dict.fromkeys(pids))
    if sys.platform == "darwin" and shutil.which("lsof"):
        try:
            proto = f"UDP:{port}" if udp else f"TCP:{port}"
            args = ["lsof", "-n", "-P", "-t", "-i", proto]
            if not udp:
                args = ["lsof", "-n", "-P", "-t", "-iTCP:%d" % port, "-sTCP:LISTEN"]
            result = subprocess.run(
                args,
                capture_output=True, text=True, timeout=5, check=False,
            )
            for pid_str in (result.stdout or "").split():
                try:
                    pid = int(pid_str)
                except ValueError:
                    continue
                if pid > 0:
                    pids.append(pid)
        except (subprocess.TimeoutExpired, OSError):
            pass
        return list(dict.fromkeys(pids))
    try:
        flag = "-u" if udp else "-t"
        result = subprocess.run(
            ["ss", "-H", "-n", flag, "-lp"],
            capture_output=True, text=True, timeout=3,
        )
        for line in result.stdout.splitlines():
            if needle not in line:
                continue
            for match in re.finditer(r"pid=(\d+)", line):
                pids.append(int(match.group(1)))
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return list(dict.fromkeys(pids))


def _is_port_in_use(port, sock_type=socket.SOCK_DGRAM, host="0.0.0.0"):
    try:
        s = socket.socket(socket.AF_INET, sock_type)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((host, port))
        s.close()
        return False
    except OSError:
        return True


def stop_stale_chatx5_servers(exclude_pid=None):
    """Stop other chatx5 server/cli processes holding RNS ports."""
    if is_android():
        return 0
    exclude_pid = exclude_pid or os.getpid()
    targets = set()
    for port in (4242, 8742):
        for pid in _port_holder_pids(port, udp=(port == 4242)):
            if pid != exclude_pid and _is_chatx5_process(pid):
                targets.add(pid)
    if sys.platform == "win32":
        try:
            result = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq chatx5.exe", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=5,
                creationflags=_win_subprocess_flags(),
            )
            for line in result.stdout.splitlines():
                if "chatx5.exe" not in line.lower():
                    continue
                parts = [p.strip('"') for p in line.split('","')]
                if len(parts) < 2:
                    continue
                try:
                    pid = int(parts[1])
                except ValueError:
                    continue
                if pid != exclude_pid:
                    targets.add(pid)
        except (subprocess.TimeoutExpired, OSError):
            pass
    else:
        try:
            result = subprocess.run(
                ["pgrep", "-f", "chatx5\\.web\\.server|chatx5\\.app|chatx5-web"],
                capture_output=True, text=True, timeout=3,
            )
            for pid_str in result.stdout.split():
                pid = int(pid_str)
                if pid != exclude_pid:
                    targets.add(pid)
        except (ValueError, subprocess.TimeoutExpired, OSError):
            pass

    if not targets:
        return 0

    print(f"[startup] Stopping stale chatx5 process(es): {', '.join(str(p) for p in sorted(targets))}")
    for pid in sorted(targets):
        try:
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/F"],
                    capture_output=True, timeout=5,
                    creationflags=_win_subprocess_flags(),
                )
            else:
                os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except (PermissionError, subprocess.TimeoutExpired, OSError):
            print(f"[startup] No permission to stop PID {pid}")

    deadline = time.time() + 5
    while time.time() < deadline:
        if not any(os.path.exists(f"/proc/{p}") for p in targets):
            break
        time.sleep(0.2)

    for pid in sorted(targets):
        if os.path.exists(f"/proc/{pid}"):
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass

    cleanup_rns_stale()
    return len(targets)


def ensure_rns_ports_free(force=False):
    """Free UDP 4242 (RNS) before startup; exit with a clear message if blocked."""
    if is_android():
        return True
    if not _is_port_in_use(4242):
        return True

    holders = _port_holder_pids(4242, udp=True)
    chatx5_holders = [p for p in holders if _is_chatx5_process(p)]

    if chatx5_holders or force:
        stop_stale_chatx5_servers()
        time.sleep(0.5)
        if not _is_port_in_use(4242):
            return True

    holders = _port_holder_pids(4242, udp=True)
    holder_txt = ", ".join(f"PID {p} ({_proc_cmdline(p)[:60]})" for p in holders) or "unknown"
    print(f"[startup] ERROR: UDP port 4242 is already in use by {holder_txt}")
    print("[startup] Another chatx5/RNS instance is probably still running.")
    if sys.platform == "win32":
        print("[startup] Close other chatx5.exe windows, or end the process in Task Manager.")
    else:
        print("[startup] Stop it with:  pkill -f chatx5.web.server")
        hint = "run.bat web --share --force" if sys.platform == "win32" else "./run.sh web --share --force"
        print(f"[startup] Or restart with:  {hint}")
    return False


def _rns_startup_failure(msg):
    """Fatal RNS startup errors must not call sys.exit from a worker thread."""
    print(f"[startup] {msg}")
    raise RuntimeError(msg)


def _pick_directory_tkinter(start):
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        try:
            root.attributes("-topmost", True)
        except tk.TclError:
            pass
        picked = filedialog.askdirectory(initialdir=start, mustexist=True, parent=root)
        root.destroy()
        return picked or None
    except Exception:
        return None