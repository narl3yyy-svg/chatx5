function closeWS() {
  wsGen++;
  if (wsReconnectTimer) {
    clearTimeout(wsReconnectTimer);
    wsReconnectTimer = null;
  }
  if (!ws) return;
  const old = ws;
  ws = null;
  old.onclose = null;
  old.onerror = null;
  try { old.close(1000, 'client closing'); } catch (_) {}
}

function connectWS() {
  closeWS();
  const gen = wsGen;
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  ws = new WebSocket(proto + '//' + location.host + '/ws');
  ws.onopen = () => {
    if (gen !== wsGen) return;
    console.log('[ws] Connected');
    setWsStatus('connected', 'Connected');
    fetchIdentity();
    syncUiState();
  };
  ws.onmessage = e => {
    if (gen !== wsGen) return;
    console.log('[ws] Received:', e.data.substring(0, 200));
    try {
      const msg = JSON.parse(e.data);
      handleWSMessage(msg);
    } catch(_) {}
  };
  ws.onclose = () => {
    if (gen !== wsGen) return;
    ws = null;
    console.log('[ws] Disconnected, reconnecting...');
    setWsStatus('disconnected', 'Offline');
    setLinkStatus('disconnected', 'Inactive');
    wsReconnectTimer = setTimeout(connectWS, 2000);
  };
  ws.onerror = (e) => {
    if (gen !== wsGen) return;
    console.error('[ws] Error:', e);
  };
}

window.addEventListener('pagehide', closeWS);

