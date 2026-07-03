function init() {
  if (window.chatx5Android?.isAndroid?.()) {
    appPlatform = 'android';
    document.body.classList.add('android-shell');
  }
  loadUnreadCounts();
  requestNotificationPermission();
  initMobileLayout();
  showWelcome();
  fetchIdentity();
  pollRnsHealth(0);
  fetch('/api/settings').then(r => r.json()).then(s => {
    window._hubRole = s.hub_role || 'off';
    window._appSettings = s;
    applySettingsToForm(s);
    updateHubUi();
    if (!s.setup_complete) {
      openSetupWizard(s);
    }
    trackAppVersion(s);
  }).catch(() => {});
  connectWS();
  loadEmojiKeywords();
  startPeerPolling();
  setupGlobalHandlers();
  setupVisibilitySync();
}

function mergeInterfaceLists(...lists) {
  const byKey = {};
  lists.flat().filter(Boolean).forEach(iface => {
    const name = iface?.name;
    if (!name) return;
    const ip = iface?.ip || 'disconnected';
    const key = name + '|' + ip;
    byKey[key] = iface;
  });
  return Object.values(byKey).sort((a, b) => {
    const score = (iface) => {
      if (iface.gateway_iface) return 0;
      if (iface.kind === 'vpn') return 3;
      if (iface.kind === 'wifi' || iface.kind === 'ethernet') return 1;
      return 2;
    };
    const diff = score(a) - score(b);
    return diff !== 0 ? diff : String(a.name).localeCompare(String(b.name));
  });
}

function renderLanInterfaceSummary(interfaces) {
  const el = document.getElementById('lan-interface-summary');
  if (!el) return;
  const items = (interfaces || []).filter(i => i?.name);
  if (!items.length) {
    el.innerHTML = '<p class="field-hint" style="margin:10px 0 0">No interfaces found — tap Rescan.</p>';
    return;
  }
  const rows = items.map(iface => {
    const ip = iface.ip && iface.ip !== 'disconnected' ? iface.ip : '—';
    const up = iface.up && ip !== '—';
    const gw = iface.gateway_iface
      ? ' <span class="net-gw-badge" title="Default route">default</span>' : '';
    return `<tr>
      <td>${escapeHtml(iface.name)}${gw}</td>
      <td><code class="net-ip">${escapeHtml(ip)}</code></td>
      <td><span class="iface-status"><span class="status-dot ${up ? 'up' : 'down'}"></span>${up ? 'Up' : 'Down'}</span></td>
    </tr>`;
  }).join('');
  el.innerHTML = `<table class="net-iface-table"><thead><tr><th>Interface</th><th>IPv4</th><th>Status</th></tr></thead><tbody>${rows}</tbody></table>`;
}

function syncTransportCards() {
  const sel = document.getElementById('settings-lan-transport');
  const val = sel?.value || 'udp_lan';
  document.querySelectorAll('.net-transport-card').forEach(card => {
    card.classList.toggle('active', card.dataset.transport === val);
  });
}

function pickLanTransport(mode) {
  const sel = document.getElementById('settings-lan-transport');
  if (!sel) return;
  sel.value = mode;
  syncTransportCards();
  applyLanTransport();
}

