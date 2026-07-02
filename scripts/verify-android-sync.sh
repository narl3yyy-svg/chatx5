#!/usr/bin/env bash
# Fail if the Android Chaquopy bundle diverges from chatx5/ (run sync-android.sh to fix).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/chatx5"
DST="$ROOT/android/app/src/main/python/chatx5"

if [ ! -d "$SRC" ] || [ ! -d "$DST" ]; then
  echo "verify-android-sync: missing source or destination tree" >&2
  exit 1
fi

DIFF="$(diff -rq "$SRC" "$DST" \
  --exclude='__pycache__' \
  --exclude='*.pyc' 2>/dev/null || true)"

if [ -n "$DIFF" ]; then
  echo "Android Python bundle is out of sync with chatx5/:" >&2
  echo "$DIFF" >&2
  echo "Run: bash scripts/sync-android.sh" >&2
  exit 1
fi

echo "Android Python bundle matches chatx5/ ($(find "$SRC" -name '*.py' | wc -l | tr -d ' ') files)"