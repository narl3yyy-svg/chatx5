function pickReceivedDirFallback(input) {
  if (input.files && input.files.length > 0) {
    const rel = input.files[0].webkitRelativePath;
    const hint = rel.substring(0, rel.indexOf('/'));
    document.getElementById('settings-received-dir').value = '.../' + hint;
    toast('Enter the full absolute path, then Save.');
  }
}

function regenerateIdentity(role) {
  role = (role || 'lan').toLowerCase();
  const label = role === 'serial' ? 'serial/USB' : 'LAN';
  if (!confirm(`Generate a new ${label} identity? The old ${label} hash will be deleted. Peers on that transport will need to reconnect.`)) return;
  fetch('/api/identity/regenerate', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({role}),
  })
    .then(r => r.json())
    .then(d => {
      if (d.status === 'ok') {
        const announceHint = role === 'serial' ? 'Announce Serial' : 'Announce LAN';
        toast(d.live ? `${label} identity updated — tap ${announceHint} to advertise` : `New ${label} identity saved`);
        fetchIdentity();
      } else {
        toast('Failed: ' + (d.error || ''));
      }
    });
}

function applyIdentityChange(newHash, identityHash, oldHash, role) {
  role = (role || 'lan').toLowerCase();
  const clean = stripHashColons(newHash || identityHash || '');
  if (role === 'serial') mySerialHash = clean;
  else myHash = clean;
  updateIdentityDisplays();
  if (oldHash) {
    const oldKey = peerKey(oldHash);
    linkedPeers.delete(oldKey);
    if (peerKey(viewingPeer) === oldKey) viewingPeer = null;
    if (peerKey(linkPeer) === oldKey) linkPeer = null;
    window._discoveredPeers = (window._discoveredPeers || []).filter(p =>
      peerKey(p.hash) !== oldKey && peerKey(p.identity_hash) !== oldKey
    );
    renderDiscovered(window._discoveredPeers);
  }
  setLinkStatus('disconnected', 'Inactive');
  updatePeerHeader();
}

function restartServer() {
  const msg = appPlatform === 'android'
    ? 'Restart the app to apply network/serial settings?'
    : 'Reload network stack? The terminal stays open.';
  if (!confirm(msg)) return;
  fetch('/api/restart', {method: 'POST'})
    .then(async r => {
      const d = await r.json();
      if (!r.ok && d.status !== 'ok' && d.status !== 'restarting') {
        toast('Restart failed: ' + (d.error || 'unknown'));
        return;
      }
      if (d.android && window.chatx5Android?.restartApp) {
        toast('Restarting app...');
        window.chatx5Android.restartApp();
        return;
      }
      if (d.reloaded) {
        toast(d.message || 'Server reloaded');
        setTimeout(() => location.reload(), 600);
        return;
      }
      toast('Server restarting...');
      setTimeout(() => location.reload(), 3000);
    })
    .catch(() => toast('Restart failed'));
}

document.addEventListener('paste', e => {
  const items = e.clipboardData.items;
  for (const item of items) {
    if (item.type.startsWith('image/')) {
      const file = item.getAsFile();
      if (file) uploadFiles([file]);
    }
  }
});

document.addEventListener('dragover', e => e.preventDefault());
document.addEventListener('drop', e => {
  e.preventDefault();
  if (e.dataTransfer.files.length) uploadFiles(e.dataTransfer.files);
});

const imageViewer = { scale: 1, minScale: 0.4, maxScale: 6, tx: 0, ty: 0, dragging: false, lastX: 0, lastY: 0, pinchDist: 0, bound: false };

function imageViewerApply() {
  const img = document.getElementById('modal-img');
  if (!img) return;
  img.style.transform = `translate(${imageViewer.tx}px, ${imageViewer.ty}px) scale(${imageViewer.scale})`;
  const label = document.getElementById('image-zoom-label');
  if (label) label.textContent = Math.round(imageViewer.scale * 100) + '%';
}

function imageZoomReset() {
  imageViewer.scale = 1;
  imageViewer.tx = 0;
  imageViewer.ty = 0;
  imageViewerApply();
}

function imageZoomIn() {
  imageViewer.scale = Math.min(imageViewer.maxScale, imageViewer.scale * 1.35);
  imageViewerApply();
}

function imageZoomOut() {
  imageViewer.scale = Math.max(imageViewer.minScale, imageViewer.scale / 1.35);
  if (imageViewer.scale <= 1) { imageViewer.tx = 0; imageViewer.ty = 0; }
  imageViewerApply();
}

function closeImageViewer() {
  document.getElementById('image-modal')?.classList.remove('open');
  imageViewer.scale = 1;
  imageViewer.tx = 0;
  imageViewer.ty = 0;
}