function applySettingsToForm(s, ifaces) {
  if (!s) return;
  const nameEl = document.getElementById('settings-name');
  if (nameEl) nameEl.value = s.name || '';
  const retentionEl = document.getElementById('settings-retention');
  if (retentionEl) retentionEl.value = s.history_retention || 'never';
  const receivedEl = document.getElementById('settings-received-dir');
  if (receivedEl) receivedEl.value = s.received_dir || '';
  const autoReset = document.getElementById('settings-network-auto-reset');
  if (autoReset) autoReset.checked = s.network_stats_auto_reset !== false;
  if (Array.isArray(ifaces)) {
    populateLanInterfaceSelect(ifaces, s.lan_interface || '');
    renderLanInterfaceSummary(ifaces);
  }
  window._hubRole = s.hub_role || 'off';
  window._lanTransportHubTcp = s.lan_transport_hub_tcp || {};
  window._lastLanInterface = s.lan_interface || '';
  const hubRole = document.getElementById('settings-hub-role');
  if (hubRole) hubRole.value = window._hubRole;
  const hubHost = document.getElementById('settings-hub-host');
  if (hubHost) hubHost.value = s.hub_host || '';
  const hubPort = document.getElementById('settings-hub-port');
  if (hubPort) hubPort.value = s.hub_port || 4242;
  applyBrandTitle(s.brand_title);
  trackAppVersion(s);
  const brandTitleEl = document.getElementById('settings-brand-title');
  if (brandTitleEl) brandTitleEl.value = (s.brand_title || '').slice(0, 18);
  const lanProbeEl = document.getElementById('settings-lan-probe-interval');
  if (lanProbeEl) {
    const v = s.lan_probe_interval_s != null ? s.lan_probe_interval_s : s.probe_interval_s;
    lanProbeEl.value = String(v != null ? v : 30);
  }
  const serialProbeEl = document.getElementById('settings-serial-probe-interval');
  if (serialProbeEl) {
    const v = s.serial_probe_interval_s != null ? s.serial_probe_interval_s : s.probe_interval_s;
    serialProbeEl.value = String(v != null ? v : 30);
  }
  const lanAnnEl = document.getElementById('settings-lan-announce-interval');
  if (lanAnnEl) {
    let v = s.lan_announce_interval_s;
    if (v == null && s.auto_announce) v = 30;
    lanAnnEl.value = String(v != null ? v : 0);
  }
  const serialAnnEl = document.getElementById('settings-serial-announce-interval');
  if (serialAnnEl) {
    let v = s.serial_announce_interval_s;
    if (v == null && s.auto_announce) v = 30;
    serialAnnEl.value = String(v != null ? v : 0);
  }
  const maxLinksEl = document.getElementById('settings-max-peer-links');
  if (maxLinksEl) maxLinksEl.value = String(s.max_peer_links != null ? s.max_peer_links : 0);
  onHubRoleChange();
  updateHubUi();
}

function refreshLanInterfaces(showToast, btn, forceRefresh) {
  const el = btn || document.getElementById('lan-refresh-btn');
  if (el) {
    el.classList.add('spinning');
    el.disabled = true;
  }
  const qs = forceRefresh ? '?refresh=1' : '';
  return fetch('/api/interfaces' + qs)
    .then(async r => {
      let d = {};
      try { d = await r.json(); } catch (_) {}
      if (!r.ok) {
        throw new Error(d.error || `HTTP ${r.status}`);
      }
      return d;
    })
    .then(d => {
      const ifaces = d.interfaces || [];
      const sel = document.getElementById('settings-lan-interface');
      const pinned = sel?.value || '';
      populateLanInterfaceSelect(ifaces, pinned);
      populateSetupLanSelect(ifaces, document.getElementById('setup-lan-interface')?.value || '');
      renderLanInterfaceSummary(ifaces);
      if (showToast) toast(ifaces.length ? `Refreshed — ${ifaces.length} network card(s)` : 'Refreshed — no network cards found');
      return ifaces;
    })
    .catch((err) => {
      if (showToast) toast('Failed to refresh network cards' + (err?.message ? `: ${err.message}` : ''));
      return [];
    })
    .finally(() => {
      if (el) {
        el.classList.remove('spinning');
        el.disabled = false;
      }
    });
}

function loadSetupInterfacesWithRetry(attempt, settings) {
  const refresh = attempt > 0 ? '?refresh=1' : '';
  fetch('/api/interfaces' + refresh)
    .then(r => r.json())
    .then(d => {
      const ifaces = d.interfaces || [];
      populateLanInterfaceSelect(ifaces, settings?.lan_interface);
      populateSetupLanSelect(ifaces, settings?.lan_interface);
      renderLanInterfaceSummary(ifaces);
      if (ifaces.length === 0 && attempt < 4) {
        setTimeout(() => loadSetupInterfacesWithRetry(attempt + 1, settings), 300);
      }
    })
    .catch(() => {
      if (attempt < 10) {
        setTimeout(() => loadSetupInterfacesWithRetry(attempt + 1, settings), 500);
      }
    });
}

