# chatx5

Encrypted peer-to-peer chat over the [Reticulum Network Stack](https://reticulum.network/). No accounts, no cloud servers — each transport uses its own RNS identity, and messages travel over encrypted links on your LAN (Wi‑Fi, Ethernet, USB serial).

Forked from [chatxz v0.5.13](https://github.com/narl3yyy-svg/chatxz/releases/tag/v0.5.13), rebranded as chatx5.

**Current version:** 0.6.2

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

Regenerate identities under **Settings → Profile**. View **release notes** via the version badge in the bottom dock or **Settings → System → About**.

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

```bash
bash scripts/check.sh          # tests + Android sync verify
bash scripts/bump-version.sh X.Y.Z
bash scripts/sync-android.sh   # after editing chatx5/
```

See [REFACTOR_SUMMARY.md](REFACTOR_SUMMARY.md) for the modular refactor status (messaging package split, web server split in progress).

---

## License

MIT — see [LICENSE](LICENSE).