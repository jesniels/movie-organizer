'use strict';
// Shared core of the web UI: state used by several features, the API helper, modal/result/toast
// helpers and HTML escaping. Loaded first — every other script in web/static/js/ relies on it.
// All scripts are classic (non-module) scripts sharing one global scope, so the inline
// onclick/onchange handlers in index.html can call their functions; load order is set in index.html.

// ── Shared state ──────────────────────────────────────────────────────────────
let allItems            = [];
let filteredItems       = [];
let selectedIds         = new Set();
let currentDetail       = null;    // item currently shown in detail modal
let configCache         = { locations: [], downloads: [] };
let activeSpecialFilter = null;    // null | 'duplicates' | 'missing-nfo' | 'missing-eps'
let duplicateMap        = new Map(); // item.id → [{ id, title, location, path }, ...]
let duplicateGroups     = [];      // [{ title, items: [...] }, ...] from /api/status
let dupGroupOf          = new Map(); // item.id → index into duplicateGroups

// ── Init ──────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  // Run the result modal's one-time on-close callback (see showResultModal)
  document.getElementById('dryrunModal').addEventListener('hidden.bs.modal', () => {
    const cb = resultModalOnClose;
    resultModalOnClose = null;
    if (cb) cb();
  });
});

// ── Bootstrap modal helpers ───────────────────────────────────────────────────
const getModal = id => bootstrap.Modal.getOrCreateInstance(document.getElementById(id));

// ── API helper ────────────────────────────────────────────────────────────────
async function api(method, path, body) {
  const opts = { method, headers: { 'Content-Type': 'application/json' } };
  if (body !== undefined) opts.body = JSON.stringify(body);
  const res = await fetch(path, opts);
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    const err = new Error(`${res.status}: ${text}`);
    err.status = res.status;
    err.path = path.split('?')[0];
    throw err;
  }
  return res.json();
}

// ── API errors ────────────────────────────────────────────────────────────────
// Extracts FastAPI's `detail` from errors thrown by api()
function _apiErr(e) {
  const m = /^\d+: ([\s\S]*)$/.exec(e.message);
  if (!m) return e.message;
  let detail = m[1];
  try {
    const d = JSON.parse(m[1]).detail;
    detail = typeof d === 'string' ? d : JSON.stringify(d);
  } catch (_) {}
  if (e.status === 404 && detail === 'Not Found') {
    return `the server has no endpoint ${e.path} (HTTP 404). The running server is older than this page — restart it (python py/movie-organizer-ui.py).`;
  }
  return `${detail} (HTTP ${e.status}, ${e.path})`;
}

// ── Formatting ────────────────────────────────────────────────────────────────
// Human-readable file size (B … TB); '–' when unknown
function fmtSize(bytes) {
  if (bytes === null || bytes === undefined) return '–';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let n = bytes, u = 0;
  while (n >= 1024 && u < units.length - 1) { n /= 1024; u++; }
  return n.toFixed(u === 0 ? 0 : 1) + ' ' + units[u];
}

// ── Action result handler ─────────────────────────────────────────────────────
function handleActionResults(results, dryRun, verb, pastVerb) {
  const ok   = results.filter(r => r.ok);
  const fail = results.filter(r => !r.ok);

  if (dryRun && ok.length) {
    const lines = ok.map(r => {
      if (r.to)   return `${r.from}\n  → ${r.to}`;
      if (r.path) return `Would ${verb}: ${r.path}`;
      return JSON.stringify(r);
    });
    showDryRunModal(lines);
  } else {
    let msg = `${pastVerb}: ${ok.length} item(s)`;
    if (fail.length) msg += ` · ${fail.length} failed`;
    showToast(msg, fail.length ? 'warning' : 'success');
  }
}

function showDryRunModal(lines) {
  showResultModal({ lines });
}

// Called once when the result modal closes (e.g. to return to the Move dialog).
let resultModalOnClose = null;

/**
 * Show the result modal.
 * @param {object}   opts
 * @param {string[]} opts.lines       detail lines, separated by a blank line
 * @param {string}   [opts.title]     modal title (default "Dry Run Result")
 * @param {boolean}  [opts.good]      true = green banner, false = red banner, undefined = no banner
 * @param {string}   [opts.status]    banner text (HTML)
 * @param {string}   [opts.closeText] close button label (default "Close")
 * @param {Function} [opts.onClose]   called once after the modal has closed
 * @param {object}   [opts.action]    extra button {html, onClick} — closes the modal, then runs onClick
 */
function showResultModal({ lines, title = 'Dry Run Result', good, status = '', closeText = 'Close', onClose = null,
                           action = null }) {
  const actBtn = document.getElementById('dryrun-action-btn');
  actBtn.classList.toggle('d-none', !action);
  actBtn.innerHTML = action ? action.html : '';
  actBtn.onclick = action ? () => { getModal('dryrunModal').hide(); action.onClick(); } : null;
  document.getElementById('dryrun-title').textContent = title;
  const banner = document.getElementById('dryrun-status');
  if (good === undefined) {
    banner.className = 'alert py-2 d-none';
  } else {
    banner.className = `alert py-2 ${good ? 'alert-success' : 'alert-danger'}`;
    banner.innerHTML = `<i class="bi ${good ? 'bi-check-circle-fill' : 'bi-x-octagon-fill'} me-2"></i>${status}`;
  }
  document.getElementById('dryrun-content').textContent = lines.join('\n\n');
  document.getElementById('dryrun-close-btn').textContent = closeText;
  resultModalOnClose = onClose;
  getModal('dryrunModal').show();
}

// ── In-dialog confirmation (no browser popups) ────────────────────────────────
/**
 * Two-step confirmation for a dangerous button inside a dialog. The first click shows `message`
 * in `noteEl` and relabels the button to `confirmHtml`; a second click within 10 seconds returns
 * true. Returns false on the first click. resetConfirm() cancels it (call it on any change).
 */
function confirmInDialog(btn, noteEl, message, confirmHtml) {
  if (btn.dataset.confirming === '1') {
    resetConfirm(btn, noteEl);
    return true;
  }
  btn.dataset.confirming = '1';
  btn.dataset.originalHtml = btn.innerHTML;
  btn.innerHTML = confirmHtml;
  noteEl.innerHTML = `<span class="confirm-note"><i class="bi bi-exclamation-triangle-fill me-1"></i>${esc(message)}`
    + ' Click the button again to confirm.</span>';
  btn._confirmTimer = setTimeout(() => resetConfirm(btn, noteEl), 10000);
  return false;
}

/** Cancel a pending confirmInDialog(): original button label back, warning removed. */
function resetConfirm(btn, noteEl) {
  if (!btn || btn.dataset.confirming !== '1') return;
  clearTimeout(btn._confirmTimer);
  btn.dataset.confirming = '';
  btn.innerHTML = btn.dataset.originalHtml;
  if (noteEl) noteEl.innerHTML = '';
}

// ── Toast ─────────────────────────────────────────────────────────────────────
function showToast(message, type = 'info') {
  const toastEl = document.getElementById('app-toast');
  toastEl.className = `toast align-items-center text-bg-${type} border-0`;
  document.getElementById('toast-body').textContent = message;
  bootstrap.Toast.getOrCreateInstance(toastEl, { delay: 5000 }).show();
}

// ── Escape HTML ───────────────────────────────────────────────────────────────
function esc(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}
