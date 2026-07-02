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
  announce.py      # AnnounceMixin — LAN/serial announce loops
  queue.py         # QueueMixin — enqueue, drain, retry
  transfer.py      # TransferMixin — files, resources, LAN HTTP fallback
  backend.py       # MessagingBackend (core lifecycle, callbacks)
```

Imports like `from chatx5.core.messaging import MessagingBackend` are unchanged.

Tests that mock internals patch `chatx5.core.messaging.backend.<symbol>` (links.py delegates patched symbols through backend for compatibility).

## Phase 2 — links.py extraction (done)

Extracted ~840 lines into `PeerLinkMixin` in `links.py`:

- Transport normalization and link map keys
- Per-peer link registry, consolidation, teardown helpers
- `linked_peers()`, `_best_outgoing_link()`, `_peer_link_active()`, `_peer_link_usable()`
- Inbound link adoption helpers (`_find_active_link_for_peer`, `_handoff_to_link`, etc.)

`MessagingBackend` now inherits `PeerLinkMixin`, `ConnectMixin`, `HubMixin`, `QueueMixin`, and `TransferMixin`. `backend.py` is ~2,520 lines.

## Phase 3 — connect.py extraction (done)

Extracted ~1,070 lines into `ConnectMixin` in `connect.py`:

- LAN unreachable tracking, HTTP wake, UDP/TCP/serial path priming
- `_connect_serial_peer`, `_establish_outbound_link`, inbound wait helpers
- `_identity_for_hash`, `_wait_for_identity`, `connect_to`, `_connect_to_locked`

## Phase 4 — queue.py extraction (done)

Extracted ~280 lines into `QueueMixin` in `queue.py` (enqueue, drain, retry, prune).

## Phase 5 — transfer.py extraction (done)

Extracted ~830 lines into `TransferMixin` in `transfer.py`:

- RNS resource send/receive, progress, cancellation
- LAN HTTP file fallback (`_send_file_lan_http`, `_download_lan_http_offer`)
- Long-text resource transfer, `send_file`, `cancel_transfer`

`MessagingBackend` now inherits `TransferMixin`. `backend.py` is ~2,520 lines.

## Phase 6 — hub.py extraction (done)

Expanded `HubMixin` in `hub.py` (~400 lines) with full hub relay logic moved from `backend.py`:

- `_load_hub_settings`, `_hub_endpoint_from_settings`, `_link_is_hub_transport`, `_link_is_hub_tcp`
- `_hub_tcp_linked_peers`, `_hub_send_targets`, `send_hub_message`, `relay_hub_message`
- `drain_hub_group_queue`, `ensure_hub_link`, inbound hub TCP scope helpers

`backend.py` is ~2,200 lines after Phase 6.

## Phase 7 — announce.py extraction (done)

Extracted ~310 lines into `AnnounceMixin` in `announce.py`:

- `_announce_payload`, `_announce_on_interface`, `_fallback_announce`
- `_burst_serial_announce`, `_silent_announce`, `_announce`, `_announce_loop`
- `_lan_transport_ready`, `_serial_transport_ready`, `_peer_lan_ip_usable`

`MessagingBackend` now inherits `AnnounceMixin` before `ConnectMixin` (connect uses transport-ready helpers). `backend.py` is ~1,900 lines.

## Phase 8 — `web/server.py` split (done)

`ChatWebServer` was ~5,300 lines; now a **536-line orchestrator** that wires mixins and lifecycle only.

| Step | Module | Status | Contents |
|------|--------|--------|----------|
| 8a | `web/routes/` | **done** | `register.py` route table; handler mixins per domain (`static`, `identity`, `contacts`, `discovery`, `transfers`, `queue`, `debug`, `system`) |
| 8b | `web/ws/` | **done** | `WebSocketMixin` — connect, broadcast, WS protocol dispatch |
| 8c | `web/hub_runtime.py` | **done** | `HubRuntimeMixin` — hub settings, TCP hot-add, host resolve, server hash |
| 8d | `web/discovery_bridge.py` | **done** | `DiscoveryBridgeMixin` — scope, peer callbacks, contact sync, supersede |
| 8e | `web/rns_lifecycle.py` | **done** | `RNSLifecycleMixin` — `start_rns`, interface config, announce, network API handlers |
| 8f | `web/server.py` | **done** | Slim orchestrator — `__init__`, peer helpers, lifecycle, `run` / `run_embedded` |
| 8g | `web/share_browser.py` | **done** | `ShareBrowserMixin` — shared folder sessions, remote proxy, P2P + hub group |
| 8h | release notes API | **done** | `GET /api/release-notes` — full version history for Settings → About |

**Supporting modules (Phase 8 structural split):**

```
chatx5/web/
  server.py              # 536 lines — orchestrator
  rns_utils.py           # port/process helpers, CONFIG_DIR, detect_lan_ip
  rns_lifecycle.py       # RNS startup, interfaces, network HTTP handlers
  messaging_bridge.py    # messaging callbacks, link/progress events
  peer_connect.py        # connect/request_connect, failover loop
  history_store.py       # history load/save/prune + history HTTP handlers
  settings_store.py      # settings load/save + settings HTTP handlers
  background_tasks.py    # probe loop, discovery broadcaster, queue retry
  routes/register.py     # single route table (delegates to server handlers)
  ws/manager.py          # WebSocketMixin
