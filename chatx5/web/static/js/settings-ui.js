function toggleSettings() {
  if (settingsShown) closeSettings();
  else openSettings('profile');
}

function trackAppVersion(settings) {
  const version = settings?.app_version || settings?.version || '';
  if (!version) return;
  const verEl = document.getElementById('settings-app-version');
  if (verEl) verEl.textContent = 'v' + version;
  const dockVer = document.getElementById('app-version-dock');
  if (dockVer) dockVer.textContent = 'v' + version;
  const seen = settings?.last_release_notes_seen || '';
  if (seen !== version) {
    fetch('/api/settings', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({last_release_notes_seen: version}),
    }).then(r => r.json()).then(d => {
      if (d.settings) window._appSettings = d.settings;
    }).catch(() => {});
  }
}

function renderReleaseNotesPage(data) {
  const container = document.getElementById('release-notes-all');
  const currentEl = document.getElementById('release-notes-current-version');
  if (!container) return;
  const current = data?.current_version || '';
  if (currentEl) currentEl.textContent = current ? 'v' + current : '—';
  const releases = data?.releases || [];
  container.innerHTML = releases.map(rel => {
    const isCurrent = rel.version === current;
    const items = (rel.notes || []).map(n => `<li>${escapeHtml(n)}</li>`).join('');
    return `<div class="release-notes-version${isCurrent ? ' current' : ''}">
      <h3>v${escapeHtml(rel.version)}${isCurrent ? ' (installed)' : ''}</h3>
      <ul>${items}</ul>
    </div>`;
  }).join('');
}

function openReleaseNotesPage() {
  const overlay = document.getElementById('release-notes-overlay');
  if (!overlay) return;
  overlay.style.display = 'flex';
  const container = document.getElementById('release-notes-all');
  if (container) container.innerHTML = '<div style="color:var(--text3);font-size:13px">Loading…</div>';
  fetch('/api/release-notes')
    .then(r => r.json())
    .then(renderReleaseNotesPage)
    .catch(() => {
      if (container) container.innerHTML = '<div style="color:var(--danger)">Failed to load release notes</div>';
    });
}

function closeReleaseNotesPage() {
  const overlay = document.getElementById('release-notes-overlay');
  if (overlay) overlay.style.display = 'none';
}

function toggleConnectPanel() {
  connectShown = !connectShown;
  if (settingsShown) closeSettings();
  if (actionShown) { actionShown = false; document.getElementById('action-panel').classList.remove('open'); }
  const panel = document.getElementById('connect-panel');
  if (connectShown) {
    panel.classList.add('open');
    document.getElementById('connect-hash').value = '';
    document.getElementById('contact-name').value = '';
    document.getElementById('connect-hash').focus();
  } else {
    panel.classList.remove('open');
  }
}

function doConnectFromPanel() {
  const hash = document.getElementById('connect-hash').value.trim();
  const name = document.getElementById('contact-name').value.trim();
  if (!hash) return;
  if (name) {
    fetch('/api/contacts', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({hash, name})
    });
  }
  const meta = peerMetaForHash(hash);
  connectTo(hash, meta?.ip, meta?.port);
  toggleConnectPanel();
}

function saveRetentionOnly() {
  const retention = document.getElementById('settings-retention').value;
  fetch('/api/settings', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({history_retention: retention})
  }).then(r => r.json()).then(d => {
    if (d.status === 'ok') toast('Retention saved');
  });
}

