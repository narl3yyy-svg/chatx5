function netClass(ok) {
  return ok ? 'net-ok' : 'net-bad';
}

const SERIAL_BAUD_RATES = [1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600];
const SERIAL_DEFAULT_BAUD = 57600;

function updatePlatformHints() {
  const os = detectClientPlatform();
  const serialHint = document.getElementById('serial-setup-hint');
  if (serialHint) {
    if (os === 'android') {
      serialHint.textContent = 'Use a USB OTG cable, plug in your serial adapter, tap Refresh devices, grant USB access, pick baud (default 57600), click Apply, then restart the app.';
    } else if (os === 'windows') {
      serialHint.textContent = 'Plug in USB serial, pick device + baud (default 57600), click Apply, then restart chatx5 (run.bat web --share).';
    } else {
      serialHint.textContent = 'Plug in USB serial, pick device + baud (default 57600), click Apply. Start with ./run.sh web --share for dialout permissions. Restart chatx5 after applying.';
    }
  }
}

function requestUsbPermissionForIface(ifaceId) {
  const port = document.querySelector(`select[data-serial-port="${ifaceId}"]`)?.value;
  requestUsbPermission(port);
}

function requestUsbPermission(device) {
  if (!device) return;
  if (window.chatx5Android?.requestUsbPermission) {
    window.chatx5Android.requestUsbPermission(device);
    toast('USB permission requested');
    setTimeout(refreshSerialPorts, 1200);
    return;
  }
  fetch('/api/serial-ports/permission', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({device}),
  }).then(r => r.json()).then(d => {
    if (d.granted) toast('USB access granted');
    else if (d.requested) toast('USB permission requested');
    else if (d.error) toast('USB permission failed: ' + d.error);
    setTimeout(refreshSerialPorts, 1200);
  }).catch(() => toast('USB permission request failed'));
}

function serialStatusLabel(status) {
  if (status === 'ok') return { text: 'Ready', cls: 'ok' };
  if (status === 'permission_denied') return { text: 'No permission', cls: 'denied' };
  if (status === 'missing') return { text: 'Not connected', cls: 'missing' };
  return { text: 'Unknown', cls: 'missing' };
}

function renderSerialDevicesPanel(ports, portData) {
  const panel = document.getElementById('serial-devices-panel');
  if (!panel) return;
  const items = ports || [];
  if (!items.length) {
    panel.style.display = 'block';
    panel.innerHTML = '<h5>Connected serial devices</h5><div style="color:var(--text3)">No USB serial devices detected. Plug in adapter and click Refresh below.</div>';
    return;
  }
  const ready = items.filter(p => p.status === 'ok').length;
  let html = `<h5>Connected serial devices (${ready}/${items.length} ready)</h5>`;
  items.forEach(p => {
    const st = serialStatusLabel(p.status);
    const meta = [p.description, p.hwid].filter(Boolean).join(' · ') || 'USB serial';
    html += `<div class="serial-device-row">
      <div><div class="dev-name">${escapeHtml(p.device)}</div><div class="dev-meta">${escapeHtml(meta)}</div></div>
      <div class="dev-status ${st.cls}">${escapeHtml(st.text)}</div>
    </div>`;
  });
  if (portData && portData.has_group_access === false) {
    html += '<div style="margin-top:6px;color:var(--warn,#e8a838);font-size:10px">dialout group not active — use ./run.sh web --share</div>';
  }
  panel.style.display = 'block';
  panel.innerHTML = html;
}

function serialPortSelectOptions(ports, selected) {
  const devices = (ports || []).map(p => p.device);
  let html = '<option value="">— select device —</option>';
  (ports || []).forEach(p => {
    const desc = p.description ? ` — ${p.description}` : '';
    const st = p.status === 'ok' ? '' : (p.status === 'permission_denied' ? ' [no permission]' : ' [offline]');
    const sel = p.device === selected ? ' selected' : '';
    html += `<option value="${escapeHtml(p.device)}"${sel}>${escapeHtml(p.device + desc + st)}</option>`;
  });
  if (selected && !devices.includes(selected)) {
    html += `<option value="${escapeHtml(selected)}" selected>${escapeHtml(selected)} (saved)</option>`;
  }
  return html;
}

