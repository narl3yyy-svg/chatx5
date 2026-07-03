function peerKey(hash) {
  return (hash || '').replace(/:/g, '').toLowerCase();
}

function normalizeVia(via) {
  const v = (via || 'lan').toLowerCase();
  return v === 'serial' ? 'serial' : 'lan';
}

function linkKey(hash, via) {
  const h = peerKey(hash);
  if (!h) return '';
  return `${h}:${normalizeVia(via)}`;
}

function isCorruptContactName(name, hashes) {
  const raw = (name || '').trim();
  if (!raw) return true;
  if (/[\r\n]/.test(raw)) return true;
  if (raw.length > 48) return true;
  if (/\bLAN\b|\bUSB\b/i.test(raw)) return true;
  if (/\d+ms\b/i.test(raw)) return true;
  const compact = raw.replace(/[: ]/g, '');
  if (compact.length >= 20 && /^[0-9a-f]+$/i.test(compact)) return true;
  if (/[0-9a-f]{16,}/i.test(compact)) {
    const hexChars = (compact.match(/[0-9a-f]/gi) || []).length;
    if (hexChars >= Math.max(16, compact.length * 0.6)) return true;
  }
  const key = peerKey(raw);
  if (key && hashes && hashes.has(key)) return true;
  for (const h of hashes || []) {
    if (!h) continue;
    const hk = peerKey(h);
    if (key === hk || key === hk.slice(0, 8) || key === hk.slice(0, 12) || key.startsWith(hk.slice(0, 8))) return true;
  }
  return false;
}

function contactDisplayName(c) {
  const hashes = new Set(
    [c?.hash, c?.lan_hash, c?.serial_hash, contactLanHash(c), contactSerialHash(c)]
      .map(peerKey).filter(Boolean)
  );
  const raw = (c?.name || '').trim();
  const cleanRaw = isCorruptContactName(raw, hashes) ? '' : raw;
  if (c?.custom_name && cleanRaw) return cleanRaw;
  if (cleanRaw && !hashes.has(peerKey(cleanRaw)) && cleanRaw.length <= 48) return cleanRaw;
  const disc = bestPeerForContact(window._discoveredPeers || [], c);
  const discName = (disc?.name || '').trim();
  if (!cleanRaw && discName && !isCorruptContactName(discName, hashes) && !hashes.has(peerKey(discName))) return discName;
  if (cleanRaw && cleanRaw.length <= 16) return cleanRaw;
  return truncateHash(c?.lan_hash || c?.serial_hash || c?.hash || cleanRaw);
}

function isLocalPeerHash(hash) {
  const key = peerKey(hash);
  if (!key) return false;
  return [myHash, mySerialHash].map(peerKey).filter(Boolean).some(h => h === key || peersMatch(h, key));
}

function discoveredPeerExact(hash) {
  const key = peerKey(hash);
  if (!key) return null;
  return (window._discoveredPeers || []).find(p => peerKey(p.hash) === key) || null;
}

function relatedDiscoveredPeers(hash) {
  const seed = discoveredPeerExact(hash) || discoveredPeerForHash(hash);
  if (!seed) return [];
  const peers = window._discoveredPeers || [];
  const out = [];
  const seen = new Set();
  const add = (p) => {
    const k = peerMergeKey(p);
    if (!k || seen.has(k)) return;
    seen.add(k);
    out.push(p);
  };
  add(seed);
  const name = (seed.name || '').trim().toLowerCase();
  const ident = peerKey(seed.identity_hash);
  peers.forEach(p => {
    if (ident && peerKey(p.identity_hash) === ident) add(p);
    else if (name && namesRelated(p.name, seed.name)) add(p);
  });
  return out;
}

function discoveredPeerForHash(hash) {
  const key = peerKey(hash);
  if (!key) return null;
  const exact = discoveredPeerExact(hash);
  if (exact) return exact;
  return (window._discoveredPeers || []).find(p =>
    peerKey(p.identity_hash) === key
    || peersMatch(p.hash, hash)
    || peersMatch(p.identity_hash, hash)
  );
}