function saveSettings() {
  const name = document.getElementById('settings-name').value.trim();
  const retention = document.getElementById('settings-retention').value;
  const received_dir = document.getElementById('settings-received-dir').value.trim();
  const hub_role = document.getElementById('settings-hub-role')?.value || 'off';
  const hub_host = document.getElementById('settings-hub-host')?.value.trim() || '';
  const hub_port = parseInt(document.getElementById('settings-hub-port')?.value, 10) || 4242;
  const hub_listen_interfaces = hub_role === 'server' ? hubListenSelection() : undefined;
  const lan_probe_interval_s = clampIntervalInput(document.getElementById('settings-lan-probe-interval'), 30);
  let serial_probe_interval_s = clampIntervalInput(document.getElementById('settings-serial-probe-interval'), 30);
  if (serial_probe_interval_s > 0 && serial_probe_interval_s < 3) serial_probe_interval_s = 3;
  let serial_quality_interval_s = clampIntervalInput(document.getElementById('settings-serial-quality-interval'), 5);
  if (serial_quality_interval_s > 0 && serial_quality_interval_s < 3) serial_quality_interval_s = 3;
  const lan_announce_interval_s = clampIntervalInput(document.getElementById('settings-lan-announce-interval'), 0);
  const serial_announce_interval_s = clampIntervalInput(document.getElementById('settings-serial-announce-interval'), 0);
  let max_peer_links = parseInt(document.getElementById('settings-max-peer-links')?.value, 10);
  if (!Number.isFinite(max_peer_links) || max_peer_links < 0) max_peer_links = 0;
  if (max_peer_links > 64) max_peer_links = 64;
  fetch('/api/settings', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      name, history_retention: retention, received_dir, hub_role, hub_host, hub_port,
      hub_listen_interfaces,
      lan_probe_interval_s, serial_probe_interval_s,
      brand_title: (document.getElementById('settings-brand-title')?.value || '').trim().slice(0, 18),
      lan_announce_interval_s, serial_announce_interval_s, max_peer_links,
      wan_secure_mode: !!document.getElementById('settings-wan-secure-mode')?.checked,
      serial_quality_interval_s,
    })
  }).then(async r => {
    const d = await r.json();
    if (r.ok && d.status === 'ok') {
      window._appSettings = d.settings || {};
      applySettingsToForm(window._appSettings);
      window._hubRole = d.settings?.hub_role || hub_role;
      updateHubUi();
      toast('Settings saved');
    } else {
      toast('Failed to save: ' + (d.error || 'unknown error'));
    }
  }).catch(() => toast('Failed to save settings'));
}

function setLinkPeer(hash, linked, via) {
  const k = linkKey(hash, via || viewingVia);
  if (!k) return;
  if (linked === false) {
    linkedPeers.delete(k);
  } else {
    linkedPeers.add(k);
  }
  if (viewingPeer && isPeerLinked(viewingPeer, viewingVia)) {
    linkPeer = linkKey(viewingPeer, viewingVia);
  } else {
    linkPeer = null;
  }
  renderContacts(allContacts);
  renderDiscovered(window._discoveredPeers || []);
}

function closeChatView() {
  if (!viewingPeer) return;
  viewingPeer = null;
  viewingVia = null;
  try {
    localStorage.removeItem(LS_VIEWING_PEER);
    localStorage.removeItem(LS_VIEWING_VIA);
  } catch (_) {}
  document.getElementById('messages').innerHTML = '';
  document.getElementById('placeholder').style.display = 'flex';
  document.getElementById('messages').style.display = 'none';
  document.getElementById('peer-name').textContent = 'No chat selected';
  document.getElementById('peer-status').textContent = 'Pick a contact or discovered peer';
  document.getElementById('input-area').style.display = 'none';
  document.getElementById('disconnect-btn').style.display = 'none';
  if (isAndroidShell()) {
    document.body.classList.remove('android-chat-open');
    setSidebarOpen(true);
  }
  updateAndroidShellLayout();
  syncUiState();
  renderContacts(allContacts);
  renderDiscovered(window._discoveredPeers || []);
}

function androidHandleBack() {
  if (document.getElementById('save-contact-dialog')?.classList.contains('open')) {
    closeSaveContact();
    return true;
  }
  if (document.body.classList.contains('sidebar-open') && !isAndroidShell()) {
    closeSidebar();
    return true;
  }
  if (actionShown) {
    toggleActionPanel();
    return true;
  }
  if (settingsShown) {
    toggleSettings();
    return true;
  }
  if (connectShown) {
    toggleConnectPanel();
    return true;
  }
  if (viewingPeer) {
    closeChatView();
    return true;
  }
  return false;
}