function handleWSMessage(msg) {
  console.log('[ws] Message type:', msg.type);
  if (msg.type === 'message') {
    const data = msg.data || {};
    const chatPeer = data.chat_peer || data.peer || data.sender;
    const isIncoming = !data.outgoing && data.sender !== 'system';
    if (isIncoming && chatPeer) {
      const linked = isPeerLinked(chatPeer);
      const viewing = messageBelongsToPeer(data);
      if (!linked || !viewing) {
        bumpUnread(chatPeer);
        renderContacts(allContacts);
        renderDiscovered(window._discoveredPeers || []);
      }
      if (!linked || !viewing) {
        const preview = data.type === 'text' || data.type === 'emoji'
          ? data.content
          : (data.file_name || data.type || 'New message');
        showMessageNotification(chatPeer, preview);
      }
    }
    addMessage(data);
  } else if (msg.type === 'receipt') {
    const r = msg.data;
    const el = document.querySelector(`.msg[data-msgid="${r.msg_id}"] .receipt`);
    if (el) el.textContent = receiptIcon(r.status);
  } else if (msg.type === 'peers') {
    console.log('[ws] Peers received:', msg.data ? msg.data.length : 0, msg.data);
    renderDiscovered(msg.data, { authoritative: !!msg.authoritative });
  } else if (msg.type === 'contacts') {
    if (Array.isArray(msg.data)) {
      renderContacts(msg.data);
      syncContactIpsFromDiscovered(window._discoveredPeers || []);
    }
  } else if (msg.type === 'message_removed') {
    const mid = msg.data && msg.data.msg_id;
    if (mid) {
      document.querySelectorAll(`.msg[data-msgid="${mid}"]`).forEach(el => el.remove());
    }
  } else if (msg.type === 'identity_changed') {
    const data = msg.data || {};
    applyIdentityChange(data.hash, data.identity_hash, data.old_hash || data.old_identity_hash, data.role);
    toast('Identity updated — tap Announce LAN or Serial so peers see the new hash');
  } else if (msg.type === 'peer_superseded') {
    const data = msg.data || {};
    const removed = data.removed || [];
    const contactHasHash = (c, h) => {
      const key = peerKey(h);
      if (!key) return false;
      return [c.hash, c.lan_hash, c.serial_hash, c.identity_hash]
        .map(peerKey).filter(Boolean).some(k => k === key || peersMatch(k, h));
    };
    removed.forEach(old => {
      const key = peerKey(old);
      ['lan', 'serial'].forEach(via => linkedPeers.delete(linkKey(old, via)));
      linkedPeers.delete(key);
      const repl = data.replacement || (data.replacement_peer && data.replacement_peer.hash);
      if (repl && viewingPeer && peerKey(viewingPeer) === key) {
        viewingPeer = repl;
      } else if (peerKey(viewingPeer) === key && !repl) {
        viewingPeer = null;
        viewingVia = null;
        closeChatView();
      }
      if (linkPeer && peerKey(linkPeer.split(':')[0]) === key) linkPeer = null;
      window._discoveredPeers = (window._discoveredPeers || []).filter(p =>
        peerKey(p.hash) !== key && peerKey(p.identity_hash) !== key
      );
    });
    if (data.replacement_peer) {
      const repl = data.replacement_peer;
      registerPeerAliases(repl.hash, repl.identity_hash);
      const peers = window._discoveredPeers || [];
      const replKey = peerKey(repl.hash);
      const merged = peers.filter(p => peerKey(p.hash) !== replKey);
      merged.push(repl);
      window._discoveredPeers = merged;
    }
    fetchIdentity();
    renderDiscovered(window._discoveredPeers || []);
    updatePeerHeader();
    if (removed.length) {
      const repl = data.replacement || (data.replacement_peer && data.replacement_peer.hash);
      const replVia = data.replacement_peer?.via;
      if (repl) {
        toast('Peer list updated — your contact was refreshed automatically');
        if (viewingPeer && removed.some(h => peerKey(h) === peerKey(viewingPeer))) {
          openChat(repl, false, { ...data.replacement_peer, via: replVia });
        }
      } else {
        toast('Peer list updated — pick the peer from Discovered');
        if (!viewingPeer) setLinkStatus('disconnected', 'Inactive');
      }
    }
  } else if (msg.type === 'progress') {
    showProgress(msg.data);
  } else if (msg.type === 'link_established') {
    if (msg.data?.hash) {
      const h = msg.data.hash;
      registerPeerAliases(h, ...(msg.data.aliases || []));
      const linkVia = msg.data.via || viaForLinkedPeer(h) || viewingVia;
      if (msg.data.rtt_ms != null) applyLinkRttToDiscovered(h, msg.data.rtt_ms, linkVia);
      if (msg.data.link_quality_pct != null) applyLinkQuality(h, msg.data.link_quality_pct, linkVia);
      const passive = msg.data.passive || msg.data.user_disconnected;
      if (msg.data.linked_peers) {
        syncLinkedPeers(msg.data.linked_peers);
      } else if (!passive && msg.data.promote_active !== false) {
        setLinkPeer(h, true, linkVia);
      }
      const now = Date.now();
      if (connectInFlight && !msg.data.background) {
        connectInFlight = false;
        if (!viewingPeer || peersMatch(viewingPeer, h)) {
          openChat(h, false, {via: linkVia});
          toast('Connected');
          lastLinkToastAt = now;
        }
      } else if (msg.data.path_switch && viewingPeer && peersMatch(viewingPeer, h)) {
        updatePeerHeader();
        if (now - lastLinkToastAt > 45000) {
          toast('Switched to faster link');
          lastLinkToastAt = now;
        }
      } else {
        updatePeerHeader();
      }
      pollQueue();
    }
  } else if (msg.type === 'link_quality') {
    if (msg.data?.hash) {
      const h = msg.data.hash;
      const linkVia = msg.data.via || 'serial';
      if (msg.data.rtt_ms != null) applyLinkRttToDiscovered(h, msg.data.rtt_ms, linkVia);
      if (msg.data.link_quality_pct != null) applyLinkQuality(h, msg.data.link_quality_pct, linkVia);
      if (viewingPeer && peersMatch(viewingPeer, h) && normalizeVia(viewingVia) === 'serial') {
        updatePeerHeader();
      }
    }
  } else if (msg.type === 'link_closed') {
    if (msg.data?.linked_peers) {
      syncLinkedPeers(msg.data.linked_peers);
    } else if (msg.data?.peer) {
      const closedVia = msg.data.via || viewingVia;
      setLinkPeer(msg.data.peer, false, closedVia);
      applyLinkRttToDiscovered(msg.data.peer, null, closedVia);
    }
    updatePeerHeader();
    if (!linkedPeers.size) {
      setLinkStatus('disconnected', 'Inactive');
    }
    pollQueue();
  } else if (msg.type === 'connect_ok') {
    if (connectInFlight) {
      connectInFlight = false;
      const okVia = msg.via || viewingVia || viaForLinkedPeer(msg.hash);
      setLinkPeer(msg.hash, true, okVia);
      if (msg.linked_peers) syncLinkedPeers(msg.linked_peers);
      if (!viewingPeer || peersMatch(viewingPeer, msg.hash)) {
        openChat(msg.hash, false, {via: okVia});
      }
      toast('Connected!');
    }
  } else if (msg.type === 'connect_fail') {
    const alreadyLinked = viewingPeer && isPeerLinked(viewingPeer, viewingVia);
    connectInFlight = false;
    if (!alreadyLinked && !linkedPeers.size) {
      toast('Connection failed: ' + (msg.error || 'peer not reachable'));
    }
  } else if (msg.type === 'peer_history_cleared') {
    if (viewingPeer && peersMatch(msg.data?.peer, viewingPeer)) {
      document.getElementById('messages').innerHTML = '';
    }
  } else if (msg.type === 'queue_cleared' || msg.type === 'queue_drained') {
    pollQueue();
    if (msg.type === 'queue_drained' && msg.data?.sent) {
      toast('Sent ' + msg.data.sent + ' queued item(s)');
    }
  } else if (msg.type === 'network_reset') {
    peerAliasGroups.clear();
    linkPeer = null;
    linkedPeers.clear();
    updatePeerHeader();
    setLinkStatus('disconnected', 'Inactive');
    renderDiscovered([]);
    refreshNetworkStatus();
  } else if (msg.type === 'message_deleted') {
    const id = msg.data?.msg_id;
    if (id) document.querySelector(`.msg[data-msgid="${id}"]`)?.remove();
  } else if (msg.type === 'rns_ready') {
    hideRnsErrorBanner();
    fetchIdentity();
  } else if (msg.type === 'info') {
    const text = msg.data || '';
    if (text.toLowerCase().includes('network stack failed')) showRnsErrorBanner(text);
    toast(text);
  }
}

