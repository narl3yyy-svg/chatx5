# chatx5

[![Checks](https://github.com/narl3yyy-svg/chatx5/actions/workflows/checks.yml/badge.svg)](https://github.com/narl3yyy-svg/chatx5/actions/workflows/checks.yml)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)

Encrypted peer-to-peer chat over the [Reticulum Network Stack](https://reticulum.network/). No accounts, no cloud servers — each transport uses its own RNS identity, and messages travel over encrypted links on your LAN (Wi‑Fi, Ethernet, USB serial).

Forked from [chatxz v0.5.13](https://github.com/narl3yyy-svg/chatxz/releases/tag/v0.5.13), rebranded as chatx5.

**Current version:** 0.6.25

## How chatx5 works

chatx5 treats **LAN** and **USB serial** as **separate endpoints** on the same device:

| Transport | Identity file | Connect hash | Discovered label |
|-----------|---------------|--------------|------------------|
| **LAN** (UDP/TCP) | `identities/identity_lan` | LAN hash | `ubuntu · LAN` |
| **USB serial** | `identities/identity_serial` | Serial hash | `ubuntu · USB` |

- **No auto-failover** — the transport you tap is the transport used for chat.
- **Independent links** — LAN and USB can both stay connected to the same peer; pick the sub-row to chat on that path.
- **One contact card** can hold both hashes with **LAN** and **USB** sub-rows.
- **Hub group chat** — optional TCP relay on port 4242 for multi-peer group messaging (hub server + hub clients).
- **Shared folder browse** — share a folder with a peer or hub group to browse, download, and upload files (🗂️ in composer).
- Upgrading from older versions **migrates** `identities/identity` → `identity_lan` automatically.

## Transports & how each feature is sent

chatx5 uses **three independent network paths**. The transport you pick in the UI is the transport used — there is no silent auto-failover between LAN and USB for 1:1 chat.

| Path | RNS interface | Typical use | Requires |
|------|---------------|-------------|----------|
| **LAN P2P** | UDP (default) or TCP LAN | Wi‑Fi / Ethernet on same subnet | Pinned IPv4 on both devices |
| **USB serial** | `SerialInterface` (`/dev/ttyUSB0`, etc.) | Direct cable between two machines | USB serial enabled; `dialout` on Linux |
| **Hub group** | TCP client → hub server **:4242** | Multi-peer group chat relay | Hub server + `--share`; clients dial hub host |

Run with **`--share`** so LAN HTTP fast transfers, shared-folder browse, and hub identity discovery work (`http://<lan-ip>:8742`).

### Text & emoji (1:1 chat)

| What | Wire format | Transport |
|------|-------------|-----------|
| Short message | Encrypted **RNS packet** on the active link | **LAN** or **USB** — whichever sub-row you opened |
| Long message (over link MTU) | **RNS resource** (`longtext`) on the same link | Same as above |
| Queued message | Stored locally; drained when link is up | Same target hash + transport |

Flow: Web UI → WebSocket → `send_message()` → RNS `Link` on the transport you selected. Receipts (`received` / `read`) travel back on that same link.

### Hub group chat

| What | Wire format | Transport |
|------|-------------|-----------|
| Group text / emoji | Encrypted RNS packet with `hub: true` + wire `sender` hash | **Hub TCP :4242** only (not LAN P2P) |
| Group photos / files / voice | RNS resource on hub TCP; `sender` preserved on relay | Hub TCP |
| Server relay | Hub server re-sends to other hub TCP clients with original sender | Hub TCP |
| Catch-up after 1:1 detour | `POST /api/hub/sync-group` pulls missed messages from hub server | HTTP to hub `:8742` |

Flow: open **Hub Group** → `/api/hub/ensure` + optional sync → `send_hub_message()` / `send_hub_file()` over hub TCP link. LAN and USB links to the same peer stay separate; group chat never rides the P2P path.

### Large files, images, and video (1:1)

| Size / condition | Metadata | File bytes | Transport |
|------------------|----------|------------|-----------|
| **≥ 512 KiB**, LAN up, `--share`, not hub peer | `__lan_http_offer` over **RNS** (token + URL) | Plain **HTTP GET** to sender `:8742` | **LAN** only (fast path) |
| Smaller files, USB, or no LAN HTTP | `file` / `image` / `video` chat message over **RNS** | **RNS resource** stream on active link | **LAN** or **USB** |

The LAN HTTP path sends a one-time token inside an encrypted RNS packet; the receiver downloads bytes over HTTP on your subnet. USB and cross-subnet paths use full in-band RNS resources (slower but works without routable LAN HTTP).

### Voice notes (1:1)

| What | Wire format | Transport |
|------|-------------|-----------|
| Voice clip | `voice` message + **RNS resource** (audio file) | **LAN** or **USB** — active chat transport |

Voice uses the same resource pipeline as files. LAN HTTP fast path applies only to large generic files (≥ 512 KiB), not voice clips.

### Shared folder browse (🗂️)

| Mode | Offer (how the peer learns the session) | Browse / download / upload |
|------|----------------------------------------|----------------------------|
| **1:1 P2P** | `share_browse` over **RNS** on LAN or USB link | **HTTP** to sharer’s `:8742` (`/api/share/...`) |
| **Hub group** | `share_browse` over **hub TCP** (`hub: true`) | Same HTTP API on the machine that shared the folder |

Flow: tap 🗂️ → pick folder → server creates a 2-hour session (`session_id` + `token`) → offer sent to peer → peer taps **Browse folder** → UI proxies list/download/upload via HTTP to the sharer’s LAN IP. Group shares include `hub_group: true` so all hub clients see the offer in Group Chat.

### Quick reference

| Feature | LAN P2P | USB serial | Hub group |
|---------|---------|------------|-----------|
| Text / emoji | ✅ RNS packet | ✅ RNS packet | ✅ RNS over TCP :4242 |
| Long text | ✅ RNS resource | ✅ RNS resource | ✅ RNS resource on hub link |
| Files / images / video | ✅ RNS resource; **≥512 KiB → LAN HTTP** with `--share` | ✅ RNS resource | ❌ (use 1:1 or shared folder) |
| Voice | ✅ RNS resource | ✅ RNS resource | ❌ |
| Shared folder | ✅ RNS offer + HTTP browse | ✅ RNS offer + HTTP browse | ✅ Hub TCP offer + HTTP browse |

### First-time setup

1. Pick your **display name**.
2. **Select a LAN IPv4** from the list (required — no “Auto”).
3. Optionally enable USB serial in Settings → Network later.
4. For hub group chat: set **Hub role** (server on one machine, client on others) under Settings → Network.

### Sidebar quick guide

| Action | What it does |
|--------|----------------|
| **Announce LAN** | RNS announce + UDP beacon on your pinned IPv4 |
| **Announce Serial** | RNS announce on USB (shown when serial is online) |
| **Hub Group** | Group chat via hub TCP relay (when hub mode is on) |
| Tap **Discovered** row | Opens chat on that transport |
| Tap **contact sub-row** | Opens chat on LAN or USB for that saved peer |

### Settings → Network

| Setting | Meaning |
|---------|---------|
| **Hub role** | Off / Server (listens on 0.0.0.0:4242) / Client (dials hub host) |
| **Hub host** | IP of the hub server on your subnet |
| **LAN / Serial probe interval** | RTT ping frequency (0 = off) |
| **LAN IPv4** | Scope for discovery, beacons, and wake |

Regenerate identities under **Settings → Profile**. View **release notes** via the version badge in the bottom dock.

**Maintainer:** [narl3yyy-svg](https://github.com/narl3yyy-svg) — sole author and contributor.

### Troubleshooting

- **Serial peer missing after Announce** — tap **Announce Serial**; ensure USB serial is online in Settings → Network.
- **Hub group messages pending** — hub server must run with `--share`; client needs hub host IP and hub server hash (auto-fetched). Both need hub TCP link separate from LAN P2P.
- **Two rows for one name** — expected when a peer has both LAN and USB.
- **Cross-subnet LAN** — pick matching pinned IPv4 on both devices.

## Download

**Android APK** on **[GitHub Releases](https://github.com/narl3yyy-svg/chatx5/releases)**. Desktop: clone the repo and use the platform runner below.

| Platform | Run |
|----------|-----|
| **Android** | `chatx5-X.Y.Z.apk` from Releases — sideload (arm64) |
| **Windows** | `git clone` → **cmd** → `run.bat web --share` |
| **macOS / Linux** | `git clone` → `./run.sh web --share` |

Use `--share` so LAN HTTP transfers and hub hash discovery work.

---

## Windows

**Command Prompt (cmd) only.**

1. Install [Python 3.10+](https://www.python.org/downloads/windows/) — check **Add python.exe to PATH**
2. Install [Git](https://git-scm.com/download/win)
3. Open **cmd** in the repo folder:

```cmd
git clone https://github.com/narl3yyy-svg/chatx5.git
cd chatx5
run.bat web --share
```

Open `http://127.0.0.1:8742` in your browser.

---

## Linux / macOS

```bash
git clone https://github.com/narl3yyy-svg/chatx5.git
cd chatx5
./run.sh web --share
```

For USB serial on Linux, `./run.sh web --share` adds dialout permissions. Open `http://127.0.0.1:8742`.

---

## Hub group chat setup

1. **Hub server** (e.g. Arch at 10.0.30.112): Settings → Network → Hub role = **Server**, port **4242**. Restart with `./run.sh web --share`.
2. **Hub clients** (e.g. Ubuntu): Hub role = **Client**, hub host = **10.0.30.112**. Restart with `./run.sh web --share`.
3. Open **Hub Group** in the sidebar and send messages. The server relays to all connected hub TCP clients.

LAN P2P chat between the same peers still uses UDP; group chat uses the separate hub TCP path on port 4242.

---

## Development

### Setup

```bash
git clone https://github.com/narl3yyy-svg/chatx5.git
cd chatx5
python -m venv .venv && .venv/bin/pip install -e ".[dev]"   # ruff, mypy, pre-commit
pre-commit install                                          # run lint/format on commit
```

### Everyday workflow

```bash
./run.sh web --share                    # run the desktop app on http://127.0.0.1:8742
bash scripts/check.sh                   # ruff + mypy + unit tests + Chaquopy src verify
bash scripts/sync-android.sh            # after editing chatx5/ (Gradle also runs this)
bash scripts/bump-version.sh X.Y.Z      # bump version in properties + Gradle metadata
cd android && ./gradlew assembleDebug   # local APK (needs JDK 17 + Android SDK)
```

`scripts/check.sh` is the single source of truth for "is this change OK?" — it
is what CI runs. It fails if lint, types, tests, or Chaquopy src configuration is wrong.

### Continuous integration

| Workflow | Trigger | Does |
|----------|---------|------|
| **Checks** (`.github/workflows/checks.yml`) | push / PR to `main` | `scripts/check.sh`: ruff + mypy + tests + Chaquopy src verify |
| **Build Android APK** (`.github/workflows/build-apk.yml`) | version tag `v*` (or manual) | Builds and publishes the release APK |

### Architecture

chatx5 is a single Python package (`chatx5/`) that runs the same code on
desktop and Android (via Chaquopy). The layers:

```
chatx5/
  app.py                 # CLI entry point / process bootstrap
  core/                  # transport-independent domain logic
    identity.py          # per-transport RNS identities (LAN / serial)
    discovery.py         # peer discovery + hash normalization
    lan_rns.py           # RNS path/interface state on the LAN
    rns_interfaces/      # RNS interface presets, serial/TCP runtime, config render
    serial_transfer.py   # USB serial framing/transfer
    messaging/           # the messaging backend (mixin-composed) — see below
  web/                   # local HTTP/WebSocket UI server (orchestrator + modules)
  utils/                 # platform, logging, file-serve, folder-picker helpers
  android_usb/           # Android USB serial shim
android/                 # Chaquopy Android app wrapper (srcDir → repo-root chatx5/)
tests/                   # unittest suite (no network required)
```

The messaging backend is composed from focused mixins so no single file owns
the whole protocol:

```
chatx5/core/messaging/
  __init__.py            # public API (stable import paths)
  constants.py           # timeouts, message types
  models.py              # ChatMessage
  peers.py               # hub-peer hash helpers
  links.py               # PeerLinkMixin — link map, transport zones, selection
  connect.py             # ConnectMixin — wake, path prime, connect_to
  failover.py            # FailoverMixin — transport failover + session reconnect
  hub.py                 # HubMixin — hub TCP link, hash fetch
  announce.py            # AnnounceMixin — LAN/serial announce loops
  queue.py               # QueueMixin — enqueue, drain, retry
  transfer.py            # TransferMixin — files, resources, LAN HTTP fallback
  inbound_callbacks.py   # InboundCallbacksMixin — link/packet callbacks
  backend.py             # MessagingBackend — orchestrator (lifecycle, identity, send path)
```

### Android Python sources (Phase 13)

Chaquopy reads the canonical `chatx5/` package directly via `chaquopy.sourceSets { srcDir("../../chatx5") }` in `android/app/build.gradle.kts`. There is no tracked duplicate tree in git — edit `chatx5/` only.

- `scripts/verify-android-sync.sh` — fails `check.sh` if Gradle is not pointed at `../../chatx5`
- `scripts/sync-android.sh` — legacy no-op (kept for older docs/scripts)

### Project layout (web layer)

The web server is a thin orchestrator; HTTP, WebSocket, and RNS logic live in dedicated modules:

```
chatx5/web/
  server.py              # ChatWebServer orchestrator (~500 lines)
  rns_utils.py           # port helpers, CONFIG_DIR, detect_lan_ip
  rns_lifecycle.py       # RNS startup, interfaces, network HTTP handlers
  messaging_bridge.py    # messaging callbacks, link/progress events
  peer_connect.py        # connect API, failover loop
  history_store.py       # chat history persistence + API
  settings_store.py      # settings load/save + API
  background_tasks.py    # probe loop, discovery broadcaster, queue retry
  hub_runtime.py         # hub TCP relay runtime
  discovery_bridge.py    # discovery scope + peer callbacks
  share_browser.py       # shared-folder browse sessions
  routes/register.py     # HTTP route table
  routes/*_routes.py     # domain handler mixins (identity, contacts, transfers, …)
  ws/manager.py          # WebSocket connect, broadcast, protocol dispatch
  static/
    index.html           # HTML shell (~600 lines)
    css/app.css          # styles
    js/                  # 15 client modules (state, ws, contacts, chat, …)
```

Static files are served at `/static/css/*` and `/static/js/*`. Edit modules under
`chatx5/web/static/` only (canonical); Android picks them up via Chaquopy `srcDir`.

See [REFACTOR_SUMMARY.md](REFACTOR_SUMMARY.md) for the full modular refactor history (messaging package + web server + frontend split).

---

## Contributing

1. **Branch** off `main` (e.g. `fix/…` or `refactor/…`).
2. **Edit `chatx5/` only** — Android builds read this tree via Chaquopy `srcDir`.
3. **Keep public import paths stable** (e.g.
   `from chatx5.core.messaging import MessagingBackend`).
4. **Run `bash scripts/check.sh`** before pushing — it must pass (it is the CI gate).
5. **Add or update tests** under `tests/` for behavioural changes; the suite is
   pure `unittest` and needs no network.
6. **Match the surrounding style** — 100-col lines, ruff `E/F/I/UP`, mypy-clean.
   `pre-commit install` runs ruff + formatting on every commit.
7. **Open a PR** to `main`; the **Checks** workflow runs `scripts/check.sh`
   automatically.

Bump the version with `bash scripts/bump-version.sh X.Y.Z`; tagging `vX.Y.Z`
triggers the APK release build.

## License

GPL-3.0-only — see [LICENSE](LICENSE).