function openSetupWizard(settings) {
  const wiz = document.getElementById('setup-wizard');
  if (!wiz) return;
  settings = settings || {};
  const nameEl = document.getElementById('setup-name');
  if (nameEl) nameEl.value = settings.name || '';
  const retentionEl = document.getElementById('setup-retention');
  if (retentionEl) retentionEl.value = settings.history_retention || 'never';
  const receivedEl = document.getElementById('setup-received-dir');
  if (receivedEl) receivedEl.value = settings.received_dir || '';
  const autoEl = document.getElementById('setup-auto-announce');
  if (autoEl) {
    autoEl.checked = settings.auto_announce !== undefined
      ? !!settings.auto_announce
      : true;
  }
  const recvHint = document.getElementById('setup-received-hint');
  if (recvHint) {
    recvHint.textContent = (window.chatx5Android?.isAndroid?.() || appPlatform === 'android')
      ? 'Tap Select folder to choose where received files are saved.'
      : 'Choose where incoming files are saved. You can change this later in Settings.';
  }
  loadSetupInterfacesWithRetry(0, settings);
  wiz.classList.add('open');
}

function completeSetupWizard() {
  const name = document.getElementById('setup-name')?.value.trim() || '';
  if (!name) {
    toast('Enter a display name');
    document.getElementById('setup-name')?.focus();
    return;
  }
  const btn = document.querySelector('#setup-wizard .btn-block.primary');
  if (btn?.disabled) return;
  if (btn) {
    btn.disabled = true;
    btn.textContent = 'Saving…';
  }
  const lan_interface = document.getElementById('setup-lan-interface')?.value || '';
  if (!lan_interface) {
    toast('Pick an IPv4 address for LAN chat');
    document.getElementById('setup-lan-interface')?.focus();
    if (btn) {
      btn.disabled = false;
      btn.textContent = 'Get started';
    }
    return;
  }
  const auto_announce = !!document.getElementById('setup-auto-announce')?.checked;
  const history_retention = document.getElementById('setup-retention')?.value || 'never';
  const received_dir = document.getElementById('setup-received-dir')?.value.trim() || '';
  const lan_transport = document.getElementById('setup-lan-transport')?.value || 'udp_lan';
  document.getElementById('setup-wizard')?.classList.remove('open');
  toast('Saving setup…');
  fetch('/api/settings', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      name,
      lan_interface,
      lan_transport,
      auto_announce,
      history_retention,
      received_dir,
      setup_complete: true,
    }),
  }).then(r => r.json()).then(d => {
    if (d.status !== 'ok') {
      toast('Setup failed: ' + (d.error || 'unknown'));
      return;
    }
    window._appSettings = d.settings || {};
    window._hubRole = d.settings?.hub_role || 'off';
    updateHubUi();
    toast('Setup complete');
    maybeShowReleaseNotes(d.settings || {});
    if (!auto_announce) toast('Tap 📡 Announce LAN when ready to discover peers');
  }).catch(() => toast('Setup failed'))
  .finally(() => {
    if (btn) {
      btn.disabled = false;
      btn.textContent = 'Get started';
    }
  });
}

function clampIntervalInput(el, fallback, max) {
  let sec = parseInt(el?.value, 10);
  if (!Number.isFinite(sec)) sec = fallback;
  sec = Math.max(0, Math.min(max || 18000, sec));
  if (el) el.value = String(sec);
  return sec;
}

function applyBrandTitle(title) {
  const el = document.getElementById('brand-title');
  if (!el) return;
  const text = (title || '').trim().slice(0, 18) || 'chatx5';
  el.textContent = text;
}

function saveBrandTitle() {
  const el = document.getElementById('settings-brand-title');
  const title = (el?.value || '').trim().slice(0, 18);
  if (el) el.value = title;
  applyBrandTitle(title);
  fetch('/api/settings', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({brand_title: title}),
  }).then(r => r.json()).then(d => {
    if (d.status === 'ok') {
      window._appSettings = d.settings || {};
      applyBrandTitle(d.settings?.brand_title);
    } else toast('Save failed: ' + (d.error || ''));
  }).catch(() => toast('Save failed'));
}