```

**v0.6.2 hub group fix:** Hub server accepts inbound TCP client links in parallel with LAN/serial P2P; hub client requires dedicated hub-TCP link for `send_hub_message` (no LAN session reuse).

`ChatWebServer` inherits all route/ws/rns mixins plus `HubRuntimeMixin`, `DiscoveryBridgeMixin`, and `ShareBrowserMixin`. Public API unchanged (`from chatx5.web.server import ChatWebServer`, `detect_lan_ip`, etc.).

**282 tests pass** (1 skipped). Android bundle synced via `scripts/sync-android.sh`.

## Planned phases

| Phase | Target | Notes |
|-------|--------|-------|
| 2 | `links.py` | Done — see above |
| 3 | `connect.py` | Done — see above |
| 4 | `queue.py` | Done — see above |
| 5 | `transfer.py` | Done — see above |
| 6 | `hub.py` | Done — see above |
| 7 | `announce.py` | Done — see above |
| 8 | `web/server.py` split | Done — orchestrator 536 lines; routes/ws/rns_lifecycle extracted |
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

- `backend.py` still ~1,900 lines.
- Extracted modules still carry broad import headers from the automated split — trim unused imports over time.
- `setup.py` duplicates `pyproject.toml` — deprecate after setuptools entry-point migration.
- No pre-commit hook yet; ruff optional in check.sh.

## v0.5.24 hotfixes (done, pre–Phase 8)

Mesh debugging fixes shipped before the `web/server.py` split:

| Area | Fix |
|------|-----|
| Hub TCP relay | Server: inbound `TCPClientInterface` (no `target_host`) counts as hub client. Client: only TCP dials to configured hub host count (not LAN P2P TCP). |
| Hub group chat | `connect_to(prefer_transport=tcp)` reuses active hub link; 8s rate limit on hub open attempts; server logs `N TCP client(s) linked`. |
| Serial discovery | IP-less USB announces accepted when serial transport is active (`announce_receiving_interface` fallback). |
| Serial runtime | `configured_serial_enabled` / `serial_runtime_active` ignore stale `enabled: false` when port is accessible. |

**276 tests pass** (1 skipped). Tag: `v0.5.24`.

## Constraints honored

- No user-visible API changes in Phase 0–7 or v0.5.24 hotfixes.
- All unit tests pass (`bash scripts/check.sh`).
- `run.sh` / Android CI unchanged except verify step.