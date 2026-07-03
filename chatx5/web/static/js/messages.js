function pickFiles() {
  const input = document.getElementById('file-input');
  if (!input) return;
  input.click();
}

function pickCamera() {
  const input = document.getElementById('camera-input');
  if (!input) return;
  input.click();
}

function pickSendFolder() {
  if (window.chatx5Android?.pickSendFolder) {
    window.chatx5Android.pickSendFolder();
    return;
  }
  document.getElementById('folder-input').click();
}

window.onChatx5FolderSendOk = function(name, size) {
  toast('Sent ' + name + ' (' + formatSize(size) + ')');
};
window.onChatx5FolderSendError = function(msg) {
  toast('Folder send failed: ' + (msg || 'unknown'));
};

function isSessionSystemMessage(data) {
  if (data.type !== 'system' && data.sender !== 'system') return false;
  const c = data.content || '';
  return c.startsWith('Link established with ') || c.includes('Link closed') || c.startsWith('Connected to ');
}

function parseShareOfferContent(content) {
  if (!content || typeof content !== 'string') return null;
  const trimmed = content.trim();
  if (!trimmed.startsWith('{')) return null;
  try {
    const offer = JSON.parse(trimmed);
    if (offer && offer.session_id && offer.token) return offer;
  } catch (_) {}
  return null;
}

function addMessage(data, opts) {
  const scroll = !opts || opts.scroll !== false;
  const msgs = document.getElementById('messages');
  if (msgs.style.display === 'none' || !viewingPeer) return;
  if (isSessionSystemMessage(data)) return;
  if (!messageBelongsToPeer(data)) return;

  if (data.msg_id) {
    const existing = document.querySelector(`.msg[data-msgid="${data.msg_id}"]`);
    if (existing) {
      const statusEl = existing.querySelector('.receipt');
      if (statusEl) statusEl.textContent = receiptIcon(data.status || '');
      if (data.status === 'received' || data.status === 'read') {
        data.type === 'text' && !data.outgoing && sendReadReceipt(data.msg_id);
      }
      return;
    }
  }

  const div = document.createElement('div');
  const isSelf = data.outgoing === true;
  const isSystem = data.sender === 'system' || data.type === 'system';
  div.className = 'msg msg-enter ' + (isSystem ? 'system' : isSelf ? 'self' : 'other');
  if (data.msg_id) div.setAttribute('data-msgid', data.msg_id);
  const time = new Date(data.timestamp * 1000).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'});
  const copyable = data.type === 'text' || data.type === 'emoji';
  let html = '';
  if (data.msg_id && !isSystem) {
    html += '<div class="msg-actions">';
    if (copyable) html += '<button class="msg-action-btn copy-btn" onclick="event.stopPropagation();copyMsgText(this)" title="Copy text">📋</button>';
    html += `<button class="msg-action-btn delete-btn" onclick="event.stopPropagation();deleteMessage('${data.msg_id}')" title="Delete message">🗑</button></div>`;
  }
  const isGroupChat = viewingPeer === HUB_GROUP_PEER || data.hub_group;
  if (isGroupChat && !isSelf && !isSystem) {
    const senderLabel = data.sender_name || contactNameFor(data.sender) || truncateHash(data.sender);
    if (senderLabel) {
      html += `<div class="sender-label">${escapeHtml(senderLabel)}</div>`;
    }
  }
  if (data.type === 'text' || data.type === 'emoji') {
    html += `<span class="msg-text">${linkify(escapeHtml(data.content))}</span>`;
  } else if (data.type === 'image') {
    html += `<div>📷 ${escapeHtml(data.file_name || 'Image')}</div>`;
    if (data.content) {
      const url = fileUrl(data.content, data);
      if (url) {
        html += `<img class="msg-img" src="${url}" onclick="showImage(this.src)" onerror="this.style.display='none'">`;
        html += `<div style="margin-top:2px"><a href="${url}" download="${escapeHtml(data.file_name || 'image')}" style="color:var(--primary);font-size:11px">💾 Save image</a></div>`;
      } else {
        html += `<div style="margin-top:4px;color:var(--text3);font-size:12px">Image saved locally</div>`;
      }
    }
  } else if (data.type === 'video' || (data.type === 'file' && isVideoFile(data.file_name))) {
    div.classList.add('video-msg');
    html += renderVideoMessage(data);
  } else if (data.type === 'voice') {
    div.classList.add('voice-msg');
    html += `<div>🎤 ${escapeHtml(data.file_name || 'Voice note')}</div>`;
    if (data.content) {
      const url = fileUrl(data.content, data);
      if (url) html += `<div class="voice-wrap"><audio class="voice-player" controls preload="metadata" src="${url}"></audio></div>`;
    }
  } else if (data.type === 'file') {
    const sz = data.file_size ? formatSize(data.file_size) : '';
    html += `<div class="file-info"><span class="file-icon">📄</span><span>${escapeHtml(data.file_name || 'File')} ${sz}</span></div>`;
    if (data.content) {
      const url = fileUrl(data.content, data);
      html += `<div style="margin-top:4px"><a href="${url}" download="${escapeHtml(data.file_name || 'file')}" style="color:var(--primary);font-size:12px">Download</a></div>`;
    }
  } else if (data.type === 'share_browse' || (data.type === 'text' && parseShareOfferContent(data.content))) {
    const share = data.share || parseShareOfferContent(data.content) || {};
    try {
      if (!share.session_id && data.content) Object.assign(share, JSON.parse(data.content));
    } catch (_) {}
    const root = escapeHtml(share.root_name || data.file_name || 'Shared folder');
    const shareKey = 'share-' + (data.msg_id || Date.now());
    window._shareOffers = window._shareOffers || {};
    window._shareOffers[shareKey] = share;
    html += `<div class="file-info"><span class="file-icon">📂</span><span>${root}</span></div>`;
    html += `<div style="margin-top:6px"><button class="dlg-btn primary" style="font-size:12px;padding:6px 12px" onclick="openShareBrowser('${shareKey}')">Browse folder</button></div>`;
  } else if (data.type === 'system') {
    html += escapeHtml(data.content);
  }
  const icon = receiptIcon(data.status || (isSelf && data.type !== 'system' ? 'sending' : ''));
  html += `<div class="time">${time}${icon ? ' <span class="receipt" style="font-size:11px;margin-left:4px">' + icon + '</span>' : ''}</div>`;
  div.innerHTML = html;
  msgs.appendChild(div);
  if (scroll) msgs.scrollTop = msgs.scrollHeight;

  if (isSelf && data.msg_id && (data.status === 'received' || data.status === 'read')) {
    data.type === 'text' && sendReadReceipt(data.msg_id);
  }
}

