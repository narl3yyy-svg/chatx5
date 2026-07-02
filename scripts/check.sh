#!/usr/bin/env bash
# Pre-push verification: lint, types, unit tests, startup smoke checks.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Prefer project venv for lint/type tools (pip install -e ".[dev]")
if [ -x "$ROOT/.venv/bin/ruff" ]; then
  RUFF="$ROOT/.venv/bin/ruff"
  MYPY="$ROOT/.venv/bin/mypy"
else
  RUFF="ruff"
  MYPY="mypy"
fi

# Runtime tests need RNS/aiohttp — use venv only when those imports work.
PYTHON="python"
if [ -x "$ROOT/.venv/bin/python" ] && "$ROOT/.venv/bin/python" -c "import RNS" 2>/dev/null; then
  PYTHON="$ROOT/.venv/bin/python"
fi

echo "==> Verifying Android Python bundle sync"
bash scripts/verify-android-sync.sh

if ! command -v "$RUFF" >/dev/null 2>&1; then
  echo "ERROR: ruff is required. Install dev tools:" >&2
  echo "  python -m venv .venv && .venv/bin/pip install -e '.[dev]'" >&2
  exit 1
fi

echo "==> Ruff lint (chatx5/, tests/)"
"$RUFF" check chatx5/ tests/

if ! command -v "$MYPY" >/dev/null 2>&1; then
  echo "ERROR: mypy is required. Install dev tools:" >&2
  echo "  python -m venv .venv && .venv/bin/pip install -e '.[dev]'" >&2
  exit 1
fi

echo "==> Mypy (chatx5/)"
"$MYPY" chatx5

echo "==> Running unit tests"
"$PYTHON" -m unittest discover -s tests -v

echo "==> Signal patch smoke test (background thread)"
"$PYTHON" - <<'PY'
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
"$PYTHON" - <<'PY'
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