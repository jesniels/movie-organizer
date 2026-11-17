'use strict';
// Leftover folders: the sidebar row, the Leftover folders dialog (list, expand, All/None, dry run,
// in-dialog confirmation), the "N leftover folders — Review…" link of action results, and the
// Settings "Leftover folders" tab (its code lives here, not in settings.js — ADR 0004; settings.js calls
// fillLeftoverSettings / readLeftoverSettings / leftoverSettingsError). The scan only lists the folders;
// nothing is deleted without a run here.

// ── Leftover state ────────────────────────────────────────────────────────────
let leftoverFolders = [];          // GET /api/leftovers: [{ path, root, location, files, hidden, label }]
let leftoverTicked  = new Set();   // paths ticked in the dialog — nothing by default
let leftoverOpen    = new Set();   // paths whose full file list is shown
let leftoverBusy    = false;       // a delete request is running — the dialog stays open

// Mirror DEFAULT_LEFTOVER and VIDEO_EXTS in py/ui/settings.py (the server re-validates on save)
const _LEFTOVER_DEFAULTS = {
  jellyfin_files:    ['.nfo', '.jpg', '.jpeg', '.png'],
  subtitle_files:    ['.srt', '.sub', '.idx', '.ass', '.ssa', '.vtt', '.sup'],
  ignored_files:     ['Thumbs.db', 'desktop.ini', '.DS_Store'],
  jellyfin_folders:  ['extrafanart', 'extrathumbs', '*.trickplay'],
  thumbnail_folders: ['@eaDir', '.@__thumb'],
  system_folders:    ['$RECYCLE.BIN', 'System Volume Information', '#recycle'],
};
const _LEFTOVER_VIDEO_EXTS = ['.mkv', '.mp4', '.avi', '.m4v', '.mov', '.wmv'];
const _LEFTOVER_FILE_LISTS = ['jellyfin_files', 'subtitle_files', 'ignored_files'];
const _leftoverInputId = key => 'lo-' + key.replace('_', '-');

// ── Init ──────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  const list = document.getElementById('lo-list');
  list.addEventListener('change', e => {
    if (!e.target.classList.contains('lo-tick')) return;
    const path = leftoverFolders[+e.target.dataset.idx].path;
    if (e.target.checked) leftoverTicked.add(path); else leftoverTicked.delete(path);
    _leftoverCancelConfirm();
    _leftoverRunLabel();
  });
  list.addEventListener('click', e => {
    const btn = e.target.closest('[data-lo-expand]');
    if (!btn) return;
    const path = leftoverFolders[+btn.dataset.loExpand].path;
    if (leftoverOpen.has(path)) leftoverOpen.delete(path); else leftoverOpen.add(path);
    _renderLeftovers();
  });
  document.getElementById('lo-dryrun').addEventListener('change', _leftoverCancelConfirm);
  const modal = document.getElementById('leftoverModal');
  modal.addEventListener('hide.bs.modal', e => { if (leftoverBusy) e.preventDefault(); });
  modal.addEventListener('hidden.bs.modal', _leftoverCancelConfirm);
});

// ── Fetch and sidebar ─────────────────────────────────────────────────────────
/** Load the current leftover folders (called with every /api/status fetch) and refresh the sidebar row. */
async function fetchLeftovers() {
  try {
    leftoverFolders = await api('GET', '/api/leftovers');
  } catch (_) {
    return;
  }
  const known = new Set(leftoverFolders.map(f => f.path));
  leftoverTicked = new Set([...leftoverTicked].filter(p => known.has(p)));
  if (lastStatus) renderStatusPanel(lastStatus);
}

/** Sidebar row below the special filters that opens the Leftover folders dialog (not a filter). */
function leftoversRowHtml() {
  const n = leftoverFolders.length;
  return `
    <div class="special-filter-row mt-1" onclick="openLeftovers()" title="Folders left behind without a video — review and delete">
      <i class="bi bi-folder-x me-2"></i>
      <span>Leftover folders</span>
      <span class="ms-auto badge ${n ? 'bg-warning-subtle text-warning-emphasis' : 'bg-secondary-subtle text-secondary-emphasis'}">${n}</span>
    </div>`;
}

// ── Review link for action results ────────────────────────────────────────────
/** The folders of `paths` that do not lie inside another one of them. */
function _leftoverTopmost(paths) {
  const keys = paths.map(_normRoot);
  return paths.filter((p, i) => !keys.some((k, j) => j !== i && (keys[i].startsWith(k + '\\') || keys[i].startsWith(k + '/'))));
}

/**
 * Result-dialog button "N leftover folders — Review…" for the results of a Move, Delete or bundle move
 * (each result's `leftovers` = folders that action left behind), or null when there are none.
 */