function isHubGroupLinked() {
  const role = window._hubRole || 'off';
  if (role === 'off') return false;
  if (window._hubGroupLinked) return true;
  if (role === 'server') return !!window._tcpHubOnline;
  return !!window._tcpClientOnline;
}

function hubGroupStatusLabel() {
  const role = window._hubRole || 'off';
  if (role === 'off') return 'Not connected';
  if (isHubGroupLinked()) {
    const n = window._hubClientsLinked || 0;
    if (role === 'server' && n > 0) return `Connected (${n} client${n === 1 ? '' : 's'})`;
    if (role === 'server') return 'Listening';
    return 'Connected';
  }
  if (role === 'server' && window._tcpHubOnline) return 'Listening';
  if (role === 'client' && window._tcpClientOnline) return 'Connecting…';
  return 'Not connected';
}

function isPeerLinked(hash, via) {
  if (hash === HUB_GROUP_PEER || peerKey(hash) === HUB_GROUP_PEER) {
    return isHubGroupLinked();
  }
  const h = peerKey(hash);
  if (!h) return false;
  const explicit = via ? normalizeVia(via) : (viewingVia ? normalizeVia(viewingVia) : null);
  const hashMatches = (base) => peersMatch(base, h);
  if (explicit) {
    if (linkedPeers.has(linkKey(h, explicit))) return true;
    for (const lp of linkedPeers) {
      const parts = String(lp).split(':');
      if (parts.length >= 2 && normalizeVia(parts[1]) === explicit && hashMatches(parts[0])) {
        return true;
      }
    }
    return false;
  }
  if (linkedPeers.has(h)) return true;
  for (const lp of linkedPeers) {
    const base = String(lp).split(':')[0];
    if (hashMatches(base)) return true;
  }
  for (const alias of peerAliasGroups.get(h) || []) {
    for (const lp of linkedPeers) {
      const base = String(lp).split(':')[0];
      if (peersMatch(base, alias)) return true;
    }
  }
  return false;
}

function syncLinkedPeers(list) {
  if (!Array.isArray(list)) return;
  linkedPeers = new Set(
    list.map(item => {
      const text = String(item || '').toLowerCase();
      if (!text) return '';
      if (text.includes(':')) return text;
      return peerKey(text);
    }).filter(Boolean)
  );
  updatePeerHeader();
  renderContacts(allContacts);
  renderDiscovered(window._discoveredPeers || []);
}

function registerPeerAliases(...hashes) {
  // Only merge alternate hashes for the SAME peer (identity vs message dest).
  // Never pass viewingPeer or unrelated contact hashes here.
  const keys = [...new Set(hashes.map(peerKey).filter(Boolean))];
  if (keys.length < 2) return;
  let group = null;
  for (const key of keys) {
    if (peerAliasGroups.has(key)) {
      group = peerAliasGroups.get(key);
      break;
    }
  }
  if (!group) group = new Set();
  keys.forEach(k => group.add(k));
  group.forEach(k => peerAliasGroups.set(k, group));
}

function peersMatch(a, b) {
  const ka = peerKey(a);
  const kb = peerKey(b);
  if (!ka || !kb) return false;
  if (ka === kb) return true;
  const ga = peerAliasGroups.get(ka);
  const gb = peerAliasGroups.get(kb);
  return !!(ga && gb && ga === gb);
}

function messageBelongsToPeer(data) {
  if (!viewingPeer) return false;
  const active = peerKey(viewingPeer);
  const isHubMsg = !!(data.hub_group || peerKey(data.chat_peer || data.peer) === HUB_GROUP_PEER);
  if (active === HUB_GROUP_PEER) return isHubMsg;
  if (isHubMsg) return false;
  const chatPeer = peerKey(data.chat_peer || data.peer);
  if (chatPeer) return peersMatch(chatPeer, active);
  if (data.sender && data.sender !== 'system') return peersMatch(data.sender, active);
  return data.type === 'system';
}