function sendReadReceipt(msgId) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({type: 'read_receipt', msg_id: msgId}));
  }
}

function renderIncomingTransfers() {
  const el = document.getElementById('incoming-transfers');
  if (!el) return;
  const items = Object.values(incomingTransfers);
  if (!items.length) {
    el.classList.remove('active');
    el.innerHTML = '';
    return;
  }
  el.classList.add('active');
  el.innerHTML = items.map(t => {
    const pct = t.progress || 0;
    let status = t.status || 'active';
    const via = t.transport === 'serial' ? 'USB' : (t.transport ? String(t.transport).toUpperCase() : 'LAN');
    if (status === 'active') status = pct > 0 ? pct + '%' : 'waiting…';
    return `<div class="incoming-item"><span>↓ ${escapeHtml(t.file_name || 'file')} <span style="opacity:0.65;font-size:10px">${escapeHtml(via)}</span></span><span>${escapeHtml(String(status))}</span></div>`;
  }).join('');
}

function updateIncomingTransfers(data) {
  if (!data || data.direction !== 'receive') return;
  const key = data.transfer_id || data.file_name || ('recv-' + Date.now());
  if (data.status === 'complete' || data.status === 'failed' || data.status === 'cancelled') {
    Object.keys(incomingTransfers).forEach(k => {
      const t = incomingTransfers[k];
      if (
        (data.transfer_id && t.transfer_id === data.transfer_id) ||
        (data.file_name && t.file_name === data.file_name)
      ) {
        delete incomingTransfers[k];
      }
    });
    delete incomingTransfers[key];
  } else {
    incomingTransfers[key] = {...incomingTransfers[key], ...data, _key: key, _updated: Date.now()};
  }
  const now = Date.now();
  Object.keys(incomingTransfers).forEach(k => {
    const t = incomingTransfers[k];
    if (t._updated && now - t._updated > 120000) delete incomingTransfers[k];
  });
  renderIncomingTransfers();
}

function showProgress(data) {
  updateIncomingTransfers(data);
  const tid = data.transfer_id;
  if (tid && cancelledTransferIds.has(tid) && data.status === 'active') return;
  if (data.status === 'cancelled' && tid) cancelledTransferIds.add(tid);
  var bar = document.getElementById('progress-bar');
  var label = document.getElementById('progress-label');
  var speedEl = document.getElementById('transfer-speed');
  var cancelBtn = document.getElementById('transfer-cancel-btn');
  if (!bar) return;

  if (data.status === 'cancelled' || data.status === 'failed') {
    progressLastUpdate = 0;
    hideProgress(data.status === 'cancelled' ? 'Transfer cancelled' : 'Transfer failed');
    return;
  }
  if (data.status === 'complete' || data.progress >= 100) {
    progressLastUpdate = 0;
    bar.querySelector('.progress-fill').style.width = '100%';
    label.textContent = (data.file_name || 'File') + ' — complete';
    if (speedEl && data.speed) {
      speedEl.textContent = data.speed;
      speedEl.classList.add('active');
    }
    setTimeout(hideProgress, 2500);
    return;
  }

  const now = Date.now();
  if (now - progressLastUpdate < 200 && (data.progress || 0) < 100) return;
  progressLastUpdate = now;

  bar.classList.add('active');
  cancelBtn?.classList.add('visible');
  activeTransfer = data;
  var pct = data.progress || 0;
  bar.querySelector('.progress-fill').style.width = Math.max(pct, 1) + '%';
  var dir = data.direction === 'receive' ? '↓' : '↑';
  var stage = data.stage === 'zipping' ? 'Zipping' : (data.direction === 'receive' ? 'Receiving' : 'Sending');
  var via = data.transport === 'serial' ? 'USB' : (data.transport ? String(data.transport).toUpperCase() : '');
  var text = dir + ' ' + stage + ' ' + (data.file_name || 'file');
  if (via) text += ' [' + via + ']';
  text += pct > 0 ? (' — ' + pct + '%') : (data.direction === 'receive' ? ' — waiting…' : ' — starting…');
  if (data.stage === 'zipping' && data.total) text += ' (' + (data.current || 0) + '/' + data.total + ' files)';
  if (data.size) text += ' (' + formatSize(data.size) + ')';
  label.textContent = text;
  if (speedEl) {
    if (data.speed) {
      speedEl.textContent = data.speed;
      speedEl.classList.add('active');
    } else {
      speedEl.textContent = '';
      speedEl.classList.remove('active');
    }
  }
}

