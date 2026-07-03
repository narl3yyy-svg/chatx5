# Changelog

All notable changes to chatx5 are documented here. The README lists only the latest release summary.

## [0.6.20] — 2026-07-02

### Fixed
- **Dual-transport send routing** — Web UI `viewing_via` (LAN vs USB serial tab) now drives message sends, file transfers, and queue drains to the correct transport-specific peer hash; fixes Arch→Ubuntu messages failing when both transports are connected.
- **LAN file transfer speed** — Large files on the LAN tab use the LAN link (and HTTP fast-path) instead of silently falling back to the USB serial link (~300 KB/s cap).
- **Hub duplicate links** — Inbound hub TCP links are consolidated immediately, reducing “Token HMAC was invalid” decryption errors from stale parallel links.
- **Queue alias matching** — Queued messages match dual-transport contact aliases (lan_hash ↔ serial_hash).
- **Contact delete** — Deleted contacts disappear from the list immediately; contact migration no longer fails when the contacts directory is missing.

## [0.6.19] — 2026-07-03

### Fixed
- **Announce transport isolation** — Announce Serial sends only USB RNS packets; Announce LAN sends only LAN RNS + UDP beacon (no cross-transport companion beacon).

## [0.6.18] — 2026-07-03

### Fixed
- **CI Checks** — `SettingsStoreMixin` reads/writes `settings.json` under `config_dir` so tests and isolated installs work on fresh runners.

## [0.6.17] — 2026-07-03

### Fixed
- **HTTPS LAN file transfers** — `--share` auto-TLS no longer breaks large-file fast-path, peer wake/connect, hub hash fetch, or shared-folder browse (offers include `scheme: https`; clients trust self-signed certs).
- **WAN secure mode** — checkbox now persists in `settings.json` and applies immediately (disables LAN HTTP fast-path without restart).

### Added
- **Serial RF quality refresh** — Settings → Network: interval (seconds) for live link quality % updates while connected over USB serial (default 5s).
- **Phase 12 start** — `chatx5/core/http_peer.py` centralizes HTTP/HTTPS peer requests.

## [0.6.16] — 2026-07-02

### Fixed
- **Hub group file transfer** — large files, images, video, and voice in Group Chat now send over hub TCP (`send_hub_file`); the hub server relays received files to other linked clients; queued hub files drain correctly.
- **Serial transport stability** — removed cross-transport serial↔LAN auto-failover when you pick a transport; links stay on the path you chose (same-transport reconnect only).

### Added
- **Serial RF link quality** — when connected over USB serial, the peer header and contact row show link quality % derived from handshake RTT.
- **HTTPS with `--share`** — LAN sharing auto-enables TLS with a self-signed cert when OpenSSL is available (`--tls`, `--cert`/`--key`, `--no-tls` to opt out).
- **WAN secure mode** — Settings → System toggles encrypted-only transfers (disables plain HTTP LAN file fast-path) for internet-facing use.

## [0.6.15] — 2026-07-03

### Changed
- **Frontend modularization (Phase 11)** — `index.html` is now a ~600-line shell; styles live in `web/static/css/app.css` and client logic in 15 scripts under `web/static/js/`. No user-visible behavior change; static asset integrity covered by `tests/test_static_frontend.py`.

## [0.6.14] — 2026-07-02

### Fixed
- **Contact name persistence** — saved contact labels survive server restarts and stay put when the peer is offline; discovery sync no longer overwrites a stored display name unless you explicitly rename the contact.
- **Live network status accuracy** — duplicate inbound hub `TCPClientInterface` rows are collapsed into grouped summaries (e.g. “Hub relay clients (inbound) ×N”); discovered peers are deduped for the status panel.

### Changed
- **Live network status UI** — Settings → Live network status redesigned with session hero, transport cards (LAN / Serial / Hub / Discovery), grouped runtime interface table, and cleaner discovered-peer list.

## [0.6.13] — 2026-07-02

### Fixed
- **Hub group chat latency** — hub TCP `connect_to` no longer primes serial paths first; hub links skip serial-only peer detection; group queue drains immediately (~0.1s) when a hub TCP session is established; hub link retry throttle reduced when messages are queued.
- **Shared folder in group chat** — hub group shares send `share_browse` (not JSON `text`); inbound JSON share offers are coerced for the UI; failed hub shares are queued and drained; UI renders JSON-looking text as a Browse folder button.
- **Discovery after USB chat** — hash supersede no longer disconnects peers or clears ephemeral history when a replacement hash exists; UI merges `replacement_peer` into the discovered list immediately.
- **Transport RTT labels** — link-established RTT probes pass `via` so LAN probes no longer overwrite USB row latency (and vice versa).

