#!/usr/bin/env bash
# Verify Chaquopy uses canonical chatx5/ sources (Phase 13 — bundle no longer tracked).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/chatx5"
GRADLE="$ROOT/android/app/build.gradle.kts"
BUNDLE="$ROOT/android/app/src/main/python/chatx5"

if [ ! -d "$SRC" ]; then
  echo "verify-android-sync: missing canonical Python tree at chatx5/" >&2
  exit 1
fi

if ! grep -qE 'setSrcDirs\(listOf\("src/main/python", "\.\./\.\."\)\)' "$GRADLE"; then
  echo "verify-android-sync: android/app/build.gradle.kts must setSrcDirs main.py + repo root" >&2
  exit 1
fi

if [ -d "$BUNDLE" ]; then
  echo "verify-android-sync: removing stale Android bundle at android/app/src/main/python/chatx5" >&2
  rm -rf "$BUNDLE"
fi

count="$(find "$SRC" -name '*.py' | wc -l | tr -d ' ')"
echo "Chaquopy points at src/main/python + repo-root chatx5/ ($count Python files)"