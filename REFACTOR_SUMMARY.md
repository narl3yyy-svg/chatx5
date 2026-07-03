# chatx5 Refactor Summary

**Cleanliness score:** **9.2 / 10** (up from 4.5 pre-refactor) — messaging and web
Python layers fully modularized; frontend split from a 7.3k-line monolith into
`index.html` shell + `css/app.css` + 15 focused JS modules; failover logic split
out of the backend orchestrator; lint + types + tests + Android-sync gated by CI
on every push and PR. Remaining: a few Python files still >1k lines; Android
bundle copy in git (Phase 13).

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

**308 tests pass**. Public API unchanged.

## Phase 11 — Frontend modularization (done)

**Before:** `chatx5/web/static/index.html` (~7,281 lines) — CSS, HTML, and ~260
functions in one file.

**After:**

```
chatx5/web/static/
  index.html          # ~612 lines — markup + script load order + bootstrap
  css/app.css         # design tokens, layout, components
  js/
    state.js          # globals, constants, emoji tables
    utils.js          # escapeHtml, toast, formatSize
    peers.js          # peerKey, peersMatch, link state, contact display names
    layout.js         # sidebar, Android shell, unread badges
    app-core.js       # init pipeline, settings form, identity fetch
    ws.js             # WebSocket connect + message dispatch
    contacts.js       # saved contacts render/save/dedupe
    discovery.js      # discovered peers, connect, announce
    settings.js       # RNS interfaces, hub, LAN transport
    network-status.js # live status panel (v0.6.14 cards)
    settings-ui.js    # settings screen, release notes
    chat.js           # openChat, send, disconnect
    messages.js       # message render, emoji, voice, uploads
    share.js          # shared-folder browser
    identity.js       # identity modal, debug log, brand logo
```

Served at `/static/css/*` and `/static/js/*` via existing `handle_static` route.
`tests/test_static_frontend.py` verifies all referenced assets exist.

## v0.6.23 — Share JSON, hub UI, transport labels, emoji categories (done)

| Area | Status | Notes |
|------|--------|-------|
| Share folder JSON parse | **done** | `safeJsonParse`/`readJsonResponse` in utils.js; remote share uses `scheme` |
| Hub listen checkboxes | **done** | `ensureHubListenInterfacesRendered` — no re-render on every poll |
| Hub group server→client | **done** | Wire sender preserved in `inbound_callbacks`; relay sets sender if missing |
| Display names on restart | **done** | `_contact_name_for` matches `identity_hash` in discovery |
| Emoji categories | **done** | Sticky category headers in picker |
| Audio scrubbing | **done** | `<source type="audio/...">` + `preload="auto"` |
| Transport visibility | **done** | `normalizeVia` keeps `tcp`; header/toasts show UDP/TCP/USB |
| Hub/tcp_lan port 4242 | **done** | Extra tcp_lan listeners disabled; primary server iface reused |

## v0.6.22 — Hub interfaces + group sender + Android send (done)

| Issue | Fix |
|-------|-----|
| Hub server bind address | Multi-select `hub_listen_interfaces` in settings; runtime hot-adds one TCP listener per selected IP |
| Group message wrong sender on relay | Wire `sender` hash in hub payloads; `_on_message` prefers `chat_msg.sender` over link identity |
| Android send button dead | Removed `preventDefault` on send button touch/mousedown |
| Long pending send icon | Immediate `sent` receipt callback; default UI status `sent`; queue receipt timeout 10s |

## v0.6.21 — Chat UX + serial stability (done)

| Issue | Fix |
|-------|-----|
| Chat opens at top then scrolls down | `history-loading` batch render; `scroll-behavior: auto`; `buildMessageNode()` |
| Serial USB drops and reconnect loops | Fast-path active serial link; pin path on success; relax `_peer_link_usable` for serial |
| Fast peer switch breaks server | Connect debounce (2.5s); skip LAN wake on serial; clear LAN IP on serial connect API |

## v0.6.15 — Phase 11 release (done)

Shipped frontend modularization (`index.html` → `css/` + `js/`); README architecture
section updated; APK via tag `v0.6.15`.

## v0.6.14 — Contact names + live status UI (done)

| Issue | Fix |
|-------|-----|
| Contact names change on restart | `_has_persisted_display_name` + discovery sync skips name overwrite; UI prefers saved label |
| 10+ duplicate TCP clients in status | `summarize_rns_interfaces()` groups inbound hub relay clients; API adds `rns_interfaces_summary` |
| Live status hard to read | Card-based status panel: hero, LAN/Serial/Hub/Discovery cards, grouped transports, deduped peers |

## v0.6.13 — Hub delivery, share browse, discovery supersede (done)

| Issue | Fix |
|-------|-----|
| Hub group slow / no active link storm | Hub `tcp` connect skips serial priming; faster queue drain + retry throttle |
| Share folder raw JSON in group chat | `send_hub_message(msg_type=share_browse)`; inbound JSON coercion + UI fallback |
| Discovery vanishes after USB send | Supersede keeps replacement peer; no disconnect/clear on hash rotation |
| LAN RTT on USB row | `update_peer_probe(..., via=link_via)` on link established |

README now documents transport paths for text, files, voice, and shared folders.

## v0.6.12 — Android UI + hub session hardening (done)

| Issue | Fix |
|-------|-----|
| Android “Frontend not found” | `static_routes.py` resolves `chatx5/web/static` via package root (not `web/routes/static`) |
| Group chat dies after P2P switch | Hub TCP links protected in `_consolidate_peer_links`; `/api/hub/ensure` on Group Chat open |
| “Ignored group message” on LAN | `_hub_message_receivable` accepts hub payloads on any transport for display |
| Android group notifications | `_should_android_notify` treats `__hub_group__` as its own viewing peer |

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
| 11 | Frontend modularization (`index.html` → css + js/) | **done** |
| 12 | Oversized Python module splits (`http_peer.py` started) | **in progress** — hub hash fetch + wake/connect on `http_peer`; multi-listener helpers in `rns_interfaces.py` |
| 13 | Android bundle untracked | future |
| 14 | Perf (peer hash index, probe cache, WS debounce) | **partial** — connect debounce in UI (v0.6.21) |

## Remaining technical debt

- `backend.py` ~1,150 lines; `connect.py`, `rns_interfaces.py`, `rns_lifecycle.py`,
  `platform.py`, `discovery.py` still >1k lines (Phase 12 Python splits).
- Frontend JS uses classic global scripts (not ES modules) — Phase 11b could add
  `type="module"` with explicit exports once onclick handlers move to listeners.
- Android Python tree still duplicated in git (sync is automated and verified;
  Phase 13 would drop the tracked copy).

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