function baudOptions(rates, selected) {
  const speed = parseInt(selected, 10) || SERIAL_DEFAULT_BAUD;
  const list = rates && rates.length ? rates : SERIAL_BAUD_RATES;
  let html = list.map(r =>
    `<option value="${r}"${r === speed ? ' selected' : ''}>${r}${r === SERIAL_DEFAULT_BAUD ? ' (default)' : ''}</option>`
  ).join('');
  if (!list.includes(speed)) {
    html = `<option value="${speed}" selected>${speed}</option>` + html;
  }
  return html;
}

function renderRnsInterfaceRow(iface, serialPorts, baudRates) {
  const id = escapeHtml(iface.id || '');
  const label = escapeHtml(iface.name || iface.type || iface.preset || 'iface');
  const isSerial = iface.preset === 'serial' || iface.type === 'SerialInterface';
  const isTcpClient = iface.preset === 'tcp_client' || iface.type === 'TCPClientInterface';
  const isTcpLan = iface.preset === 'tcp_lan';
  const isTcpServer = iface.preset === 'tcp_server' || (iface.type === 'TCPServerInterface' && !isTcpLan);
  const isTcpListen = isTcpLan || isTcpServer;
  let metaBits = iface.type || '';
  if (isSerial) {
    if (iface.serial_active) metaBits += ' · active';
    else if (iface.port_status === 'permission_denied') metaBits += ' · saved, no permission yet';
    else if (iface.port_status === 'missing') metaBits += ' · device unplugged';
    else if (iface.port) metaBits += ' · ready after restart';
    else metaBits += ' · pick a device below';
  } else if (iface.enabled === false) {
    metaBits += ' · disabled';
  }
  const meta = escapeHtml(metaBits);
  const active = iface.enabled !== false;
  const activeToggle = `<label class="iface-active-toggle"><input type="checkbox"${active ? ' checked' : ''} onchange="toggleRnsInterfaceEnabled('${id}', this.checked)"> Active</label>`;
  let body = `<div class="iface-head"><span class="net-label">${label}<div style="font-size:10px;color:var(--text3)">${meta}</div></span><div class="iface-head-actions">${activeToggle}<button class="btn-block danger" style="width:auto;padding:4px 8px;font-size:11px" onclick="deleteRnsInterface('${id}')">Delete</button></div></div>`;
  if (isSerial) {
    body += `<div class="rns-iface-serial-fields">
      <div><label>Device</label>
        <select data-serial-port="${id}">${serialPortSelectOptions(serialPorts, iface.port)}</select>
      </div>
      <div><label>Baud rate</label>
        <select data-serial-baud="${id}">${baudOptions(baudRates, iface.speed || SERIAL_DEFAULT_BAUD)}</select>
      </div>
    </div>
    <div class="rns-iface-serial-actions">
      <button type="button" class="btn-block" onclick="saveSerialInterface('${id}')">Apply</button>
      <button type="button" class="rns-serial-refresh" onclick="refreshSerialPorts()">↻ Refresh devices</button>
      <button type="button" class="rns-serial-refresh" onclick="requestUsbPermissionForIface('${id}')">Grant USB access</button>
    </div>`;
  } else if (isTcpClient) {
    body += `<div class="rns-iface-serial-fields">
      <div><label>Target host</label>
        <input data-tcp-host="${id}" value="${escapeHtml(iface.target_host || '127.0.0.1')}" spellcheck="false">
      </div>
      <div><label>Target port</label>
        <input data-tcp-port="${id}" type="number" min="1" max="65535" value="${iface.target_port || 4242}">
      </div>
    </div>
    <div class="rns-iface-serial-actions">
      <button type="button" class="btn-block" onclick="saveTcpInterface('${id}')">Apply</button>
    </div>`;
  } else if (isTcpListen) {
    const lanHint = isTcpLan ? ' · peers dial this port on connect' : '';
    body += `<div class="rns-iface-serial-fields">
      <div><label>Listen IP</label>
        <input data-tcp-listen-ip="${id}" value="${escapeHtml(iface.listen_ip || '0.0.0.0')}" spellcheck="false">
      </div>
      <div><label>Listen port</label>
        <input data-tcp-listen-port="${id}" type="number" min="1" max="65535" value="${iface.listen_port || 4242}">
      </div>
    </div>
    <div class="rns-iface-serial-actions">
      <button type="button" class="btn-block" onclick="saveTcpInterface('${id}')">Apply</button>
      ${isTcpLan ? `<span style="font-size:10px;color:var(--text3);align-self:center">Beacon discovery + TCP transport${lanHint}</span>` : ''}
    </div>`;
  }
  return `<div class="rns-iface-card">${body}</div>`;
}

