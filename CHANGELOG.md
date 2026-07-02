# Changelog

All notable changes to chatx5 are documented here. The README lists only the latest release summary.

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