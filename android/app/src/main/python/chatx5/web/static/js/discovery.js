function showActionPanel(e, hash) {
  ctxHash = hash;
  if (settingsShown) closeSettings();
  if (connectShown) { connectShown = false; document.getElementById('connect-panel').classList.remove('open'); }
  document.getElementById('action-panel').classList.add('open');
  document.getElementById('action-panel-title').textContent = hash;
  actionShown = true;
}

function toggleActionPanel() {
  actionShown = !actionShown;
  document.getElementById('action-panel').classList.toggle('open', actionShown);
}

function peerMetaForHash(hash) {
  const key = peerKey(hash);
  const contact = allContacts.find(c =>
    peerKey(c.hash) === key
    || peerKey(c.lan_hash) === key
    || peerKey(c.serial_hash) === key
    || peerKey(c.identity_hash) === key
    || peerKey(c.lan_identity_hash) === key
    || peerKey(c.serial_identity_hash) === key
    || peersMatch(c.hash, hash)
    || peersMatch(c.lan_hash, hash)
    || peersMatch(c.serial_hash, hash)
    || peersMatch(c.identity_hash, hash)
  );
  if (contact) {
    if (peerKey(contact.serial_hash) === key || peersMatch(contact.serial_hash, hash)) {
      const p = discoveredForContactTransport(contact, 'serial');
      return {
        hash: contact.serial_hash,
        via: 'serial',
        port: p?.port || contact.port || 8742,
        identity_hash: contact.serial_identity_hash || contact.identity_hash,
      };
    }
    const lanHash = contact.lan_hash || contact.hash;
    const p = discoveredForContactTransport(contact, 'lan');
    return {
      hash: lanHash,
      ip: contact.ip || p?.ip,
      port: p?.port || contact.port || 8742,
      identity_hash: contact.lan_identity_hash || contact.identity_hash,
      via: p?.via,
    };
  }
  return (window._discoveredPeers || []).find(p =>
    peerKey(p.hash) === key
    || peerKey(p.identity_hash) === key
    || peersMatch(p.hash, hash)
    || peersMatch(p.identity_hash, hash)
  );
}

function actionConnect() {
  if (ctxHash) {
    const p = peerMetaForHash(ctxHash);
    connectTo(ctxHash, p?.ip, p?.port, p?.via);
  }
  toggleActionPanel();
}

function openSaveContactDialog(hash) {
  document.getElementById('msg-input')?.blur();
  document.getElementById('save-contact-hash').value = hash;
  document.getElementById('save-contact-name').value = '';
  document.body.classList.add('modal-open');
  document.getElementById('save-contact-dialog').classList.add('open');
  requestAnimationFrame(() => {
    const nameInput = document.getElementById('save-contact-name');
    nameInput?.focus();
    nameInput?.select();
  });
}

function actionSaveContact() {
  if (ctxHash) openSaveContactDialog(ctxHash);
  toggleActionPanel();
}

function closeSaveContact() {
  document.body.classList.remove('modal-open');
  document.getElementById('save-contact-dialog').classList.remove('open');
}