function toggleRnsInterfaceEnabled(id, enabled) {
  fetch('/api/rns-interfaces/update', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({id, enabled: !!enabled}),
  }).then(r => r.json()).then(d => {
    if (d.status === 'ok') {
      toast(enabled ? 'Interface enabled — restart chatx5 to apply' : 'Interface disabled — restart chatx5 to apply');
      loadRnsInterfaces();
      refreshNetworkStatus();
    } else {
      toast('Update failed: ' + (d.error || ''));
      loadRnsInterfaces();
    }
  }).catch(() => {
    toast('Update failed');
    loadRnsInterfaces();
  });
}

function saveTcpInterface(id) {
  const hostEl = document.querySelector(`input[data-tcp-host="${id}"]`);
  const portEl = document.querySelector(`input[data-tcp-port="${id}"]`);
  const listenIpEl = document.querySelector(`input[data-tcp-listen-ip="${id}"]`);
  const listenPortEl = document.querySelector(`input[data-tcp-listen-port="${id}"]`);
  const body = {id};
  if (hostEl) body.target_host = hostEl.value.trim();
  if (portEl) body.target_port = parseInt(portEl.value, 10) || 4242;
  if (listenIpEl) body.listen_ip = listenIpEl.value.trim();
  if (listenPortEl) body.listen_port = parseInt(listenPortEl.value, 10) || 4242;
  fetch('/api/rns-interfaces/update', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body),
  }).then(r => r.json()).then(d => {
    if (d.status === 'ok') {
      toast('TCP settings saved — restart chatx5 to apply');
      loadRnsInterfaces();
      refreshNetworkStatus();
    } else toast('Save failed: ' + (d.error || ''));
  }).catch(() => toast('Save failed'));
}

function loadRnsInterfaces() {
  const list = document.getElementById('rns-interfaces-list');
  if (!list) return;
  list.textContent = 'Loading...';
  Promise.all([
    fetch('/api/rns-interfaces').then(r => r.json()),
    fetch('/api/serial-ports').then(r => r.json()).catch(() => ({ports: [], baud_rates: SERIAL_BAUD_RATES, default_baud: SERIAL_DEFAULT_BAUD})),
  ]).then(([ifaceData, portData]) => {
    const items = ifaceData.interfaces || [];
    window._serialPorts = portData.ports || [];
    window._serialPortData = portData;
    window._serialPermissionHint = portData.permission_hint || '';
    if (portData.platform) appPlatform = portData.platform;
    updatePlatformHints();
    const baudRates = portData.baud_rates || SERIAL_BAUD_RATES;
    const hasSerial = items.some(i => i.preset === 'serial' || i.type === 'SerialInterface');
    const serialAddRow = document.getElementById('settings-serial-add-row');
    if (serialAddRow) serialAddRow.style.display = hasSerial ? 'none' : 'block';
    if (hasSerial) renderSerialDevicesPanel(window._serialPorts, portData);
    else {
      const panel = document.getElementById('serial-devices-panel');
      if (panel) { panel.style.display = 'none'; panel.innerHTML = ''; }
    }
    const hintEl = document.getElementById('serial-permission-hint');
    const needsPerm = (portData.ports || []).some(p => p.status === 'permission_denied')
      || items.some(i => i.port_status === 'permission_denied');
    if (hintEl) {
      if (needsPerm && window._serialPermissionHint) {
        let text = window._serialPermissionHint;
        if (portData.process_needs_restart) {
          text += ' You are in dialout but this server was started too early — stop and restart chatx5.';
        }
        hintEl.style.display = 'block';
        hintEl.textContent = text;
      } else {
        hintEl.style.display = 'none';
        hintEl.textContent = '';
      }
    }
    const hubRole = currentHubRole();
    const displayItems = items.filter(iface => {
      const preset = iface.preset;
      const type = iface.type;
      if (preset === 'udp_lan' || type === 'UDPInterface') return false;
      if (preset === 'tcp_lan') return false;
      if (preset === 'tcp_server' || (type === 'TCPServerInterface' && preset !== 'tcp_lan')) {
        if (hubRole === 'server') return false;
      }
      if (preset === 'tcp_client' || type === 'TCPClientInterface') {
        if (hubRole === 'client') return false;
      }
      return preset === 'serial' || type === 'SerialInterface';
    });
    list.innerHTML = displayItems.length
      ? displayItems.map(iface => renderRnsInterfaceRow(iface, window._serialPorts, baudRates)).join('')
      : '';
    updateLanTransportSelect(items);
    const serialActive = items.some(i =>
      (i.preset === 'serial' || i.type === 'SerialInterface') &&
      (i.serial_active || (i.enabled !== false && !i.user_disabled && (i.port || '').trim())));
    updateSerialAnnounceVisibility(serialActive || !!window._serialActive);
  }).catch(() => { list.textContent = 'Failed to load interfaces.'; });
}

