function seedPeerAliasesFromContacts(contacts) {
  (contacts || []).forEach(c => {
    if (c.identity_hash) registerPeerAliases(c.hash, c.identity_hash);
    if (c.lan_identity_hash) registerPeerAliases(c.lan_hash || c.hash, c.lan_identity_hash);
    if (c.serial_identity_hash && c.serial_hash) registerPeerAliases(c.serial_hash, c.serial_identity_hash);
  });
}

function rttForContact(contact) {
  const p = bestPeerForContact(window._discoveredPeers || [], contact);
  return formatRtt(
    p?.rtt_avg_ms || p?.rtt_ms || contact.rtt_avg_ms || contact.rtt_ms
  );
}

function contactLanHash(c) {
  const lan = (c.lan_hash || '').replace(/:/g, '');
  if (lan) return lan;
  const serial = (c.serial_hash || '').replace(/:/g, '');
  const legacy = (c.hash || '').replace(/:/g, '');
  if (legacy && legacy !== serial) return legacy;
  return '';
}

function contactSerialHash(c) {
  const serial = (c.serial_hash || '').replace(/:/g, '');
  const lan = contactLanHash(c);
  if (serial && lan && serial === lan) return '';
  return serial;
}

function isValidSerialDiscoveredPeer(p, lanPeer) {
  if (!p || (p.via || p.transport || '') !== 'serial') return false;
  const h = peerKey(p.hash);
  if (!h) return false;
  const lanH = lanPeer ? peerKey(lanPeer.hash) : '';
  if (lanH && h === lanH) return false;
  if ((p.ip || '').trim()) return false;
  return true;
}

function pickLanAndSerialPeers(hash) {
  const related = relatedDiscoveredPeers(hash);
  const lanPeer = related.find(p => (p.via || p.transport || '') !== 'serial') || null;
  const serialPeer = related.find(p => isValidSerialDiscoveredPeer(p, lanPeer)) || null;
  return { lanPeer, serialPeer, related };
}

function contactViewingActive(c) {
  if (!viewingPeer) return false;
  const via = normalizeVia(viewingVia);
  const targets = via === 'serial'
    ? [contactSerialHash(c)]
    : via === 'lan'
      ? [contactLanHash(c)]
      : [contactLanHash(c), contactSerialHash(c)];
  return targets.filter(Boolean).some(h => peersMatch(h, viewingPeer));
}

function discoveredForContactTransport(contact, via) {
  const target = via === 'serial' ? contactSerialHash(contact) : contactLanHash(contact);
  const peers = window._discoveredPeers || [];
  const wantVia = via === 'serial' ? 'serial' : 'lan';
  let match = peers.find(p => {
    const pVia = (p.via || '') === 'serial' ? 'serial' : 'lan';
    if (pVia !== wantVia) return false;
    return target && (peersMatch(p.hash, target) || peerKey(p.hash) === peerKey(target));
  });
  if (!match && wantVia === 'lan' && contact.ip) {
    match = peers.find(p => {
      const pVia = (p.via || '') === 'serial' ? 'serial' : 'lan';
      return pVia === 'lan' && p.ip === contact.ip;
    });
  }
  if (!match) {
    match = peers.find(p => {
      const pVia = (p.via || '') === 'serial' ? 'serial' : 'lan';
      if (pVia !== wantVia) return false;
      return contactMatchesPeer(contact, p);
    });
  }
  return match || null;
}

function rttForContactTransport(contact, via) {
  const p = discoveredForContactTransport(contact, via);
  const rtt = p?.rtt_avg_ms || p?.rtt_ms;
  if (via === 'lan' && !rtt) return formatRtt(contact.rtt_avg_ms || contact.rtt_ms);
  return formatRtt(rtt);
}

