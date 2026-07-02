#!/usr/bin/env bash
# chatx5 uninstaller — removes everything ./run.sh web / install.sh creates.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"

echo "=== chatx5 Uninstaller ==="
echo

CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/chatx5"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/chatx5"
CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/chatx5"
PORTABLE_DATA=""

if [ -n "${CHATX5_PORTABLE:-}" ]; then
    PORTABLE_DATA="$CHATX5_PORTABLE/chatx5-data"
elif [ -d "$ROOT/chatx5-data" ]; then
    PORTABLE_DATA="$(cd "$ROOT/chatx5-data" && pwd)"
fi

stop_chatx5_processes() {
    echo "[1/7] Stopping running chatx5 / RNS processes..."
    local stopped=0
    for pattern in "chatx5.web.server" "chatx5.app" "run.sh web" "launch-server.sh"; do
        if pgrep -f "$pattern" >/dev/null 2>&1; then
            pkill -f "$pattern" 2>/dev/null || true
            stopped=1
        fi
    done
    if [ "$stopped" -eq 1 ]; then
        sleep 1
        echo "  Stopped chatx5 server process(es)"
    else
        echo "  No chatx5 server process found"
    fi
}

remove_dir_prompt() {
    local label="$1"
    local path="$2"
    if [ ! -e "$path" ]; then
        return 0
    fi
    echo "  $label: $path"
    read -r -p "    Remove? (y/N) " reply
    if [[ $reply =~ ^[Yy]$ ]]; then
        if rm -rf "$path"; then
            echo "    Removed."
        else
            echo "    FAILED to remove (check permissions)."
            return 1
        fi
    else
        echo "    Kept."
    fi
}

remove_local_venv() {
    echo "[3/7] Local project environment (.venv, egg-info)..."
    if [ -d "$ROOT/.venv" ]; then
        echo "  .venv: $ROOT/.venv"
        read -r -p "    Remove? (y/N) " reply
        if [[ $reply =~ ^[Yy]$ ]]; then
            rm -rf "$ROOT/.venv" && echo "    Removed .venv"
        else
            echo "    Kept .venv"
        fi
    else
        echo "  No .venv in project folder"
    fi
    if [ -d "$ROOT/chatx5.egg-info" ]; then
        rm -rf "$ROOT/chatx5.egg-info"
        echo "  Removed chatx5.egg-info"
    fi
}

remove_pip_installs() {
    echo "[2/7] Removing pip / pipx package installs..."
    if command -v pipx &>/dev/null; then
        if pipx uninstall chatx5 2>/dev/null; then
            echo "  Removed chatx5 from pipx"
        else
            echo "  chatx5 not found in pipx"
        fi
    else
        echo "  pipx not found, skipping"
    fi
    if command -v python3 &>/dev/null; then
        python3 -m pip uninstall -y chatx5 2>/dev/null && echo "  Removed chatx5 from python3 -m pip" || true
    fi
}

cleanup_rns_sockets() {
    echo "[6/7] Cleaning stale RNS sockets in /tmp/rns ..."
    local count=0
    if [ -d /tmp/rns ]; then
        while IFS= read -r -d '' sock; do
            rm -f "$sock" 2>/dev/null && count=$((count + 1)) || true
        done < <(find /tmp/rns -name socket -print0 2>/dev/null)
        find /tmp/rns -type d -empty -delete 2>/dev/null || true
    fi
    echo "  Removed $count stale socket(s)"
}

stop_chatx5_processes
remove_pip_installs
remove_local_venv

echo "[4/7] Application data (identity, settings, chat history, RNS config, transfers):"
remove_dir_prompt "Config" "$CONFIG_DIR"
remove_dir_prompt "Data" "$DATA_DIR"
remove_dir_prompt "Cache" "$CACHE_DIR"
if [ -n "$PORTABLE_DATA" ]; then
    remove_dir_prompt "Portable data" "$PORTABLE_DATA"
fi

echo "[5/7] Optional: remove editable install artifacts in project folder"
if [ -d "$ROOT/build" ]; then
    echo "  build/: $ROOT/build"
    read -r -p "    Remove? (y/N) " reply
    if [[ $reply =~ ^[Yy]$ ]]; then
        rm -rf "$ROOT/build" && echo "    Removed build/"
    fi
fi

cleanup_rns_sockets

echo "[7/7] Checking for leftover binaries..."
LEFTOVER=0
for bin in chatx5 chatx5-web; do
    if command -v "$bin" &>/dev/null; then
        echo "  WARNING: $bin still found at $(command -v "$bin")"
        LEFTOVER=1
    fi
done
if [ "$LEFTOVER" -eq 0 ]; then
    echo "  No leftover binaries found."
fi

echo
echo "=== Uninstall complete ==="
echo "To reinstall: ./install.sh  or  ./run.sh web --share"