function leftoverReviewAction(results) {
  const n = _leftoverTopmost([...new Set(results.flatMap(r => r.leftovers || []))]).length;
  if (!n) return null;
  fetchLeftovers();
  return { html: `<i class="bi bi-folder-x me-1"></i>${n} leftover folder(s) — Review…`, onClick: () => openLeftovers() };
}

/** The same as a link (HTML) for the Reorganize footer; opening it returns to `fromId` when closed. */
function leftoverReviewLink(results, fromId) {
  const n = _leftoverTopmost([...new Set(results.flatMap(r => r.leftovers || []))]).length;
  if (!n) return '';
  fetchLeftovers();
  return ` <a href="#" class="link-warning" onclick="openLeftovers('${fromId}'); return false;">`
    + `<i class="bi bi-folder-x me-1"></i>${n} leftover folder(s) — Review…</a>`;
}

// ── Dialog ────────────────────────────────────────────────────────────────────
/** Open the Leftover folders dialog; with `fromId` (another dialog) closing it returns there. */
async function openLeftovers(fromId = null) {
  leftoverTicked = new Set();
  leftoverOpen = new Set();
  document.getElementById('lo-dryrun').checked = true;
  document.getElementById('lo-confirm-note').innerHTML = '';
  await fetchLeftovers();
  _renderLeftovers();
  if (fromId) leaveModalFor(fromId, 'leftoverModal');
  getModal('leftoverModal').show();
}

const _leftoverName = p => p.split(/[\\/]/).pop();

function _renderLeftovers() {
  const list = document.getElementById('lo-list');
  if (!leftoverFolders.length) {
    list.innerHTML = '<div class="text-muted fst-italic">No leftover folders. Every scan lists them, and Move, Delete, '
      + 'bundle moves and Reorganize add the ones they leave behind.</div>';
    _leftoverRunLabel();
    return;
  }
  list.innerHTML = leftoverFolders.map((f, i) => {
    const hidden = new Set(f.hidden || []);
    const shown = f.files.filter(x => !hidden.has(x));
    const names = shown.slice(0, 4).map(_leftoverName).join(', ') + (shown.length > 4 ? `, … (${shown.length} files)` : '');
    const open = leftoverOpen.has(f.path);
    const expand = f.files.length ? `
        <button type="button" class="btn btn-sm btn-link text-light p-0 flex-shrink-0" data-lo-expand="${i}"
                title="${open ? 'Hide' : 'Show'} the full file list"><i class="bi bi-chevron-${open ? 'down' : 'right'}"></i></button>` : '';
    const full = !open ? '' : `
      <div class="ms-4 mt-1 mb-1 font-monospace text-muted">
        ${shown.map(x => `<div>${esc(x)}</div>`).join('')}
        ${[...hidden].map(x => `<div class="fst-italic">${esc(x)} — system file, deleted with the folder</div>`).join('')}
      </div>`;
    return `
    <div class="border-bottom border-secondary-subtle py-1">
      <div class="d-flex align-items-center gap-2">
        <input class="form-check-input mt-0 lo-tick flex-shrink-0" type="checkbox" data-idx="${i}" id="lo-tick-${i}"
               ${leftoverTicked.has(f.path) ? 'checked' : ''}>
        <label class="font-monospace text-truncate" for="lo-tick-${i}" title="${esc(f.path)}">${esc(f.path)}</label>
        <span class="ms-auto text-muted text-truncate" title="${esc(shown.join('\n'))}">${esc(names)}</span>
        <span class="text-nowrap text-info-emphasis">(${esc(f.label)})</span>
        ${expand}
      </div>${full}
    </div>`;
  }).join('');
  _leftoverRunLabel();
}

/** All / None. */
function tickLeftovers(on) {
  leftoverTicked = on ? new Set(leftoverFolders.map(f => f.path)) : new Set();
  _leftoverCancelConfirm();
  _renderLeftovers();
}

function _leftoverRunLabel() {
  const btn = document.getElementById('lo-run-btn');
  if (btn.dataset.confirming === '1') return;
  btn.innerHTML = `<i class="bi bi-trash me-1"></i>Delete selected (${leftoverTicked.size})`;
  btn.disabled = leftoverBusy || !leftoverTicked.size;
}

/** Cancel a pending in-dialog confirmation of Delete selected. */
function _leftoverCancelConfirm() {
  resetConfirm(document.getElementById('lo-run-btn'), document.getElementById('lo-confirm-note'));
  _leftoverRunLabel();
}

function _setLeftoverBusy(busy) {
  leftoverBusy = busy;
  for (const id of ['lo-all-btn', 'lo-none-btn', 'lo-dryrun', 'lo-close-btn', 'lo-run-btn']) {
    document.getElementById(id).disabled = busy;
  }
  document.querySelector('#leftoverModal .btn-close').disabled = busy;
  document.querySelectorAll('#lo-list input, #lo-list button').forEach(c => { c.disabled = busy; });
  if (!busy) _leftoverRunLabel();
}