function toggleEmoji() {
  const picker = document.getElementById('emoji-picker');
  const open = picker.classList.toggle('open');
  if (open) {
    const search = document.getElementById('emoji-search');
    if (search) {
      search.value = '';
      filterEmojiPicker('');
      search.focus();
    }
  }
}

function emojiSearchTerms(index, emoji) {
  const terms = [EMOJI_KEYWORD_MAP[emoji] || '', EMOJI_EXTRA_TERMS[emoji] || ''];
  EMOJI_SEARCH_GROUPS.forEach(g => {
    if (index >= g.from && index <= g.to) terms.push(g.terms);
  });
  return terms.join(' ').trim();
}

async function loadEmojiKeywords() {
  try {
    const r = await fetch('/static/emoji-keywords.json');
    if (r.ok) EMOJI_KEYWORD_MAP = await r.json();
  } catch (_) {}
  buildEmojiPicker();
}

function filterEmojiPicker(query) {
  const q = (query || '').trim().toLowerCase();
  const words = q ? q.split(/\s+/).filter(Boolean) : [];
  const items = document.querySelectorAll('#emoji-grid .emoji-item');
  let visible = 0;
  items.forEach(el => {
    const hay = (el.dataset.search || '').toLowerCase();
    const glyph = (el.textContent || '').trim();
    const show = !words.length || words.every(w => hay.includes(w) || glyph.includes(w));
    el.style.display = show ? '' : 'none';
    if (show) visible++;
  });
  let empty = document.getElementById('emoji-empty');
  if (!empty) {
    empty = document.createElement('div');
    empty.id = 'emoji-empty';
    empty.className = 'emoji-empty';
    document.getElementById('emoji-grid')?.appendChild(empty);
  }
  empty.style.display = visible ? 'none' : 'block';
  empty.textContent = visible ? '' : 'No emoji match';
}

function buildEmojiPicker() {
  const grid = document.getElementById('emoji-grid');
  if (!grid) return;
  grid.innerHTML = '';
  EMOJIS.forEach((e, idx) => {
    const span = document.createElement('span');
    span.className = 'emoji-item';
    span.textContent = e;
    span.dataset.search = emojiSearchTerms(idx, e);
    span.onclick = () => {
      const input = document.getElementById('msg-input');
      input.value += e;
      input.focus();
      onComposerInput(input);
    };
    grid.appendChild(span);
  });
}