function renderContactTransportRow(contact, via) {
  const p = discoveredForContactTransport(contact, via);
  const hash = (p?.hash || (via === 'serial' ? contactSerialHash(contact) : contactLanHash(contact)));
  if (!hash) return '';
  const label = via === 'serial' ? 'USB' : 'LAN';
  const labelClass = via === 'serial' ? 'usb' : 'lan';
  const linked = isPeerLinked(hash, via);
  const rowActive = viewingPeer && peersMatch(viewingPeer, hash) && normalizeVia(viewingVia) === normalizeVia(via);
  let rttHint = rttForContactTransport(contact, via);
  if (via === 'serial' && linked && typeof serialQualityFromRtt === 'function') {
    const lk = linkKey(hash, 'serial');
    const q = (lk && linkQualityByKey[lk] != null)
      ? linkQualityByKey[lk]
      : serialQualityFromRtt(linkRttByKey[lk]);
    if (q != null) rttHint = `${q}% RF`;
  }
  const rttBadge = rttHint ? `<span class="peer-rtt">${escapeHtml(rttHint)}</span>` : '';
  const ipHint = via === 'lan' && (p?.ip || contact.ip) ? ` · ${escapeHtml(p?.ip || contact.ip)}` : '';
  return `<div class="contact-transport-row${rowActive ? ' active' : ''}" data-via="${via}">
    <span class="contact-transport-label ${labelClass}">${label}</span>
    <span class="contact-transport-hash">${truncateHash(hash)}${ipHint}${linked ? ' <span style="color:var(--success);font-size:10px">●</span>' : ''}${rttBadge}</span>
  </div>`;
}

function contactMatchesDiscoveryPeer(contact, peer) {
  if (!contact || !peer) return false;
  const isSerial = (peer.via || peer.transport || '') === 'serial';
  const peerH = peerKey(peer.hash);
  if (peerH) {
    const targets = isSerial
      ? [contact.serial_hash]
      : [contact.lan_hash, contact.hash];
    if (targets.map(peerKey).filter(Boolean).some(h =>
      h === peerH || peersMatch(h, peer.hash)
    )) return true;
  }
  if (peer.identity_hash) {
    const ident = peerKey(peer.identity_hash);
    const savedIdents = [
      contact.identity_hash,
      contact.lan_identity_hash,
      contact.serial_identity_hash,
    ].map(peerKey).filter(Boolean);
    if (savedIdents.some(h => h === ident || peersMatch(h, peer.identity_hash))) {
      return true;
    }
  }
  if (!isSerial && peer.ip && contact.ip && peer.ip === contact.ip) return true;
  return contactMatchesPeer(contact, peer);
}

function contactSavedHashes(c) {
  return [c.hash, c.lan_hash, c.serial_hash, c.identity_hash, c.lan_identity_hash, c.serial_identity_hash]
    .map(peerKey).filter(Boolean);
}

function isSavedContactHash(hash) {
  const key = peerKey(hash);
  if (!key) return false;
  return allContacts.some(c =>
    contactSavedHashes(c).some(h => h === key || peersMatch(h, hash))
  );
}

function viaForLinkedPeer(hash) {
  const h = peerKey(hash);
  if (!h) return null;
  for (const lp of linkedPeers) {
    const parts = String(lp).split(':');
    if (parts.length >= 2 && peersMatch(parts[0], h)) {
      return normalizeVia(parts[1]);
    }
  }
  return null;
}

function isDiscoveredPeerSaved(peer) {
  const peerH = peerKey(peer.hash);
  const peerIdent = peerKey(peer.identity_hash);
  const isSerial = (peer.via || peer.transport || '') === 'serial';
  return allContacts.some(c => {
    if (contactMatchesDiscoveryPeer(c, peer)) return true;
    const hashes = contactSavedHashes(c);
    if (hashes.some(h =>
      (peerH && (h === peerH || peersMatch(h, peer.hash)))
      || (peerIdent && (h === peerIdent || peersMatch(h, peer.identity_hash)))
    )) return true;
    const serialH = contactSerialHash(c);
    const lanH = contactLanHash(c);
    if (peerH && serialH && (peerH === serialH || peersMatch(peerH, serialH))) return true;
    if (peerH && lanH && !isSerial && (peerH === lanH || peersMatch(peerH, lanH))) return true;
    return false;
  });
}