function bindImageViewer() {
  if (imageViewer.bound) return;
  const viewer = document.getElementById('image-viewer');
  const modal = document.getElementById('image-modal');
  if (!viewer || !modal) return;
  imageViewer.bound = true;

  viewer.addEventListener('wheel', e => {
    if (!modal.classList.contains('open')) return;
    e.preventDefault();
    const delta = e.deltaY < 0 ? 1.12 : 1 / 1.12;
    imageViewer.scale = Math.min(imageViewer.maxScale, Math.max(imageViewer.minScale, imageViewer.scale * delta));
    if (imageViewer.scale <= 1) { imageViewer.tx = 0; imageViewer.ty = 0; }
    imageViewerApply();
  }, { passive: false });

  viewer.addEventListener('pointerdown', e => {
    if (!modal.classList.contains('open') || e.button !== 0) return;
    imageViewer.dragging = true;
    imageViewer.lastX = e.clientX;
    imageViewer.lastY = e.clientY;
    viewer.classList.add('dragging');
    viewer.setPointerCapture(e.pointerId);
  });

  viewer.addEventListener('pointermove', e => {
    if (!imageViewer.dragging || imageViewer.scale <= 1) return;
    imageViewer.tx += e.clientX - imageViewer.lastX;
    imageViewer.ty += e.clientY - imageViewer.lastY;
    imageViewer.lastX = e.clientX;
    imageViewer.lastY = e.clientY;
    imageViewerApply();
  });

  const endDrag = e => {
    if (!imageViewer.dragging) return;
    imageViewer.dragging = false;
    viewer.classList.remove('dragging');
    try { viewer.releasePointerCapture(e.pointerId); } catch (_) {}
  };
  viewer.addEventListener('pointerup', endDrag);
  viewer.addEventListener('pointercancel', endDrag);

  viewer.addEventListener('touchstart', e => {
    if (!modal.classList.contains('open') || e.touches.length !== 2) return;
    const t = e.touches;
    imageViewer.pinchDist = Math.hypot(t[1].clientX - t[0].clientX, t[1].clientY - t[0].clientY);
  }, { passive: true });

  viewer.addEventListener('touchmove', e => {
    if (!modal.classList.contains('open') || e.touches.length !== 2 || !imageViewer.pinchDist) return;
    e.preventDefault();
    const t = e.touches;
    const dist = Math.hypot(t[1].clientX - t[0].clientX, t[1].clientY - t[0].clientY);
    const ratio = dist / imageViewer.pinchDist;
    imageViewer.pinchDist = dist;
    imageViewer.scale = Math.min(imageViewer.maxScale, Math.max(imageViewer.minScale, imageViewer.scale * ratio));
    imageViewerApply();
  }, { passive: false });

  let lastTap = 0;
  viewer.addEventListener('touchend', e => {
    const now = Date.now();
    if (now - lastTap < 300 && e.touches.length === 0) {
      if (imageViewer.scale > 1.2) imageZoomReset();
      else { imageViewer.scale = 2.5; imageViewerApply(); }
    }
    lastTap = now;
    if (e.touches.length < 2) imageViewer.pinchDist = 0;
  });

  modal.addEventListener('click', e => {
    if (e.target === modal) closeImageViewer();
  });
}

function showImage(src) {
  const img = document.getElementById('modal-img');
  const modal = document.getElementById('image-modal');
  if (!img || !modal) return;
  bindImageViewer();
  imageZoomReset();
  img.onload = () => imageZoomReset();
  img.src = src;
  modal.classList.add('open');
}

function copyMsgText(btn) {
  const textEl = btn.closest('.msg')?.querySelector('.msg-text');
  const text = textEl ? textEl.textContent : '';
  if (text) {
    navigator.clipboard.writeText(text).then(() => toast('Copied!'));
  }
}

function deleteMessage(msgId) {
  if (!msgId) return;
  fetch('/api/history/' + encodeURIComponent(msgId), { method: 'DELETE' })
    .then(r => {
      if (!r.ok) throw new Error();
      document.querySelector(`.msg[data-msgid="${msgId}"]`)?.remove();
    })
    .catch(() => toast('Delete failed'));
}

function formatRtt(ms) {
  if (ms == null || ms === undefined) return '';
  const n = parseInt(ms, 10);
  if (!Number.isFinite(n) || n < 0) return '';
  if (n >= 10000) return '>10s';
  if (n >= 1000) return (n / 1000).toFixed(1) + 's';
  return n + 'ms';
}

function loadBrandLogo() {
  const img = document.getElementById('brand-logo-img');
  const mark = document.getElementById('brand-mark');
  if (!img || !mark) return;
  const probe = new Image();
  probe.onload = () => {
    img.src = '/api/brand-logo?t=' + Date.now();
    mark.classList.add('has-logo');
  };
  probe.onerror = () => {
    img.removeAttribute('src');
    mark.classList.remove('has-logo');
  };
  probe.src = '/api/brand-logo?t=' + Date.now();
}

