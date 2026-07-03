function openChat(hash, tryConnect, meta) {
  chatSwitchGen++;
  connectGeneration++;
  connectInFlight = false;
  viewingPeer = (hash || '').replace(/:/g, '');
  if (hash === HUB_GROUP_PEER) viewingPeer = HUB_GROUP_PEER;
  const inferred = meta?.via
    || peerMetaForHash(hash)?.via
    || viaForLinkedPeer(hash)
    || null;
  viewingVia = inferred ? normalizeVia(inferred) : null;
  clearUnread(viewingPeer);
  const persistView = viewingPeer === HUB_GROUP_PEER || isSavedContactHash(viewingPeer);
  try {
    if (persistView) {
      localStorage.setItem(LS_VIEWING_PEER, viewingPeer);
      if (viewingVia) localStorage.setItem(LS_VIEWING_VIA, viewingVia);
      else localStorage.removeItem(LS_VIEWING_VIA);
    } else {
      localStorage.removeItem(LS_VIEWING_PEER);
      localStorage.removeItem(LS_VIEWING_VIA);
    }
  } catch (_) {}
  document.getElementById('placeholder').style.display = 'none';
  document.getElementById('messages').style.display = 'flex';
  updatePeerHeader();
  if (isAndroidShell()) {
    document.body.classList.add('android-chat-open');
  } else {
    closeSidebar();
  }
  updateAndroidShellLayout();
  syncUiState();
  updateHubUi();
  if (viewingPeer === HUB_GROUP_PEER && window._hubRole && window._hubRole !== 'off') {
    fetch('/api/hub/ensure', {method: 'POST'}).then(r => r.json()).then(d => {
      if (d.hub_group_linked) {
        window._hubGroupLinked = true;
        updatePeerHeader();
      }
    }).catch(() => {});
  }
  loadHistoryForPeer(viewingPeer);
  pollQueue();
  renderContacts(allContacts);
  renderDiscovered(window._discoveredPeers || []);
  if (tryConnect) {
    const m = meta || peerMetaForHash(hash);
    const wake = m?.via !== 'serial';
    if (wake || !isPeerLinked(viewingPeer, viewingVia)) {
      connectTo(hash, m?.ip, m?.port, m?.via, {wake});
    }
  } else if (!document.body.classList.contains('modal-open') && isPeerLinked(viewingPeer, viewingVia)) {
    document.getElementById('msg-input').focus();
  }
}

function disconnect() {
  const peer = viewingPeer || '';
  const via = viewingVia ? normalizeVia(viewingVia) : null;
  fetch('/api/disconnect', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({peer, via}),
  }).then(r => r.json()).then(d => {
    if (d.linked_peers) syncLinkedPeers(d.linked_peers);
    else {
      if (peer) setLinkPeer(peer, false, via);
      if (peer) applyLinkRttToDiscovered(peer, null, via);
      updatePeerHeader();
    }
    if (!linkedPeers.size) setLinkStatus('disconnected', 'Inactive');
  }).catch(() => toast('Disconnect failed'));
}

function sendMessage() {
  const input = document.getElementById('msg-input');
  const text = input.value.trim();
  if (!text || !ws || ws.readyState !== WebSocket.OPEN) return;
  if (!viewingPeer) {
    toast('Select a chat first');
    return;
  }
  const linked = isPeerLinked(viewingPeer, viewingVia);
  const isHubChat = viewingPeer === HUB_GROUP_PEER;
  const payload = {
    type: 'send',
    text,
    peer: viewingPeer,
    chat_gen: chatSwitchGen,
    hub_group: isHubChat,
  };
  if (viewingVia && !isHubChat) payload.via = normalizeVia(viewingVia);
  ws.send(JSON.stringify(payload));
  input.value = '';
  autoResize(input);
  document.getElementById('emoji-picker')?.classList.remove('open');
  if (isAndroidShell()) {
    requestAnimationFrame(() => {
      input.focus();
      try { input.setSelectionRange(0, 0); } catch (_) {}
    });
  }
  const hubActive = window._hubRole && window._hubRole !== 'off';
  if (!linked && !(isHubChat && hubActive)) {
    toast('Message queued — will send when connected');
  }
}

function onInputKey(e) {
  if (document.body.classList.contains('modal-open')) return;
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
}

function autoResize(el) {
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 120) + 'px';
}

function isVideoFile(name) {
  return /\.(mp4|webm|mkv|mov|avi|m4v|ogv|mpeg|mpg)$/i.test(name || '');
}

function videoMimeType(name) {
  const ext = (name || '').split('.').pop().toLowerCase();
  return {
    mp4: 'video/mp4', m4v: 'video/mp4', webm: 'video/webm', mkv: 'video/x-matroska',
    mov: 'video/quicktime', avi: 'video/x-msvideo', ogv: 'video/ogg',
    mpeg: 'video/mpeg', mpg: 'video/mpeg',
  }[ext] || 'video/mp4';
}

function renderVideoMessage(data) {
  const fname = data.file_name || 'Video';
  const name = escapeHtml(fname);
  const sz = data.file_size ? ' ' + formatSize(data.file_size) : '';
  let html = `<div>🎬 ${name}${sz}</div>`;
  if (data.content) {
    const url = fileUrl(data.content, data);
    if (url) {
      const mime = videoMimeType(fname);
      html += `<video class="msg-video" controls playsinline preload="metadata">`;
      html += `<source src="${url}" type="${mime}">`;
      html += `</video>`;
      html += `<div style="margin-top:4px"><a href="${url}" download="${escapeHtml(fname)}" style="color:var(--primary);font-size:11px">💾 Save video</a></div>`;
    } else {
      html += `<div style="margin-top:4px;color:var(--text3);font-size:12px">Video saved locally</div>`;
    }
  }
  return html;
}

function fileUrl(path, data) {
  if (data?.file_url) return data.file_url;
  if (!path) return '';
  var idx = path.indexOf('/received/');
  if (idx >= 0) {
    const rel = path.substring(idx + 10).split('/').map(encodeURIComponent).join('/');
    return '/api/file/received/' + rel;
  }
  idx = path.indexOf('/sent/');
  if (idx >= 0) {
    const rel = path.substring(idx + 6).split('/').map(encodeURIComponent).join('/');
    return '/api/file/sent/' + rel;
  }
  return '';
}

function receiptIcon(status) {
  if (status === 'queued') return '⏳';
  if (status === 'sending') return '🕐';
  if (status === 'sent') return '✓';
  if (status === 'received') return '✓✓';
  if (status === 'read') return '👁';
  if (status === 'failed') return '✗';
  return '';
}