/** Result lines per folder: what was (or would be) deleted, failures first; a stopped run marks ✓ done / · not done. */
function _leftoverResultLines(results) {
  const ops = r => (r.operations || []).map((o, k) => {
    const mark = r.dry_run || r.ok ? '' : k < (r.done || 0) ? '✓ ' : '· ';
    const what = o.path === r.path ? 'the folder itself' : o.path.slice(r.path.length + 1);
    return `\n  ${mark}${o.op === 'delete' ? '− file' : '− folder'} ${what}`;
  }).join('');
  const fail = results.filter(r => !r.ok).map(r => `✗ ${r.path}\n  ${r.error}${r.done ? ops(r) : ''}`);
  const ok = results.filter(r => r.ok).map(r => `✓ ${r.path}${ops(r)}`);
  return [...fail, ...ok];
}

/** Delete selected: dry run (result with Back), or — after the in-dialog confirmation — the real delete. */
async function runLeftoverDelete() {
  const folders = leftoverFolders.filter(f => leftoverTicked.has(f.path)).map(f => ({ path: f.path, files: f.files }));
  if (!folders.length) {
    showToast('Tick at least one folder.', 'warning');
    return;
  }
  const dryRun = document.getElementById('lo-dryrun').checked;
  const btn = document.getElementById('lo-run-btn');
  if (!dryRun && !confirmInDialog(btn, document.getElementById('lo-confirm-note'),
    `${folders.length} folder(s) and every file listed in them will be deleted permanently.`,
    '<i class="bi bi-exclamation-triangle-fill me-1"></i>Confirm delete')) return;
  _setLeftoverBusy(true);
  let results;
  try {
    results = await api('POST', '/api/leftovers/delete', { folders, dry_run: dryRun });
  } catch (e) {
    showToast((dryRun ? 'Dry run failed: ' : 'Nothing deleted: ') + _apiErr(e), 'danger');
    return;
  } finally {
    _setLeftoverBusy(false);
  }
  const ok = results.filter(r => r.ok).length;
  const fail = results.length - ok;
  let status;
  if (dryRun) {
    status = fail ? `${fail} of ${results.length} folder(s) would be refused — they will be skipped.`
      : `All ${ok} folder(s) can be deleted.`;
  } else {
    status = fail ? `Deleted ${ok} of ${results.length} folder(s). ${fail} refused or stopped — see below.`
      : `Deleted ${ok} folder(s).`;
    await fetchLeftovers();
  }
  swapModal('leftoverModal', () => showResultModal({
    title: dryRun ? 'Dry Run Result' : 'Leftover folders — Result', good: !fail, status,
    lines: _leftoverResultLines(results),
    closeText: 'Back to Leftover folders', onClose: () => { _renderLeftovers(); getModal('leftoverModal').show(); },
  }));
}

// ── Settings → Leftover folders ───────────────────────────────────────────────
/** Fill the Settings tab from the config's `leftover` section (defaults for missing lists). */
function fillLeftoverSettings(lists) {
  lists = lists || {};
  for (const key of Object.keys(_LEFTOVER_DEFAULTS)) {
    document.getElementById(_leftoverInputId(key)).value = (lists[key] || _LEFTOVER_DEFAULTS[key]).join(', ');
  }
  updateLeftoverSettingsUI();
}

/** The six lists as entered (comma-separated, trimmed, empty entries dropped). */
function readLeftoverSettings() {
  const out = {};
  for (const key of Object.keys(_LEFTOVER_DEFAULTS)) {
    out[key] = document.getElementById(_leftoverInputId(key)).value.split(',').map(s => s.trim()).filter(Boolean);
  }
  return out;
}

/** Error text when a file list holds a video (mirrors settings.leftover_error on the server), else null. */
function leftoverSettingsError(lists) {
  for (const key of _LEFTOVER_FILE_LISTS) {
    for (const entry of lists[key] || []) {
      const ext = _LEFTOVER_VIDEO_EXTS.find(v => entry.toLowerCase().endsWith(v));
      if (ext) return `${entry} is a video (${ext}) — videos can never be on a leftover list`;
    }
  }
  return null;
}

/** Mark a field holding a video entry and show the error line. */
function updateLeftoverSettingsUI() {
  const lists = readLeftoverSettings();
  for (const key of _LEFTOVER_FILE_LISTS) {
    document.getElementById(_leftoverInputId(key)).classList.toggle('is-invalid', !!leftoverSettingsError({ [key]: lists[key] }));
  }
  document.getElementById('lo-settings-error').textContent = leftoverSettingsError(lists) || '';
}

function resetLeftoverSettings() {
  fillLeftoverSettings(_LEFTOVER_DEFAULTS);
}
