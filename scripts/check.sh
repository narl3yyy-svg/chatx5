#!/usr/bin/env bash
# Pre-push verification: unit tests + startup smoke checks.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "==> Verifying Android Python bundle sync"
bash scripts/verify-android-sync.sh

if command -v ruff >/dev/null 2>&1; then
  echo "==> Ruff lint (chatx5/)"
  ruff check chatx5/ tests/
else
  echo "==> Skipping ruff (install with: pip install ruff)"
fi

echo "==> Running unit tests"
python -m unittest discover -s tests -v

echo "==> Signal patch smoke test (background thread)"
python - <<'PY'
import signal
import threading
from chatx5.utils.platform import patch_embedded_signals

patch_embedded_signals()
errors = []

def worker():
    try:
        signal.signal(signal.SIGINT, signal.SIG_DFL)
    except ValueError as exc:
        errors.append(str(exc))

t = threading.Thread(target=worker)
t.start()
t.join()
if errors:
    raise SystemExit("signal patch failed in worker thread: " + errors[0])
print("signal patch ok")
PY

echo "==> RNS config render smoke test"
python - <<'PY'
from chatx5.core.rns_interfaces import render_rns_config, normalize_interface_list

ifaces = normalize_interface_list([
    {"id": "u1", "preset": "udp_lan", "name": "UDP", "enabled": False},
    {"id": "s1", "preset": "serial", "name": "Serial", "port": "/dev/ttyUSB0", "enabled": True},
])
text = render_rns_config(ifaces, broadcast_ip="10.0.30.255", auto_interface_enabled=False)
assert "type = UDPInterface" not in text, "disabled UDP should be omitted"
assert "type = AutoInterface" not in text, "auto interface should be off"
print("rns config render ok")
PY

echo "All checks passed."