function refreshSerialPorts() {
  fetch('/api/serial-ports')
    .then(r => r.json())
    .then(d => {
      window._serialPorts = d.ports || [];
      window._serialPortData = d;
      renderSerialDevicesPanel(window._serialPorts, d);
      document.querySelectorAll('select[data-serial-port]').forEach(sel => {
        const current = sel.value;
        sel.innerHTML = serialPortSelectOptions(window._serialPorts, current);
      });
      const ready = d.ready_count ?? window._serialPorts.filter(p => p.status === 'ok').length;
      const total = d.count ?? window._serialPorts.length;
      toast(total ? `Found ${total} device(s), ${ready} ready` : 'No USB serial devices detected');
    })
    .catch(() => toast('Failed to refresh serial devices'));
}

function saveSerialInterface(id) {
  const portEl = document.querySelector(`select[data-serial-port="${id}"]`);
  const baudEl = document.querySelector(`select[data-serial-baud="${id}"]`);
  if (!portEl || !baudEl) return;
  const port = portEl.value;
  if (!port) {
    toast('Select a serial port first');
    return;
  }
  fetch('/api/rns-interfaces/update', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({id, port, speed: parseInt(baudEl.value, 10)}),
  }).then(r => r.json()).then(d => {
    if (d.status === 'ok') {
      loadRnsInterfaces();
      const saved = (d.interfaces || []).find(i => i.id === id);
      if (saved && saved.port_status === 'permission_denied') {
        requestUsbPermission(port);
        toast('Saved ' + port + ' — grant USB access, then Apply again');
      } else if (d.serial_hot_added || (saved && saved.serial_active)) {
        toast('Serial active in RNS — no restart needed');
      } else if (saved && saved.port_status === 'ok') {
        toast('Serial port ready — tap Announce Serial to discover peers');
      } else {
        toast(appPlatform === 'android' ? 'Serial settings saved — pick port & grant USB access' : 'Serial settings saved — connect USB device');
      }
      refreshNetworkStatus();
    } else {
      toast('Save failed: ' + (d.error || ''));
    }
  }).catch(() => toast('Save failed'));
}

function currentLanTransportPreset(interfaces) {
  const items = interfaces || [];
  if (items.some(i => i.preset === 'tcp_lan' && i.enabled !== false)) return 'tcp_lan';
  if (items.some(i => (i.preset === 'udp_lan' || i.type === 'UDPInterface') && i.enabled !== false)) return 'udp_lan';
  return 'udp_lan';
}

function updateLanTransportSelect(interfaces) {
  const sel = document.getElementById('settings-lan-transport');
  if (!sel) return;
  sel.value = currentLanTransportPreset(interfaces);
  updateLanTransportHubHint();
  syncTransportCards();
}