function hideProgress(msg) {
  var bar = document.getElementById('progress-bar');
  var speedEl = document.getElementById('transfer-speed');
  var cancelBtn = document.getElementById('transfer-cancel-btn');
  if (bar) {
    bar.classList.remove('active');
    bar.querySelector('.progress-fill').style.width = '0%';
  }
  speedEl?.classList.remove('active');
  if (speedEl) speedEl.textContent = '';
  cancelBtn?.classList.remove('visible');
  activeTransfer = null;
  if (msg) toast(msg);
}

function cancelTransfer() {
  if (!activeTransfer) return;
  const tid = activeTransfer.transfer_id;
  const fname = activeTransfer.file_name;
  if (tid) cancelledTransferIds.add(tid);
  hideProgress('Transfer cancelled');
  fetch('/api/transfer/cancel', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      transfer_id: tid,
      file_name: fname,
    })
  }).catch(() => toast('Cancel failed'));
}

function startPeerPolling() {
  if (peersPollTimer) clearInterval(peersPollTimer);
  peersPollTimer = setInterval(() => {
    fetch('/api/discover')
      .then(r => r.json())
      .then(d => {
        renderDiscovered(d.peers);
      })
      .catch(() => {});
    pollTemperature();
    pollCpu();
    pollQueue();
    pollLinkStatus();
  }, 5000);
}

function pollTemperature() {
  fetch('/api/temperature')
    .then(r => r.json())
    .then(d => {
      const el = document.getElementById('temp-display');
      const approx = d.approx ? '~' : '';
      el.textContent = d.avg_celsius != null ? '🌡 ' + approx + d.avg_celsius + '°C' : '🌡 --°C';
    })
    .catch(() => {
      document.getElementById('temp-display').textContent = '🌡 --°C';
    });
}

function pollCpu() {
  fetch('/api/cpu')
    .then(r => r.json())
    .then(d => {
      const el = document.getElementById('cpu-display');
      if (d.cpu_percent != null) {
        el.textContent = '⚡ ' + d.cpu_percent + '%' + (d.approx ? '~' : '');
      } else {
        el.textContent = '⚡ --%';
        if (d.traceback) console.warn('CPU error:\n' + d.traceback);
      }
    })
    .catch(() => {
      document.getElementById('cpu-display').textContent = '⚡ --%';
    });
}

function pollQueue() {
  const peerQ = viewingPeer ? ('?peer=' + encodeURIComponent(viewingPeer)) : '';
  fetch('/api/queue' + peerQ)
    .then(r => r.json())
    .then(q => {
      var el = document.getElementById('queue-count');
      const count = q.count || 0;
      const total = q.total || count;
      if (count > 0) {
        el.style.display = 'block';
        el.textContent = 'Queue: ' + count + ' pending for this chat (tap to clear)';
      } else if (!viewingPeer && total > 0) {
        el.style.display = 'block';
        el.textContent = 'Queue: ' + total + ' pending (other chats)';
      } else {
        el.style.display = 'none';
      }
    })
    .catch(() => {});
}

function clearQueue() {
  const peer = viewingPeer || '';
  const body = peer ? JSON.stringify({peer}) : '{}';
  fetch('/api/queue', {method: 'DELETE', headers: {'Content-Type': 'application/json'}, body})
    .then(r => r.json())
    .then(d => {
      pollQueue();
      document.querySelectorAll('.msg .receipt').forEach(el => {
        if (el.textContent === '⏳') el.closest('.msg')?.remove();
      });
      toast(d.cleared ? ('Cleared ' + d.cleared + ' queued item(s)') : 'Queue cleared');
    })
    .catch(() => toast('Failed to clear queue'));
}