let voiceChunks = [];
function toggleVoice() {
  if (recording) { stopVoice(); } else { startVoice(); }
}

let pendingVoiceAfterPermission = false;

function setVoiceRecordingUi(on) {
  ['voice-btn', 'voice-btn-mobile'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.classList.toggle('recording', on);
  });
}

function pickVoiceRecorderMime() {
  const candidates = [
    'audio/webm;codecs=opus',
    'audio/webm',
    'audio/ogg;codecs=opus',
    'audio/mp4',
    'audio/aac',
    ''
  ];
  for (const mime of candidates) {
    if (!mime || (window.MediaRecorder && MediaRecorder.isTypeSupported(mime))) {
      return mime;
    }
  }
  return '';
}

window.onChatx5AudioPermissionGranted = function() {
  if (pendingVoiceAfterPermission) {
    pendingVoiceAfterPermission = false;
    startVoice(true);
  }
  refreshNetworkStatus();
};

function micBrowserLabel() {
  const ua = navigator.userAgent || '';
  if (/Firefox/i.test(ua)) return 'Firefox';
  if (/Helium/i.test(ua)) return 'Helium';
  if (/Edg\//i.test(ua)) return 'Edge';
  if (/Chrome/i.test(ua)) return 'Chrome';
  if (/Safari/i.test(ua) && !/Chrome/i.test(ua)) return 'Safari';
  return 'your browser';
}

function firefoxMicHint() {
  return 'Firefox → Settings → Privacy & Security → Permissions → Microphone → allow http://localhost:8742';
}

function micAccessHint(err) {
  const os = detectClientPlatform();
  const browser = micBrowserLabel();
  const localUrl = os === 'darwin' ? 'http://localhost:8742' : 'http://127.0.0.1:8742';
  if (!window.isSecureContext) {
    return `Microphone needs a secure page — open ${localUrl} (not a raw LAN IP in ${browser})`;
  }
  const name = err?.name || '';
  if (name === 'NotFoundError' || name === 'DevicesNotFoundError') {
    if (os === 'darwin') {
      if (browser === 'Firefox') {
        return `No microphone — allow mic for localhost in Firefox (${firefoxMicHint()}) and enable Firefox under System Settings → Privacy & Security → Microphone`;
      }
      return 'No microphone found — check System Settings → Sound → Input, then Privacy & Security → Microphone for your browser';
    }
    if (os === 'windows') return 'No microphone found — plug one in or enable it in Windows Sound settings';
    if (os === 'linux') return 'No microphone found — check your audio input in system sound settings';
    return 'No microphone found on this device';
  }
  if (name === 'NotReadableError' || name === 'TrackStartError') {
    return 'Microphone busy — close other apps using it, then try again';
  }
  if (name === 'NotAllowedError' || name === 'PermissionDeniedError' || name === 'SecurityError') {
    if (os === 'darwin') {
      const ff = browser === 'Firefox' ? ` ${firefoxMicHint()}.` : '';
      return `Microphone blocked — allow mic for this site in ${browser}${ff} Then System Settings → Privacy & Security → Microphone → enable ${browser}`;
    }
    if (os === 'windows') {
      return `Microphone blocked — in ${browser} click the lock/site icon → Allow microphone. Also Windows Settings → Privacy & security → Microphone → allow desktop apps.`;
    }
    if (os === 'linux') {
      return `Microphone blocked — allow mic for this site in ${browser} and check system audio/privacy settings`;
    }
    return `Microphone blocked — allow mic for this site in ${browser}`;
  }
  if (os === 'darwin') {
    return `Microphone access denied — allow mic in ${browser} and macOS Privacy & Security → Microphone`;
  }
  if (os === 'windows') {
    return `Microphone access denied — allow mic in ${browser} and Windows Privacy settings`;
  }
  return `Microphone access denied — allow mic for this site in ${browser}`;
}

async function queryMicPermissionState() {
  if (!navigator.permissions?.query) return null;
  try {
    const status = await navigator.permissions.query({name: 'microphone'});
    return status?.state || null;
  } catch (_) {
    return null;
  }
}

async function startVoice(skipPermissionCheck) {
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    toast('Voice recording not supported in this browser');
    return;
  }
  if (!window.isSecureContext) {
    toast(micAccessHint({name: 'SecurityError'}));
    return;
  }
  if (!skipPermissionCheck && window.chatx5Android && !window.chatx5Android.hasAudioPermission()) {
    pendingVoiceAfterPermission = true;
    window.chatx5Android.requestAudioPermission();
    toast('Allow microphone access to record');
    return;
  }
  if (!skipPermissionCheck) {
    const perm = await queryMicPermissionState();
    if (perm === 'denied') {
      toast(micAccessHint({name: 'NotAllowedError'}));
      return;
    }
  }
  const mimeType = pickVoiceRecorderMime();
  const recorderOpts = mimeType ? {mimeType} : {};
  const richAudio = {
    audio: {
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    },
  };
  const requestMic = () => navigator.mediaDevices.getUserMedia(richAudio)
    .catch(err => {
      if (err?.name === 'NotFoundError' || err?.name === 'OverconstrainedError') {
        return navigator.mediaDevices.getUserMedia({audio: true});
      }
      throw err;
    });
  requestMic()
    .then(stream => {
      recording = true;
      voiceChunks = [];
      setVoiceRecordingUi(true);
      toast('Recording... tap 🎤 to stop');
      try {
        mediaRecorder = new MediaRecorder(stream, recorderOpts);
      } catch (err) {
        mediaRecorder = new MediaRecorder(stream);
      }
      const blobType = mediaRecorder.mimeType || mimeType || 'audio/webm';
      mediaRecorder.ondataavailable = e => { if (e.data.size > 0) voiceChunks.push(e.data); };
      mediaRecorder.onstop = () => {
        stream.getTracks().forEach(t => t.stop());
        const blob = new Blob(voiceChunks, {type: blobType});
        const reader = new FileReader();
        reader.onloadend = () => {
          const b64 = reader.result.split(',')[1];
          fetch('/api/voice', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({audio: b64, peer: viewingPeer || ''})
          }).then(r => r.json()).then(d => {
            if (d.status === 'ok') toast('Voice sent');
            else toast('Voice send failed');
          });
        };
        reader.readAsDataURL(blob);
      };
      mediaRecorder.start();
    })
    .catch(err => {
      console.warn('getUserMedia failed', err);
      if (window.chatx5Android && !window.chatx5Android.hasAudioPermission()) {
        pendingVoiceAfterPermission = true;
        window.chatx5Android.requestAudioPermission();
        toast('Allow microphone access to record');
      } else if (window.chatx5Android && window.chatx5Android.openAppSettings) {
        toast('Microphone blocked — open Settings to allow');
        window.chatx5Android.openAppSettings();
      } else {
        toast(micAccessHint(err));
      }
    });
}