function saveLanProbeInterval() {
  const el = document.getElementById('settings-lan-probe-interval');
  const sec = clampIntervalInput(el, 30);
  fetch('/api/settings', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({lan_probe_interval_s: sec}),
  }).then(r => r.json()).then(d => {
    if (d.status === 'ok') {
      window._appSettings = d.settings || {};
      toast(sec ? ('LAN link ping: ' + sec + 's') : 'LAN link ping: off');
    } else toast('Save failed: ' + (d.error || ''));
  }).catch(() => toast('Save failed'));
}

function saveSerialProbeInterval() {
  const el = document.getElementById('settings-serial-probe-interval');
  let sec = clampIntervalInput(el, 30);
  if (sec > 0 && sec < 3) sec = 3;
  if (el) el.value = String(sec);
  fetch('/api/settings', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({serial_probe_interval_s: sec}),
  }).then(r => r.json()).then(d => {
    if (d.status === 'ok') {
      window._appSettings = d.settings || {};
      toast(sec ? ('Serial link ping: ' + sec + 's') : 'Serial link ping: off');
    } else toast('Save failed: ' + (d.error || ''));
  }).catch(() => toast('Save failed'));
}

function saveMaxPeerLinks() {
  const el = document.getElementById('settings-max-peer-links');
  let limit = parseInt(el?.value, 10);
  if (!Number.isFinite(limit) || limit < 0) limit = 0;
  if (limit > 64) limit = 64;
  if (el) el.value = String(limit);
  fetch('/api/settings', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({max_peer_links: limit}),
  }).then(r => r.json()).then(d => {
    if (d.status === 'ok') {
      window._appSettings = d.settings || {};
      toast(limit ? (`Max connections: ${limit}`) : 'Max connections: unlimited');
    } else toast('Save failed: ' + (d.error || ''));
  }).catch(() => toast('Save failed'));
}

function saveLanAnnounceInterval() {
  const el = document.getElementById('settings-lan-announce-interval');
  const sec = clampIntervalInput(el, 0);
  fetch('/api/settings', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({lan_announce_interval_s: sec}),
  }).then(r => r.json()).then(d => {
    if (d.status === 'ok') {
      window._appSettings = d.settings || {};
      toast(sec ? ('LAN auto-announce: ' + sec + 's') : 'LAN auto-announce: off');
    } else toast('Save failed: ' + (d.error || ''));
  }).catch(() => toast('Save failed'));
}

function saveSerialAnnounceInterval() {
  const el = document.getElementById('settings-serial-announce-interval');
  const sec = clampIntervalInput(el, 0);
  fetch('/api/settings', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({serial_announce_interval_s: sec}),
  }).then(r => r.json()).then(d => {
    if (d.status === 'ok') {
      window._appSettings = d.settings || {};
      toast(sec ? ('Serial auto-announce: ' + sec + 's') : 'Serial auto-announce: off');
    } else toast('Save failed: ' + (d.error || ''));
  }).catch(() => toast('Save failed'));
}

function setupVisibilitySync() {
  const sync = () => syncUiState();
  document.addEventListener('visibilitychange', sync);
  window.addEventListener('focus', sync);
  window.addEventListener('blur', sync);
}

function syncUiState() {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  ws.send(JSON.stringify({type: 'visibility', hidden: document.hidden}));
  if (viewingPeer) {
    ws.send(JSON.stringify({
      type: 'viewing',
      peer: viewingPeer,
      via: viewingVia ? normalizeVia(viewingVia) : null,
      chat_gen: chatSwitchGen,
    }));
  }
}

function updateAndroidComposerMode() {
  const useAndroidComposer = appPlatform === 'android' || isMobileLayout();
  document.body.classList.toggle('android-composer', useAndroidComposer);
}

let composerBlurTimer = null;
function onComposerFocus() {
  if (!document.body.classList.contains('android-composer')) return;
  if (composerBlurTimer) { clearTimeout(composerBlurTimer); composerBlurTimer = null; }
  document.getElementById('attach-submenu')?.classList.add('open');
}

function onComposerBlur() {
  if (!document.body.classList.contains('android-composer')) return;
  composerBlurTimer = setTimeout(() => {
    const input = document.getElementById('msg-input');
    if (!input?.value.trim()) {
      document.getElementById('attach-submenu')?.classList.remove('open');
    }
  }, 180);
}