function dedupeContacts(contacts) {
  const byKey = {};
  const keyToPrimary = {};
  (contacts || []).forEach(c => {
    const ident = peerKey(c.identity_hash);
    const lan = peerKey(c.lan_hash || c.hash);
    const serial = peerKey(c.serial_hash);
    const name = (c.name || '').trim().toLowerCase();
    const nameIsHash = name && (name === lan?.slice(0, 8) || name === serial?.slice(0, 8));
    const keys = [];
    if (ident) keys.push(`ident:${ident}`);
    if (lan || serial) keys.push(`h:${lan || ''}:${serial || ''}`);
    if (name && !nameIsHash) keys.push(`name:${name}`);
    if (!keys.length) keys.push(`orphan:${lan || serial || 'unknown'}`);
    let primary = null;
    for (const key of keys) {
      if (keyToPrimary[key]) {
        primary = keyToPrimary[key];
        break;
      }
    }
    if (!primary) {
      primary = keys[0];
      byKey[primary] = {...c};
      keys.forEach(k => { keyToPrimary[k] = primary; });
      return;
    }
    const merged = {...byKey[primary]};
    ['hash', 'lan_hash', 'serial_hash', 'identity_hash', 'lan_identity_hash',
      'serial_identity_hash', 'ip', 'port', 'name'].forEach(field => {
      if (!merged[field] && c[field]) merged[field] = c[field];
    });
    if (c.custom_name) merged.custom_name = true;
    byKey[primary] = merged;
    keys.forEach(k => { keyToPrimary[k] = primary; });
  });
  const merged = Object.values(byKey);
  const out = [];
  const used = new Set();
  merged.forEach((a, i) => {
    if (used.has(i)) return;
    let current = {...a};
    merged.forEach((b, j) => {
      if (j <= i || used.has(j)) return;
      const aLan = peerKey(current.lan_hash || current.hash);
      const aSerial = peerKey(current.serial_hash);
      const bLan = peerKey(b.lan_hash || b.hash);
      const bSerial = peerKey(b.serial_hash);
      const complementary = (
        (aLan && bSerial && !aSerial && !bLan)
        || (aSerial && bLan && !aLan && !bSerial)
        || (aSerial && (aSerial === bLan || aSerial === peerKey(b.hash)))
        || (bSerial && (bSerial === aLan || bSerial === peerKey(a.hash)))
      );
      if (!complementary) return;
      ['hash', 'lan_hash', 'serial_hash', 'identity_hash', 'lan_identity_hash',
        'serial_identity_hash', 'ip', 'port', 'name'].forEach(field => {
        if (!current[field] && b[field]) current[field] = b[field];
      });
      if (b.custom_name) current.custom_name = true;
      used.add(j);
    });
    out.push(current);
  });
  return out.length ? out : merged;
}

function renderContacts(contacts) {
  contacts = dedupeContacts(contacts);
  if (contacts === allContacts || !document.getElementById('contact-search')?.value) allContacts = contacts;
  seedPeerAliasesFromContacts(contacts);
  const el = document.getElementById('contacts-list');
  el.innerHTML = '';
  contacts.forEach(c => {
    const div = document.createElement('div');
    const name = contactDisplayName(c);
    const active = contactViewingActive(c);
    const lanHash = contactLanHash(c);
    const serialDisc = discoveredForContactTransport(c, 'serial');
    const serialHash = contactSerialHash(c) || (serialDisc ? peerKey(serialDisc.hash) : '');
    const unread = Math.max(
      unreadForPeer(lanHash),
      serialHash ? unreadForPeer(serialHash) : 0,
      unreadForPeer(c.hash),
    );
    const lanRow = renderContactTransportRow(c, 'lan');
    const serialRow = serialHash && serialHash !== lanHash ? renderContactTransportRow(c, 'serial') : '';
    const rows = lanRow + serialRow;
    const nameRtt = formatRtt(
      (discoveredForContactTransport(c, 'lan')?.rtt_avg_ms
        || discoveredForContactTransport(c, 'lan')?.rtt_ms
        || discoveredForContactTransport(c, 'serial')?.rtt_avg_ms
        || discoveredForContactTransport(c, 'serial')?.rtt_ms
        || c.rtt_avg_ms
        || c.rtt_ms)
    );
    const nameRttBadge = nameRtt ? `<span class="peer-rtt">${escapeHtml(nameRtt)}</span>` : '';
    div.className = 'contact-item contact-card' + (active ? ' active' : '');
    div.innerHTML = `<div class="contact-avatar contact-avatar-saved">${escapeHtml(name).charAt(0).toUpperCase()}</div>
      <div class="contact-info">
        <div class="contact-name">${escapeHtml(name)}${nameRttBadge}</div>
        ${rows
          ? `<div class="contact-transport-rows">${rows}</div>`
          : `<div class="contact-hash">${truncateHash(c.hash)}</div>`}
      </div>
      ${unread > 0 ? `<span class="contact-badge unread">${unread > 99 ? '99+' : unread}</span>` : ''}`;
    div.querySelectorAll('.contact-transport-row').forEach(row => {
      row.onclick = (e) => {
        e.stopPropagation();
        const via = row.dataset.via;
        const p = discoveredForContactTransport(c, via);
        const hash = (p?.hash || (via === 'serial' ? contactSerialHash(c) : contactLanHash(c)));
        openChat(hash, true, {
          via: via === 'serial' ? 'serial' : 'lan',
          ip: via === 'lan' ? (p?.ip || c.ip) : undefined,
          port: p?.port || c.port,
        });
      };
    });
    if (!rows) {
      div.onclick = () => openChat(c.hash, true, { ip: c.ip, port: c.port });
    }
    div.oncontextmenu = (e) => {
      e.preventDefault();
      showActionPanel(e, contactLanHash(c) || c.hash);
    };
    el.appendChild(div);
  });
  const cc = document.getElementById('contacts-count');
  if (cc) cc.textContent = contacts.length;
}

