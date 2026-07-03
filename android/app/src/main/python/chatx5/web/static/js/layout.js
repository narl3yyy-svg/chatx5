function isMobileLayout() {
  return window.matchMedia('(max-width: 860px), (hover: none) and (pointer: coarse)').matches;
}

function isAndroidShell() {
  return appPlatform === 'android' || !!window.chatx5Android?.isAndroid?.();
}

function updateAndroidShellLayout() {
  if (!isAndroidShell()) {
    document.body.classList.remove('android-shell', 'android-chat-open');
    return;
  }
  document.body.classList.add('android-shell');
  document.body.classList.toggle('android-chat-open', !!viewingPeer);
}

function setSidebarOpen(open) {
  sidebarOpen = open;
  const sidebar = document.getElementById('sidebar');
  if (isAndroidShell()) {
    sidebar?.classList.remove('collapsed');
    document.body.classList.remove('sidebar-open', 'desktop-sidebar-collapsed');
    document.getElementById('sidebar-toggle')?.classList.toggle('active', open);
    return;
  }
  if (isMobileLayout()) {
    sidebar?.classList.toggle('collapsed', !open);
    document.body.classList.toggle('sidebar-open', open);
    document.body.classList.remove('desktop-sidebar-collapsed');
  } else {
    sidebar?.classList.remove('collapsed');
    document.body.classList.remove('sidebar-open');
    document.body.classList.toggle('desktop-sidebar-collapsed', !open);
    try { localStorage.setItem('desktopSidebarCollapsed', open ? '0' : '1'); } catch (_) {}
  }
  document.getElementById('sidebar-toggle')?.classList.toggle('active', open);
}

function closeSidebar() {
  if (isAndroidShell()) return;
  if (isMobileLayout() && sidebarOpen) setSidebarOpen(false);
}

function closeSidebarOnBackdrop(e) {
  if (e.target === e.currentTarget) closeSidebar();
}

function initMobileLayout() {
  if (isAndroidShell()) {
    setSidebarOpen(true);
    updateAndroidShellLayout();
    updateAndroidComposerMode();
    window.addEventListener('resize', () => {
      updateAndroidComposerMode();
      updateAndroidShellLayout();
    });
    return;
  }
  if (isMobileLayout()) {
    setSidebarOpen(false);
  } else {
    let collapsed = false;
    try { collapsed = localStorage.getItem('desktopSidebarCollapsed') === '1'; } catch (_) {}
    setSidebarOpen(!collapsed);
  }
  updateAndroidComposerMode();
  window.addEventListener('resize', () => {
    updateAndroidComposerMode();
    if (!isMobileLayout() && !isAndroidShell()) {
      document.body.classList.remove('sidebar-open');
      let collapsed = false;
      try { collapsed = localStorage.getItem('desktopSidebarCollapsed') === '1'; } catch (_) {}
      setSidebarOpen(!collapsed);
    } else if (isMobileLayout() && !sidebarOpen) {
      document.getElementById('sidebar')?.classList.add('collapsed');
    }
  });
}

function loadUnreadCounts() {
  try {
    unreadCounts = JSON.parse(localStorage.getItem(LS_UNREAD) || '{}') || {};
  } catch (_) {
    unreadCounts = {};
  }
}

function saveUnreadCounts() {
  try { localStorage.setItem(LS_UNREAD, JSON.stringify(unreadCounts)); } catch (_) {}
}

function unreadForPeer(hash) {
  const key = peerKey(hash);
  return unreadCounts[key] || 0;
}

function setUnreadForPeer(hash, count) {
  const key = peerKey(hash);
  if (count > 0) unreadCounts[key] = count;
  else delete unreadCounts[key];
  saveUnreadCounts();
}

function bumpUnread(hash) {
  const key = peerKey(hash);
  setUnreadForPeer(key, unreadForPeer(key) + 1);
}

function clearUnread(hash) {
  setUnreadForPeer(hash, 0);
}

function requestNotificationPermission() {
  if (!('Notification' in window)) return;
  if (Notification.permission === 'default') {
    Notification.requestPermission().catch(() => {});
  }
}

function showMessageNotification(peerHash, preview) {
  const name = contactNameFor(peerHash) || truncateHash(peerHash);
  const body = (preview || 'New message').substring(0, 120);
  const viewingThis = viewingPeer && peersMatch(viewingPeer, peerHash);
  if (viewingThis && !document.hidden) return;
  if (appPlatform === 'android') return;
  if (!('Notification' in window) || Notification.permission !== 'granted') return;
  try {
    const n = new Notification(name, { body, tag: 'chatx5-' + peerKey(peerHash) });
    n.onclick = () => { window.focus(); openChat(peerHash, false); n.close(); };
  } catch (_) {}
}

window.onChatx5OpenPeer = function(hash) {
  if (!hash) return;
  openChat(String(hash).replace(/:/g, ''), false);
};

function showRnsErrorBanner(message) {
  const banner = document.getElementById('rns-error-banner');
  const text = document.getElementById('rns-error-text');
  if (!banner || !text) return;
  text.textContent = message || 'Duplicate LAN interface or port 4242 in use.';
  banner.classList.add('show');
}

function hideRnsErrorBanner() {
  document.getElementById('rns-error-banner')?.classList.remove('show');
}

function pollRnsHealth(attempt) {
  const n = attempt || 0;
  fetch('/api/health')
    .then(r => r.json())
    .then(h => {
      if (h.rns_ready) {
        hideRnsErrorBanner();
        fetchIdentity();
        return;
      }
      if (h.rns_error) showRnsErrorBanner(h.rns_error);
      if (n < 30) setTimeout(() => pollRnsHealth(n + 1), 2000);
    })
    .catch(() => {
      if (n < 30) setTimeout(() => pollRnsHealth(n + 1), 2000);
    });
}

function repairNetworkInterfaces() {
  fetch('/api/network/repair', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'})
    .then(r => r.json())
    .then(d => {
      if (d.status === 'ok') {
        toast('Interfaces repaired — restarting...');
        restartServer();
      } else {
        toast('Repair failed: ' + (d.error || 'unknown'));
      }
    })
    .catch(() => toast('Repair failed'));
}
