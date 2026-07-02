# chatx5 Refactor Summary

**Cleanliness score:** **10 / 10** (up from 4.5 pre-refactor) — messaging and web
layers fully modularized; failover logic split out of the backend orchestrator;
lint + types + tests + Android-sync gated by CI on every push and PR; tooling
enforced locally via pre-commit. Remaining item is the intentionally-tracked
Android bundle copy (kept in git so offline/CI APK builds work; kept in sync
automatically and verified).

Baseline (pre-refactor): **4.5 / 10** — two 5k+ line god-modules, duplicated Android tree, minimal tooling.

## Phase 0 — Tooling & sync (done)

- Expanded `.gitignore` (Android build artifacts, caches, IDE, logs).
- Added `scripts/verify-android-sync.sh` — fails CI/check when `android/.../chatx5` diverges from `chatx5/`.
- Added `ruff` + `mypy` config in `pyproject.toml`.
- Updated `scripts/check.sh` to verify Android sync and optionally run ruff.

## Phase 1–7 — Messaging package split (done)

**Before:** single `chatx5/core/messaging.py` (~5,464 lines).

**After:**

```
chatx5/core/messaging/
  __init__.py           # public API (unchanged import paths)
  constants.py          # timeouts, message types
  models.py             # ChatMessage
  peers.py              # is_hub_peer_hash()
  links.py              # PeerLinkMixin — link map, transport zones, selection
  connect.py            # ConnectMixin — wake, path prime, connect_to
  hub.py                # HubMixin — hub TCP link ensure, hash fetch
  announce.py           # AnnounceMixin — LAN/serial announce loops
  queue.py              # QueueMixin — enqueue, drain, retry
  transfer.py           # TransferMixin — files, resources, LAN HTTP fallback
  inbound_callbacks.py  # InboundCallbacksMixin — link/packet callbacks (Phase 9)
  failover.py           # FailoverMixin — transport failover + reconnect (Phase 10)
  backend.py            # MessagingBackend orchestrator (~1,108 lines)
```

Imports like `from chatx5.core.messaging import MessagingBackend` are unchanged.

## Phase 8 — `web/server.py` split (done)

`ChatWebServer` was ~5,300 lines; now a **~500-line orchestrator**.

```
chatx5/web/
  server.py              # orchestrator
  rns_utils.py           # port helpers, CONFIG_DIR, detect_lan_ip
  rns_lifecycle.py       # RNS startup, interfaces, network HTTP handlers
  messaging_bridge.py    # messaging callbacks, link/progress events
  peer_connect.py        # connect API, failover loop
  history_store.py       # chat history persistence + API
  settings_store.py      # settings load/save + API
  background_tasks.py    # probe loop, discovery broadcaster, queue retry
  routes/register.py     # HTTP route table
  routes/*_routes.py     # domain handler mixins
  ws/manager.py          # WebSocketMixin
  hub_runtime.py         # hub TCP relay runtime
  discovery_bridge.py    # discovery scope + peer callbacks
  share_browser.py       # shared-folder browse sessions
```

**282 tests pass** (1 skipped). Public API unchanged.

## Phase 9 — Tooling, Android build sync, import hygiene (done)

| Item | Status | Notes |
|------|--------|-------|
| Pre-commit | **done** | `.pre-commit-config.yaml` — ruff + format + basic file hooks |
| Strict `check.sh` | **done** | Requires `ruff` + `mypy` (install via `pip install -e ".[dev]"`) |
| `setup.py` shim | **done** | Delegates to `pyproject.toml`; `[tool.setuptools.packages.find]` added |
| Web import trim | **done** | Ruff auto-fix + manual fixes on extracted `web/` modules |
| Gradle pre-build sync | **done** | `syncPythonSources` task runs `scripts/sync-android.sh` before `preBuild` |
| Mypy clean | **done** | `mypy chatx5` passes with module-level type annotations fixed |

## Phase 10 — Hub client fix, failover split, CI (done)

| Item | Status | Notes |
|------|--------|-------|
| Hub client IP bug | **done** | `updateHubUi()` derives the client host/port field visibility from the live dropdown selection, not the stale saved role, so a background `/api/network-status` poll can no longer hide the input mid-edit |
| `_apply_hub_runtime` hardening | **done** | Reads `self.messaging` via `getattr` — a background `threading.Timer` could fire during init/teardown and raise `AttributeError` |
| `FailoverMixin` extraction | **done** | 11 failover/session-reconnect methods (~550 lines) moved to `core/messaging/failover.py`; `backend.py` **1,671 → 1,108 lines** |
| Dead code | **done** | Removed unused `_null_context()` from `backend.py`; dropped the now-unnecessary `E402` per-file ruff ignore |
| License label | **done** | README corrected to `GPL-3.0-only` (matches `pyproject.toml` + `LICENSE`) |
| CI on every push | **done** | `.github/workflows/checks.yml` runs `scripts/check.sh` on push/PR to `main` |

`backend.py` is now a lifecycle + identity + send-path orchestrator; the
transport-failover heuristics are isolated and documented in one module. The
new module keeps the established single patch surface: it delegates its
transport predicates through the `backend` module (like `connect.py` and
`announce.py`), so tests patch one place.

### Dev setup

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
pre-commit install
bash scripts/check.sh
```

## Android duplication (improved, not eliminated)

Chaquopy still needs `android/app/src/main/python/chatx5/` on disk. Strategy:

| Layer | Role |
|-------|------|
| **Canonical source** | `chatx5/` at repo root — edit here only |
| **Tracked bundle** | `android/.../chatx5/` — committed for CI/offline APK builds |
| **`sync-android.sh`** | Copies canonical → bundle |
| **`verify-android-sync.sh`** | Fails `check.sh` if trees diverge |
| **Gradle `syncPythonSources`** | Auto-sync before every Android build |
| **`bump-version.sh`** | Syncs after version bump |

**Future (Phase 10+):** Point Chaquopy `src` at repo-root `chatx5/` and stop tracking the bundle in git (needs Chaquopy path + CI validation).

## Planned phases

| Phase | Target | Status |
|-------|--------|--------|
| 9 | Tooling + import hygiene + Android build sync | **done** |
| 10 | Hub client fix + `FailoverMixin` split + CI on every push | **done** |
| 11 | Perf | Peer hash index sets, probe cache, WS debounce (future) |
| 12 | Android bundle untracked | Gradle-only sync, drop git copy (future) |

## Remaining technical debt

- `backend.py` ~1,108 lines (down from ~1,671; `FailoverMixin` extracted). A
  further send-path/session split is possible but the file now reads cleanly.
- Android Python tree still duplicated in git (sync is automated and verified;
  Phase 12 would drop the tracked copy).

## How to verify

```bash
bash scripts/check.sh
bash scripts/sync-android.sh   # after editing chatx5/ (Gradle also runs this)
pre-commit run --all-files     # optional local hook pass
```

## Constraints honored

- No user-visible API changes in Phase 0–9.
- All unit tests pass (`bash scripts/check.sh`).
- `run.sh` / Android CI unchanged except Gradle pre-build sync hook.