### Added
- **README transport guide** — documents how text, files, voice, and shared folders travel over LAN, USB, and hub TCP.

## [0.6.12] — 2026-07-02

### Fixed
- **Android UI** — static asset lookup uses `chatx5/web/static` (package root), fixing “Frontend not found” after the web layer was split into `routes/`.
- **Hub group chat after P2P switch** — hub TCP relay links are no longer torn down when consolidating LAN/serial sessions; opening Group Chat calls `/api/hub/ensure` to reconnect; clients fall back to any active hub TCP peer when hash aliases differ.
- **Group message display** — hub group messages are accepted on any transport for display/relay (not only hub TCP); Android notifications fire for group chat when viewing another peer.

## [0.6.11] — 2026-07-02

### Fixed
- **Hub group chat** — hub clients fetch `identity_hash` + `identity_pubkey` from the hub server's `/api/network-status` and register the RNS identity before dialing TCP port 4242 (fixes "Hub server identity unknown" and `send_hub_message: no active link`).
- **Hub TCP inbound** — server and client finalize hub relay links once remote identity is available, so the server counts connected TCP clients and group relay works.
- **Serial link UI** — `link_active` in network-status respects transport-suffixed peer keys (`peer:serial`) and the UI session transport.

## [0.6.10] — 2026-07-02

### Fixed
- **Serial USB messaging (critical)** — RNS `path_table` rows now use the correct 7-field format (`next_hop` as bytes, `random_blobs` as `[]`). The previous 6-field injection caused `'int' object is not subscriptable` serial port crashes and one-way “connected” links with no delivery.
- **Serial inbound callback crash** — fixed `UnboundLocalError` on `prune_lan_path_for_peer` that aborted link setup when a USB peer connected.
- **Serial connect validation** — outbound USB connects are only marked established when the link is actually attached to `SerialInterface`; stale/dead USB links are torn down when the port drops.

## [0.6.9] — 2026-07-02

### Fixed
- **Serial USB messaging** — known serial endpoint peers are accepted for inbound scope even when RNS attaches a stale LAN interface; serial discovery rows now match by `identity_hash` as well as connect hash (fixes inbound rejected as “outside LAN scope”).
- **Serial path priming** — beacon-discovered USB peers get a direct 1-hop `SerialInterface` path seeded in the RNS path table so connect no longer times out waiting for an announce that never arrives over the cable.

## [0.6.8] — 2026-07-02

### Fixed
- **Hub group chat** — hub TCP relay links registered under `peer:tcp` are now counted and relayed even when RNS reports a UDP/serial attached interface; discovery supersede no longer tears down hub relay sessions.
- **Announce LAN isolation** — manual and periodic LAN announces no longer fall back to USB serial when `also_serial=False` (fixes LAN announce also announcing on serial).
- **Transport routing in web UI** — explicit `hub_group` flag on send; P2P and group chats no longer cross-route; fast peer/transport switching uses generation counters and disconnects the previous transport before reconnecting.
- **Announce Serial feedback** — toast shows USB RNS success plus companion LAN beacon packet count (or why beacon was skipped).
- **LAN ↔ Serial switching** — switching between USB and LAN rows disconnects the previous transport path before opening the new one.

### Changed
- **Uninstall scripts** — `uninstall.sh` and `uninstall.bat` now remove `.venv`, pip/pipx installs, cache, portable `chatx5-data`, build artifacts, and RNS temp sockets in addition to config/data.

## [0.6.7] — 2026-07-02

### Fixed
- **Announce Serial now reaches LAN peers** — tapping Announce Serial (or auto-announcing on USB attach) also sends a companion LAN UDP beacon that includes `serial_hash`. Previously only the RNS USB packet went out, so the remote machine (e.g. Arch) never learned the USB connect hash unless it happened to catch a periodic LAN beacon.
- **Dual-identity name matching** — ip-less RNS announces for a serial endpoint hash are classified as `via=serial` when the display name matches an existing LAN discovery row, even though LAN and USB use separate RNS identity keys.

## [0.6.6] — 2026-07-02