function doSaveContact() {
  const hash = document.getElementById('save-contact-hash').value;
  const name = document.getElementById('save-contact-name').value.trim();
  if (!hash) return;
  if (isLocalPeerHash(hash)) {
    toast('Cannot save your own device hash as a contact');
    return;
  }
  const disc = discoveredPeerExact(hash) || discoveredPeerForHash(hash);
  let { lanPeer, serialPeer } = pickLanAndSerialPeers(hash);
  const meta = peerMetaForHash(hash) || {};
  const clean = hash.replace(/:/g, '');
  const existing = (allContacts || []).find(c => {
    const hashes = contactSavedHashes(c);
    return hashes.some(h => peersMatch(h, hash) || h === clean);
  });
  if (!lanPeer && existing) {
    const lanH = contactLanHash(existing);
    if (lanH) lanPeer = (window._discoveredPeers || []).find(p =>
      peerKey(p.hash) === lanH || peersMatch(p.hash, lanH)
    ) || { hash: lanH, name: existing.name, ip: existing.ip, port: existing.port, via: 'rns' };
  }
  if (!serialPeer && existing) {
    const serialH = contactSerialHash(existing);
    if (serialH) serialPeer = (window._discoveredPeers || []).find(p =>
      (p.via || '') === 'serial' && (peerKey(p.hash) === serialH || peersMatch(p.hash, serialH))
    ) || { hash: serialH, name: existing.name, via: 'serial' };
  }
  if (!lanPeer && !serialPeer && name) {
    const related = (window._discoveredPeers || []).filter(p =>
      namesRelated(p.name, name) || namesRelated(p.name, disc?.name)
    );
    lanPeer = lanPeer || related.find(p => (p.via || '') !== 'serial') || null;
    serialPeer = serialPeer || related.find(p => isValidSerialDiscoveredPeer(p, lanPeer)) || null;
  }
  const discVia = (disc?.via || disc?.transport || meta.via || '').toLowerCase();
  const via = discVia === 'serial' ? 'serial' : (discVia ? 'lan' : undefined);
  const dual = !!(lanPeer && serialPeer);
  const body = {
    name: name || disc?.name || serialPeer?.name || lanPeer?.name || existing?.name || meta.name || clean.substring(0, 12),
    port: disc?.port || lanPeer?.port || serialPeer?.port || meta.port || undefined,
    custom_name: !!name,
  };
  if (lanPeer) {
    body.lan_hash = peerKey(lanPeer.hash);
    body.hash = body.lan_hash;
    body.ip = lanPeer.ip || undefined;
    body.via = 'lan';
    if (lanPeer.identity_hash) {
      body.identity_hash = peerKey(lanPeer.identity_hash);
      body.lan_identity_hash = body.identity_hash;
    }
  } else if (via === 'lan') {
    body.lan_hash = clean;
    body.hash = clean;
    body.ip = disc?.ip || meta.ip || undefined;
    body.via = 'lan';
    if (disc?.identity_hash || meta.identity_hash) {
      body.identity_hash = peerKey(disc?.identity_hash || meta.identity_hash);
      body.lan_identity_hash = body.identity_hash;
    }
  } else {
    body.hash = clean;
  }
  if (serialPeer) {
    body.serial_hash = peerKey(serialPeer.hash);
    if (serialPeer.identity_hash) {
      body.serial_identity_hash = peerKey(serialPeer.identity_hash);
    }
    if (!dual) {
      body.hash = body.serial_hash;
      body.via = 'serial';
      delete body.ip;
    }
  } else if (via === 'serial' && isValidSerialDiscoveredPeer(disc, lanPeer)) {
    body.serial_hash = clean;
    body.hash = clean;
    body.via = 'serial';
  }
  fetch('/api/contacts', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body)
  }).then(r => r.json()).then(d => {
    if (d.status !== 'ok') {
      toast('Save failed: ' + (d.error || 'unknown'));
      return;
    }
    toast('Contact saved');
    closeSaveContact();
    if (d.contact) {
      allContacts = dedupeContacts([...(allContacts || []), d.contact]);
      renderContacts(allContacts);
      renderDiscovered(window._discoveredPeers || []);
    }
    fetchIdentity();
  }).catch(() => toast('Save failed'));
}

function actionClearPeerHistory() {
  if (ctxHash) clearPeerHistory(ctxHash);
  toggleActionPanel();
}

function actionDelete() {
  if (ctxHash) {
    const deletedKey = peerKey(ctxHash);
    fetch('/api/contacts/' + ctxHash.replace(/:/g, ''), { method: 'DELETE' })
      .then(r => r.json())
      .then(d => {
        if (d.status !== 'ok') {
          toast('Delete failed: ' + (d.error || ''));
          return;
        }
        if (viewingPeer && peersMatch(viewingPeer, ctxHash)) {
          closeChatView();
        }
        try {
          const saved = localStorage.getItem(LS_VIEWING_PEER);
          if (saved && peerKey(saved) === deletedKey) {
            localStorage.removeItem(LS_VIEWING_PEER);
          }
        } catch (_) {}
        allContacts = (allContacts || []).filter(c =>
          peerKey(c.hash) !== deletedKey &&
          peerKey(c.lan_hash) !== deletedKey &&
          peerKey(c.serial_hash) !== deletedKey
        );
        renderContacts(allContacts);
        toast('Contact deleted');
        fetchIdentity();
      })
      .catch(() => toast('Delete failed'));
  }
  toggleActionPanel();
}

function clearPeerHistory(peerHash) {
  const peer = (peerHash || '').replace(/:/g, '');
  if (!peer) return;
  const label = contactNameFor(peer) || truncateHash(peer);
  if (!confirm('Delete all messages with ' + label + '?')) return;
  const aliases = new Set([peerKey(peer)]);
  const contact = allContacts.find(c => contactSavedHashes(c).some(h => peersMatch(h, peer)));
  if (contact) contactSavedHashes(contact).forEach(h => aliases.add(h));
  fetch('/api/history/clear', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({peer, aliases: [...aliases]})
  }).then(r => r.json()).then(d => {
    if (d.status === 'ok') {
      if (viewingPeer && (peersMatch(viewingPeer, peer) || [...aliases].some(h => peersMatch(h, viewingPeer)))) {
        document.getElementById('messages').innerHTML = '';
      }
      toast('Chat history cleared');
    } else {
      toast('Failed to clear history');
    }
  }).catch(() => toast('Failed to clear history'));
}

