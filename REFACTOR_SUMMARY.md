# chatx5 Refactor Summary (in progress)

Baseline cleanliness score (pre-refactor): **4.5 / 10** — working dual-transport product, but two 5k+ line god-modules, duplicated Android tree, minimal tooling.

## Phase 0 — Tooling & sync (done)

- Expanded `.gitignore` (Android build artifacts, caches, IDE, logs).
- Added `scripts/verify-android-sync.sh` — fails CI/check when `android/.../chatx5` diverges from `chatx5/`.
- Added `ruff` + `mypy` config stubs in `pyproject.toml`.
- Updated `scripts/check.sh` to verify Android sync and optionally run ruff.

## Phase 1 — Messaging package split (done)

**Before:** single `chatx5/core/messaging.py` (~5,464 lines).

**After:**

```
chatx5/core/messaging/
  __init__.py      # public API (unchanged import paths)
  constants.py     # timeouts, message types
  models.py        # ChatMessage
  peers.py         # is_hub_peer_hash()
  links.py         # PeerLinkMixin — link map, transport zones, selection
  connect.py       # ConnectMixin — wake, path prime, connect_to
  hub.py           # HubMixin — hub TCP link ensure, hash fetch
  queue.py         # QueueMixin — enqueue, drain, retry
  backend.py       # MessagingBackend (transfer/announce/hub relay remain)
```

Imports like `from chatx5.core.messaging import MessagingBackend` are unchanged.

Tests that mock internals patch `chatx5.core.messaging.backend.<symbol>` (links.py delegates patched symbols through backend for compatibility).

## Phase 2 — links.py extraction (done)

Extracted ~840 lines into `PeerLinkMixin` in `links.py`:

- Transport normalization and link map keys
- Per-peer link registry, consolidation, teardown helpers
- `linked_peers()`, `_best_outgoing_link()`, `_peer_link_active()`, `_peer_link_usable()`
- Inbound link adoption helpers (`_find_active_link_for_peer`, `_handoff_to_link`, etc.)

`MessagingBackend` now inherits `PeerLinkMixin`, `ConnectMixin`, `HubMixin`, and `QueueMixin`. `backend.py` is ~3,350 lines.

## Phase 3 — connect.py extraction (done)

Extracted ~1,070 lines into `ConnectMixin` in `connect.py`:

- LAN unreachable tracking, HTTP wake, UDP/TCP/serial path priming
- `_connect_serial_peer`, `_establish_outbound_link`, inbound wait helpers
- `_identity_for_hash`, `_wait_for_identity`, `connect_to`, `_connect_to_locked`

## Phase 4 — queue.py extraction (done)

Extracted ~280 lines into `QueueMixin` in `queue.py` (enqueue, drain, retry, prune).

## Planned phases

| Phase | Target | Notes |
|-------|--------|-------|
| 2 | `links.py` | Done — see above |
| 3 | `connect.py` | Done — see above |
| 4 | `queue.py` | Done — see above |
| 5 | `transfer.py` | Files, resources, LAN HTTP fallback |
| 6 | `hub.py` | Hub relay and group messaging |
| 7 | `announce.py` | Announce loops, serial burst |
| 8 | `web/server.py` split | Routes vs WS vs discovery helpers |
| 9 | Android | Stop committing bundle; sync-only at build (optional) |
| 10 | Perf | Peer hash index sets, probe cache, WS debounce |

## Android duplication

- **Current:** `scripts/sync-android.sh` copies `chatx5/` → Android bundle; CI runs it on tag builds; `bump-version.sh` auto-syncs.
- **Verification:** `scripts/verify-android-sync.sh` in `check.sh`.
- **Future:** Gradle pre-build task only; drop tracked copy from git (needs Chaquopy path validation).

## How to verify

```bash
bash scripts/check.sh
bash scripts/sync-android.sh   # after editing chatx5/
```

## Remaining technical debt

- `backend.py` still ~3,350 lines — next extraction is `transfer.py` (Phase 5).
- `web/server.py` still ~5,700 lines.
- `setup.py` duplicates `pyproject.toml` — deprecate after setuptools entry-point migration.
- No pre-commit hook yet; ruff optional in check.sh.

## Constraints honored

- No user-visible behavior changes in Phase 0–1.
- All 254 unit tests pass.
- `run.sh` / Android CI unchanged except verify step.