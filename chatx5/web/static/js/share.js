function closeFolderPicker() {
  document.getElementById('folder-picker-dialog')?.classList.remove('open');
}

let _shareSession = null;

function closeShareBrowser() {
  document.getElementById('share-browser-dialog')?.classList.remove('open');
  _shareSession = null;
}

function shareRemoteQuery(extra) {
  const s = _shareSession;
  if (!s) return '';
  const q = new URLSearchParams({
    host: s.host,
    port: String(s.port),
    session_id: s.session_id,
    token: s.token,
    ...(extra || {}),
  });
  return q.toString();
}

function shareIsLocal() {
  return !!(_shareSession && _shareSession.local);
}

async function loadShareListing() {
  const s = _shareSession;
  const list = document.getElementById('share-browser-list');
  if (!s || !list) return;
  const pathEl = document.getElementById('share-browser-path');
  const upBtn = document.getElementById('share-browser-up');
  const uploadLabel = document.getElementById('share-browser-upload-label');
  list.innerHTML = '<div style="color:var(--text3);font-size:12px">Loading…</div>';
  const rel = s.path || '';
  const base = shareIsLocal()
    ? `/api/share/${encodeURIComponent(s.session_id)}/list?token=${encodeURIComponent(s.token)}&path=${encodeURIComponent(rel)}`
    : `/api/share/remote/list?${shareRemoteQuery({path: rel})}`;
  try {
    const r = await fetch(base);
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || 'list failed');
    s.writable = !!d.writable;
    if (pathEl) pathEl.textContent = '/' + (d.path || '').replace(/^\//, '');
    if (upBtn) upBtn.style.display = d.parent !== undefined && d.parent !== null && d.path ? '' : 'none';
    if (uploadLabel) uploadLabel.style.display = d.writable ? '' : 'none';
    list.innerHTML = '';
    (d.entries || []).forEach(entry => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'share-entry-btn';
      const meta = entry.dir ? 'folder' : formatSize(entry.size || 0);
      btn.innerHTML = `<span>${entry.dir ? '📁' : '📄'} ${escapeHtml(entry.name)}</span><span class="share-entry-meta">${escapeHtml(meta)}</span>`;
      btn.onclick = () => {
        if (entry.dir) {
          s.path = entry.path;
          loadShareListing();
        } else {
          downloadShareFile(entry.path, entry.name);
        }
      };
      list.appendChild(btn);
    });
    if (!d.entries || !d.entries.length) {
      list.innerHTML = '<div style="color:var(--text3);font-size:12px">Empty folder</div>';
    }
  } catch (e) {
    list.innerHTML = `<div style="color:var(--danger);font-size:12px">${escapeHtml(e.message || 'Failed to load')}</div>`;
  }
}

function shareBrowserUp() {
  if (!_shareSession) return;
  const cur = (_shareSession.path || '').replace(/\\/g, '/').replace(/\/$/, '');
  if (!cur) return;
  const parent = cur.includes('/') ? cur.replace(/\/[^/]+$/, '') : '';
  _shareSession.path = parent;
  loadShareListing();
}

function openShareBrowser(keyOrOffer) {
  let offer = keyOrOffer;
  if (typeof keyOrOffer === 'string') {
    offer = (window._shareOffers || {})[keyOrOffer];
  }
  if (!offer || !offer.session_id || !offer.token) {
    toast('Share session unavailable');
    return;
  }
  const myHost = window.location.hostname;
  const local = String(offer.host) === myHost && Number(offer.port) === Number(window.location.port || location.port);
  _shareSession = {
    session_id: offer.session_id,
    token: offer.token,
    host: offer.host,
    port: offer.port,
    root_name: offer.root_name || 'Shared folder',
    writable: !!offer.writable,
    path: '',
    local,
  };
  const title = document.getElementById('share-browser-title');
  if (title) title.textContent = offer.root_name || 'Shared folder';
  document.getElementById('share-browser-dialog')?.classList.add('open');
  loadShareListing();
}

function downloadShareFile(relPath, fileName) {
  const s = _shareSession;
  if (!s) return;
  const rel = relPath || '';
  const url = shareIsLocal()
    ? `/api/share/${encodeURIComponent(s.session_id)}/download?token=${encodeURIComponent(s.token)}&path=${encodeURIComponent(rel)}`
    : `/api/share/remote/download?${shareRemoteQuery({path: rel})}`;
  const a = document.createElement('a');
  a.href = url;
  a.download = fileName || 'download';
  a.click();
}

async function uploadShareFile(files) {
  const s = _shareSession;
  if (!s || !files || !files.length) return;
  if (!s.writable) {
    toast('Read-only share');
    return;
  }
  const form = new FormData();
  form.append('path', s.path || '');
  form.append('file', files[0]);
  const url = shareIsLocal()
    ? `/api/share/${encodeURIComponent(s.session_id)}/upload`
    : `/api/share/remote/upload?${shareRemoteQuery()}`;
  const headers = shareIsLocal() ? {'X-Share-Token': s.token} : {};
  try {
    const r = await fetch(url, {method: 'POST', headers, body: form});
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || 'upload failed');
    toast('Uploaded ' + (files[0].name || 'file'));
    loadShareListing();
  } catch (e) {
    toast('Upload failed: ' + (e.message || 'error'));
  }
}