function connectTo(hash, ip, port, via, opts) {
  opts = opts || {};
  const wake = !!opts.wake;
  const myGen = ++connectGeneration;
  if (connectInFlight) {
    connectInFlight = false;
  }
  if (isPeerLinked(hash, via) && !wake) {
    openChat(hash, false, {via});
    toast('Already connected');
    return;
  }
  if (!via) {
    const meta = peerMetaForHash(hash);
    via = meta?.via;
  }
  const targetVia = via ? normalizeVia(via) : null;
  if (
    viewingPeer && peersMatch(viewingPeer, hash)
    && viewingVia && targetVia
    && normalizeVia(viewingVia) !== targetVia
    && isPeerLinked(hash, viewingVia)
  ) {
    const prevVia = normalizeVia(viewingVia);
    setLinkPeer(hash, false, prevVia);
    fetch('/api/disconnect', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({peer: hash, via: prevVia}),
    }).catch(() => {});
  }
  if (via === 'serial') {
    ip = undefined;
  } else if (!ip) {
    const meta = peerMetaForHash(hash);
    if (meta?.via === 'serial') {
      ip = undefined;
    } else if (meta?.ip) {
      ip = meta.ip;
      port = meta.port || port;
    }
  }
  if (appPlatform === 'android' && !ip) {
    toast('Resolving peer IP from discovery...');
  }
  connectInFlight = true;
  toast(wake ? 'Waking peer & connecting...' : 'Connecting...');
  const body = {hash};
  if (ip) body.ip = ip;
  if (port) body.port = port;
  if (via) body.via = via;
  if (wake) body.wake = true;
  fetch('/api/connect', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body)
  }).then(r => r.json()).then(d => {
    if (myGen !== connectGeneration) return;
    if (d.status === 'ok') {
      const connected = d.hash || hash;
      if (connected !== hash) registerPeerAliases(connected, hash);
      setLinkPeer(connected, true, via);
      if (d.linked_peers) syncLinkedPeers(d.linked_peers);
      if (viewingPeer && peersMatch(viewingPeer, connected)) updatePeerHeader();
      else openChat(connected, false, {via});
      toast('Connected!');
    } else if (!(viewingPeer && isPeerLinked(viewingPeer, via))) {
      toast('Connection failed: ' + (d.error || 'unknown'));
    }
  }).catch(() => {
    if (myGen !== connectGeneration) return;
    if (!(viewingPeer && isPeerLinked(viewingPeer, via))) toast('Connection failed');
  })
    .finally(() => {
      if (myGen === connectGeneration) connectInFlight = false;
    });
}

let announceInFlight = false;

function announceSelf(transport) {
  if (announceInFlight) return;
  announceInFlight = true;
  const t = (transport || 'lan').toLowerCase();
  const viaLabel = t === 'serial' ? 'serial/USB' : 'LAN';
  toast('Starting ' + viaLabel + ' announce...');
  fetch('/api/announce', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({transport: t}),
  })
    .then(async r => {
      const d = await r.json();
      if (r.ok && d.status === 'ok') {
        let hint = '';
        if (t === 'serial') {
          hint = d.serial_port ? ` on ${d.serial_port}` : ' on USB serial';
          if (d.serial_announced) {
            hint += ' — USB RNS sent';
          } else {
            hint += ' — USB announce failed (check cable/port)';
          }
        } else if (d.broadcast) {
          hint = ` via ${d.broadcast}`;
        }
        const found = d.discovered_count ? `, ${d.discovered_count} peer(s) seen` : '';
        const debounced = d.debounced ? ' (debounced — wait before tapping again)' : '';
        toast('Announcing on ' + viaLabel + hint + found + debounced);
        fetch('/api/discover').then(r => r.json()).then(p => renderDiscovered(p.peers)).catch(() => {});
        refreshNetworkStatus();
        return;
      }
      toast('Announce failed: ' + (d.error || 'unknown'));
    })
    .catch(() => toast('Announce failed — server not reachable'))
    .finally(() => { announceInFlight = false; });
}

let discoverRefreshInFlight = false;

function refreshDiscoveredPeers() {
  if (discoverRefreshInFlight) return;
  discoverRefreshInFlight = true;
  const btn = document.getElementById('discovered-refresh-btn');
  if (btn) btn.classList.add('spinning');
  fetch('/api/discover/refresh', { method: 'POST' })
    .then(async r => {
      const d = await r.json();
      if (!r.ok || d.error) {
        toast('Refresh failed: ' + (d.error || 'unknown'));
        return;
      }
      renderDiscovered(d.peers || [], { authoritative: true });
      const n = d.count != null ? d.count : (d.peers || []).length;
      toast(n ? ('Refreshed — ' + n + ' peer(s)') : 'Refreshed — no peers found');
    })
    .catch(() => toast('Refresh failed — server not reachable'))
    .finally(() => {
      discoverRefreshInFlight = false;
      if (btn) btn.classList.remove('spinning');
    });
}

window.onChatx5FolderPicked = function(path) {
  if (!path) return;
  if (window._folderPickTarget === 'debug-export') {
    window._folderPickTarget = null;
    doExportDebugLogs(path);
    return;
  }
  if (window._folderPickTarget === 'setup') {
    const el = document.getElementById('setup-received-dir');
    if (el) el.value = path;
    window._folderPickTarget = null;
    toast('Folder selected');
    return;
  }
  document.getElementById('settings-received-dir').value = path;
  toast('Folder selected — click Save to apply');
};

window.onChatx5FolderPickError = function(message) {
  if (message) toast(message);
};

let settingsShown = false;

let connectShown = false;
