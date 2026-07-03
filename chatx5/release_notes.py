"""Release notes shown on first install and after updates."""

from chatx5._version import __version__ as CURRENT_VERSION

RELEASE_NOTES = {
    "0.6.14": [
        "Fix: Saved contact names persist across restarts — discovery no longer overwrites your stored labels when a peer is offline.",
        "Fix: Live network status groups duplicate hub TCP client interfaces instead of listing each inbound relay separately.",
        "New: Live network status UI overhaul — session hero, transport cards, grouped interface table, deduped peer list.",
    ],
    "0.6.13": [
        "Fix: Hub group chat delivers faster — hub TCP connects no longer compete with USB serial path setup; queued group messages drain in under a second when the link comes up.",
        "Fix: Shared folder offers in group chat use share_browse (Browse folder button) instead of raw JSON text.",
        "Fix: Discovery stays visible after USB chat — hash supersede no longer disconnects or clears ephemeral peers when a replacement hash exists.",
        "Fix: RTT probes update the correct LAN or USB row (no more LAN latency shown on USB).",
    ],
    "0.6.12": [
        "Fix: Android app loads the web UI again (static assets resolved from chatx5/web/static, not web/routes/static).",
        "Fix: Group chat survives P2P transport switches — hub TCP relay links are protected and reopen via /api/hub/ensure when you return to Group Chat.",
        "Fix: Group messages display even when they arrive on a LAN/serial link; Android notifications fire for group chat when you are in another chat.",
    ],
    "0.6.11": [
        "Fix: Hub group chat overhaul — clients fetch and register the hub server identity (pubkey) from network-status before opening the TCP link.",
        "Fix: Hub TCP inbound links register once remote identity is available (server sees connected clients; group relay works).",
        "Fix: Serial/LAN link status in the UI when the active session uses a transport-suffixed peer key (e.g. peer:serial).",
    ],
    "0.6.10": [
        "Fix: Serial USB messaging — corrected RNS path table format (fixes serial port crashes and messages stuck awaiting receipt).",
        "Fix: USB inbound link callback no longer crashes; connect only shows established when the link is on SerialInterface.",
    ],
    "0.6.9": [
        "Fix: USB serial messaging — connect no longer fails after discovery; serial paths are seeded and inbound links from known USB peers are accepted.",
        "Fix: Serial identity hash from RNS links matches beacon discovery rows (330s · USB connect works both directions).",
    ],
    "0.6.8": [
        "Fix: Hub group chat — server counts connected TCP clients and relays messages reliably (hub links no longer dropped on hash supersede).",
        "Fix: Announce LAN no longer also announces on USB serial; Announce Serial toast confirms USB + companion LAN beacon.",
        "Fix: Fast switching between LAN, USB, and Group Chat no longer sends messages to the wrong chat.",
        "Uninstall scripts now remove .venv, pip installs, cache, portable data, and RNS temp sockets.",
    ],
    "0.6.7": [
        "Fix: Announce Serial now also sends a LAN beacon with your USB serial_hash — Arch sees Ubuntu · USB after you tap Announce Serial on Ubuntu.",
        "Serial RNS announces match existing LAN peers by display name (dual LAN/USB identities).",
    ],
    "0.6.6": [
        "Fix: LAN beacons now include each peer's USB serial connect hash — both machines see LAN and USB rows (fixes Arch only seeing Ubuntu on LAN).",
        "RNS announces for a known peer's serial endpoint hash are classified as USB when the identity already has a LAN row.",
        "Settings → About no longer duplicates release notes — tap the version badge in the bottom dock instead.",
    ],
    "0.6.5": [
        "Fix: Hub client host/port field no longer disappears while you type — it now follows the Hub-mode dropdown instead of the saved role.",
        "Hub group chat: hub TCP links register under the proven remote hash (fixes relay + client send when identity/message-dest hashes differ).",
        "Serial discovery: both peers now see each other's USB endpoint reliably (symmetric ip-less serial announces).",
        "RTT: LAN and serial sub-rows show distinct latencies; bogus ~0 ms serial probe values are gone.",
        "Phase 10 refactor: transport failover + session reconnect moved to messaging/failover.py; backend.py trimmed to ~1,100 lines.",
        "CI now runs ruff + mypy + tests + Android-sync verification on every push and PR.",
    ],
    "0.6.4": [
        "Phase 9: pre-commit + mandatory ruff/mypy in check.sh; setup.py delegates to pyproject.toml.",
        "Gradle auto-syncs Python sources before Android builds; web module imports trimmed.",
        "Messaging: inbound link callbacks extracted to inbound_callbacks.py (backend.py slimmer).",
    ],
    "0.6.3": [
        "Phase 8 complete: web server split into routes/, ws/, rns_lifecycle.py, and focused mixins.",
        "server.py is now a ~500-line orchestrator — easier to debug HTTP, WebSocket, and RNS layers.",
        "No user-visible behavior changes; all existing APIs and tests preserved.",
    ],
    "0.6.2": [
        "Hub group chat: hub server keeps inbound TCP client links alongside LAN/serial P2P (no longer torn down as worse path).",
        "Hub client opens a dedicated hub-TCP RNS link for group messages instead of reusing the LAN session.",
        "Release notes: click the version badge in the bottom dock or Settings → System → About.",
    ],
    "0.6.1": [
        "Saved contacts show USB serial rows when serial discovery finds the peer (even before serial_hash is stored).",
        "Serial discovery sync matches saved LAN contacts by display name (fixes Arch not seeing Ubuntu USB).",
        "Hub group tab stays visible when hub mode is on (role synced from live network status).",
        "Release notes page in Settings → System → About (no more pop-up on every update).",
        "Contacts directory is always created at startup; discovery contact sync is more reliable.",
    ],
    "0.6.0": [
        "Voice, video, image, and file messages show the trash icon; delete removes immediately with no confirm dialog.",
        "Shared folder browse: share a folder with a peer (or hub group) to browse, download, and upload files for 2 hours.",
        "Contact names no longer corrupt to hash/RTT/LAN garbage when discovery updates arrive after voice or link events.",
        "Phase 8: ShareBrowserMixin wired into web server (share_browser.py).",
    ],
    "0.5.24": [
        "Hub TCP: server counts inbound TCPClientInterface links as hub relay; hub-client TCP dials match hub host only.",
        "Hub group chat: connect reuses active hub TCP link; stops ensure_hub_link retry storm; server logs linked client count.",
        "Serial discovery: IP-less USB announces register as serial when serial transport is active.",
        "Serial runtime: stale enabled=false no longer blocks hot-add when the USB port is accessible.",
    ],
    "0.5.23": [
        "Fix serial connect crash (clear_peer_path_unless_family scoping bug in connect.py).",
        "Hub group chat: hub server accepts inbound TCP clients on port 4242; clients keep LAN P2P and hub TCP links separate.",
        "Serial USB inbound links accepted for known serial endpoint hashes (outside LAN scope fix).",
        "Contact sync from discovery ensures contacts directory exists before write.",
    ],
    "0.5.22": [
        "Hub group chat: hub clients open RNS links over hub TCP (port 4242) instead of falling back to LAN/UDP P2P.",
        "Hub send/relay uses hub TCP links only; server counts inbound hub TCP clients correctly.",
        "LAN HTTP fast transfers: offer is sent over encrypted RNS; file bytes use plain HTTP on LAN with a one-time token (--share required).",
        "Phase 7 refactor: announce loops moved to messaging/announce.py.",
    ],
    "0.5.21": [
        "LAN file transfers use HTTP fast-path again while hub mode is on (hub blocked LAN HTTP for all P2P in v0.5.20).",
        "Large LAN files: 512KB+ threshold, 1MB HTTP chunks, MTU tuned before send — much faster on gigabit Ethernet.",
        "Hub clients auto-retry TCP connect after host resolve; group chat shows Listening/Connected with client count.",
        "Phase 6 refactor: hub relay logic consolidated in messaging/hub.py.",
    ],
    "0.5.20": [
        "Hub TCP inbound links from remote clients are accepted on the hub server even before RNS attaches an interface (fixes unknown/outside LAN scope rejections).",
        "Hub clients persist an auto-resolved in-scope hub host when the saved IP is on another subnet; hub_server_hash is only recorded from hub TCP links, not LAN P2P.",
        "Group chat shows Connected/Listening from hub_group_linked status; Android skips duplicate WS notifications, keeps keyboard open after send, and uses native logo picker.",
        "Orphan serial-hash contact files merge into dual LAN+USB saved contacts.",
    ],
    "0.5.19": [
        "Serial USB inbound links no longer rejected as outside LAN scope when the peer hash is a serial-only endpoint (distinct from LAN hash).",
        "Connect to a serial discovery hash uses the USB path instead of attempting LAN when dual identities use separate hashes.",
        "Saving a contact merges LAN + USB rows when the peer name matches; hub clients auto-resolve an in-scope hub host when the saved IP is on another subnet.",
        "Phase 5 refactor: file transfer logic moved to messaging/transfer.py.",
    ],
    "0.5.18": [
        "Fix connect regression: outbound links no longer fail with _null_context / serial_path_is_pinned NameError (Phase 3 extraction).",
        "Hub group chat: clients auto-fetch hub server identity from /api/network-status, open a TCP RNS link, and drain queued group messages.",
        "Hub TCP inbound links bypass LAN scope checks; Phase 4 refactor moves queue logic to messaging/queue.py.",
    ],
    "0.5.17": [
        "Discovered peers: ↻ refresh button re-announces and rescans the peer list without a full restart.",
        "Hub settings: clearer hints — hub server uses 0.0.0.0:4242; hub client host is separate from LAN discovery IP; UDP LAN is correct for P2P while hub is on.",
        "Internal refactor: connect flow moved to messaging/connect.py (no user-visible API changes).",
    ],
    "0.5.16": [
        "LAN connect from the initiator works when the peer web UI is localhost-only — HTTP wake failure no longer marks the peer unreachable or skips UDP/inbound connect.",
        "Initiator waits for inbound links after a failed quick UDP connect instead of jumping straight to a short outbound timeout.",
        "Internal refactor: peer link map and transport matching moved to messaging/links.py (no user-visible API changes).",
    ],
    "0.5.14": [
        "Serial discovery rows from genuine USB announces are no longer purged when LAN rebroadcast updates the path table.",
        "LAN and USB sessions stay independent: switching transport in the UI keeps the correct link, send path, and connection state.",
        "Automatic cross-transport failover reconnect is disabled — use separate LAN and USB chat rows instead.",
    ],
    "0.5.13": [
        "Dual-hash contact save no longer copies the LAN hash into the USB row when both transports are discovered.",
        "Phantom serial discovery rows (same hash as LAN) are filtered client-side; duplicate serial_hash is dropped when it matches lan_hash.",
    ],
    "0.5.12": [
        "Saving a USB/serial contact no longer copies the serial hash into the LAN row — both transports merge in one save when discovered.",
        "Voice message bubbles are wider so the audio scrubber is easier to use.",
    ],
    "0.5.11": [
        "Serial inbound links no longer rejected as outside LAN scope when RNS has not set attached_interface yet.",
        "Announce Serial button stays visible when USB serial is configured, even before RNS hot-add completes.",
        "Serial peer scope lookup prefers via=serial over stale LAN entries for the same identity.",
    ],
    "0.5.10": [
        "Disconnect no longer removes saved contacts when peer hashes are superseded.",
        "Connected state shows correctly for background LAN links; LAN contact rows pass via=lan.",
        "Clear chat history matches all LAN/serial aliases for a saved contact.",
        "Deleting a contact clears session state so restart does not auto-reconnect.",
        "Serial USB stays off when disabled in settings (no hot-add or discovery).",
    ],
    "0.5.9": [
        "Serial USB enables without restart — identity and RNS hot-add on first connect.",
        "USB unplug/replug recovers automatically while the server stays running.",
        "Contact save respects custom names; disconnect is reliable and moved to the bottom dock.",
        "Chat header shows one RTT badge using live link latency when connected.",
        "LAN RTT uses fixed minimum probe size; serial ping interval minimum is 3 seconds.",
    ],
    "0.5.8": [
        "Chat header shows the full peer hash and LAN/USB interface type.",
        "RTT clears when a peer disconnects — no more stale 2ms after unplugging.",
        "Fixed contact click crash and delete not removing contacts from the list.",
        "LAN ping interval changes apply immediately; optional ping packet size (32–1472 B).",
        "Custom sidebar title (18 chars), better emoji search, robot sidebar toggle.",
    ],
    "0.5.7": [
        "Duplicate contacts from fast LAN/USB saves or page refresh are merged automatically.",
        "Saved peers no longer show in Discovered — only unsaved hashes appear there.",
        "Latency in ms shows on contacts, Discovered peers, and in the chat header when connected.",
        "Android sends your device name (or model) in discovery instead of a hash prefix.",
        "Desktop web UI: collapse the sidebar with the menu button; it remembers your preference.",
    ],
    "0.5.6": [
        "Saved contacts pick up the correct LAN/USB hash from Discovered automatically.",
        "Fixes contacts stuck on an old hash that fails to connect while Discovered shows the right peer.",
        "Tapping a contact's LAN row connects using the live discovered hash.",
    ],
    "0.5.5": [
        "Custom contact names stick — discovery no longer overwrites what you saved.",
        "Saving LAN or USB adds the right hash to one contact; tap LAN or USB row to connect on that path.",
        "LAN-only peers no longer show a false USB row when your machine has serial enabled.",
        "Your own device hash no longer appears in Discovered or saved contacts.",
    ],
    "0.5.4": [
        "Announce Serial now announces only on the USB serial port — no more LAN broadcast in the toast.",
        "USB hot-add works without restart: serial identity is created and peers appear in Discovered.",
        "Your own LAN/serial hashes no longer show up as USB peers in Discovered.",
        "Reconnect stays on the transport you chose (LAN or USB).",
    ],
    "0.5.3": [
        "Saved contacts survive server restart and USB unplug — LAN and USB hashes are kept separately.",
        "LAN and USB peers no longer evict each other in Discovered when both are visible.",
        "Connect on a saved contact's USB or LAN row uses that transport, not the other discovered row.",
        "Announce Serial button shows on page load when USB is configured.",
        "No more false 'connection failed' toast when you are already connected on LAN.",
    ],
    "0.5.2": [
        "Fixed Discovered list not showing peers in the web UI (server log was fine).",
        "LAN and USB rows in Discovered stay separate instead of merging into one.",
    ],
    "0.5.1": [
        "LAN and USB are separate connections — tap the LAN or USB sub-row to connect on that transport; both can stay linked at once.",
        "Discovered list keeps serial and LAN rows for the same peer instead of overwriting each other.",
        "Android: back from chat returns to contacts first; back again minimizes the app.",
        "Contact names no longer flash the full RNS hash when saving a peer.",
    ],
    "0.5.0": [
        "LAN and USB each have their own RNS identity and connect hash — no more switching transports mid-chat.",
        "Sidebar: Announce LAN and Announce Serial (separate buttons). Discovered shows name · LAN and name · USB.",
        "Contacts: one card with LAN/USB sub-rows. Settings → Profile: regenerate each identity separately.",
        "Setup requires picking a LAN IPv4. Per-transport probe/announce intervals (0 = off, up to 5 hours).",
    ],
    "0.4.2": [
        "Tap a contact to wake sleeping LAN peers — messages send again without re-announce (especially after Android screen lock).",
        "Saved contacts show live RTT; peers you already saved no longer duplicate in Discovered.",
        "Android APK: contact list is home — open a chat from there; back returns to the list, back again backgrounds the app.",
    ],
    "0.4.1": [
        "LAN peers show RTT in Discovered — pings run on a configurable interval instead of being blocked by fresh beacons.",
        "Settings → Network: Link ping interval (5–300s) applies to both LAN UDP and USB serial liveness checks.",
        "Android beacons appear on desktop even when identity registration is still pending.",
    ],
    "0.4.0": [
        "Serial RNS auto-announce is one packet every 30s (no more USB bursts that clogged messaging).",
        "Discovered peers update live when LAN scope drifts, transport switches, or stale entries are purged.",
        "Tap Announce sends a single serial packet in dual-transport mode; connect priming is gentler on USB.",
    ],
    "0.3.171": [
        "Discovered list shows one path per peer — fastest RTT wins when LAN and serial are both up.",
        "Saving a new LAN scope refreshes discovery (drops stale subnet entries, keeps USB serial).",
        "LAN auto-announce and peer ping run every 30s; serial has no periodic auto-announce (tap Announce).",
    ],
    "0.3.170": [
        "Serial badge/link hidden when USB is unplugged — peers upgrade to LAN via beacon instead of showing stale USB.",
        "Inbound scope accepts LAN links for serial-tagged peers with an in-scope IP when local USB is down.",
        "USB detach clears serial paths and discovery; transport matrix tests cover sender/receiver, RTT, and 3-device scenarios.",
    ],
    "0.3.169": [
        "Fix v0.3.168 regressions: peers no longer vanish from aggressive probe eviction.",
        "Announces reset liveness; serial peers are never probe-dropped; transfer cancel fixed.",
        "Restart uses in-process reload again (spawn only on failure); serial window restored to 2.",
    ],
    "0.3.168": [
        "Peer probe: UDP/serial liveness checks with avg RTT in sidebar; peers drop after 10s without reply.",
        "Custom sidebar logo (click cx), identity modal (click your hash), canceled file transfers removed from chat.",
        "Ubuntu restart uses restart-server.sh; serial transfers faster (window=3) and skip announces during uploads.",
    ],
    "0.3.167": [
        "Linux Restart server re-execs via launch-server.sh (preserves dialout/uucp serial groups on Ubuntu).",
        "IP-less USB announces are accepted when the packet interface is not cached yet — fixes Ubuntu not appearing on Arch.",
        "Serial announces never include a LAN IP (send-side strip + no fan-out fallback on USB).",
    ],
    "0.3.166": [
        "Cross-subnet LAN peers are fully rejected — bridged announces on USB (with an IP) are dropped.",
        "IP-less LAN ghost peers no longer appear when serial is up; beacons must arrive from your subnet.",
        "Same-identity discovery duplicates are merged; serial sessions stop failover-looping on path-table flaps.",
    ],
    "0.3.165": [
        "Discovery tags USB only when the announce packet arrived on SerialInterface — LAN peers no longer show as USB.",
        "Cross-subnet LAN peers are rejected (Ubuntu no longer sees Windows on 10.0.5.x; Arch shows Windows as LAN on 10.10.10.x).",
        "File cancel on the sender notifies the receiver to abort; progress shows USB/LAN transport and receive status on both sides.",
    ],
    "0.3.164": [
        "Serial connect restores USB routes from announce receipts when LAN rebroadcast overwrote path_table.",
        "Peer identity is registered as soon as a serial RNS announce arrives — Connect no longer waits until click.",
        "Serial path priming reinforces the USB route before opening an outbound link.",
    ],
    "0.3.163": [
        "Serial discovery uses the path table (not stale LAN rebroadcasts) to classify incoming RNS announces.",
        "Out-of-scope LAN IPs on USB are reclassified as serial peers — Arch and Ubuntu discover each other across different subnets.",
        "Discovery stays enabled when USB serial hot-attaches; scope purge no longer removes serial-only peers.",
    ],
    "0.3.162": [
        "Sends are blocked unless the link's remote RNS identity matches the target peer — fixes messages to Ubuntu arriving at Windows.",
        "Beacon discovery no longer overwrites serial-only peers with LAN IPs or via=rns.",
        "Clicking a discovered USB peer connects over serial only (UI no longer passes a stale LAN IP).",
        "Discovered list shows USB vs LAN badge; serial peers never display a cross-attached IP.",
    ],
    "0.3.161": [
        "Parallel LAN + serial sessions: connect to Windows over LAN and Ubuntu over USB at the same time — links no longer tear each other down.",
        "Send routing is transport-locked: messages to a serial peer cannot leak out over an active LAN link (and vice versa).",
        "Failover stays on the peer's transport zone in dual-transport mode — no more LAN↔serial switching mid-session.",
        "Discovery prefers serial entries when both serial and LAN records exist; stale LAN duplicates for the same name are evicted.",
    ],
    "0.3.160": [
        "Serial connect priming works during connect/failover — announces are no longer suppressed mid-handshake.",
        "Queued messages drain on all reconnect paths (failover, resume, inbound), not only manual Connect.",
        "Discovery prefers in-scope LAN peers over serial when both transports are up; stale contact IPs no longer force LAN to USB peers.",
        "Serial inbound links and IP-less peers pass scope checks while USB is configured (not only when already online).",
        "Discovery classifies peers from the receiving RNS interface; stale UDP targets are pruned on scope change.",
    ],
    "0.3.159": [
        "IP-less RNS announces are always treated as serial peers — fixes Ubuntu not discovering ARCH on USB even when Arch sees Ubuntu.",
        "Serial connect pins the path on SerialInterface so LAN UDP announces cannot steal the route mid-handshake.",
        "RNS link-establishment timeout raised to 22s+ on serial outbound (was 12s internal timeout).",
    ],
    "0.3.158": [
        "Serial inbound links are always accepted on SerialInterface — Arch no longer rejects Ubuntu when a stale UDP path exists on the shared 10.10.10.x LAN.",
        "IP-less serial RNS announces are discovered using the receiving interface, so Arch sees Ubuntu (serial) even when a LAN path is cached.",
        "Serial connect pauses announce bursts and session-resume while a link attempt is in progress.",
    ],
    "0.3.157": [
        "Serial connect: one consolidated attempt (no triple prime/outbound loops) with full 22s timeout and inbound wait — fixes Arch↔Ubuntu USB link failing with Peer not reachable.",
        "Discovery resolver prefers via=serial over stale LAN/rns entries so dual-transport nodes route serial peers correctly.",
        "Failover for serial peers stays on serial only; reconnect is blocked while a connect is already in progress.",
    ],
    "0.3.156": [
        "Settings nav split: Live status is its own tab under Network; Network tab is config only; Network maintenance moved to System.",
        "Serial failover waits for USB serial to come back online instead of hammering reconnect while the port is down.",
    ],
    "0.3.155": [
        "Settings toggle cards fixed — checkboxes no longer stretch full width; text and switch lay out cleanly.",
        "Serial link stability: accept inbound serial links before peer hash resolves; stop failover from tearing down healthy serial sessions.",
        "Serial-only peers use longer reconnect cooldown (no 4s reconnect loop); large file transfers block failover while active.",
        "Serial discovery peers stay on serial transport — bridged LAN announces no longer overwrite them.",
    ],
    "0.3.154": [
        "Network status, Refresh, Announce, and Reset moved into Settings → Network — sidebar Network button removed.",
        "Serial file transfers: no failover during active transfers; link close no longer hijacks to a different peer; longer serial timeouts.",
        "False \"peer identity changed\" fixes: dual-transport (serial + LAN) discoveries no longer supersede the same peer; contacts migrate instead of being deleted.",
        "Settings toggles use cleaner toggle cards instead of bare checkboxes.",
    ],
    "0.3.153": [
        "Settings opens as a full-page view with left navigation (Profile, Storage, Network, System) instead of a cramped sidebar overlay.",
        "Network settings reorganized: LAN discovery, transport cards, USB serial, and hub each have their own section — no duplicate Advanced UDP/TCP buttons.",
        "IPv4 interface list is now a clean table instead of cramped chips.",
    ],
    "0.3.150": [
        "Critical cross-talk fix: queued messages to a serial peer (Ubuntu) can no longer leak out over an active LAN link (Windows).",
        "RNS path requests no longer bridge between SerialInterface and UDP/LAN when both are up — stops Windows appearing on Ubuntu's serial discovery list.",
        "Out-of-scope LAN peers are rejected (not stripped) when serial is active; only direct 1-hop serial neighbors are discovered IP-less.",
    ],
    "0.3.149": [
        "Dual transport fix: when a peer has no in-scope LAN IP (USB serial neighbor), Connect goes straight to SerialInterface — no more failed LAN/UDP quick-connect to cross-subnet peers.",
        "Stale UDP path entries are cleared before serial priming so Arch↔Ubuntu messaging works while Arch↔Windows stays on the pinned 10.10 LAN.",
        "IP-less RNS discovers are tagged via=serial for reliable serial-first routing.",
    ],
    "0.3.148": [
        "Fixed Ubuntu/Linux serial startup crash when USB was already in the RNS config — chatx5 no longer hot-adds a duplicate SerialInterface on /dev/ttyUSB0 during boot.",
        "Serial dedupe now keeps the healthiest interface and stops reconnect loops without nulling the port handle (avoids RNS readLoop NoneType errors).",
    ],
    "0.3.147": [
        "Serial RNS announces no longer embed a LAN IPv4 — cross-subnet USB peers (Arch 10.0.30.x ↔ Ubuntu 10.0.5.x) stay in Discovered and connect over SerialInterface.",
        "Out-of-scope LAN IPs are stripped (not stored) when serial is active; misleading RNS discovery logs for rejected peers are suppressed.",
        "Connect skips HTTP/UDP wake to out-of-scope peer IPs and prefers the serial path when available.",
    ],
    "0.3.146": [
        "Serial + LAN dual transport: tapping Announce now bursts RNS announces on USB serial even when UDP LAN is up — fixes Arch↔Ubuntu serial discovery across different pinned subnets.",
        "LAN scope isolation no longer blocks serial peers (no IP / via serial) or links on SerialInterface — cross-subnet USB chat works while LAN stays scoped.",
    ],
    "0.3.145": [
        "Pinned LAN scope is enforced end-to-end — peers on a different /24 (e.g. 10.0.5.x vs 10.10.10.x on the same NIC) are dropped from discovery, blocked on connect, and rejected for inbound links and messages.",
        "RNS announces now include the pinned IPv4 so peers update when you switch interfaces; stale cross-subnet entries are removed automatically.",
        "Changing LAN scope tears down out-of-scope links and re-announces on the new subnet.",
    ],
    "0.3.144": [
        "Hub TCP no longer dials when hub host is on a different subnet than your pinned LAN (e.g. hub 10.0.30.109 while pinned to 10.0.5.37) — stops Connection refused spam in logs.",
        "Setting hub mode to Off now disables and removes the saved hub TCP client (was left enabled in RNS config).",
    ],
    "0.3.143": [
        "Hub client + pinned LAN: P2P on 10.0.5.x stays on direct UDP — no longer forced onto hub TCP (fixes Android seeing Ubuntu-bound messages).",
        "Pinned IPv4 scopes discovery even while hub is on — Android/10.0.30.x peers hidden when you pin enp2s0|10.0.5.37.",
        "LAN interface picker save is more reliable (survives scope-change reload; shows server error text).",
    ],
    "0.3.142": [
        "Linux Settings → Network lists every IPv4 on a NIC — secondary addresses from ip addr add (e.g. 10.0.5.37 and 10.10.10.37 on enp2s0) now appear in the picker.",
        "Pin a specific address with NIC|IP (e.g. enp2s0|10.0.5.37) to scope discovery and beacons to that subnet.",
    ],
    "0.3.141": [
        "Settings → Network shows a clear warning when TCP LAN is unavailable or limited because hub mode is on.",
        "Hub server blocks switching to TCP LAN (port 4242 is reserved for group relay); hub client explains TCP LAN is for local P2P only.",
    ],
    "0.3.140": [
        "Hub client + TCP LAN: switching Primary LAN transport to TCP LAN now works while staying a hub client — P2P peers connect over TCP, group chat stays on the hub link.",
        "Hub relay no longer treats TCP LAN peer links as hub clients (fixes accidental group-chat leak over LAN TCP).",
    ],
    "0.3.139": [
        "Hub group chat is isolated to TCP hub transport — P2P-only peers (hub off) no longer receive group messages relayed from the hub server.",
        "Group messages are dropped on receive when hub mode is off, and hub server relay targets only TCP-connected hub clients.",
        "Removed unused config/config.ini template and empty chatx5/ui package.",
    ],
    "0.3.138": [
        "Changing Settings → Network IPv4 now drops all active links and clears cached RNS paths — messages no longer cross subnets after a NIC change.",
        "Beacon discovery strictly rejects peers outside your LAN /24 (no more 10.0.30.x on 10.10.100.x).",
        "RNS broadcast address follows your pinned IPv4 instead of falling back to 255.255.255.255.",
        "Ctrl+C shuts down cleanly on Linux; Ctrl+Z suspend is disabled in run.sh to avoid stuck ports.",
    ],
    "0.3.137": [
        "Network panel (sidebar 🌐) shows live discovery/link status only.",
        "All network configuration (IPv4 pin, UDP/TCP LAN, hub, serial) lives under Settings → Network.",
        "Discovery in Auto mode now scopes to your primary LAN /24 — VPN subnets like 10.0.30.x no longer appear when you are on 10.10.100.x.",
        "Cross-subnet 10.x bleed fixed: 10.0.30.x and 10.10.100.x are separate networks again.",
        "Release notes dialog on first install and after each update.",
    ],
}


def notes_for_version(version=None):
    version = (version or CURRENT_VERSION).strip()
    return RELEASE_NOTES.get(version, [])


def release_notes_payload(version=None):
    version = (version or CURRENT_VERSION).strip()
    return {
        "version": version,
        "notes": notes_for_version(version),
        "has_notes": bool(notes_for_version(version)),
    }


def _version_sort_key(version):
    try:
        return tuple(int(part) for part in (version or "").split("."))
    except ValueError:
        return (0, 0, 0)


def all_release_notes():
    """All release notes, newest version first."""
    versions = sorted(RELEASE_NOTES.keys(), key=_version_sort_key, reverse=True)
    return [{"version": ver, "notes": RELEASE_NOTES[ver]} for ver in versions]