function onComposerInput(el) {
  autoResize(el);
  if (!document.body.classList.contains('android-composer')) return;
  const submenu = document.getElementById('attach-submenu');
  if (!submenu) return;
  if (el.value.trim()) submenu.classList.add('open');
  else if (document.activeElement !== el) submenu.classList.remove('open');
}

function setupGlobalHandlers() {
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') closeAllPanels();
  });
  document.addEventListener('click', e => {
    const picker = document.getElementById('emoji-picker');
    const emojiTriggers = '#emoji-btn, #emoji-btn-mobile';
    if (picker?.classList.contains('open') && !e.target.closest('#emoji-picker') && !e.target.closest(emojiTriggers)) {
      picker.classList.remove('open');
    }
  });
}

function closeAllPanels() {
  if (settingsShown) toggleSettings();
  if (connectShown) toggleConnectPanel();
  if (actionShown) toggleActionPanel();
  document.getElementById('settings-btn')?.classList.remove('active');
  document.getElementById('emoji-picker')?.classList.remove('open');
  if (document.getElementById('save-contact-dialog')?.classList.contains('open')) closeSaveContact();
  if (document.getElementById('folder-picker-dialog')?.classList.contains('open')) closeFolderPicker();
  closeImageViewer();
}

function setWsStatus(state, label) {
  const el = document.getElementById('ws-status');
  if (!el) return;
  el.innerHTML = `<span class="status-pill ${state}"><span id="ws-dot" class="${state}"></span><span id="ws-label">${label}</span></span>`;
}

function setLinkStatus(state, label) {
  const el = document.getElementById('link-status');
  if (!el) return;
  const title = state === 'connected'
    ? 'Encrypted RNS chat session is active'
    : 'No peer connected — select a contact and connect';
  el.innerHTML = `<span class="status-pill ${state}" title="${title}"><span id="link-dot" class="${state}"></span><span id="link-label">Link: ${label}</span></span>`;
}

function pollLinkStatus() {
  fetch('/api/network-status')
    .then(r => r.json())
    .then(d => {
      window._tcpHubOnline = !!d.tcp_hub_online;
      window._tcpClientOnline = !!d.tcp_client_online;
      window._hubGroupLinked = !!d.hub_group_linked;
      window._hubClientsLinked = d.hub_clients_linked || 0;
      if (d.hub_role) {
        window._hubRole = d.hub_role;
        updateHubUi();
      }
      if (viewingPeer === HUB_GROUP_PEER) updatePeerHeader();
      if (d.linked_peers && d.linked_peers.length) {
        syncLinkedPeers(d.linked_peers);
        updatePeerHeader();
      } else if (d.link_active && d.active_peer) {
        setLinkPeer(d.active_peer, true);
        updatePeerHeader();
      } else if (linkedPeers.size && (d.session_peer || d.active_peer)) {
        setLinkStatus('disconnected', 'Reconnecting…');
        updatePeerHeader();
      } else if (!linkedPeers.size) {
        setLinkStatus('disconnected', 'Inactive');
      }
    })
    .catch(() => {});
}

function toggleSidebar() {
  if (settingsShown) closeSettings();
  if (connectShown) {
    connectShown = false;
    document.getElementById('connect-panel')?.classList.remove('open');
  }
  if (actionShown) {
    actionShown = false;
    document.getElementById('action-panel')?.classList.remove('open');
  }
  setSidebarOpen(!sidebarOpen);
}

function filterContacts(query) {
  const q = (query || '').toLowerCase().trim();
  const filtered = q ? allContacts.filter(c => {
    const name = (c.name || c.hash || '').toLowerCase();
    const hashes = [c.hash, c.lan_hash, c.serial_hash].filter(Boolean).join(' ').toLowerCase();
    return name.includes(q) || hashes.includes(q);
  }) : allContacts;
  renderContacts(filtered);
}

function showWelcome() {
  viewingPeer = null;
  linkPeer = null;
  linkedPeers.clear();
  try { localStorage.removeItem(LS_VIEWING_PEER); } catch (_) {}
  document.getElementById('messages').innerHTML = '';
  document.getElementById('peer-name').textContent = 'No chat selected';
  document.getElementById('peer-status').textContent = 'Pick a contact or discovered peer';
  document.getElementById('placeholder').style.display = 'flex';
  document.getElementById('messages').style.display = 'none';
  document.getElementById('input-area').style.display = 'none';
  document.getElementById('disconnect-btn').style.display = 'none';
  setLinkStatus('disconnected', 'Inactive');
  renderContacts(allContacts);
}

