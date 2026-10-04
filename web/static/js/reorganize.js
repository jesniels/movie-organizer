'use strict';
// Reorganize dialog: suggestions from /api/reorganize/proposals, editable rows, preview and execution.

// ── Init ──────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  // Same while a Reorganize execution runs
  document.getElementById('reorganizeModal').addEventListener('hide.bs.modal', e => {
    if (roBusy) e.preventDefault();
  });

  // A running Reorganize execution must not be interrupted by leaving the page
  window.addEventListener('beforeunload', e => {
    if (roBusy) { e.preventDefault(); e.returnValue = ''; }
  });

  // Reorganize rows: checkboxes, NFO choices and edited names (rows are re-rendered on refresh)
  const roBody = document.getElementById('ro-body');
  roBody.addEventListener('change', e => _roChanged(e.target));
  roBody.addEventListener('input', e => {
    if (e.target.classList.contains('ro-name')) _roChanged(e.target);
  });
});

// ── Reorganize dialog ─────────────────────────────────────────────────────────
// Suggestions come from POST /api/reorganize/proposals for the items shown by the filters.
// Only what is ticked here is sent; the server re-analyses and re-checks all of it.
let roData     = null;    // last proposals response
let roFileInfo = [];      // per proposal: the video whose name the row can change (see _roFileInfo)
let roBusy     = false;   // true while a request runs — a real execution blocks closing the dialog and the page
let roExecuted = false;   // true after a real execution until the next scan — the library shows old names until then
let roConfirm  = false;   // true after the first “Do it” click — the next click executes
let roDupIdx   = new Set();   // proposal indexes of items in a duplicate group (duplicateMap)
let roDupMode  = 'out';   // 'out': duplicates left out of the default selection and “All”; 'in': treated like the rest
let roKnown    = null;    // path keys of the scanned library, built once per analysis (_roKnownPaths)
let roCounts   = '';      // header counts of the last analysis; the number of done rows is added to it

const _RO_KIND   = { loose_file: 'loose file', movie_folder: 'movie', series: 'series' };
const _RO_SOURCE = { nfo: 'NFO', name: 'name', 'nfo+name': 'NFO + name' };
const _RO_REASON = {
  no_year: 'no year', multi_video: 'several videos', stale_cache: 'rescan first',
  sanitize_empty: 'no legal characters', episode_changed: 'episode number',
};

const _baseName = p => p.split(/[/\\]/).pop();
const _dirName  = p => p.replace(/[/\\][^/\\]*$/, '');
const _splitExt = n => { const i = n.lastIndexOf('.'); return i > 0 ? [n.slice(0, i), n.slice(i)] : [n, '']; };
const _pathKey  = p => p.replace(/\\/g, '/').toLowerCase();
const _quoted   = s => `“${s}”`;   // makes a trailing space visible

