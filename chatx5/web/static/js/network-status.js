function requestMicFromNetworkPanel() {
  if (window.chatx5Android) {
    window.chatx5Android.requestAudioPermission();
    toast('Allow microphone access to record voice notes');
  }
}

function nsBadge(label, ok, warn) {
  const cls = ok ? 'ok' : (warn ? 'warn' : 'bad');
  return `<span class="ns-badge ${cls}"><span class="dot"></span>${escapeHtml(label)}</span>`;
}

function nsKv(label, value) {
  return `<div class="ns-kv-row"><span>${escapeHtml(label)}</span><span>${escapeHtml(String(value ?? '—'))}</span></div>`;
}

function nsCard(title, stateText, stateOk, rowsHtml) {
  const stateCls = stateOk === undefined ? '' : netClass(stateOk);
  return `<div class="ns-card">
    <div class="ns-card-head">
      <span class="ns-card-title">${escapeHtml(title)}</span>
      <span class="ns-card-state ${stateCls}">${escapeHtml(stateText)}</span>
    </div>
    <div class="ns-kv">${rowsHtml}</div>
  </div>`;
}

function summarizeRnsInterfacesClient(raw, summary) {
  if (Array.isArray(summary) && summary.length) return summary;
  if (!Array.isArray(raw) || !raw.length) return [];
  const groups = {};
  raw.forEach(iface => {
    const type = iface.type || 'Interface';
    const name = (iface.name || '').trim();
    let key = type;
    let label = type;
    if (type === 'TCPClientInterface') {
      const outbound = /→|TCP Client \d/.test(name) || /^TCP Client [0-9.]+:/.test(name);
      if (outbound) {
        key = `out:${name}`;
        label = name || 'TCP client (outbound)';
      } else {
        key = 'inbound';
        label = 'Hub relay clients (inbound)';
      }
    } else if (name) {
      key = `${type}:${name}`;
      label = name;
    }
    if (!groups[key]) groups[key] = { type, name: label, online: 0, total: 0 };
    groups[key].total += 1;
    if (iface.online) groups[key].online += 1;
  });
  return Object.values(groups).map(g => ({
    type: g.type,
    name: g.name,
    online: g.online > 0,
    count: g.total,
    online_count: g.online,
  }));
}

