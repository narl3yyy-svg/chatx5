#!/usr/bin/env bash
# chatx5 uninstaller — removes app data chatx5 creates (with prompts).
set -euo pipefail

echo "=== chatx5 Uninstaller ==="
echo

CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/chatx5"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/chatx5"
CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/chatx5"
PORTABLE_DATA=""

if [ -n "${CHATX5_PORTABLE:-}" ]; then
    PORTABLE_DATA="$CHATX5_PORTABLE/chatx5-data"
elif [ -d "./chatx5-data" ]; then
    PORTABLE_DATA="$(cd "./chatx5-data" && pwd)"
fi

stop_chatx5_processes() {
    echo "[1/5] Stopping running chatx5 / RNS processes..."
    local stopped=0
    for pattern in "chatx5.web.server" "chatx5.app" "run.sh web"; do
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

cleanup_rns_sockets() {
    echo "[4/5] Cleaning stale RNS sockets in /tmp/rns ..."
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

if command -v pipx &>/dev/null; then
    echo "[2/5] Removing pipx package..."
    if pipx uninstall chatx5 2>/dev/null; then
        echo "  Removed chatx5 from pipx"
    else
        echo "  chatx5 not found in pipx (already removed)"
    fi
else
    echo "[2/5] pipx not found, skipping package removal"
fi

echo "[3/5] Application data (identity, settings, chat history, RNS config):"
remove_dir_prompt "Config" "$CONFIG_DIR"
remove_dir_prompt "Data" "$DATA_DIR"
remove_dir_prompt "Cache" "$CACHE_DIR"
if [ -n "$PORTABLE_DATA" ]; then
    remove_dir_prompt "Portable data" "$PORTABLE_DATA"
fi

cleanup_rns_sockets

echo "[5/5] Checking for leftover binaries..."
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