function stopVoice() {
  recording = false;
  setVoiceRecordingUi(false);
  if (mediaRecorder && mediaRecorder.state !== 'inactive') {
    mediaRecorder.stop();
  }
}

function uploadFiles(files) {
  const peerQ = viewingPeer ? ('?peer=' + encodeURIComponent(viewingPeer)) : '';
  Array.from(files).forEach(file => {
    const formData = new FormData();
    formData.append('file', file);
    fetch('/api/file' + peerQ, {method: 'POST', body: formData})
      .then(r => r.json()).then(d => {
        if (d.status === 'ok') toast(`Sent ${d.name}`);
        else if (d.status === 'queued') toast(`Queued ${d.name} (${d.size})`);
        else toast('Upload failed: ' + (d.error || ''));
      });
  });
}

function uploadFolder(files) {
  const total = files.length;
  if (total === 0) return;
  const folderPath = files[0].webkitRelativePath || files[0].name;
  const folderName = folderPath.split('/')[0];
  if (!confirm(`Compress "${folderName}" (${total} files) and send as ${folderName}.zip?`)) return;
  const formData = new FormData();
  Array.from(files).forEach(file => {
    const path = file.webkitRelativePath || file.name;
    formData.append('file', file, path);
  });
  showProgress({stage: 'zipping', file_name: folderName + '.zip', progress: 0, direction: 'send', status: 'active', current: 0, total: total});
  const peerQ = viewingPeer ? ('?peer=' + encodeURIComponent(viewingPeer) + '&') : '?';
  fetch('/api/folder' + peerQ + 'name=' + encodeURIComponent(folderName), {method: 'POST', body: formData})
    .then(r => r.json())
    .then(d => {
      if (d.status === 'ok') toast(`Sent ${d.name} (${formatSize(d.size)})`);
      else if (d.status === 'queued') toast(`Queued ${d.name} (${formatSize(d.size)})`);
      else toast('Folder upload failed: ' + (d.error || ''));
    })
    .catch(() => toast('Folder upload failed'));
}

function linkify(text) {
  const urlRe = /(https?:\/\/[^\s<]+[^\s<.,;:!?)\]'"])/g;
  return text.replace(urlRe, '<a href="$1" target="_blank" rel="noopener noreferrer" class="msg-link">$1</a>');
}
