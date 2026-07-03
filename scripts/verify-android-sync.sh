#!/usr/bin/env bash
# Sync and verify Android Chaquopy bundle matches canonical chatx5/.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/chatx5"
DST="$ROOT/android/app/src/main/python/chatx5"
GRADLE="$ROOT/android/app/build.gradle.kts"

if [ ! -d "$SRC" ]; then
  echo "verify-android-sync: missing canonical Python tree at chatx5/" >&2
  exit 1
fi

if ! grep -qE 'setSrcDirs\(listOf\("src/main/python"\)\)' "$GRADLE"; then
  echo "verify-android-sync: android/app/build.gradle.kts must setSrcDirs src/main/python only" >&2
  exit 1
fi

bash "$ROOT/scripts/sync-android.sh" >/dev/null

src_count="$(find "$SRC" -name '*.py' | wc -l | tr -d ' ')"
dst_count="$(find "$DST" -name '*.py' | wc -l | tr -d ' ')"
if [ "$src_count" != "$dst_count" ]; then
  echo "verify-android-sync: bundle mismatch ($src_count canonical vs $dst_count android)" >&2
  exit 1
fi

echo "Android Python bundle matches chatx5/ ($dst_count files)"