function applyLanTransport() {
  const sel = document.getElementById('settings-lan-transport');
  const lan_transport = sel?.value || 'udp_lan';
  const prev = window._lastLanTransport || currentLanTransportPreset(window._rnsInterfaces || []);
  const hubRole = currentHubRole();
  if (lan_transport === 'tcp_lan' && (hubRole === 'server' || window._lanTransportHubTcp?.allowed === false)) {
    if (sel) sel.value = prev;
    const msg = (hubRole === 'server'
      ? 'Hub server already uses TCP port 4242 for group-chat relay. LAN peers still connect via UDP discovery and links. Set hub mode to Off to use TCP LAN for peer traffic.'
      : window._lanTransportHubTcp?.warning)
      || 'TCP LAN is unavailable while hub server is on — port 4242 is reserved for group relay.';
    toast(msg);
    updateLanTransportHubHint();
    return;
  }
  fetch('/api/settings', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({lan_transport}),
  }).then(r => r.json()).then(d => {
    if (d.status === 'ok') {
      window._lastLanTransport = lan_transport;
      if (d.settings?.lan_transport_hub_tcp) window._lanTransportHubTcp = d.settings.lan_transport_hub_tcp;
      const extra = (lan_transport === 'tcp_lan' && currentHubRole() === 'client')
        ? ' Group chat still uses the hub link.'
        : '';
      toast('LAN transport set to ' + (lan_transport === 'tcp_lan' ? 'TCP LAN' : 'UDP LAN') + ' — restart chatx5 to apply.' + extra);
      loadRnsInterfaces();
      refreshNetworkStatus();
    } else {
      if (sel) sel.value = prev;
      toast(d.error || 'Failed to save LAN transport');
      updateLanTransportHubHint();
    }
  }).catch(() => toast('Failed to save LAN transport'));
}

function addRnsInterface(preset) {
  fetch('/api/rns-interfaces/add', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({preset})
  }).then(r => r.json()).then(d => {
    if (d.status === 'ok') {
      if (preset === 'serial') {
        toast('Serial added — pick device, default baud 57600, click Apply');
      } else {
        toast(d.message || 'Interface added');
      }
      loadRnsInterfaces();
      refreshNetworkStatus();
    } else toast('Add failed: ' + (d.error || ''));
  }).catch(() => toast('Add failed'));
}

function deleteRnsInterface(id) {
  if (!confirm('Delete this interface preset?')) return;
  fetch('/api/rns-interfaces/delete', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({id})
  }).then(r => r.json()).then(d => {
    if (d.status === 'ok') {
      toast(d.message || 'Interface removed');
      loadRnsInterfaces();
      refreshNetworkStatus();
    } else toast('Delete failed: ' + (d.error || ''));
  }).catch(() => toast('Delete failed'));
}

function showSettingsSection(section) {
  const name = section || 'profile';
  window._settingsSection = name;
  document.querySelectorAll('.settings-nav-item').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.section === name);
  });
  document.querySelectorAll('.settings-pane').forEach(pane => {
    pane.classList.toggle('active', pane.id === 'settings-section-' + name);
  });
  if (name === 'status') {
    refreshNetworkStatus();
    startNetworkAutoRefresh();
  } else {
    stopNetworkAutoRefresh();
  }
}

function openSettings(section) {
  if (connectShown) {
    connectShown = false;
    document.getElementById('connect-panel')?.classList.remove('open');
  }
  if (actionShown) {
    actionShown = false;
    document.getElementById('action-panel')?.classList.remove('open');
  }
  settingsShown = true;
  document.getElementById('settings-screen')?.classList.add('open');
  document.getElementById('settings-btn')?.classList.add('active');
  document.body.classList.add('settings-open');
  showSettingsSection(section || window._settingsSection || 'profile');
  applySettingsToForm(window._appSettings || {});
  loadRnsInterfaces();
  Promise.all([
    fetch('/api/settings').then(r => r.json()),
    refreshLanInterfaces(false, null, false),
  ]).then(([s, ifaces]) => {
    window._appSettings = s;
    applySettingsToForm(s, ifaces);
    updateIdentityDisplays();
  });
}

function closeSettings() {
  if (!settingsShown) return;
  settingsShown = false;
  document.getElementById('settings-screen')?.classList.remove('open');
  document.getElementById('settings-btn')?.classList.remove('active');
  document.body.classList.remove('settings-open');
  stopNetworkAutoRefresh();
}

function openSettingsFromNetwork() {
  openSettings('status');
}

function openNetworkSettings() {
  openSettings('status');
}