/** Error text when name is not a single, Windows-legal path component (mirrors settings.valid_component). */
function _nameError(name) {
  if (!name || !name.trim()) return 'must not be empty';
  if (/[\\/:*?"<>|\x00-\x1f]/.test(name)) return 'characters not allowed in file names (\\ / : * ? " < > |)';
  if (name.trim() === '.' || name.trim() === '..') return 'invalid name';
  if (/[. ]$/.test(name)) return 'must not end with a dot or space';
  if (/^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$/i.test(name.split('.')[0].trim())) return 'reserved Windows device name';
  return null;
}

/** The folder a row's folder/file targets live in; operation paths are shown relative to it. */
function _roBase(p) {
  return p.kind === 'loose_file' ? p.path : _dirName(p.path);
}

/** A path relative to base when it lies below it, else the full path. */
function _roRel(path, base) {
  if (!path || !base) return path || '';
  const b = base.replace(/[\\/]+$/, '');
  return _pathKey(path).startsWith(_pathKey(b) + '/') ? path.slice(b.length + 1) : path;
}

/**
 * The video file whose name a row can change, or null: {from, fromStem, ext, toStem, proposed}.
 * A file name that already follows the format is only offered next to a folder field (for the
 * "= folder" sync). Loose files only together with their new folder; movie folders only with
 * exactly one video in the folder itself (the server refuses everything else).
 */
function _roFileInfo(p) {
  const naming = configCache.naming || {};
  let from = null;
  if (p.file) {
    from = p.file.from;
  } else if (naming.rename_files === false) {
    return null;
  } else if (p.kind === 'loose_file' && p.folder) {
    from = _baseName(p.item_id);
  } else if (p.kind === 'movie_folder' && p.folder && !roData.blocked.some(b =>
             b.item_id === p.item_id && (b.reason === 'multi_video' || b.reason === 'stale_cache'))) {
    const item = allItems.find(i => i.id === p.item_id);
    const files = item ? item.files || [] : [];
    if (files.length === 1 && _pathKey(_dirName(files[0])) === _pathKey(p.path)) from = _baseName(files[0]);
  }
  if (!from) return null;
  const [fromStem, ext] = _splitExt(from);
  return { from, fromStem, ext, toStem: p.file ? _splitExt(p.file.to)[0] : fromStem, proposed: !!p.file };
}

/** Open the Reorganize dialog and analyse right away (decision #1: only on open/Refresh, never automatic). */
async function openReorganize() {
  if (!roBusy) document.getElementById('ro-dryrun').checked = true;
  getModal('reorganizeModal').show();
  if (!roBusy) refreshReorganize();
}

/** Run the analysis on the items shown by the filters and render the suggestions. */
async function refreshReorganize() {
  if (roBusy) return;
  const ids = filteredItems.map(i => i.id);
  const body = document.getElementById('ro-body');
  roData = null;
  roConfirm = false;
  _setRoLocked(false);
  document.getElementById('ro-rescan-btn').classList.toggle('d-none', !roExecuted);
  document.getElementById('ro-footer-note').textContent = '';
  document.getElementById('ro-counts').textContent = '';
  document.getElementById('ro-scope').textContent = `the ${ids.length} item(s) shown by the filters`;
  updateReorganizeRunBtn();
  _roUpdateDupButton();
  if (!ids.length) {
    body.innerHTML = '<div class="text-muted py-3">No items are shown by the filters — change the filters on the left first.</div>';
    return;
  }
  body.innerHTML = `<div class="text-muted py-3"><i class="bi bi-arrow-repeat spin me-1"></i>Analysing ${ids.length} item(s)…</div>`;
  roDupMode = 'out';   // asked again after every analysis; leaving duplicates out is the safe answer
  try { configCache = await api('GET', '/api/config'); } catch (_) {}
  await fetchStatus();   // fresh duplicate groups for the duplicate panel
  try {
    roData = await api('POST', '/api/reorganize/proposals', { item_ids: ids });
  } catch (e) {
    body.innerHTML = `<div class="ro-bad py-3">Analysis failed: ${esc(_apiErr(e))}</div>`;
    return;
  }
  renderReorganize();
}

/** Render the analysis: header counts, one row per proposal, Parent folders, duplicates panel, Blocked list. */
function renderReorganize() {
  const d = roData;
  roFileInfo = d.proposals.map(_roFileInfo);
  roDupIdx = new Set(d.proposals.map((p, i) => duplicateMap.has(p.item_id) ? i : -1).filter(i => i >= 0));
  roKnown = _roKnownPaths();
  const sanitizeCount = d.proposals.reduce((n, p) => n + p.sanitize.length, 0) + d.parent_folders.length;
  roCounts = `${d.proposals.length} with suggestions · ${d.blocked.length} blocked · ${d.compliant_count} compliant`
    + (sanitizeCount ? ` · ${sanitizeCount} name(s) to sanitize` : '') + ` — ${d.analysed_count} analysed`;

  const empty = !d.proposals.length && !d.parent_folders.length
    ? `<div class="text-success py-3"><i class="bi bi-check-circle me-1"></i>Nothing to suggest — every analysed item follows the naming format${d.blocked.length ? ' or is listed below' : ''}.</div>`
    : '';
  const titleOf = id => (allItems.find(i => i.id === id) || {}).title;
  const blocked = d.blocked.length ? `
    <details class="mt-3">
      <summary class="text-warning">Cannot propose (${d.blocked.length})</summary>
      ${d.blocked.map(b => `
        <div class="py-1 ps-3 border-bottom border-secondary-subtle">
          <div>${esc(titleOf(b.item_id) || _baseName(b.path))}
            <span class="badge bg-warning-subtle ms-1">${esc(_RO_REASON[b.reason] || b.reason)}</span></div>
          <div class="text-muted">${esc(b.detail)}</div>
          <div class="text-muted font-monospace text-truncate" title="${esc(b.path)}">${esc(b.path)}</div>
        </div>`).join('')}
    </details>` : '';
  const nm = configCache.naming || {};
  const hint = `
    <div class="text-muted mt-3">
      <i class="bi bi-info-circle me-1"></i>Naming format: movie folder <code>${esc(nm.movie_folder || '—')}</code>,
      movie file <code>${esc(nm.movie_file || '—')}</code>, series folder <code>${esc(nm.series_folder || '—')}</code>
      — change in Settings → Naming. Nothing changes until you untick dry run and click Do it;
      the server checks every change again first.
    </div>`;

  document.getElementById('ro-body').innerHTML = empty
    + d.proposals.map(_roRowHtml).join('')
    + (d.parent_folders.length ? _roParentsHtml(d.parent_folders) : '')
    + _roDupPanelHtml() + blocked + hint;
  document.querySelectorAll('#ro-body .ro-row').forEach(_roUpdateRow);
  if (roDupMode === 'out') _roDupRows().forEach(row => { _roSelectRow(row, false); _roUpdateRow(row); });
  _roUpdateCounts();
  _roUpdateDupButton();
  _roUpdateConflicts();
  updateReorganizeRunBtn();
}

/**
 * Header counts (with the rows done so far) and the toolbar badge: the number of rows of the
 * most recent analysis that still have suggestions. Only updated by an analysis or an execution —
 * nothing is ever analysed in the background.
 */
function _roUpdateCounts() {
  const rows = document.querySelectorAll('#ro-body .ro-row');
  const done = document.querySelectorAll('#ro-body .ro-row.ro-done').length;
  const open = [...rows].filter(r => !_roLocked(r)).length;
  document.getElementById('ro-counts').textContent = roCounts + (done ? ` · ${done} done` : '');
  const badge = document.getElementById('ro-badge');
  badge.textContent = open;
  badge.classList.toggle('d-none', !open);
  badge.title = `${open} item(s) with suggestions in the last analysis — may be outdated until the dialog is opened again`;
}

// ── Reorganize: items in a duplicate group ────────────────────────────────────
// Renaming a potential duplicate before the duplicate is resolved gets in the way of the
// duplicate search, so these rows can be left out of the default selection and “All”.

/** Unlocked rows of items in a duplicate group. */
function _roDupRows() {
  return [...document.querySelectorAll('#ro-body .ro-row.ro-dup:not(.ro-locked)')];
}

/** Panel below the rows listing the potential duplicates, with the leave-out/include choice. */
function _roDupPanelHtml() {
  if (!roDupIdx.size) return '';
  const names = [...roDupIdx].map(i => {
    const p = roData.proposals[i];
    const current = p.kind === 'loose_file' ? _baseName(p.item_id) : _baseName(p.path);
    return `<div class="text-truncate" title="${esc(p.item_id)}"><span class="font-monospace">${esc(_quoted(current))}</span>
      <span class="text-muted">${esc(p.title)}${p.year ? ` (${esc(p.year)})` : ''}</span></div>`;
  }).join('');
  return `
    <div class="ro-dup-panel mt-3">
      <div class="fw-semibold mb-1"><i class="bi bi-copy me-1"></i>${roDupIdx.size} item(s) are listed as potential duplicates</div>
      <div class="mb-2">Renaming them now can get in the way of resolving the duplicates later.
        Resolve them with the Duplicates filter first.</div>
      <div class="ro-dup-list mb-2">${names}</div>
      <div class="form-check">
        <input class="form-check-input" type="radio" name="ro-dup-mode" id="ro-dup-out" value="out"
               ${roDupMode === 'out' ? 'checked' : ''} onchange="setRoDupMode(this.value)">
        <label class="form-check-label" for="ro-dup-out">
          Leave them out of the default selection and “All” (recommended) — tick them yourself or use “Select duplicates”</label>
      </div>
      <div class="form-check">
        <input class="form-check-input" type="radio" name="ro-dup-mode" id="ro-dup-in" value="in"
               ${roDupMode === 'in' ? 'checked' : ''} onchange="setRoDupMode(this.value)">
        <label class="form-check-label" for="ro-dup-in">Include them like the other items</label>
      </div>
      <button type="button" class="btn btn-sm btn-outline-light mt-2" onclick="showDuplicatesFromReorganize()">
        <i class="bi bi-copy me-1"></i>Show duplicates
      </button>
      <span class="text-muted ms-2">closes this dialog and switches the list to the Duplicates filter</span>
    </div>`;
}

/** Leave the duplicate rows out (untick them) or include them (back to the default selection). */
function setRoDupMode(mode) {
  roDupMode = mode;
  _roCancelConfirm();
  _roDupRows().forEach(row => {
    row.querySelectorAll('.ro-check').forEach(c => { c.checked = mode === 'in' && !!c.dataset.default; });
    row.querySelectorAll('.ro-nfos').forEach(s => { s.value = 'leave'; });
    row.querySelector('.ro-result').innerHTML = '';
    _roUpdateRow(row);
  });
  _roUpdateDupButton();
  _roUpdateConflicts();
  updateReorganizeRunBtn();
}

/** “Select N duplicates” — only while duplicates are left out and some are still selectable. */
function _roUpdateDupButton() {
  const btn = document.getElementById('ro-dups-btn');
  const n = _roDupRows().length;
  btn.classList.toggle('d-none', !(roData && roDupMode === 'out' && n));
  btn.innerHTML = `<i class="bi bi-copy me-1"></i>Select ${n} duplicate(s)`;
}

/** Tick every suggestion of the duplicate rows, like clicking each row's checkbox. */
function selectReorganizeDuplicates() {
  if (!roData || roBusy) return;
  _roCancelConfirm();
  _roDupRows().forEach(row => {
    _roSelectRow(row, true);
    row.querySelector('.ro-result').innerHTML = '';
    _roUpdateRow(row);
  });
  _roUpdateConflicts();
  updateReorganizeRunBtn();
}

/** Close Reorganize and show the duplicate groups in the item list. */
function showDuplicatesFromReorganize() {
  if (roBusy) return;
  getModal('reorganizeModal').hide();
  if (activeSpecialFilter !== 'duplicates') toggleSpecialFilter('duplicates');
}

/** One sanitize entry with its own checkbox; conflicts start unticked. */
function _roSanitizeLine(e, base) {
  const follows = e.follows.length
    ? `<div class="ro-sub text-muted">${e.follows.map(f => `${esc(_quoted(f.from))} → ${esc(_quoted(f.to))}`).join('<br>')}</div>`
    : '';
  return `
    <div class="ro-line">
      <input type="checkbox" class="form-check-input ro-check" data-op="sanitize" data-path="${esc(e.path)}"
             ${e.conflict ? 'data-conflict="1"' : 'data-default="1" checked'}>
      <span class="font-monospace text-break">${esc(_quoted(_roRel(e.path, base)))} → ${esc(_quoted(e.proposed))}</span>
      ${e.conflict ? '<span class="ro-bad text-nowrap">target exists or is suggested twice</span>' : ''}
    </div>${follows}`;
}

/**
 * One proposal row: row checkbox and current name, then a line per suggestion (folder, file,
 * NFO, several NFOs, images) and the sanitize entries. Checkboxes carry data-default (ticked by
 * default and by All) or data-conflict (unticked, ticked only as an approval).
 */
function _roRowHtml(p, i) {
  const fi = roFileInfo[i];
  const base = _roBase(p);
  const current = p.kind === 'loose_file' ? _baseName(p.item_id) : _baseName(p.path);
  const input = (field, value) => `<input type="text" class="form-control form-control-sm bg-dark text-light
    border-secondary ro-name" data-field="${field}" value="${esc(value)}" spellcheck="false" autocomplete="off">`;
  const lines = [];
  const details = [];

  if (p.folder) {
    const f = p.folder;
    lines.push(`
      <div class="ro-line">
        <input type="checkbox" class="form-check-input ro-check" data-op="folder"
               ${f.conflict ? 'data-conflict="1"' : 'data-default="1" checked'}>
        <span class="ro-label">${f.create ? 'New folder' : 'Rename folder'}</span>
        ${input('folder', f.conflict && f.suggested ? f.suggested : f.to)}
      </div>
      ${f.conflict ? `<div class="ro-sub ro-bad">${esc(_quoted(f.to))} already exists or is suggested for another item${
        f.suggested ? ` — ${esc(_quoted(f.suggested))} is suggested instead; tick to approve or edit the name` : ''}</div>` : ''}
      <div class="ro-sub ro-bad ro-folder-warn"></div>`);
  }
  if (fi) {
    const conflict = !!(p.file && p.file.conflict);
    // Off on folder conflicts: approving "Name (1)" for the folder should not put "(1)" in the video name
    const sync = !!p.folder && !p.folder.conflict && ((configCache.naming || {}).file_equals_folder ?? true);
    lines.push(`
      <div class="ro-line">
        <input type="checkbox" class="form-check-input ro-check" data-op="file"
               ${conflict ? 'data-conflict="1"' : fi.proposed ? 'data-default="1" checked' : ''}>
        <span class="ro-label">Rename file</span>
        ${input('file', fi.toStem)}<span class="text-muted font-monospace">${esc(fi.ext)}</span>
        ${p.folder ? `<label class="form-check-label text-nowrap ms-2" title="File name = folder name">
          <input type="checkbox" class="form-check-input ro-sync me-1" ${sync ? 'checked' : ''}>= folder</label>` : ''}
      </div>
      ${conflict ? '<div class="ro-sub ro-bad">A file with the suggested name already exists or is suggested twice</div>' : ''}
      <div class="ro-sub ro-bad ro-file-warn"></div>`);
  }
  if (p.nfo) {
    lines.push(`
      <div class="ro-line">
        <input type="checkbox" class="form-check-input ro-check" data-op="nfo"
               ${p.nfo.conflict ? 'data-conflict="1"' : 'data-default="1" checked'}>
        <span class="ro-label">Rename NFO</span>
        <span class="font-monospace ro-nfo-label"></span>
        ${p.nfo.conflict ? '<span class="ro-bad text-nowrap">target exists</span>' : ''}
      </div>`);
  }
  if (p.nfos) {
    const n = p.nfos;
    const label = {
      use_best: `Use the best (${n.best || ''}${n.rename_best ? ` → ${n.target}` : ''}), delete the others`,
      delete_all: 'Delete all (Jellyfin creates a new one on its next scan)',
      leave: 'Leave as is',
    };
    lines.push(`
      <div class="ro-line">
        <span class="ro-check-gap"></span>
        <span class="ro-label">Several NFOs</span>
        <select class="form-select form-select-sm bg-dark text-light border-secondary ro-nfos">
          ${n.options.map(o => `<option value="${o}" ${o === n.default ? 'selected' : ''}>${esc(label[o])}</option>`).join('')}
        </select>
      </div>
      <div class="ro-sub text-muted">${n.files.map(f => `${esc(f.name)} — ${esc(f.quality)}, ${f.fields} field(s)${
        f.name === n.best ? ' <span class="text-success">★ best</span>' : ''}`).join('<br>')}${
        n.detail ? `<br><span class="text-warning">${esc(n.detail)}</span>` : ''}</div>`);
  }
  if (p.images.length) {
    lines.push(`
      <div class="ro-line">
        <input type="checkbox" class="form-check-input ro-check" data-op="images">
        <span>Delete ${p.images.length} image(s) — Jellyfin regenerates them; unticked they move into the new folder</span>
      </div>
      <div class="ro-sub text-muted font-monospace">${p.images.map(esc).join('<br>')}</div>`);
  }

  // What comes along: moved files (loose), sidecars following the video (folder)
  if (p.move.length > 1) {
    details.push(`<div class="text-muted mt-1">Moved into the new folder (sidecars follow the video's new name):</div>
      <div class="ro-sub font-monospace">${p.move.map(m => esc(m.from)).join('<br>')}</div>`);
  } else if (p.file && p.file.follows.length) {
    details.push(`<div class="text-muted mt-1">Follow the video:</div>
      <div class="ro-sub font-monospace">${p.file.follows.map(f => `${esc(f.from)} → ${esc(f.to)}`).join('<br>')}</div>`);
  }
  const sanitize = p.sanitize.map(e => _roSanitizeLine(e, base)).join('');
  if (sanitize && lines.length) {
    details.push(`<div class="text-muted mt-1">Sanitize names (illegal characters, trailing dots/spaces):</div>${sanitize}`);
  } else if (sanitize) {
    lines.push(`<div class="text-muted mt-1">Sanitize names (illegal characters, trailing dots/spaces):</div>${sanitize}`);
  }

  const warn = roData.blocked.filter(b => b.item_id === p.item_id).map(b =>
    `<div class="text-warning"><i class="bi bi-exclamation-triangle me-1"></i>${esc(b.detail)}</div>`).join('');
  const dup = roDupIdx.has(i);
  const dupHint = dup ? `<div class="ro-sub ro-bad">Listed as a potential duplicate — resolve it with the Duplicates filter
    first, so renaming does not get in the way</div>` : '';
  const conflict = p.folder?.conflict || p.file?.conflict || p.nfo?.conflict || p.sanitize.some(e => e.conflict);
  return `
    <div class="ro-row${conflict ? ' ro-conflict' : ''}${dup ? ' ro-dup' : ''}" data-idx="${i}">
      <div class="d-flex align-items-center gap-2 min-w-0">
        <input type="checkbox" class="form-check-input ro-row-check" title="Tick / untick everything in this row">
        <span class="fw-semibold text-truncate font-monospace" title="${esc(p.item_id)}">${esc(_quoted(current))}</span>
        <span class="badge bg-secondary-subtle">${esc(_RO_KIND[p.kind] || p.kind)}</span>
        ${dup ? '<span class="badge bg-danger-subtle"><i class="bi bi-copy me-1"></i>duplicate</span>' : ''}
        <span class="text-muted text-nowrap">${esc(p.title)}${p.year ? ` (${esc(p.year)})` : ''} · from ${esc(_RO_SOURCE[p.source] || p.source)}</span>
        <span class="ms-auto ro-summary text-muted text-nowrap"></span>
      </div>
      <div class="ps-4">${dupHint}${lines.join('')}${warn}
        ${details.length ? `<details class="mt-1"><summary class="text-muted">Details</summary>${details.join('')}</details>` : ''}
        <div class="ro-bad ro-err"></div>
        <div class="ro-result"></div>
      </div>
    </div>`;
}

/** The “Parent folders” group: sanitize entries for folders above the items, executed after all items. */
function _roParentsHtml(entries) {
  return `
    <div class="ro-row${entries.some(e => e.conflict) ? ' ro-conflict' : ''}" data-idx="parents">
      <div class="d-flex align-items-center gap-2">
        <input type="checkbox" class="form-check-input ro-row-check" title="Tick / untick everything in this group">
        <span class="fw-semibold">Parent folders</span>
        <span class="text-muted">folders above the items — renamed after all items</span>
        <span class="ms-auto ro-summary text-muted text-nowrap"></span>
      </div>
      <div class="ps-4">${entries.map(e => _roSanitizeLine(e, '')).join('')}
        <div class="ro-bad ro-err"></div>
        <div class="ro-result"></div>
      </div>
    </div>`;
}

/** Sync the file name, check the edited names and refresh a row's summary and tick state. */
function _roUpdateRow(row) {
  const q = s => row.querySelector(s);
  const check = op => q(`.ro-check[data-op="${op}"]`);
  const parts = [];
  const errs = [];
  if (row.dataset.idx !== 'parents') {
    const i = +row.dataset.idx;
    const p = roData.proposals[i];
    const fi = roFileInfo[i];
    const folderIn = q('.ro-name[data-field="folder"]');
    const fileIn = q('.ro-name[data-field="file"]');
    const sync = q('.ro-sync');
    if (fileIn && sync) {
      if (sync.checked) fileIn.value = folderIn.value;
      fileIn.readOnly = sync.checked;
    }
    // A loose file is only renamed together with creating its folder
    if (p.kind === 'loose_file' && check('file')) {
      check('file').disabled = !check('folder').checked;
      if (!check('folder').checked) check('file').checked = false;
    }
    if (folderIn) {
      const err = check('folder').checked ? _nameError(folderIn.value) : null;
      folderIn.classList.toggle('is-invalid', !!err);
      if (err) errs.push(`Folder name ${err}`);
      if (check('folder').checked) parts.push(p.folder.create ? 'new folder' : 'folder renamed');
    }
    let stem = fi ? fi.fromStem : p.nfo ? _splitExt(p.nfo.to)[0] : '';
    if (fileIn) {
      const on = check('file').checked;
      const err = on ? _nameError(fileIn.value) || _nameError(fileIn.value + fi.ext) : null;
      fileIn.classList.toggle('is-invalid', !!err);
      if (err) errs.push(`File name ${err}`);
      if (on && !err) stem = fileIn.value;
      if (on && fileIn.value !== fi.fromStem) parts.push('file renamed');
    }
    if (p.nfo) {
      q('.ro-nfo-label').textContent = `${_quoted(p.nfo.from)} → ${_quoted(stem + '.nfo')}`;
      if (check('nfo').checked) parts.push('NFO renamed');
    }
    const nfos = q('.ro-nfos');
    if (nfos && nfos.value !== 'leave') parts.push(nfos.value === 'use_best' ? 'best NFO kept, others deleted' : 'all NFOs deleted');
    if (check('images') && check('images').checked) parts.push(`${p.images.length} image(s) deleted`);
  }
  const san = [...row.querySelectorAll('.ro-check[data-op="sanitize"]')];
  const sanOn = san.filter(c => c.checked).length;
  if (sanOn) parts.push(`${sanOn} of ${san.length} sanitized`);

  q('.ro-summary').textContent = parts.join(' · ') || 'nothing selected';
  q('.ro-err').textContent = errs.join(' · ');
  // Row checkbox: ticked when anything is selected, dash when part of the suggestions is not.
  // Extras (image deletion, an unproposed file rename) and the NFO choice do not count as suggestions.
  const suggested = [...row.querySelectorAll('.ro-check')]
    .filter(c => !c.disabled && (c.dataset.default || c.dataset.conflict));
  const nfos = q('.ro-nfos');
  const any = [...row.querySelectorAll('.ro-check')].some(c => c.checked) || !!(nfos && nfos.value !== 'leave');
  const all = suggested.every(c => c.checked);
  const master = q('.ro-row-check');
  master.checked = any;
  master.indeterminate = any && suggested.length > 0 && !all;
  master.dataset.partial = master.indeterminate ? '1' : '';
  // Nothing to tick (e.g. only the several-NFOs choice): the row checkbox would do nothing
  master.disabled = !suggested.length && !any;
  master.title = master.disabled
    ? 'Nothing to tick here — choose what to do in the row (e.g. the NFO choice)'
    : 'Tick / untick everything in this row';
}

/**
 * Tick everything a row suggests (conflicts included, as approval), or untick everything.
 * The several-NFOs choice is never changed by ticking — the user picks it (default leave as is).
 */
function _roSelectRow(row, on) {
  // Never image deletions or unproposed file renames — those are ticked one by one
  row.querySelectorAll('.ro-check').forEach(c => {
    c.checked = on && (!!c.dataset.default || !!c.dataset.conflict);
  });
  if (!on) row.querySelectorAll('.ro-nfos').forEach(s => { s.value = 'leave'; });
}

/** Path keys of every folder and video known from the last scan (for the live conflict check). */
function _roKnownPaths() {
  const known = new Set();
  for (const i of allItems) {
    known.add(_pathKey(i.id));
    known.add(_pathKey(i.path));
    for (const f of i.files || []) known.add(_pathKey(f));
  }
  return known;
}

/**
 * The ticked folder and file targets of a row as path keys (null when not ticked or invalid),
 * plus the row's own current paths (a case-only rename of itself is no conflict).
 */
function _roTargets(row) {
  const i = +row.dataset.idx;
  const p = roData.proposals[i];
  const fi = roFileInfo[i];
  const q = s => row.querySelector(s);
  const folderCb = q('.ro-check[data-op="folder"]');
  const fileCb = q('.ro-check[data-op="file"]');
  const folderName = folderCb && folderCb.checked ? q('.ro-name[data-field="folder"]').value : null;
  const out = { folder: null, file: null, ownFolder: null, ownFile: null };
  if (folderName !== null && !_nameError(folderName)) {
    out.folder = _pathKey(_roBase(p) + '/' + folderName);
    if (p.kind !== 'loose_file') out.ownFolder = _pathKey(p.path);
  }
  if (fileCb && fileCb.checked && fi) {
    const stem = q('.ro-name[data-field="file"]').value;
    if (!_nameError(stem) && !_nameError(stem + fi.ext)) {
      // The server renames the file inside the current folder; a loose file goes into its new folder
      const dir = p.kind === 'loose_file' ? (out.folder ? _roBase(p) + '/' + folderName : null) : p.path;
      if (dir) out.file = _pathKey(dir + '/' + stem + fi.ext);
      out.ownFile = _pathKey((p.kind === 'loose_file' ? _roBase(p) : p.path) + '/' + fi.from);
    }
  }
  return out;
}

/**
 * Live conflict check of the ticked folder/file names against the scanned library and the
 * other ticked rows. Advisory only — the server checks the disk again on Preview / Do it.
 */
function _roUpdateConflicts() {
  const known = roKnown || (roKnown = _roKnownPaths());
  const rows = [...document.querySelectorAll('#ro-body .ro-row')].filter(r => r.dataset.idx !== 'parents');
  const targets = new Map(rows.map(r => [r, _roTargets(r)]));
  const count = new Map();
  for (const t of targets.values()) {
    for (const k of [t.folder, t.file]) if (k) count.set(k, (count.get(k) || 0) + 1);
  }
  for (const row of rows) {
    if (_roLocked(row)) continue;
    const p = roData.proposals[+row.dataset.idx];
    const t = targets.get(row);
    const folderEl = row.querySelector('.ro-folder-warn');
    if (folderEl) {
      const warn = [];
      const value = row.querySelector('.ro-name[data-field="folder"]').value;
      if (t.folder && p.folder.conflict && value.toLowerCase() === p.folder.to.toLowerCase()) {
        warn.push('This name is taken — the server will refuse it unless the other item is renamed first');
      } else if (t.folder && t.folder !== t.ownFolder && known.has(t.folder)) {
        warn.push('A folder or file with this name exists in the scanned library');
      }
      if (t.folder && count.get(t.folder) > 1) warn.push('Same folder name as another ticked suggestion');
      folderEl.textContent = warn.join(' · ');
    }
    const fileEl = row.querySelector('.ro-file-warn');
    if (fileEl) {
      const warn = [];
      if (t.file && t.file !== t.ownFile && known.has(t.file)) warn.push('A video with this name exists in the scanned library');
      if (t.file && count.get(t.file) > 1) warn.push('Same file name as another ticked suggestion');
      fileEl.textContent = warn.join(' · ');
    }
  }
}

const _roLocked = row => row.classList.contains('ro-locked');

/**
 * Lock a row for good: nothing in it can be selected again until rescan + Refresh.
 * Done rows keep their ticks; rows below a renamed parent folder are unticked.
 */
function _roLockRow(row, cls, label, keepTicks) {
  if (!keepTicks) {
    row.querySelectorAll('.ro-check, .ro-row-check').forEach(c => { c.checked = false; c.indeterminate = false; });
    row.querySelectorAll('.ro-nfos').forEach(s => { s.value = 'leave'; });
  }
  row.querySelectorAll('input, select').forEach(el => { el.disabled = true; });
  row.classList.add('ro-locked', cls);
  row.querySelector('.ro-summary').innerHTML = label;
}

/** Lock the rows of items below renamed parent folders — their paths changed, the server would refuse them. */
function _roLockBelow(renamed) {
  if (!renamed.length) return;
  document.querySelectorAll('#ro-body .ro-row').forEach(row => {
    if (_roLocked(row) || row.dataset.idx === 'parents') return;
    const p = roData.proposals[+row.dataset.idx];
    if (!renamed.some(f => _pathKey(p.item_id).startsWith(_pathKey(f) + '/'))) return;
    _roLockRow(row, 'ro-moved', '<span class="text-warning">parent folder renamed</span>', false);
    _roShowResult(row, null, 'Parent folder renamed — rescan and refresh first');
  });
}

/** Handle any change inside the rows (delegated from #ro-body). */
function _roChanged(el) {
  const row = el.closest('.ro-row');
  if (!row || !roData || _roLocked(row)) return;
  if (el.classList.contains('ro-row-check')) {
    // A partly ticked row is completed by a click, not cleared
    _roSelectRow(row, el.checked || el.dataset.partial === '1');
  } else if (el.classList.contains('ro-name')) {
    // Editing a name selects it
    const cb = row.querySelector(`.ro-check[data-op="${el.dataset.field}"]`);
    if (cb && !cb.disabled) cb.checked = true;
  }
  row.querySelector('.ro-result').innerHTML = '';   // an earlier preview no longer matches
  _roCancelConfirm();
  _roUpdateRow(row);
  _roUpdateConflicts();
  updateReorganizeRunBtn();
}

/** All / None: the default suggestions of every unlocked row (duplicates left out stay unticked on All). */
function selectReorganize(on) {
  if (!roData || roBusy) return;
  _roCancelConfirm();
  document.querySelectorAll('#ro-body .ro-row:not(.ro-locked)').forEach(row => {
    // Duplicates left out are not ticked by “All” (“Select duplicates” does that); “None” clears them too
    if (on && roDupMode === 'out' && row.classList.contains('ro-dup')) return;
    row.querySelectorAll('.ro-check').forEach(c => { c.checked = on && !!c.dataset.default; });
    if (!on) row.querySelectorAll('.ro-nfos').forEach(s => { s.value = 'leave'; });
    row.querySelector('.ro-result').innerHTML = '';
    _roUpdateRow(row);
  });
  _roUpdateConflicts();
  updateReorganizeRunBtn();
}

/** The request entry for a row's ticked choices, or null when nothing is ticked. */
function _roRowChange(row) {
  const q = s => row.querySelector(s);
  const on = op => !!(q(`.ro-check[data-op="${op}"]`) || {}).checked;
  const sanitize = [...row.querySelectorAll('.ro-check[data-op="sanitize"]:checked')].map(c => c.dataset.path);
  if (row.dataset.idx === 'parents') return sanitize.length ? { item_id: null, sanitize } : null;
  const i = +row.dataset.idx;
  const p = roData.proposals[i];
  const c = {
    item_id: p.item_id, apply_folder: on('folder'), apply_file: false, apply_nfo: on('nfo'),
    nfo_action: (q('.ro-nfos') || {}).value || 'leave',
    nfo_files: p.nfos ? p.nfos.files.map(f => f.name) : [],   // the server refuses when the NFOs changed
    delete_images: on('images'), sanitize,
  };
  if (c.apply_folder) c.folder_name = q('.ro-name[data-field="folder"]').value;
  if (on('file')) {
    const stem = q('.ro-name[data-field="file"]').value;
    if (stem !== roFileInfo[i].fromStem) { c.apply_file = true; c.file_name = stem; }
  }
  const any = c.apply_folder || c.apply_file || c.apply_nfo || c.nfo_action !== 'leave' || c.delete_images || sanitize.length;
  return any ? c : null;
}

/**
 * Ticked rows in execution order (Parent folders are rendered last) and the rows with invalid names.
 * Locked rows (done, or below a renamed parent folder) are never sent again.
 */
function _roCollect() {
  const picked = [];
  const invalid = [];
  document.querySelectorAll('#ro-body .ro-row:not(.ro-locked)').forEach(row => {
    const change = _roRowChange(row);
    if (!change) return;
    picked.push({ row, change });
    if (row.querySelector('.ro-err').textContent) invalid.push(row);
  });
  return { picked, invalid };
}

/**
 * Refresh the run button. Called after every change in the dialog, so any change
 * (dry run, ticks, names) also cancels a pending in-dialog confirmation.
 */
function updateReorganizeRunBtn() {
  const btn = document.getElementById('ro-run-btn');
  const dry = document.getElementById('ro-dryrun').checked;
  const n = roData ? _roCollect().picked.length : 0;
  btn.disabled = roBusy || !roData || n === 0;
  btn.className = `btn ${dry ? 'btn-info' : 'btn-danger'}`;
  btn.innerHTML = roBusy
    ? '<span class="spinner-border spinner-border-sm me-1"></span>Working…'
    : roConfirm
      ? `<i class="bi bi-exclamation-triangle-fill me-1"></i>Confirm (${n})`
      : `<i class="bi ${dry ? 'bi-eye' : 'bi-play-fill'} me-1"></i>${dry ? 'Preview' : 'Do it'}${n ? ` (${n})` : ''}`;
}

/** Cancel a pending “Do it” confirmation (any change in the dialog). */
function _roCancelConfirm() {
  if (!roConfirm) return;
  roConfirm = false;
  document.getElementById('ro-footer-note').textContent = '';
}

/** Lock all rows and the selection controls while a request runs. */
function _setRoLocked(locked) {
  document.getElementById('ro-fieldset').disabled = locked;
  for (const id of ['ro-all-btn', 'ro-none-btn', 'ro-dups-btn', 'ro-dryrun']) document.getElementById(id).disabled = locked;
}

/** While a request runs: lock the rows and controls; a running execution cannot be closed. */
function _setRoBusy(busy) {
  roBusy = busy;
  _setRoLocked(busy);
  for (const id of ['ro-close-btn', 'ro-refresh-btn']) document.getElementById(id).disabled = busy;
  document.querySelector('#reorganizeModal .btn-close').disabled = busy;
  updateReorganizeRunBtn();
}

/** Show a server result (or a pending/not-attempted message) under a row, with the exact operations. */
function _roShowResult(row, r, message) {
  const el = row.querySelector('.ro-result');
  if (!r) {
    el.innerHTML = message === 'working'
      ? '<span class="text-info"><span class="spinner-border spinner-border-sm me-1"></span>Working…</span>'
      : `<span class="text-warning"><i class="bi bi-dash-circle me-1"></i>${esc(message)}</span>`;
    return;
  }
  const base = row.dataset.idx === 'parents' ? '' : _roBase(roData.proposals[+row.dataset.idx]);
  const ops = r.operations || [];
  const done = r.dry_run ? 0 : (r.done ?? ops.length);
  const head = r.ok
    ? `<span class="text-success"><i class="bi bi-check-circle me-1"></i>${r.dry_run ? 'Would do' : 'Done'}: ${ops.length} operation(s)</span>`
    : `<span class="ro-bad"><i class="bi bi-x-circle me-1"></i>${r.dry_run ? 'Refused' : 'Failed'}: ${esc(r.error)}</span>`;
  const list = ops.map((o, k) => {
    const mark = r.dry_run ? '' : k < done ? '✓ ' : '· ';
    const text = o.op === 'mkdir' ? `create folder ${_quoted(_roRel(o.to, base))}`
      : o.op === 'delete' ? `delete ${_quoted(_roRel(o.from, base))}`
      : `${o.op} ${_quoted(_roRel(o.from, base))} → ${_quoted(_roRel(o.to, base))}`;
    return `<div>${mark}${esc(text)}</div>`;
  }).join('');
  el.innerHTML = head + (list ? `<div class="ro-ops-list">${list}</div>` : '');
}

/** Preview (dry run, one request) or execute (one request per row, in order, with progress). */
async function runReorganize() {
  if (!roData || roBusy) return;
  const dryRun = document.getElementById('ro-dryrun').checked;
  const { picked, invalid } = _roCollect();
  if (invalid.length) {
    showToast(`Fix the names marked in red first (${invalid.length} row(s)).`, 'warning');
    invalid[0].scrollIntoView({ block: 'center' });
    return;
  }
  if (!picked.length) return;
  const note = document.getElementById('ro-footer-note');
  // Locked rows keep their result ("Done: …") for the rest of the session
  document.querySelectorAll('#ro-body .ro-row:not(.ro-locked) .ro-result').forEach(el => { el.innerHTML = ''; });

  if (dryRun) {
    _setRoBusy(true);
    try {
      const results = await api('POST', '/api/reorganize', { changes: picked.map(x => x.change), dry_run: true });
      const byId = new Map(results.map(r => [r.item_id, r]));
      picked.forEach(x => _roShowResult(x.row, byId.get(x.change.item_id)));
      const bad = results.filter(r => !r.ok).length;
      note.innerHTML = bad
        ? `<span class="ro-bad">Preview: ${bad} of ${results.length} would be refused — see the marked rows.</span>`
        : `<span class="text-success">Preview: all ${results.length} can be done. Untick dry run and click Do it.</span>`;
    } catch (e) {
      note.innerHTML = `<span class="ro-bad">Preview failed: ${esc(_apiErr(e))}</span>`;
    } finally {
      _setRoBusy(false);
    }
    return;
  }

  // First click asks inside the dialog; the second click executes
  if (!roConfirm) {
    roConfirm = true;
    note.innerHTML = `<span class="ro-confirm-note"><i class="bi bi-exclamation-triangle-fill me-1"></i>`
      + `Files of ${picked.length} row(s) will be renamed, moved or deleted. There is no undo. `
      + `Click <strong>Confirm</strong> to go ahead, or change anything to cancel.</span>`;
    updateReorganizeRunBtn();
    return;
  }
  roConfirm = false;
  _setRoBusy(true);
  let ok = 0, failed = 0, skipped = 0, stopped = '';
  for (let n = 0; n < picked.length; n++) {
    const { row, change } = picked[n];
    if (stopped) {
      _roShowResult(row, null, `Not attempted — ${stopped}`);
      skipped++;
      continue;
    }
    _roShowResult(row, null, 'working');
    note.textContent = `Working on ${n + 1} of ${picked.length}…`;
    try {
      const [r] = await api('POST', '/api/reorganize', { changes: [change], dry_run: false });
      _roShowResult(row, r);
      // Done rows are locked; failed rows stay selectable so they can be fixed and run again
      if (r.ok) {
        ok++;
        _roLockRow(row, 'ro-done',
          '<span class="text-success"><i class="bi bi-check-circle-fill me-1"></i>already reorganized</span>', true);
      } else {
        failed++;
      }
      if (change.item_id === null) {
        const renamed = (r.operations || []).slice(0, r.done ?? 0)
          .filter(o => o.op === 'rename' && o.kind === 'folder').map(o => o.from);
        _roLockBelow(renamed);
      }
    } catch (e) {
      // Refused as a whole (scan/transcode started, server gone) — the remaining rows would be too
      stopped = _apiErr(e);
      _roShowResult(row, null, `Not attempted — ${stopped}`);
      skipped++;
    }
  }
  roExecuted = true;
  _setRoBusy(false);
  note.innerHTML = `<span class="${failed || skipped ? 'text-warning' : 'text-success'}">Done: ${ok} ok`
    + (failed ? `, ${failed} failed` : '') + (skipped ? `, ${skipped} not attempted` : '')
    + '.</span> Done rows are locked — continue with the others, or rescan to see the new names in the library.';
  document.getElementById('ro-rescan-btn').classList.remove('d-none');
  _roUpdateDupButton();   // done duplicates are locked and no longer counted
  _roUpdateCounts();
  _roUpdateConflicts();
  updateReorganizeRunBtn();
}

/** The server does not update the library after Reorganize — a rescan picks up the new names. */
function rescanAfterReorganize() {
  getModal('reorganizeModal').hide();
  triggerScan();
}
