function formatSize(bytes) {
  if (!bytes) return '';
  const units = ['B', 'KB', 'MB', 'GB'];
  let i = 0; let size = bytes;
  while (size >= 1024 && i < units.length - 1) { size /= 1024; i++; }
  return size.toFixed(1) + units[i];
}

function escapeHtml(str) {
  if (!str) return '';
  return str.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function safeJsonParse(text) {
  if (text == null) return null;
  if (typeof text === 'object') return text;
  const trimmed = String(text).trim();
  if (!trimmed) return null;
  try {
    return JSON.parse(trimmed);
  } catch (_) {
    if (trimmed.startsWith('"') && trimmed.endsWith('"')) {
      try {
        const inner = JSON.parse(trimmed);
        if (typeof inner === 'string') return safeJsonParse(inner);
      } catch (_e) {}
    }
    return null;
  }
}

async function readJsonResponse(r) {
  const text = await r.text();
  if (!text || !text.trim()) return {};
  const data = safeJsonParse(text);
  if (data == null) throw new Error(text.slice(0, 120) || `HTTP ${r.status}`);
  return data;
}

let toastTimer = null;
function toast(msg) {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('show'), 3000);
}