const linkRttByKey = {};

function applyLinkRttToDiscovered(hash, rttMs, via) {
  if (!hash) return;
  const n = parseInt(rttMs, 10);
  const clear = !Number.isFinite(n) || n < 0;
  const transport = normalizeVia(via || viewingVia);
  const lk = linkKey(hash, transport);
  if (lk) {
    if (clear) delete linkRttByKey[lk];
    else linkRttByKey[lk] = n;
  }
  window._discoveredPeers = (window._discoveredPeers || []).map(p => {
    if (!peersMatch(p.hash, hash) && !peersMatch(p.identity_hash, hash)) return p;
    if (clear) {
      const next = {...p};
      delete next.rtt_ms;
      delete next.rtt_avg_ms;
      return next;
    }
    return {...p, rtt_ms: n, rtt_avg_ms: n};
  });
  renderDiscovered(window._discoveredPeers || []);
}

function rttForViewingPeer() {
  if (!viewingPeer) return '';
  if (!isPeerLinked(viewingPeer, viewingVia)) return '';
  const via = normalizeVia(viewingVia) === 'serial' ? 'serial' : 'lan';
  const lk = linkKey(viewingPeer, via);
  if (lk && linkRttByKey[lk] != null) return formatRtt(linkRttByKey[lk]);
  const contact = allContacts.find(c =>
    peerKey(c.hash) === peerKey(viewingPeer)
    || peerKey(c.lan_hash) === peerKey(viewingPeer)
    || peerKey(c.serial_hash) === peerKey(viewingPeer)
    || peersMatch(c.hash, viewingPeer)
    || peersMatch(c.lan_hash, viewingPeer)
    || peersMatch(c.serial_hash, viewingPeer)
  );
  if (contact) {
    const fromContact = rttForContactTransport(contact, via);
    if (fromContact) return fromContact;
  }
  const p = discoveredPeerForHash(viewingPeer)
    || (window._discoveredPeers || []).find(x => peersMatch(x.hash, viewingPeer));
  return formatRtt(p?.rtt_avg_ms || p?.rtt_ms);
}

function peerInterfaceLabel(via) {
  const v = normalizeVia(via);
  if (v === 'serial') return 'USB Serial';
  if (v === 'lan') return 'LAN';
  return 'Unknown';
}

function updatePeerHeader() {
  if (!viewingPeer) return;
  const isHubChat = viewingPeer === HUB_GROUP_PEER;
  const name = contactNameFor(viewingPeer) || truncateHash(viewingPeer);
  const linked = isPeerLinked(viewingPeer, viewingVia);
  const rtt = linked && !isHubChat ? rttForViewingPeer() : '';
  const rttBadge = rtt ? ` · <span class="peer-rtt">${escapeHtml(rtt)}</span>` : '';
  document.getElementById('peer-name').textContent = name;
  linkPeer = linked && !isHubChat ? linkKey(viewingPeer, viewingVia) : null;
  const fullHash = isHubChat ? 'Group relay' : (peerKey(viewingPeer) || '');
  const iface = isHubChat ? 'Hub' : peerInterfaceLabel(viewingVia);
  const statusEl = document.getElementById('peer-status');
  if (statusEl) {
    let conn = isHubChat ? hubGroupStatusLabel() : (linked ? 'Connected' : 'Not connected');
    statusEl.innerHTML = `${escapeHtml(fullHash)} · ${escapeHtml(iface)} · ${conn}${rttBadge}`;
  }
  document.getElementById('disconnect-btn').style.display = linked ? 'block' : 'none';
  setLinkStatus(linked ? 'connected' : 'disconnected', linked ? 'Active' : 'Inactive');
  document.getElementById('input-area').style.display = 'block';
}