function renderNetworkStatusPanel(d) {
  const platform = (window.chatx5Android?.isAndroid?.() ? 'android' : null) || d.platform || 'desktop';
  const lanDiscovery = d.lan_discovery_configured !== false;
  const serialOnly = !!d.serial_only_mode;
  const lanConnected = lanDiscovery && d.lan_connected !== false && !!d.lan_ip && d.lan_ip !== 'not configured';
  const hubRole = d.hub_role || 'off';
  const linkLabel = d.link_active
    ? `Encrypted session${d.active_peer ? ' · ' + truncateHash(d.active_peer) : ''}`
    : (d.session_peer ? 'Reconnecting · ' + truncateHash(d.session_peer) : 'No active chat session');
  const heroIcon = d.link_active ? '🔗' : (d.rns_ready ? '🛰️' : '⚠️');
  let heroSub = `v${d.app_version || '—'} · ${platform} · HTTP ${d.http_bind || '—'}`;
  if (d.http_webview) heroSub += ` (WebView ${d.http_webview})`;
  if (d.rns_error) heroSub += ` · ${d.rns_error}`;

  let badges = '';
  badges += nsBadge(d.rns_ready ? 'RNS ready' : 'RNS starting', !!d.rns_ready);
  badges += nsBadge(d.discovery_active ? 'Discovery on' : 'Discovery idle', !!d.discovery_active, !d.discovery_active);
  badges += nsBadge(d.link_active ? 'Link active' : 'Link idle', !!d.link_active);
  if (hubRole !== 'off') {
    const hubOk = hubRole === 'server' ? !!d.tcp_hub_online : !!d.tcp_client_online;
    const hubText = hubRole === 'server'
      ? `Hub server${d.hub_clients_linked ? ' · ' + d.hub_clients_linked + ' client(s)' : ''}`
      : `Hub client${d.hub_group_linked ? ' · linked' : ''}`;
    badges += nsBadge(hubText, hubOk || !!d.hub_group_linked, !hubOk);
  }

  const lanRows = serialOnly
    ? nsKv('Mode', 'Serial only')
    : (!lanDiscovery
      ? nsKv('Mode', 'Not configured')
      : nsKv('IPv4', lanConnected ? d.lan_ip : 'Disconnected')
        + nsKv('Broadcast', lanConnected ? (d.broadcast || 'none') : '—')
        + nsKv('Pinned NIC', d.lan_interface ? formatLanInterfacePinned(d.lan_interface) : 'Auto'));
  const lanState = serialOnly ? 'Serial only' : (lanConnected ? 'Online' : (lanDiscovery ? 'Offline' : 'Off'));

  const serialRows = d.serial_configured_port
    ? nsKv('Port', d.serial_configured_port)
      + nsKv('In RNS', d.serial_in_rns ? 'Yes — failover ready' : 'Waiting for hot-add')
    : nsKv('Port', 'Not configured');
  const serialState = d.serial_in_rns ? 'Active' : (d.serial_configured_port ? 'Configured' : 'Off');

  let hubRows = nsKv('Role', hubRole === 'off' ? 'Disabled' : hubRole);
  if (hubRole === 'server') {
    hubRows += nsKv('Listen', `0.0.0.0:${d.hub_port || 4242}`);
    hubRows += nsKv('Clients linked', String(d.hub_clients_linked || 0));
    hubRows += nsKv('Group relay', d.hub_group_linked ? 'Ready' : 'Waiting');
  } else if (hubRole === 'client') {
    hubRows += nsKv('Hub host', d.hub_host || '—');
    hubRows += nsKv('TCP client', d.tcp_client_online ? 'Connected' : 'Connecting');
    hubRows += nsKv('Group relay', d.hub_group_linked ? 'Linked' : 'Not linked');
  }

  let sessionHtml = '';
  if (d.link_active || d.session_peer || (Array.isArray(d.linked_peers) && d.linked_peers.length)) {
    const rows = nsKv('Status', d.link_active ? 'Active' : 'Reconnecting')
      + (d.active_peer ? nsKv('Peer', truncateHash(d.active_peer)) : '')
      + (d.link_rns_interface ? nsKv('RNS path', d.link_rns_interface) : '')
      + (Array.isArray(d.linked_peers) && d.linked_peers.length
        ? nsKv('Linked transports', d.linked_peers.join(', '))
        : '');
    sessionHtml = `<div class="ns-section"><h4 class="ns-section-title">Active session</h4><div class="ns-kv">${rows}</div></div>`;
  }

  const ifaceRows = summarizeRnsInterfacesClient(d.rns_interfaces, d.rns_interfaces_summary);
  let ifaceTable = '';
  if (ifaceRows.length) {
    const body = ifaceRows.map(row => {
      const count = row.count > 1 ? ` <span style="color:var(--text3)">×${row.count}</span>` : '';
      const online = row.online_count > 1 ? `${row.online_count}/${row.count} online` : (row.online ? 'Online' : 'Offline');
      return `<tr>
        <td><span class="iface-dot ${row.online ? 'up' : 'down'}"></span>${escapeHtml(row.name)}${count}</td>
        <td>${escapeHtml(row.type)}</td>
        <td class="${netClass(row.online)}">${escapeHtml(online)}</td>
      </tr>`;
    }).join('');
    const rawNote = (d.rns_interface_count || 0) > ifaceRows.length
      ? `<div class="ns-foot">${d.rns_interface_count} runtime interface(s) collapsed into ${ifaceRows.length} group(s).</div>`
      : '';
    ifaceTable = `<div class="ns-section"><h4 class="ns-section-title">Runtime transports</h4>
      <table class="ns-iface-table"><thead><tr><th>Interface</th><th>Type</th><th>Status</th></tr></thead><tbody>${body}</tbody></table>${rawNote}</div>`;
  }

  let presetHtml = '';
  if (Array.isArray(d.configured_interfaces) && d.configured_interfaces.length) {
    const rows = d.configured_interfaces.map(iface => {
      let status = iface.enabled ? 'Enabled' : 'Disabled';
      if (iface.preset === 'serial' || iface.type === 'SerialInterface') {
        if (iface.serial_active) status = 'Active';
        else if (iface.port_status === 'permission_denied') status = 'No access';
        else if (iface.port) status = 'Port ' + iface.port;
        else status = 'No port';
      }
      return nsKv(iface.name || iface.type, status + ' · ' + (iface.preset || iface.type));
    }).join('');
    presetHtml = `<div class="ns-section"><h4 class="ns-section-title">Configured presets</h4><div class="ns-kv">${rows}</div></div>`;
  }

  const peers = (Array.isArray(d.discovered_peers_display) && d.discovered_peers_display.length)
    ? d.discovered_peers_display
    : (Array.isArray(d.discovered_peers) ? d.discovered_peers : []);
  let peersHtml = '';
  if (peers.length) {
    const items = peers.map(p => {
      const via = (p.via || '') === 'serial' ? 'serial' : ((p.via || '') === 'beacon' ? 'beacon' : 'lan');
      const tag = via === 'serial' ? 'USB' : (via === 'beacon' ? 'Beacon' : 'LAN');
      const rtt = formatRtt(p.rtt_avg_ms || p.rtt_ms);
      const meta = [
        truncateHash(p.hash),
        p.ip ? '@ ' + p.ip : '',
        rtt ? rtt : '',
        p.connected ? 'linked' : '',
      ].filter(Boolean).join(' · ');
      return `<div class="ns-peer">
        <div><div class="ns-peer-name">${escapeHtml(p.name || truncateHash(p.hash))}</div>
        <div class="ns-peer-meta">${escapeHtml(meta)}</div></div>
        <div class="ns-peer-tags"><span class="ns-tag ${via}">${tag}</span></div>
      </div>`;
    }).join('');
    const countNote = (d.discovered_count || 0) > peers.length
      ? `<div class="ns-foot">${peers.length} unique peer(s) shown (${d.discovered_count} raw discovery rows).</div>`
      : '';
    peersHtml = `<div class="ns-section"><h4 class="ns-section-title">Discovered peers</h4><div class="ns-peer-grid">${items}</div>${countNote}</div>`;
  } else {
    peersHtml = `<div class="ns-section"><h4 class="ns-section-title">Discovered peers</h4><div class="ns-empty">No peers yet — tap Announce LAN or Serial.</div></div>`;
  }

  let beaconHtml = '';
  if (d.beacon) {
    beaconHtml = `<div class="ns-section"><h4 class="ns-section-title">Beacon counters (this session)</h4><div class="ns-kv">`
      + nsKv('Mode', d.beacon.interval_sec ? 'Auto' : 'Manual')
      + nsKv('Sent', String(d.beacon.packets_sent ?? 0))
      + nsKv('Received', String(d.beacon.packets_received ?? 0))
      + nsKv('Last announce', String(d.beacon.last_announce_sent ?? 0) + ' packet(s)')
      + `</div></div>`;
  }

  let extraHtml = `<div class="ns-section"><h4 class="ns-section-title">Service ports</h4><div class="ns-kv">`
    + nsKv('RNS UDP', String(d.rns_udp_port || 4242))
    + nsKv('Beacon UDP', String(d.beacon_udp_port || 8743))
    + nsKv('WS clients', String(d.ws_clients ?? 0) + ' tab(s) on this device')
    + `</div></div>`;

  if (platform === 'android' && window.chatx5Android) {
    const micOk = window.chatx5Android.hasAudioPermission();
    extraHtml += `<div class="ns-section"><h4 class="ns-section-title">Android</h4><div class="ns-kv">`
      + (micOk
        ? nsKv('Microphone', 'Allowed')
        : `<div class="net-row net-row-action" onclick="requestMicFromNetworkPanel()"><span class="net-label">Microphone</span><span class="net-value net-bad">Denied — tap to request</span></div>`)
      + (d.usb_serial_ready != null ? nsKv('USB serial ready', String(d.usb_serial_ready) + ' device(s)') : '')
      + `</div></div>`;
  } else if (d.serial_group_access === false) {
    extraHtml += `<div class="ns-foot">Serial groups: dialout not active — launch with <code>./run.sh web</code>.</div>`;
  }

  if (d.debug_log_path) {
    extraHtml += `<div class="ns-foot">Debug log: <code>${escapeHtml(d.debug_log_path)}</code></div>`;
  }

  return `<div class="ns-wrap">
    <div class="ns-hero">
      <div class="ns-hero-icon">${heroIcon}</div>
      <div>
        <h4 class="ns-hero-title">${escapeHtml(linkLabel)}</h4>
        <p class="ns-hero-sub">${escapeHtml(heroSub)}</p>
        <div class="ns-hero-badges">${badges}</div>
      </div>
    </div>
    <div class="ns-grid">
      ${nsCard('LAN', lanState, lanConnected && !serialOnly, lanRows)}
      ${nsCard('Serial / USB', serialState, !!d.serial_in_rns, serialRows)}
      ${hubRole !== 'off' ? nsCard('Hub relay', hubRole === 'server' ? (d.tcp_hub_online ? 'Listening' : 'Starting') : (d.tcp_client_online ? 'Connected' : 'Dialing'), hubRole === 'server' ? !!d.tcp_hub_online : !!d.tcp_client_online, hubRows) : ''}
      ${nsCard('Discovery', (d.discovered_display_count ?? d.discovered_count ?? 0) + ' peer(s)', (d.discovered_count || 0) > 0, nsKv('Listening', d.discovery_active ? 'Yes' : 'Tap Announce') + nsKv('Queue', String(d.queue_size ?? 0)))}
    </div>
    ${sessionHtml}
    ${ifaceTable}
    ${presetHtml}
    ${peersHtml}
    ${beaconHtml}
    ${extraHtml}
    <div class="ns-foot">HTTP ${escapeHtml(String(d.http_bind || '—'))} is this web UI only. RNS UDP 4242 + serial/LAN/TCP paths carry encrypted chat. Inbound hub TCP clients are grouped — each remote connection creates one relay interface.</div>
  </div>`;
}