function pickBrandLogo() {
  if (window.chatx5Android?.pickBrandLogo) {
    window.chatx5Android.pickBrandLogo();
    return;
  }
  const input = document.getElementById('brand-logo-input');
  if (!input) return;
  input.onchange = () => {
    const file = input.files && input.files[0];
    if (!file) return;
    const fd = new FormData();
    fd.append('logo', file);
    fetch('/api/brand-logo', {method: 'POST', body: fd})
      .then(r => r.json())
      .then(d => {
        if (d.status === 'ok') {
          loadBrandLogo();
          toast('Sidebar logo updated');
        } else toast('Logo upload failed');
      })
      .catch(() => toast('Logo upload failed'));
    input.value = '';
  };
  input.click();
}

function openIdentityModal() {
  const overlay = document.getElementById('identity-modal-overlay');
  const lanEl = document.getElementById('identity-modal-hash-lan');
  const serialEl = document.getElementById('identity-modal-hash-serial');
  if (lanEl) lanEl.textContent = stripHashColons(myHash) || '—';
  if (serialEl) serialEl.textContent = stripHashColons(mySerialHash) || '—';
  updateSerialAnnounceVisibility(window._serialActive);
  overlay?.classList.add('open');
}

function closeIdentityModal(event) {
  if (event && event.target && event.target.id !== 'identity-modal-overlay') return;
  document.getElementById('identity-modal-overlay')?.classList.remove('open');
}

function copyIdentityFromModal(role) {
  role = (role || 'lan').toLowerCase();
  const text = role === 'serial' ? stripHashColons(mySerialHash) : stripHashColons(myHash);
  if (!text) {
    toast('No hash to copy');
    return;
  }
  navigator.clipboard.writeText(text).then(() => {
    toast((role === 'serial' ? 'Serial' : 'LAN') + ' hash copied!');
  });
}

function regenerateIdentityFromModal(role) {
  closeIdentityModal();
  regenerateIdentity(role || 'lan');
}

function copyIdentity() {
  openIdentityModal();
}

function stripHashColons(h) {
  return (h || '').replace(/:/g, '').toLowerCase();
}

function formatHashDisplay(h) {
  const clean = stripHashColons(h);
  if (!clean) return '';
  if (clean.length <= 16) return clean;
  return clean.substring(0, 16) + '...';
}

function truncateHash(h) {
  return formatHashDisplay(h);
}

function updateAndroidDebugLogHint(path) {
  const el = document.getElementById('android-debug-log-hint');
  if (!el) return;
  if (appPlatform !== 'android' || !path) {
    el.style.display = 'none';
    el.textContent = '';
    return;
  }
  el.style.display = 'block';
  el.innerHTML = '<strong>Debug log:</strong> saved in app-private storage on this device.<br><code style="font-size:10px">' + escapeHtml(path) + '</code><br>'
    + '<a href="#" onclick="viewDebugLog();return false" style="color:var(--accent)">View recent log</a>'
    + ' · <a href="#" onclick="exportDebugLogs();return false" style="color:var(--accent)">Export logs to folder</a>';
}

function exportDebugLogs() {
  if (window.chatx5Android?.pickFolder) {
    window._folderPickTarget = 'debug-export';
    window.chatx5Android.pickFolder();
    return;
  }
  toast('Use the Android app to pick an export folder');
}

function doExportDebugLogs(destPath) {
  if (!destPath) return;
  fetch('/api/debug/export', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({path: destPath}),
  })
    .then(r => r.json())
    .then(d => {
      if (d.status === 'ok') {
        const msg = `Exported ${d.copied} log file(s) to ${d.path}`;
        toast(d.warning ? msg + ' — ' + d.warning : msg);
      } else {
        toast('Export failed: ' + (d.error || 'unknown'));
      }
    })
    .catch(() => toast('Export failed'));
}

function viewDebugLog() {
  fetch('/api/debug')
    .then(r => r.json())
    .then(d => {
      const tail = d.debug_log_tail || '(log empty — restart in Debug mode and reproduce the issue)';
      const path = d.debug_log_path || '';
      const text = (path ? 'Path: ' + path + '\n\n' : '') + tail;
      const pre = document.createElement('pre');
      pre.style.cssText = 'max-height:60vh;overflow:auto;font-size:10px;white-space:pre-wrap;word-break:break-all';
      pre.textContent = text;
      const wrap = document.createElement('div');
      wrap.appendChild(pre);
      const dlg = document.createElement('div');
      dlg.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.7);z-index:9999;display:flex;align-items:center;justify-content:center;padding:16px';
      const box = document.createElement('div');
      box.style.cssText = 'background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:12px;max-width:95vw;max-height:90vh;overflow:auto';
      box.innerHTML = '<strong>Debug log</strong> <button type="button" id="debug-export-btn" style="margin-right:8px">Export to folder</button><button type="button" style="float:right">Close</button>';
      box.appendChild(wrap);
      box.querySelector('#debug-export-btn')?.addEventListener('click', () => exportDebugLogs());
      box.querySelector('button[style*="float:right"]')?.addEventListener('click', () => dlg.remove());
      dlg.appendChild(box);
      dlg.onclick = e => { if (e.target === dlg) dlg.remove(); };
      document.body.appendChild(dlg);
    })
    .catch(() => toast('Could not load debug log'));
}