function resetNetwork() {
  if (!confirm('Reset network status?\n\nClears discovered peers, disconnects any link, and zeros beacon counters. Your identity and contacts are kept.')) return;
  fetch('/api/network/reset', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'})
    .then(r => r.json())
    .then(d => {
      if (d.status === 'ok') {
        linkPeer = null;
        linkedPeers.clear();
        updatePeerHeader();
        setLinkStatus('disconnected', 'Inactive');
        renderDiscovered([]);
        refreshNetworkStatus();
        toast('Network reset');
      } else {
        toast('Reset failed: ' + (d.error || 'unknown'));
      }
    })
    .catch(() => toast('Reset failed'));
}

function saveNetworkAutoReset() {
  const enabled = !!document.getElementById('settings-network-auto-reset')?.checked;
  fetch('/api/settings', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({network_stats_auto_reset: enabled})
  }).catch(() => {});
}

function lanInterfaceOptionValue(iface) {
  const name = iface?.name || 'iface';
  const ip = iface?.ip && iface.ip !== 'disconnected' ? iface.ip : '';
  return ip ? `${name}|${ip}` : name;
}

function lanInterfaceMatchesPinned(iface, pinned) {
  if (!pinned || !iface) return false;
  const value = lanInterfaceOptionValue(iface);
  if (value === pinned) return true;
  const sep = pinned.indexOf('|');
  const name = sep >= 0 ? pinned.slice(0, sep) : pinned;
  const ip = sep >= 0 ? pinned.slice(sep + 1) : '';
  if (ip && iface.ip === ip) return true;
  if (!ip && iface.name === name) return true;
  return false;
}

function formatLanInterfacePinned(pinned) {
  if (!pinned) return 'not pinned';
  const sep = pinned.indexOf('|');
  if (sep >= 0) {
    const name = pinned.slice(0, sep);
    const ip = pinned.slice(sep + 1);
    return ip ? `${name} — ${ip} (pinned)` : `${pinned} (pinned)`;
  }
  return `${pinned} (pinned)`;
}

function selectLanInterfaceValue(sel, interfaces, selected) {
  if (!sel) return;
  sel.innerHTML = lanInterfaceSelectHtml(interfaces);
  if (!selected) {
    sel.value = '';
    return;
  }
  const match = (interfaces || []).find(i => lanInterfaceMatchesPinned(i, selected));
  sel.value = match ? lanInterfaceOptionValue(match) : selected;
}

function lanInterfaceSelectHtml(interfaces) {
  let html = '<option value="" disabled>Pick an interface…</option>';
  const items = (interfaces || []).filter(i => i?.name);
  if (!items.length) {
    html += '<option value="" disabled>(no IPv4 found — click ↻ to rescan)</option>';
    return html;
  }
  items.forEach(iface => {
    const name = iface.name || 'iface';
    const ip = iface.ip && iface.ip !== 'disconnected' ? iface.ip : 'no IP';
    const mark = iface.up ? 'up' : 'down';
    const kind = iface.kind && iface.kind !== 'other'
      ? ` · ${iface.kind === 'vpn' ? 'VPN' : iface.kind}`
      : '';
    const gw = iface.gateway_iface ? ' · default route' : '';
    const value = lanInterfaceOptionValue(iface);
    html += `<option value="${escapeHtml(value)}">${escapeHtml(name)} — ${escapeHtml(ip)} (${mark}${kind}${gw})</option>`;
  });
  return html;
}

function peerMergeKey(p) {
  if (!p) return '';
  const via = (p.via || p.transport || '') === 'serial' ? 'serial' : 'lan';
  const base = peerKey(p.identity_hash) || peerKey(p.hash) || (p.pubkey ? String(p.pubkey).slice(0, 48) : '');
  return base ? `${base}:${via}` : '';
}

function namesRelated(a, b) {
  const left = (a || '').trim().toLowerCase();
  const right = (b || '').trim().toLowerCase();
  if (!left || !right) return false;
  if (left === right) return true;
  return left.startsWith(right) || right.startsWith(left);
}