function contactNameFor(hash) {
  if (hash === HUB_GROUP_PEER) return 'Group Chat';
  const key = peerKey(hash);
  const c = allContacts.find(x =>
    peerKey(x.hash) === key
    || peerKey(x.lan_hash) === key
    || peerKey(x.serial_hash) === key
    || peersMatch(x.hash, hash)
    || peersMatch(x.lan_hash, hash)
    || peersMatch(x.serial_hash, hash)
  );
  if (c) return contactDisplayName(c);
  const d = (window._discoveredPeers || []).find(x =>
    peerKey(x.hash) === key || peersMatch(x.hash, hash)
  );
  return d?.name || '';
}

function currentHubRole() {
  return document.getElementById('settings-hub-role')?.value || window._hubRole || 'off';
}

function updateHubUi() {
  // Sidebar Hub Group entry reflects the *saved* role — group chat only works
  // once the setting is persisted and the runtime applies it.
  const savedRole = window._hubRole || 'off';
  const section = document.getElementById('hub-group-section');
  const hubEntry = document.getElementById('hub-group-entry');
  if (section) section.style.display = savedRole !== 'off' ? 'block' : 'none';
  if (hubEntry && viewingPeer === HUB_GROUP_PEER) hubEntry.classList.add('active');
  else if (hubEntry) hubEntry.classList.remove('active');
  // The client IP / port fields and server hint follow the *dropdown
  // selection*, not the saved role, so they stay visible while the user is
  // editing — a background poll of /api/network-status can refresh the saved
  // role before the new value is saved, and must not hide the input mid-edit.
  const selectedRole = currentHubRole();
  const clientFields = document.getElementById('hub-client-fields');
  if (clientFields) clientFields.style.display = selectedRole === 'client' ? 'block' : 'none';
  const serverHint = document.getElementById('hub-server-hint');
  if (serverHint) serverHint.style.display = selectedRole === 'server' ? 'block' : 'none';
  updateLanTransportHubHint();
}

function onHubRoleChange() {
  updateHubUi();
}

function updateLanTransportHubHint(policy) {
  const sel = document.getElementById('settings-lan-transport');
  const hint = document.getElementById('settings-lan-transport-hint');
  const tcpOpt = sel?.querySelector('option[value="tcp_lan"]');
  const p = policy || window._lanTransportHubTcp || {};
  const role = currentHubRole();
  const blocked = role === 'server' || p.allowed === false;
  const msg = p.warning || (
    role === 'server'
      ? 'TCP LAN is unavailable while hub server is on — port 4242 is reserved for group relay. LAN peers use UDP. Set hub to Off to switch.'
      : role === 'client'
        ? 'Hub client: TCP LAN is for local 1:1 peers; group chat stays on the hub TCP link. Restart after changing.'
        : 'Restart chatx5 after changing transport.'
  );
  if (hint) {
    hint.textContent = msg;
    hint.style.color = blocked ? 'var(--warn,#e8a838)' : '';
  }
  if (tcpOpt) tcpOpt.disabled = blocked;
  if (blocked && sel?.value === 'tcp_lan') {
    sel.value = 'udp_lan';
  }
}

function saveHubSettings() {
  const hub_role = document.getElementById('settings-hub-role')?.value || 'off';
  const hub_host = document.getElementById('settings-hub-host')?.value.trim() || '';
  const hub_port = parseInt(document.getElementById('settings-hub-port')?.value, 10) || 4242;
  return fetch('/api/settings', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({hub_role, hub_host, hub_port}),
  }).then(r => r.json()).then(d => {
    if (d.status === 'ok') {
      window._hubRole = d.settings?.hub_role || hub_role;
      if (d.settings?.lan_transport_hub_tcp) window._lanTransportHubTcp = d.settings.lan_transport_hub_tcp;
      updateHubUi();
      toast(appPlatform === 'android'
        ? 'Hub settings saved — RNS config updated'
        : 'Hub settings saved — restart chatx5 if TCP hub does not connect');
      loadRnsInterfaces();
    }
    return d;
  });
}

function loadHistoryForPeer(peerHash) {
  const peer = peerHash.replace(/:/g, '');
  const msgsEl = document.getElementById('messages');
  msgsEl.innerHTML = '';
  fetch('/api/history?limit=500&peer=' + encodeURIComponent(peer))
    .then(r => r.json())
    .then(msgs => {
      msgs.forEach(m => addMessage(m, { scroll: false }));
      requestAnimationFrame(() => {
        msgsEl.scrollTop = msgsEl.scrollHeight;
      });
    })
    .catch(() => {});
}