async function startShareBrowse(path) {
  if (!viewingPeer) {
    toast('Select a chat first');
    return;
  }
  const hub_group = viewingPeer === HUB_GROUP_PEER;
  try {
    const r = await fetch('/api/share/start', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({path, peer: viewingPeer, hub_group, writable: true}),
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || 'share failed');
    toast('Shared ' + (d.offer?.root_name || 'folder'));
  } catch (e) {
    toast('Share failed: ' + (e.message || 'error'));
  }
}

async function pickShareFolder() {
  if (!viewingPeer) {
    toast('Select a chat first');
    return;
  }
  toast('Pick a folder to share…');
  try {
    const r = await fetch('/api/browse-dir');
    const d = await r.json();
    if (d.platform === 'android' && Array.isArray(d.options) && d.options.length) {
      window._sharePickCallback = (path) => startShareBrowse(path);
      showFolderOptionsDialog(d.options, '__share_pick__');
      return;
    }
    if (r.ok && d.path) {
      await startShareBrowse(d.path);
      return;
    }
    if (d.error === 'cancelled') return;
    if (d.error) toast('Browse failed: ' + d.error);
  } catch (_) {
    toast('Browse failed');
  }
}

function showFolderOptionsDialog(options, targetId) {
  window._receivedDirTargetId = targetId || 'settings-received-dir';
  const list = document.getElementById('folder-picker-list');
  if (!list) return;
  list.innerHTML = '';
  options.forEach(opt => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'folder-option-btn';
    btn.innerHTML = `<strong>${escapeHtml(opt.label)}</strong><span class="path">${escapeHtml(opt.path)}</span>`;
    btn.onclick = () => {
      if (targetId === '__share_pick__') {
        closeFolderPicker();
        const cb = window._sharePickCallback;
        window._sharePickCallback = null;
        if (cb) cb(opt.path);
        else startShareBrowse(opt.path);
      } else {
        selectReceivedDir(opt.path);
      }
    };
    list.appendChild(btn);
  });
  document.getElementById('folder-picker-dialog')?.classList.add('open');
}

async function selectReceivedDir(path) {
  const targetId = window._receivedDirTargetId || 'settings-received-dir';
  if (targetId === '__share_pick__') {
    closeFolderPicker();
    const cb = window._sharePickCallback;
    window._sharePickCallback = null;
    if (cb) await cb(path);
    else await startShareBrowse(path);
    return;
  }
  const setupTarget = targetId === 'setup-received-dir';
  try {
    const r = await fetch('/api/browse-dir', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({path})
    });
    const d = await r.json();
    if (r.ok && d.path) {
      const el = document.getElementById(targetId);
      if (el) el.value = d.path;
      closeFolderPicker();
      toast(setupTarget ? 'Folder selected' : 'Folder selected — click Save to apply');
      return;
    }
    toast('Browse failed: ' + (d.error || 'unknown'));
  } catch (_) {
    toast('Browse failed');
  }
}

async function pickSetupReceivedDir() {
  if (window.chatx5Android?.pickFolder) {
    window._folderPickTarget = 'setup';
    window.chatx5Android.pickFolder();
    return;
  }
  toast('Opening folder picker…');
  try {
    const r = await fetch('/api/browse-dir');
    const d = await r.json();
    if (d.platform === 'android' && Array.isArray(d.options) && d.options.length) {
      showFolderOptionsDialog(d.options, 'setup-received-dir');
      return;
    }
    if (r.ok && d.path) {
      document.getElementById('setup-received-dir').value = d.path;
      toast('Folder selected');
      return;
    }
    if (d.error === 'cancelled') {
      toast('Folder picker cancelled');
      return;
    }
    if (d.error) toast('Browse failed: ' + d.error);
  } catch (_) {
    toast('Browse failed');
  }
  if (window.showDirectoryPicker) {
    try {
      const handle = await window.showDirectoryPicker();
      document.getElementById('setup-received-dir').value = handle.name;
      toast('Enter the full absolute path if needed.');
      return;
    } catch (e) {
      if (e.name === 'AbortError') return;
    }
  }
}

async function pickReceivedDir() {
  if (window.chatx5Android?.pickFolder) {
    window.chatx5Android.pickFolder();
    return;
  }

  const os = detectClientPlatform();
  const pickerMsg = os === 'windows'
    ? 'Opening folder picker… a Windows dialog should appear (check the taskbar).'
    : os === 'darwin'
      ? 'Opening folder picker… a macOS dialog should appear.'
      : 'Opening folder picker…';
  toast(pickerMsg);
  try {
    const r = await fetch('/api/browse-dir');
    const d = await r.json();
    if (d.platform === 'android' && Array.isArray(d.options) && d.options.length) {
      showFolderOptionsDialog(d.options);
      return;
    }
    if (r.ok && d.path) {
      document.getElementById('settings-received-dir').value = d.path;
      toast('Folder selected — click Save to apply');
      return;
    }
    if (d.error === 'cancelled') {
      toast('Folder picker cancelled');
      return;
    }
    if (d.error) toast('Browse failed: ' + d.error);
  } catch (_) {
    toast('Browse failed');
  }

  if (window.showDirectoryPicker) {
    try {
      const handle = await window.showDirectoryPicker();
      document.getElementById('settings-received-dir').value = handle.name;
      toast('Only folder name available in browser — enter the full absolute path, then Save.');
      return;
    } catch (e) {
      if (e.name === 'AbortError') return;
    }
  }
  document.getElementById('dir-picker-fallback').click();
}