function contactMatchesPeer(c, p) {
  if (!c || !p) return false;
  if (peersMatch(c.hash, p.hash)) return true;
  if (c.lan_hash && peersMatch(c.lan_hash, p.hash)) return true;
  if (c.serial_hash && peersMatch(c.serial_hash, p.hash)) return true;
  if (p.identity_hash && (peersMatch(c.identity_hash, p.hash) || peersMatch(c.hash, p.identity_hash))) return true;
  if (c.identity_hash && p.identity_hash && peerKey(c.identity_hash) === peerKey(p.identity_hash)) return true;
  const cn = (c.name || '').trim().toLowerCase();
  const pn = (p.name || '').trim().toLowerCase();
  if (cn && pn && (cn === pn || namesRelated(cn, pn))) return true;
  return false;
}

function sameSubnet(ipA, ipB) {
  if (!ipA || !ipB) return false;
  const a = String(ipA).split('.');
  const b = String(ipB).split('.');
  if (a.length !== 4 || b.length !== 4) return false;
  if (a[0] === b[0] && a[1] === b[1] && a[2] === b[2]) return true;
  const a0 = parseInt(a[0], 10);
  const a1 = parseInt(a[1], 10);
  const b0 = parseInt(b[0], 10);
  const b1 = parseInt(b[1], 10);
  if (a0 === 172 && b0 === 172 && a1 >= 16 && a1 <= 31 && b1 >= 16 && b1 <= 31) return true;
  if (a0 === 192 && b0 === 192 && a1 === 168 && b1 === 168) return true;
  return false;
}

function detectClientPlatform() {
  if (window.chatx5Android?.isAndroid?.()) return 'android';
  const ua = navigator.userAgent || '';
  const uad = navigator.userAgentData;
  if (uad?.platform) {
    const p = String(uad.platform).toLowerCase();
    if (p.includes('win')) return 'windows';
    if (p.includes('mac')) return 'darwin';
    if (p.includes('linux')) return 'linux';
    if (p.includes('android')) return 'android';
  }
  if (/mac os x|macintosh/i.test(ua)) return 'darwin';
  if (/windows|win32|win64/i.test(ua)) return 'windows';
  if (/android/i.test(ua)) return 'android';
  if (/linux/i.test(ua)) return 'linux';
  const plat = (navigator.platform || '').toLowerCase();
  if (/mac/i.test(plat)) return 'darwin';
  if (/win/i.test(plat)) return 'windows';
  if (/linux/i.test(plat)) return 'linux';
  if (['windows', 'darwin', 'linux', 'android'].includes(appPlatform)) return appPlatform;
  return 'desktop';
}

function bestPeerForContact(peers, contact) {
  const matches = (peers || []).filter(p => contactMatchesPeer(contact, p) && p.ip);
  if (!matches.length) return null;
  const scope = window._localLanIp;
  if (scope) {
    const local = matches.filter(p => sameSubnet(p.ip, scope));
    if (local.length) {
      local.sort((a, b) => (b.last_seen || 0) - (a.last_seen || 0));
      return local[0];
    }
  }
  matches.sort((a, b) => (b.last_seen || 0) - (a.last_seen || 0));
  return matches[0];
}

function syncContactIpsFromDiscovered(peers) {
  let changed = false;
  allContacts = allContacts.map(c => {
    const lanP = discoveredForContactTransport(c, 'lan');
    const serialP = discoveredForContactTransport(c, 'serial');
    const p = lanP || bestPeerForContact(peers, c);
    let next = {...c};
    let rowChanged = false;
    if (lanP?.hash) {
      const live = peerKey(lanP.hash);
      const saved = peerKey(contactLanHash(c));
      if (live && saved !== live) {
        next.lan_hash = live;
        next.hash = live;
        rowChanged = true;
      }
    }
    if (serialP?.hash) {
      const live = peerKey(serialP.hash);
      const saved = peerKey(contactSerialHash(c));
      const lanLive = peerKey(lanP?.hash || contactLanHash(c));
      if (live && live !== lanLive && saved !== live) {
        next.serial_hash = live;
        rowChanged = true;
      }
    }
    if (!p) {
      if (rowChanged) changed = true;
      return next;
    }
    const port = p.port || next.port || 8742;
    const scope = window._localLanIp;
    if (scope && next.ip && p.ip && sameSubnet(next.ip, scope) && !sameSubnet(p.ip, scope)) {
      const rttSame = next.rtt_ms === p.rtt_ms && next.rtt_avg_ms === p.rtt_avg_ms;
      if (!rttSame) {
        rowChanged = true;
        next.rtt_ms = p.rtt_ms;
        next.rtt_avg_ms = p.rtt_avg_ms;
      }
    } else {
      const ipSame = next.ip === p.ip && (next.port || 8742) === port;
      const idSame = (next.identity_hash || '') === (p.identity_hash || next.identity_hash || '');
      const rttSame = next.rtt_ms === p.rtt_ms && next.rtt_avg_ms === p.rtt_avg_ms;
      if (!ipSame || !rttSame || !idSame) {
        rowChanged = true;
        if (!ipSame && p.ip) next.ip = p.ip;
        next.port = port;
        next.identity_hash = p.identity_hash || next.identity_hash;
        next.rtt_ms = p.rtt_ms;
        next.rtt_avg_ms = p.rtt_avg_ms;
      }
    }
    if (rowChanged) changed = true;
    return next;
  });
  if (changed) renderContacts(allContacts);
}