### Fixed
- **Symmetric serial discovery** — LAN beacons now include the peer's USB serial connect hash (`serial_hash` + `serial_identity_hash`), so both ends see LAN **and** USB rows even when RNS serial announces only propagate one way over the USB link (fixes Arch seeing only Ubuntu LAN).
- **RNS announce dual-hash** — when an ip-less announce arrives for a serial message-dest hash whose `identity_hash` already has a LAN discovery row, it is stored as `via=serial` instead of being dropped or misclassified.

### Changed
- **Settings → About** — removed the duplicate "View release notes" button; release notes remain on the bottom-dock version badge only.

## [0.6.5] — 2026-07-02

### Fixed
- **Hub client IP field** — the Hub host/port input no longer disappears while you type. `updateHubUi()` now derives client-field visibility from the live Hub-mode dropdown selection instead of the saved role, so a background `/api/network-status` poll can't hide the input mid-edit.
- **Hub runtime apply** — `_apply_hub_runtime` reads `self.messaging` defensively, so a background `threading.Timer` firing during startup/teardown no longer raises `AttributeError`.
- **Hub group chat relay** — hub TCP links whose caller-supplied hash is a discovery/identity alias (not the link's proven message-dest hash) are now registered under the proven remote instead of dropped, so the hub server counts connected clients and group messages relay correctly.
- **Hub client send path** — `_hub_tcp_peers_for_send` matches hub-server peers by hash equivalence, not exact string, so queued group messages no longer stall with "no active link".
- **Serial discovery symmetry** — ip-less serial announces rebroadcast onto LAN are classified as serial when local USB is up, instead of being discarded by `_on_announce`; both peers now see each other's USB endpoint reliably.
- **RTT per transport** — `update_peer_probe` / `clear_peer_rtt` are transport-aware; LAN UDP probes no longer overwrite serial row latency (and vice versa).
- **Serial RTT probe** — `probe_serial_path` no longer returns a bogus ~0 ms value from a cached path-table lookup; it keeps the link handshake RTT or shows nothing until a fresh path is measured.

### Changed
- **Phase 10 refactor** — transport failover + session-reconnect logic (~550 lines) extracted from `backend.py` into `core/messaging/failover.py` (`FailoverMixin`). `backend.py` is now ~1,108 lines. Public import paths and behaviour unchanged.
- Removed dead `_null_context()` from `backend.py` and the now-unneeded `E402` ruff ignore; corrected the README license label to `GPL-3.0-only`.

### CI
- Added `.github/workflows/checks.yml` — runs `scripts/check.sh` (ruff + mypy + unit tests + Android bundle sync verification) on every push and PR to `main`.

## [0.6.4] — 2026-07-02

### Changed
- **Phase 9 (tooling & hygiene)** — pre-commit config; `check.sh` requires ruff + mypy; `setup.py` is a pyproject.toml shim; Gradle `syncPythonSources` before Android builds; trimmed imports in extracted `web/` modules.
- **Messaging** — inbound link/packet callbacks moved to `inbound_callbacks.py`; `backend.py` ~1,670 lines.

## [0.6.3] — 2026-07-02

### Changed
- **Phase 8 refactor (web layer)** — `web/server.py` reduced from ~5,300 lines to a ~500-line orchestrator. HTTP handlers moved to `web/routes/`, WebSocket to `web/ws/`, RNS startup to `web/rns_lifecycle.py`, plus `messaging_bridge`, `peer_connect`, `history_store`, `settings_store`, and `background_tasks` mixins.

## [0.5.19] — 2026-07-01

### Fixed
- **Serial inbound scope** — USB serial links no longer rejected as “outside LAN scope” when RNS has not set `attached_interface` yet, or when the peer uses a serial-only hash distinct from its LAN hash.
- **Serial connect routing** — connecting to a serial discovery hash uses the USB path instead of attempting LAN when dual identities use separate hashes.
- **Contact save merge** — saving from a USB/serial row merges LAN + serial into one contact when the peer name matches; identity hash fields are persisted correctly.
- **Hub client subnet** — when the saved hub host is outside the pinned LAN (e.g. `10.0.5.37` on a `10.0.30.x` network), the client auto-resolves an in-scope hub server IP from discovery.

### Changed
- **Phase 5 refactor** — file transfer logic moved to `chatx5/core/messaging/transfer.py` (`TransferMixin`).

## [0.5.8] — 2026-06-27

### Fixed
- **Contact crash** — merging contacts with integer `port` no longer raises `'int' object has no attribute 'strip'`.
- **Delete contact** — deleting by LAN or serial hash removes the full merged contact and updates the UI immediately.
- **Stale RTT** — latency clears when the link drops or UDP ping fails (e.g. peer unplugged); header RTT only shows while actually connected.
- **Probe interval** — changing LAN/serial ping interval takes effect immediately and re-probes on the next cycle.

### Added
- **Chat header details** — full peer hash and interface type (LAN / USB Serial) shown under the display name.
- **LAN ping packet size** — configurable UDP probe payload (32–1472 bytes) under Network settings.
- **Custom sidebar title** — replace “chatx5” in the header (max 18 characters) in Profile settings.
- **Emoji search** — common terms like happy, sad, and funny match relevant emojis.
- **Sidebar toggle** — robot-style `[=•]=` button instead of the hamburger menu.

## [0.5.7] — 2026-06-27

### Fixed
- **Duplicate contacts** — split LAN/USB save files and orphan JSON rows merge into one contact on load; stale duplicate files are removed from disk.
- **Saved peers in Discovered** — LAN and serial hashes already on a saved contact no longer appear in Discovered (including related names like 330s/330ss).
- **RTT in ms** — link RTT is preferred over UDP probes; serial peers without an IP get latency from the active RNS link; chat header and contact rows show live ms.
- **Android display name** — announces and beacons use the configured name or device model when settings name is empty (no more hash-only label).

### Added
- **Collapsible desktop sidebar** — toggle with ☰ on wide screens; state persists in localStorage.

## [0.5.6] — 2026-06-27

### Fixed
- **Stale contact hashes** — saved contacts auto-refresh `lan_hash` / `serial_hash` when discovery reports the current peer (by IP, identity, or related name like 330s/330ss).
- **Wrong hash on both LAN+USB rows** — contacts with a duplicated stale hash in `lan_hash` are corrected when the live LAN peer appears in Discovered.
- **Contact LAN connect** — tapping a saved contact's LAN row uses the discovered peer hash when the stored hash is outdated.

## [0.5.5] — 2026-06-27

### Fixed
- **Custom contact names** — user-saved names are never overwritten by device announce names on startup or discovery refresh (`custom_name` flag).
- **Dual-hash contact save** — saving LAN or USB merges into one contact with distinct `lan_hash` / `serial_hash`; connect uses the transport row you tapped.
- **False serial in Discovered** — LAN-only peers (e.g. GZ16) no longer appear as `(serial)` when USB is enabled on your machine; phantom serial rows are dropped on LAN beacon.
- **Own hash in contacts** — local LAN/serial hashes are filtered from Discovered and blocked when saving a contact.
- **Ip-less announce misclassification** — RNS announces without a receiving interface are rejected instead of defaulting to serial.

## [0.5.4] — 2026-06-27

### Fixed
- **Serial announce on LAN** — Announce Serial no longer shows LAN broadcast address; RNS announces go only over the configured serial port.
- **USB hot-add without restart** — Plugging in USB creates serial identity + destination at runtime and pushes discovered peers to the web UI immediately.
- **Duplicate self USB rows** — Local LAN and serial hashes are filtered from discovery (fixes seeing your own `1ae…` and `d0fdd…` as USB peers).
- **LAN identity on serial wire** — Serial announces no longer fall back to LAN destination/identity when serial endpoint was missing.
- **Session reconnect transport** — Failover reconnect respects the transport you connected on (serial session stays serial).
- **Outbound link race** — Active outbound links are no longer torn down before connect completes.
- **Beacon name flash** — Peers that briefly show as hash prefix keep a known display name when identity was seen before.

## [0.5.3] — 2026-06-27

### Fixed
- **Contacts deleted on restart** — discovery supersession no longer removes saved contacts when LAN and USB rows share a name; dual-hash contacts update `lan_hash` / `serial_hash` instead of deleting the file.
- **LAN + USB discovery eviction** — serial announces no longer remove the LAN peer row (and vice versa); both transports stay in Discovered.
- **Contact USB connect** — connect API honors `via: serial` and saved `serial_hash` instead of falling back to the LAN discovered peer.
- **USB unplug breaks peers** — contacts and links survive serial interface loss; transport-specific highlighting no longer crosses LAN/USB rows.
- **Announce Serial on refresh** — `/api/identity` includes `serial_active` so the Serial announce button shows without clicking Announce LAN first.
- **False connection failed** — UI suppresses failure toasts when a link is already established on the requested transport.

## [0.5.2] — 2026-06-27

### Fixed
- **Discovered list empty in web UI** — `renderDiscovered` referenced `isSerial` before it was defined (ReferenceError), so peers visible in the server log never rendered in the sidebar.
- **LAN + USB rows merging in UI** — `peerMergeKey` now includes transport so both discovered rows stay visible.

## [0.5.1] — 2026-06-27

### Fixed
- **Separate LAN + USB connections** — discovery stores `hash:lan` and `hash:serial` rows independently; connect API accepts `via` so serial and LAN links to the same peer no longer collide.
- **Android back navigation** — swipe-back from chat returns to the contact list first; second back minimizes the app (WebView `"true"` callback parsing fixed).
- **Transport-aware UI** — linked-peer state, connect, and chat header track per-transport links (`hash:lan` / `hash:serial`).
- **Contact name flash** — saved contacts no longer briefly show the full RNS hash when display name is missing.

## [0.5.0] — 2026-06-27

### Changed
- **Dual LAN + Serial identities** — `identity_lan` and `identity_serial`; separate connect hashes; legacy `identity` auto-migrates to `identity_lan`.
- **No transport failover** — links stay on the transport you chose (LAN or USB).
- **Discovery** — LAN and USB appear as separate rows (`name · LAN` / `name · USB`).
- **Contacts** — one card per person with LAN/USB sub-rows.
- **Announce** — sidebar **Announce LAN** and **Announce Serial** buttons.
- **Settings** — mandatory LAN IPv4 (no Auto); per-transport probe and announce intervals (0–18000 s).
- **Profile** — Regenerate LAN / Regenerate Serial (moved from System).

### Removed
- Auto interface selection; combined single announce; link failover loop.

## [0.4.2] — 2026-06-27

### Fixed
- **LAN wake on contact tap** — opening a contact or discovered peer sends HTTP wake + reconnect so sleeping Android/desktop peers accept messages without manual re-announce.
- **Stale link reconnect** — connect no longer treats zombie RNS links as healthy; unhealthy links are torn down and re-established.
- **RTT on saved contacts** — contact list shows live RTT from discovery even when the stored IP is unchanged.
- **Discovered dedup** — peers already saved as contacts are hidden from Discovered.

### Changed
- **Android APK navigation** — contact list is the main screen; tap a peer to open chat; back once returns to the list, back again backgrounds the app.

## [0.4.1] — 2026-06-27

### Fixed
- **LAN RTT in Discovered** — UDP beacon pings no longer skipped while peers are actively announcing; RTT updates on a configurable interval.
- **Android on desktop** — beacon peers appear even when RNS identity registration is still pending (hash/name/IP sufficient).

### Added
- **Settings → Network → Link ping interval** (5–300s, default 30) — controls LAN UDP and USB serial liveness pings and RTT refresh.

## [0.4.0] — 2026-06-27

### Fixed
- **Serial RNS auto-announce** no longer floods USB with 3–5 packet bursts; one announce per event, periodic serial every 30s when auto-announce is on.
- **Discovered peers UI** updates when transport (`via`), IP, or RTT changes; authoritative peer broadcasts on Announce, scope change, and probe eviction.
- **Live LAN scope drift** (OS IP or pinned interface change without restart) refreshes discovery, drops stale subnet peers, and pushes WebSocket updates automatically.
- **Manual Announce** sends a single serial RNS packet in dual-transport mode instead of 4× bursts that clogged the link.

### Changed
- Connect/failover serial priming uses one announce every 3s instead of multi-packet bursts.
- UI transient empty-peer hold reduced from 120s to 15s; authoritative updates bypass the hold entirely.

### Tests
- `tests/test_serial_announce_policy.py` — serial rate limits, periodic loop, serial discovery visibility.

## [0.3.171] — 2026-06-26

- Fastest-path (RTT) selection per peer in discovered list.
- LAN scope save refreshes discovery paths.
- LAN auto-announce and peer ping every 30s; serial had no periodic auto-announce.

## [0.3.170] — 2026-06-25

- Hide serial badge when USB unplugged; beacon upgrades to LAN.
- Scope checker accepts in-scope LAN for serial-tagged peers.
- Transport matrix tests.

[0.4.0]: https://github.com/narl3yyy-svg/chatx5/compare/v0.3.171...v0.4.0
[0.3.171]: https://github.com/narl3yyy-svg/chatx5/compare/v0.3.170...v0.3.171
[0.3.170]: https://github.com/narl3yyy-svg/chatx5/compare/v0.3.169...v0.3.170