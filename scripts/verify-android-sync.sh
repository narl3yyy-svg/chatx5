#!/usr/bin/env bash
# Verify Chaquopy uses canonical chatx5/ sources (Phase 13 — bundle no longer tracked).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/chatx5"
GRADLE="$ROOT/android/app/build.gradle.kts"

if [ ! -d "$SRC" ]; then
  echo "verify-android-sync: missing canonical Python tree at chatx5/" >&2
  exit 1
fi

if ! grep -q 'srcDir("../../chatx5")' "$GRADLE"; then
  echo "verify-android-sync: android/app/build.gradle.kts must set chaquopy srcDir to ../../chatx5" >&2
  exit 1
fi

count="$(find "$SRC" -name '*.py' | wc -l | tr -d ' ')"
echo "Chaquopy points at canonical chatx5/ ($count Python files)"