function filterPhantomSerialPeers(peers) {
  const list = peers || [];
  const lanHashes = new Set(
    list.filter(p => (p.via || p.transport || '') !== 'serial').map(p => peerKey(p.hash)).filter(Boolean)
  );
  return list.filter(p => {
    if ((p.via || p.transport || '') !== 'serial') return true;
    const h = peerKey(p.hash);
    if (!h) return false;
    if (lanHashes.has(h)) return false;
    if ((p.ip || '').trim()) return false;
    return true;
  });
}

function mergeDiscoveredPeers(prev, incoming) {
  if (!incoming || !incoming.length) return filterPhantomSerialPeers(prev || []);
  const byKey = {};
  (prev || []).forEach(p => {
    const k = peerMergeKey(p);
    if (k) byKey[k] = p;
  });
  incoming.forEach(p => {
    const k = peerMergeKey(p);
    if (!k) return;
    const merged = {...(byKey[k] || {}), ...p};
    if (p.identity_hash) registerPeerAliases(p.hash, p.identity_hash);
    byKey[k] = merged;
  });
  return filterPhantomSerialPeers(Object.values(byKey));
}

function populateLanInterfaceSelect(interfaces, selected) {
  const sel = document.getElementById('settings-lan-interface');
  if (!sel) return;
  const pinned = selected !== undefined ? selected : sel.value;
  selectLanInterfaceValue(sel, interfaces, pinned || '');
  renderLanInterfaceSummary(interfaces);
}

function populateSetupLanSelect(interfaces, selected) {
  const sel = document.getElementById('setup-lan-interface');
  if (!sel) return;
  selectLanInterfaceValue(sel, interfaces, selected || '');
}

function saveLanInterface() {
  const sel = document.getElementById('settings-lan-interface');
  const lan_interface = sel?.value || '';
  const prev = window._lastLanInterface || '';
  fetch('/api/settings', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({lan_interface})
  })
    .then(async r => {
      const d = await r.json().catch(() => ({}));
      if (!r.ok || d.status !== 'ok') {
        if (sel && prev !== undefined) sel.value = prev;
        throw new Error(d.error || `HTTP ${r.status}`);
      }
      return d;
    })
    .then(d => {
      window._lastLanInterface = lan_interface;
      if (d.settings?.lan_interface !== undefined) {
        window._appSettings = { ...(window._appSettings || {}), ...d.settings };
      }
      const label = lan_interface ? formatLanInterfacePinned(lan_interface) : 'not set';
      toast(lan_interface ? `LAN pinned to ${label} — links cleared` : 'Pick an IPv4 interface in Settings → Network');
      refreshLanInterfaces(true);
      refreshNetworkStatus(true);
      if (activePeer) { activePeer = null; updatePeerUI(); }
    })
    .catch(err => toast('Failed to save LAN interface: ' + (err.message || err)));
}

function startNetworkAutoRefresh() {
  stopNetworkAutoRefresh();
  networkRefreshTimer = setInterval(() => {
    if (settingsShown && window._settingsSection === 'status') refreshNetworkStatus(true);
  }, 5000);
}

function stopNetworkAutoRefresh() {
  if (networkRefreshTimer) {
    clearInterval(networkRefreshTimer);
    networkRefreshTimer = null;
  }
}