function renderDiscovered(peers, opts) {
  opts = opts || {};
  console.log('[render] Discovered peers:', peers);
  peers = peers || [];
  const prev = window._discoveredPeers || [];
  if (!peers.length && prev.length && !opts.authoritative) {
    const age = Date.now() - (window._discoveredPeersAt || 0);
    if (age < 15000) {
      console.log('[render] Keeping discovered peers during transient empty poll');
      return;
    }
  }
  const merged = mergeDiscoveredPeers(prev, peers);
  if (merged.length) window._discoveredPeersAt = Date.now();
  window._discoveredPeers = merged.length ? merged : peers;
  syncContactIpsFromDiscovered(window._discoveredPeers);
  const el = document.getElementById('discovered-list');
  el.innerHTML = '';
  let hasLink = linkedPeers.size > 0;
  let discovered = window._discoveredPeers
    ? window._discoveredPeers.filter(p =>
        !p.connected
        && !isDiscoveredPeerSaved(p)
        && !isLocalPeerHash(p.hash)
      )
    : [];
  let count = discovered.length;
  const linkCount = linkedPeers.size;
  document.getElementById('peer-count').textContent = count + ' peer' + (count !== 1 ? 's' : '') + (linkCount ? ' · ' + linkCount + ' linked' : '');
  const dc = document.getElementById('discovered-count');
  if (dc) dc.textContent = count;
  if (!discovered || discovered.length === 0) {
    el.innerHTML = '<div class="empty-state small"><span class="empty-icon">📡</span><span>No peers discovered yet</span><span class="empty-hint">Try Announce LAN or Serial</span></div>';
    if (!hasLink) return;
  }
  discovered.forEach(p => {
    if (p.identity_hash) registerPeerAliases(p.hash, p.identity_hash);
    const div = document.createElement('div');
    const isSerial = (p.via || p.transport || '') === 'serial';
    const pVia = isSerial ? 'serial' : 'lan';
    const active = peersMatch(p.hash, viewingPeer) && normalizeVia(viewingVia) === pVia;
    div.className = 'contact-item' + (active ? ' active' : '');
    const baseName = p.name || truncateHash(p.hash);
    const unread = unreadForPeer(p.hash);
    const linked = isPeerLinked(p.hash, isSerial ? 'serial' : 'lan');
    const transportLabel = isSerial ? 'USB' : 'LAN';
    const transportTag = isSerial ? ' · USB' : ' · LAN';
    const transportHint = isSerial ? '' : (p.ip ? ' · ' + escapeHtml(p.ip) : '');
    const rttHint = formatRtt(p.rtt_avg_ms || p.rtt_ms);
    const rttBadge = rttHint ? `<span class="peer-rtt">${escapeHtml(rttHint)}</span>` : '';
    div.innerHTML = `<div class="contact-avatar contact-avatar-lan">${escapeHtml(baseName).charAt(0).toUpperCase()}</div>
      <div class="contact-info">
        <div class="contact-name">${escapeHtml(baseName)}${transportTag}${linked ? ' <span style="color:var(--success);font-size:10px">●</span>' : ''}${rttBadge}</div>
        <div class="contact-hash">${truncateHash(p.hash)}${transportHint}</div>
      </div>
      ${unread > 0 ? `<span class="contact-badge unread">${unread > 99 ? '99+' : unread}</span>` : `<span class="contact-badge lan">${transportLabel}</span>`}`;
    div.onclick = () => openChat(p.hash, true, {
      ip: isSerial ? undefined : p.ip,
      port: p.port,
      via: p.via,
    });
    div.oncontextmenu = (e) => {
      e.preventDefault();
      showActionPanel(e, p.hash);
    };
    el.appendChild(div);
  });
}

let actionShown = false;