function refreshNetworkStatus(silent) {
  const panel = document.getElementById('network-status-panel');
  if (!panel) return;
  if (!silent) panel.innerHTML = '<div class="ns-empty">Loading network status…</div>';
  const url = silent ? '/api/network-status' : '/api/network-status?refresh=1';
  fetch(url)
    .then(r => r.json())
    .then(d => {
      const platform = (window.chatx5Android?.isAndroid?.() ? 'android' : null) || d.platform;
      if (platform) appPlatform = platform;
      window._tcpHubOnline = !!d.tcp_hub_online;
      window._tcpClientOnline = !!d.tcp_client_online;
      window._hubGroupLinked = !!d.hub_group_linked;
      window._hubClientsLinked = d.hub_clients_linked || 0;
      if (d.hub_role) {
        window._hubRole = d.hub_role;
        updateHubUi();
      }
      if (viewingPeer === HUB_GROUP_PEER) updatePeerHeader();
      if (d.lan_ip && d.lan_ip !== 'not configured') window._localLanIp = d.lan_ip;
      if (settingsShown && Array.isArray(d.available_interfaces)) {
        populateLanInterfaceSelect(d.available_interfaces, d.lan_interface || '');
      }
      updateSerialAnnounceVisibility(!!d.serial_in_rns || !!(Array.isArray(d.configured_interfaces) && d.configured_interfaces.some(i =>
        (i.preset === 'serial' || i.type === 'SerialInterface') && i.serial_active)));
      if (d.debug_log_path) updateAndroidDebugLogHint(d.debug_log_path);
      panel.innerHTML = renderNetworkStatusPanel(d);
    })
    .catch(() => { panel.textContent = 'Failed to load network status.'; });
}