function updateSerialAnnounceVisibility(active) {
  window._serialActive = !!active;
  ['announce-serial-btn', 'status-announce-serial-btn'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.style.display = active ? '' : 'none';
  });
  const showSerialIdentity = !!(mySerialHash || active);
  ['settings-regen-serial-btn', 'identity-modal-regen-serial', 'identity-modal-copy-serial'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.style.display = showSerialIdentity ? '' : 'none';
  });
  const serialSection = document.getElementById('settings-serial-hash-section');
  if (serialSection) serialSection.style.display = mySerialHash ? 'block' : 'none';
  const serialBlock = document.getElementById('identity-modal-serial-block');
  if (serialBlock) serialBlock.style.display = showSerialIdentity ? 'block' : 'none';
}

function updateIdentityDisplays() {
  const lanLabel = formatHashDisplay(myHash);
  const lanFull = stripHashColons(myHash);
  const serialFull = stripHashColons(mySerialHash);
  const box = document.getElementById('identity-box');
  if (box) {
    box.textContent = lanLabel;
    let title = lanFull + ' — click to manage identity';
    if (serialFull) title = lanFull + ' · serial ' + formatHashDisplay(mySerialHash) + ' — click to manage identity';
    box.title = title;
  }
  const shLan = document.getElementById('settings-hash-lan');
  if (shLan) shLan.value = lanFull;
  const shSerial = document.getElementById('settings-hash-serial');
  if (shSerial) shSerial.value = serialFull || '—';
  updateSerialAnnounceVisibility(window._serialActive);
}

function fetchIdentity() {
  loadBrandLogo();
  fetch('/api/identity')
    .then(r => r.json())
    .then(d => {
      if (d.name) {
        window._appSettings = Object.assign({}, window._appSettings || {}, {name: d.name});
        const nameEl = document.getElementById('settings-name');
        if (nameEl && !nameEl.value.trim()) nameEl.value = d.name;
      }
      window._identityLan = d.lan || {};
      window._identitySerial = d.serial || null;
      myHash = d.lan?.connect_hash || d.connect_hash || d.hash;
      mySerialHash = d.serial?.connect_hash || '';
      updateSerialAnnounceVisibility(!!d.serial_active || !!d.serial_configured || !!d.serial_in_rns);
      updateIdentityDisplays();
      updateAndroidDebugLogHint(d.debug_log_path);
      renderContacts(d.contacts);
      renderDiscovered(d.discovered);
      if (d.app_version) {
        const ver = document.getElementById('settings-version');
        if (ver) ver.textContent = 'v' + d.app_version;
        const hv = document.getElementById('header-version');
        if (hv) hv.textContent = 'v' + d.app_version;
      }
      appPlatform = d.platform || (window.chatx5Android?.isAndroid?.() ? 'android' : 'desktop');
      updatePlatformHints();
      updateAndroidComposerMode();
      updateAndroidShellLayout();
      if (d.platform === 'android') {
        const hint = document.getElementById('settings-received-hint');
        if (hint) hint.textContent = 'Tap Browse to open the Android folder picker, then Save to apply.';
      }
      if (d.linked_peers && d.linked_peers.length) {
        syncLinkedPeers(d.linked_peers);
      } else if (d.connected) {
        setLinkPeer(d.connected, true, viaForLinkedPeer(d.connected));
      } else if (!viewingPeer) {
        linkedPeers.clear();
        linkPeer = null;
      }
      updatePeerHeader();
      const saved = localStorage.getItem(LS_VIEWING_PEER);
      const savedVia = localStorage.getItem(LS_VIEWING_VIA);
      const savedIsContact = saved && isSavedContactHash(saved);
      if (savedIsContact && !viewingPeer) {
        openChat(saved, false, savedVia ? {via: savedVia} : undefined);
      }
      else if (saved && !savedIsContact) {
        try { localStorage.removeItem(LS_VIEWING_PEER); } catch (_) {}
      }
    });
  pollQueue();
}
