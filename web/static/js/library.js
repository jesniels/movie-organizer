'use strict';
// Library list: fetching items/status/config, special and location filters, list rendering,
// the sidebar status panel, row selection and the status modal.

// ── Library state ─────────────────────────────────────────────────────────────
let lastClickedId       = null;    // for shift-click range selection
let activeQuickFilter   = null;    // null | 'location:type' — sidebar summary row currently driving the filters
let lastStatus          = null;    // cached last /api/status response
let transcodedDupOnly   = false;   // duplicates view limited to transcode-caused groups

// Location filter state: per group, configured root path → checked
const locState    = { library: {}, download: {}, transcoded: {} };
const locExpanded = { library: false, download: false, transcoded: false };
const locTopEmpty = { library: true, download: true, transcoded: true };  // top state when group has no roots

// ── Init ──────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  // Location filter tree: checkbox changes + expand/collapse carets
  const locEl = document.getElementById('location-filters');
  locEl.addEventListener('change', e => {
    const cb = e.target.closest('input[type="checkbox"]');
    if (!cb) return;
    const g = cb.dataset.group;
    if (cb.dataset.top) {
      if (Object.keys(locState[g]).length) {
        for (const r of Object.keys(locState[g])) locState[g][r] = cb.checked;
      }
      locTopEmpty[g] = cb.checked;
    } else {
      locState[g][cb.dataset.root] = cb.checked;
    }
    clearQuickFilterMark();
    renderLocationFilters();
    applyFilters();
  });
  locEl.addEventListener('click', e => {
    const caret = e.target.closest('[data-expand]');
    if (caret) {
      const g = caret.dataset.expand;
      locExpanded[g] = !locExpanded[g];
      renderLocationFilters();
    }
  });

  // Event delegation for item list
  const list = document.getElementById('item-list');
  list.addEventListener('click', e => {
    const selGroupBtn = e.target.closest('[data-select-group]');
    if (selGroupBtn) {
      toggleGroupSelection(+selGroupBtn.dataset.selectGroup);
      return;
    }
    const cmpGroupBtn = e.target.closest('[data-compare-group]');
    if (cmpGroupBtn) {
      const g = duplicateGroups[+cmpGroupBtn.dataset.compareGroup];
      if (g && g.items.length) showCompareModal(g.items[0].id);
      return;
    }
    const row = e.target.closest('.item-row');
    if (!row) return;
    const id = row.dataset.id;
    if (e.target.closest('.btn-play')) {
      playItem(id);
      return;
    }
    if (e.ctrlKey || e.metaKey) {
      // Ctrl/Cmd-click: toggle individual item
      if (selectedIds.has(id)) selectedIds.delete(id);
      else selectedIds.add(id);
      _syncRowSelected(row, selectedIds.has(id));
      updateSelectionUI();
    } else if (e.shiftKey && lastClickedId) {
      // Shift-click: select range from last clicked to this row
      const ids = filteredItems.map(i => i.id);
      const a = ids.indexOf(lastClickedId);
      const b = ids.indexOf(id);
      const [lo, hi] = a < b ? [a, b] : [b, a];
      ids.slice(lo, hi + 1).forEach(rid => selectedIds.add(rid));
      _syncAllRowsSelected();
      updateSelectionUI();
    } else {
      // Plain click: select only this row (deselect others)
      const alreadyOnlyOne = selectedIds.size === 1 && selectedIds.has(id);
      selectedIds.clear();
      if (!alreadyOnlyOne) selectedIds.add(id);
      _syncAllRowsSelected();
      updateSelectionUI();
    }
    lastClickedId = id;
  });
  list.addEventListener('dblclick', e => {
    if (e.target.closest('.btn-play')) return;
    const row = e.target.closest('.item-row');
    if (!row) return;
    const id = row.dataset.id;
    if (duplicateMap.has(id)) showCompareModal(id);
    else showDetail(id);
  });
});

// ── Library fetch & render ────────────────────────────────────────────────────
async function fetchLibrary() {
  try {
    allItems = await api('GET', '/api/library');
    renderLocationFilters();   // the Transcoded group only shows once it has items
    applyFilters();
    // Refresh the NFO count in the sidebar if status is already loaded
    if (lastStatus) renderStatusPanel(lastStatus);
  } catch (e) {
    showToast('Failed to load library: ' + e.message, 'danger');
  }
}

async function fetchStatus() {
  try {
    const status = await api('GET', '/api/status');
    lastStatus = status;
    _buildDuplicateMap(status);
    renderStatusPanel(status);
  } catch (_) {}
}

async function fetchConfig() {
  try {
    configCache = await api('GET', '/api/config');
  } catch (_) { return; }
  renderLocationFilters();
  applyFilters();
}

// Build a map of item.id → array of duplicate peers for fast lookup
function _buildDuplicateMap(status) {
  duplicateMap.clear();
  duplicateGroups = [];
  dupGroupOf.clear();
  const allDups = [...(status.duplicate_movies || []), ...(status.duplicate_series || [])];
  for (const group of allDups) {
    const gIdx = duplicateGroups.length;
    duplicateGroups.push(group);
    for (const item of group.items) {
      dupGroupOf.set(item.id, gIdx);
      const others = group.items.filter(i => i.id !== item.id);
      if (others.length > 0) duplicateMap.set(item.id, others);
    }
  }
}

// ── Special filters (duplicates / incomplete NFO / missing episodes) ────────
function toggleSpecialFilter(filter) {
  activeSpecialFilter = (activeSpecialFilter === filter) ? null : filter;
  if (activeSpecialFilter !== 'duplicates') transcodedDupOnly = false;
  if (lastStatus) renderStatusPanel(lastStatus);
  applyFilters();
}

// Quick filter from sidebar/status-modal counts. null/null resets everything.
// Clicking the row that is already active clears the filters again (toggle).
function quickFilter(location, type) {
  const key = location && type ? `${location}:${type}` : null;
  if (key && activeQuickFilter === key) {
    location = null;
    type = null;
    activeQuickFilter = null;
  } else {
    activeQuickFilter = key;
  }
  setLocGroup('library',    !location || location === 'library');
  setLocGroup('download',   !location || location === 'download');
  setLocGroup('transcoded', !location || location === 'transcoded');
  renderLocationFilters();
  document.getElementById('f-movies').checked   = !type || type === 'movie';
  document.getElementById('f-series').checked   = !type || type === 'series';
  document.getElementById('search-input').value = '';
  activeSpecialFilter = null;
  transcodedDupOnly = false;
  if (lastStatus) renderStatusPanel(lastStatus);
  applyFilters();
}

// Manual change of the filters above the summary — the summary marking no longer applies
function clearQuickFilterMark() {
  if (!activeQuickFilter) return;
  activeQuickFilter = null;
  if (lastStatus) renderStatusPanel(lastStatus);
}

// onchange handler for the manual Type checkboxes / search input
function manualFilterChanged() {
  clearQuickFilterMark();
  applyFilters();
}

// From the status modal stat cards: close the modal, then filter the list
function statusModalFilter(location, type) {
  getModal('statusModal').hide();
  quickFilter(location, type);
}

// ── Location filter tree ──────────────────────────────────────────────────────
const _LOC_INFO = {
  library:    { label: 'Library',    color: 'success', icon: '📚' },
  download:   { label: 'Downloads',  color: 'warning', icon: '⬇' },
  transcoded: { label: 'Transcoded', color: 'primary', icon: '⚙' },
};
const _locInfo  = loc => _LOC_INFO[loc] || _LOC_INFO.download;
const _locLabel = loc => `${_locInfo(loc).icon} ${_locInfo(loc).label}`;

function locBadgeHtml(loc, title, extraCls) {
  const l = _locInfo(loc);
  return `<span class="badge bg-${l.color}-subtle text-${l.color}-emphasis${extraCls ? ' ' + extraCls : ''}"`
    + `${title ? ` title="${esc(title)}"` : ''}>${l.label}</span>`;
}

// Transcoded group roots are the automatic Movies/Series subfolders of the output folder
function _transcodedRoots() {
  const r = ((configCache.transcode || {}).output_root || '').trim().replace(/^"|"$/g, '').replace(/[\\/]+$/, '');
  return r ? [r + '\\Movies', r + '\\Series'] : [];
}

const _LOC_GROUPS = [
  { key: 'library',    label: 'Library',    badge: 'bg-success-subtle text-success-emphasis', roots: () => configCache.locations || [] },
  { key: 'download',   label: 'Downloads',  badge: 'bg-warning-subtle text-warning-emphasis', roots: () => configCache.downloads || [] },
  { key: 'transcoded', label: 'Transcoded', badge: 'bg-primary-subtle text-primary-emphasis', roots: _transcodedRoots,
    shortNames: true, onlyWithItems: true },
];

const _locKey = item => item.location in locState ? item.location : 'download';

function _locGroupShown(g) {
  return !g.onlyWithItems || allItems.some(i => i.location === g.key);
}

function renderLocationFilters() {
  const el = document.getElementById('location-filters');
  el.innerHTML = _LOC_GROUPS.map(g => {
    // Sync state with configured paths (keep prior choices, default new roots to on)
    const prev = locState[g.key];
    const next = {};
    for (const p of g.roots()) {
      if (p) next[p] = p in prev ? prev[p] : true;
    }
    locState[g.key] = next;
    if (!_locGroupShown(g)) return '';
    const roots = Object.keys(next);
    const expanded = locExpanded[g.key];

    const children = roots.map((r, i) => `
      <div class="form-check loc-child">
        <input class="form-check-input" type="checkbox" id="loc-${g.key}-${i}"
               data-group="${g.key}" data-root="${esc(r)}" ${next[r] ? 'checked' : ''}>
        <label class="form-check-label small text-truncate d-block" for="loc-${g.key}-${i}"
               title="${esc(r)}">${esc(g.shortNames ? r.split(/[\\/]/).pop() : r)}</label>
      </div>`).join('');

    return `
      <div class="form-check d-flex align-items-center pe-0">
        <input class="form-check-input" type="checkbox" id="f-${g.key}" data-group="${g.key}" data-top="1">
        <label class="form-check-label" for="f-${g.key}">
          <span class="badge ${g.badge} me-1">●</span>${g.label}
        </label>
        ${roots.length ? `
        <i class="bi bi-chevron-${expanded ? 'down' : 'right'} loc-caret ms-auto"
           data-expand="${g.key}" role="button" title="${expanded ? 'Collapse' : 'Expand'} locations"></i>` : ''}
      </div>
      ${roots.length && expanded ? `<div class="loc-children ms-3">${children}</div>` : ''}`;
  }).join('');

  // Top checkbox state: checked / indeterminate (square-in-square) / unchecked
  for (const g of _LOC_GROUPS) {
    const top  = document.getElementById('f-' + g.key);
    if (!top) continue;
    const vals = Object.values(locState[g.key]);
    if (vals.length) {
      top.checked       = vals.every(Boolean);
      top.indeterminate = vals.some(Boolean) && !vals.every(Boolean);
    } else {
      top.checked = locTopEmpty[g.key];
    }
  }
}

function setLocGroup(key, on) {
  for (const r of Object.keys(locState[key])) locState[key][r] = on;
  locTopEmpty[key] = on;
}

const _normRoot = p => p.toLowerCase().replace(/[\\/]+$/, '');

// Longest configured root the item's path falls under, or null
function _itemRoot(item) {
  const state = locState[_locKey(item)];
  const ip = (item.path || '').toLowerCase();
  let best = null;
  for (const r of Object.keys(state)) {
    const n = _normRoot(r);
    if (ip === n || ip.startsWith(n + '\\') || ip.startsWith(n + '/')) {
      if (!best || n.length > _normRoot(best).length) best = r;
    }
  }
  return best;
}

function _locVisible(item) {
  const key   = _locKey(item);
  const state = locState[key];
  const roots = Object.keys(state);
  if (!roots.length) return locTopEmpty[key];
  const root = _itemRoot(item);
  if (root !== null) return state[root];
  return Object.values(state).some(Boolean);   // path outside configured roots
}

// ── Filters ───────────────────────────────────────────────────────────────────
function applyFilters() {
  const showMov  = document.getElementById('f-movies').checked;
  const showSer  = document.getElementById('f-series').checked;
  const q        = document.getElementById('search-input').value.toLowerCase().trim();

  filteredItems = allItems.filter(item => {
    if (!_locVisible(item))                        return false;
    if (!showMov && item.type === 'movie')         return false;
    if (!showSer && item.type === 'series')        return false;
    if (q && !item.title.toLowerCase().includes(q) && !item.folder.toLowerCase().includes(q)) return false;
    return true;
  });

  // Special filters (mutually exclusive, applied after normal filters)
  if (activeSpecialFilter === 'duplicates') {
    filteredItems = filteredItems.filter(item => duplicateMap.has(item.id));
    if (transcodedDupOnly) {
      filteredItems = filteredItems.filter(item => {
        const g = duplicateGroups[dupGroupOf.get(item.id)];
        return g && g.transcoded;
      });
    }
  } else if (activeSpecialFilter === 'missing-nfo') {
    filteredItems = filteredItems.filter(item => (item.nfo_quality || 'none') !== 'full');
  } else if (activeSpecialFilter === 'missing-eps') {
    filteredItems = filteredItems.filter(item =>
      item.type === 'series' && Object.keys(item.missing_episodes || {}).length > 0);
  }

  renderItems();
  document.getElementById('item-count').textContent = `${filteredItems.length} item(s)`;
  _updateSelectTranscodedBtn();
}

function _updateSelectTranscodedBtn() {
  const btn = document.getElementById('btn-select-transcoded');
  const show = activeSpecialFilter === 'duplicates'
    && (transcodedDupOnly || duplicateGroups.some(g => g.transcoded));
  btn.classList.toggle('d-none', !show);
  btn.classList.toggle('active', transcodedDupOnly);
  btn.innerHTML = `<i class="bi bi-${transcodedDupOnly ? 'check-square-fill' : 'cpu'} me-1"></i>Select transcoded`;
}

// Duplicates view: show only transcode-caused groups and select their transcoded copies (toggle)
function toggleSelectTranscoded() {
  transcodedDupOnly = !transcodedDupOnly;
  selectedIds.clear();
  applyFilters();
  if (transcodedDupOnly) {
    filteredItems.filter(i => i.location === 'transcoded').forEach(i => selectedIds.add(i.id));
  }
  _syncAllRowsSelected();
  updateSelectionUI();
}

// ── Rendering ─────────────────────────────────────────────────────────────────
function renderItems() {
  const list = document.getElementById('item-list');
  if (filteredItems.length === 0) {
    list.innerHTML = `<div class="text-center text-muted mt-5 pt-4">
      <i class="bi bi-collection fs-1"></i>
      <p class="mt-2">No items match the current filters.</p>
    </div>`;
    return;
  }

  list.innerHTML = activeSpecialFilter === 'duplicates'
    ? renderDuplicateGroups()
    : filteredItems.map(i => renderRow(i)).join('');
}

// One card per duplicate group, with the copies as selectable sub-rows
function renderDuplicateGroups() {
  const byGroup = new Map();   // group index → members present in filteredItems
  const loose = [];
  for (const item of filteredItems) {
    const g = dupGroupOf.get(item.id);
    if (g === undefined) { loose.push(item); continue; }
    if (!byGroup.has(g)) byGroup.set(g, []);
    byGroup.get(g).push(item);
  }
  const cards = [...byGroup.entries()].map(([gIdx, members]) => {
    const total = duplicateGroups[gIdx].items.length;
    const countNote = members.length < total
      ? `${members.length} of ${total} copies shown (others hidden by filters)`
      : `${total} copies`;
    return `
    <div class="dup-group mx-3 my-3 border border-danger-subtle rounded">
      <div class="dup-group-header d-flex align-items-center gap-2 px-3 py-2">
        <i class="bi bi-copy text-danger"></i>
        <span class="fw-semibold text-danger-emphasis text-truncate">${esc(members[0].title)}</span>
        <span class="text-muted small">${countNote}</span>
        <div class="ms-auto d-flex gap-1 flex-shrink-0">
          <button type="button" class="btn btn-sm btn-outline-secondary py-0" data-select-group="${gIdx}"
                  title="Select/deselect all copies in this group">
            <i class="bi bi-check2-square me-1"></i>Select group
          </button>
          <button type="button" class="btn btn-sm btn-outline-danger py-0" data-compare-group="${gIdx}"
                  title="Compare copies side by side">
            <i class="bi bi-arrows-angle-expand me-1"></i>Compare
          </button>
        </div>
      </div>
      ${members.map(i => renderRow(i)).join('')}
    </div>`;
  }).join('');
  return cards + loose.map(i => renderRow(i)).join('');
}

function toggleGroupSelection(gIdx) {
  const group = duplicateGroups[gIdx];
  if (!group) return;
  const ids = group.items.map(i => i.id).filter(id => filteredItems.some(f => f.id === id));
  const allSelected = ids.length > 0 && ids.every(id => selectedIds.has(id));
  ids.forEach(id => allSelected ? selectedIds.delete(id) : selectedIds.add(id));
  _syncAllRowsSelected();
  updateSelectionUI();
}

// NFO indicator icon — 3 states: full (teal), partial (orange + strikethrough), absent (dimmed)
function nfoIconHtml(item) {
  const quality = item.nfo_quality || (item.nfo && Object.keys(item.nfo).length ? 'partial' : 'none');
  const isSeries = item.type === 'series';
  if (quality === 'full') {
    const t = isSeries ? 'NFO complete (all episodes have an NFO)' : 'NFO complete (year + ID present)';
    return `<i class="bi bi-file-earmark-text nfo-icon nfo-present" title="${t}"></i>`;
  }
  if (quality === 'partial') {
    const t = isSeries ? 'NFO incomplete — some episodes missing an NFO' : 'NFO incomplete — missing year or external ID';
    return `<i class="bi bi-file-earmark-text nfo-icon nfo-partial" title="${t}"></i>`;
  }
  return '<i class="bi bi-file-earmark nfo-icon nfo-absent" title="No NFO file"></i>';
}

function renderRow(item) {
  const selected = selectedIds.has(item.id) ? ' selected' : '';

  const locBadge = locBadgeHtml(item.location, item.path);

  const typeBadge = item.type === 'series'
    ? '<span class="badge bg-info-subtle text-info-emphasis"><i class="bi bi-tv me-1"></i>Series</span>'
    : '<span class="badge bg-secondary-subtle text-secondary-emphasis"><i class="bi bi-film me-1"></i>Movie</span>';

  const yearStr = item.year
    ? ` <span class="text-muted">(${esc(item.year)})</span>` : '';

  const epStr = item.type === 'series' && item.total_episodes
    ? ` <span class="text-muted small">${item.total_episodes} ep</span>` : '';

  const missBadge = item.type === 'series' && Object.keys(item.missing_episodes || {}).length
    ? ' <span class="badge bg-danger-subtle text-danger-emphasis"><i class="bi bi-exclamation-circle me-1"></i>Missing eps</span>'
    : '';

  const nfoIcon = nfoIconHtml(item);

  return `
  <div class="item-row d-flex align-items-center gap-2 px-3 py-2 border-bottom border-secondary-subtle${selected}"
       data-id="${esc(item.id)}">
    <div class="flex-grow-1 min-w-0">
      <div class="fw-semibold text-truncate">
        ${esc(item.title)}${yearStr}${epStr}${missBadge}
      </div>
      <div class="small text-muted text-truncate" title="${esc(item.path)}">${esc(item.path)}</div>
    </div>
    <div class="d-flex align-items-center gap-1 flex-shrink-0">
      ${nfoIcon} ${locBadge} ${typeBadge}
      <button type="button" class="btn btn-sm btn-play border-0 py-0 px-1"${_playAttrs(item, 'Play in system player')}>
        <i class="bi bi-play-circle"></i>
      </button>
    </div>
  </div>`;
}

// ── Status panel (sidebar) ────────────────────────────────────────────────────
function renderStatusPanel(status) {
  if (!status || !Object.keys(status).length) return;

  const dupCount  = (status.duplicate_movies  || []).length
                  + (status.duplicate_series  || []).length;
  const missCount = (status.missing_episodes  || []).length;

  // Count items with incomplete/absent NFO from the already-loaded library
  const incompleteNfoCount = allItems.filter(i => (i.nfo_quality || 'none') !== 'full').length;

  const sfDup  = activeSpecialFilter === 'duplicates';
  const sfNfo  = activeSpecialFilter === 'missing-nfo';
  const sfMiss = activeSpecialFilter === 'missing-eps';

  const qf = (loc, type) => activeQuickFilter === `${loc}:${type}` ? ' qf-active' : '';
  const qfTitle = (loc, type, label) =>
    activeQuickFilter === `${loc}:${type}` ? 'Active filter — click to clear' : label;

  document.getElementById('status-panel').innerHTML = `
    <div class="status-grid">
      <div class="status-row status-link${qf('library', 'movie')}" onclick="quickFilter('library', 'movie')" title="${qfTitle('library', 'movie', 'Show library movies')}">
        <span class="dot dot-success"></span>
        <span>${status.library_movies || 0} movies</span>
      </div>
      <div class="status-row status-link${qf('library', 'series')}" onclick="quickFilter('library', 'series')" title="${qfTitle('library', 'series', 'Show library series')}">
        <span class="dot dot-success"></span>
        <span>${status.library_series || 0} series
          <span class="text-muted">(${status.total_library_episodes || 0} ep)</span>
        </span>
      </div>
      <div class="status-row status-link${qf('download', 'movie')}" onclick="quickFilter('download', 'movie')" title="${qfTitle('download', 'movie', 'Show download movies')}">
        <span class="dot dot-warning"></span>
        <span>${status.download_movies || 0} Download movies</span>
      </div>
      <div class="status-row status-link${qf('download', 'series')}" onclick="quickFilter('download', 'series')" title="${qfTitle('download', 'series', 'Show download series')}">
        <span class="dot dot-warning"></span>
        <span>${status.download_series || 0} Download series
          <span class="text-muted">(${status.total_download_episodes || 0} ep)</span>
        </span>
      </div>
      ${status.transcoded_movies ? `
      <div class="status-row status-link${qf('transcoded', 'movie')}" onclick="quickFilter('transcoded', 'movie')" title="${qfTitle('transcoded', 'movie', 'Show transcoded movies')}">
        <span class="dot dot-primary"></span>
        <span>${status.transcoded_movies} Transcoded movies</span>
      </div>` : ''}
      ${status.transcoded_series ? `
      <div class="status-row status-link${qf('transcoded', 'series')}" onclick="quickFilter('transcoded', 'series')" title="${qfTitle('transcoded', 'series', 'Show transcoded series')}">
        <span class="dot dot-primary"></span>
        <span>${status.transcoded_series} Transcoded series
          <span class="text-muted">(${status.total_transcoded_episodes || 0} ep)</span>
        </span>
      </div>` : ''}
      ${missCount > 0 ? `
      <div class="status-row status-link text-warning-emphasis${sfMiss ? ' fw-semibold' : ''}"
           onclick="toggleSpecialFilter('missing-eps')" title="Show only series with missing episodes">
        <i class="bi bi-${sfMiss ? 'check-square-fill text-warning' : 'exclamation-circle text-warning'}"></i>
        <span>${missCount} series w/ missing ep</span>
      </div>` : ''}
    </div>
    <hr class="border-secondary my-2">
    <div class="filter-label">Show only</div>
    <div class="d-flex flex-column gap-1 mt-1">
      <div class="special-filter-row${sfDup ? ' sf-active' : ''}"
           onclick="toggleSpecialFilter('duplicates')">
        <i class="bi bi-${sfDup ? 'check-square-fill text-danger' : 'square'} me-2"></i>
        <span>Duplicates</span>
        <span class="ms-auto badge ${dupCount > 0 ? 'bg-danger-subtle text-danger-emphasis' : 'bg-secondary-subtle text-secondary-emphasis'}">${dupCount}</span>
      </div>
      <div class="special-filter-row${sfNfo ? ' sf-active' : ''}"
           onclick="toggleSpecialFilter('missing-nfo')">
        <i class="bi bi-${sfNfo ? 'check-square-fill text-warning' : 'square'} me-2"></i>
        <span>Incomplete NFO</span>
        <span class="ms-auto badge ${incompleteNfoCount > 0 ? 'bg-warning-subtle text-warning-emphasis' : 'bg-secondary-subtle text-secondary-emphasis'}">${incompleteNfoCount}</span>
      </div>
    </div>`;
}

// ── Selection ─────────────────────────────────────────────────────────────────
function _syncRowSelected(row, on) {
  row.classList.toggle('selected', on);
}

function _syncAllRowsSelected() {
  document.querySelectorAll('#item-list .item-row').forEach(row => {
    row.classList.toggle('selected', selectedIds.has(row.dataset.id));
  });
}

function toggleSelectAll() {
  if (selectedIds.size === filteredItems.length && filteredItems.length > 0) {
    clearSelection();
  } else {
    filteredItems.forEach(i => selectedIds.add(i.id));
    _syncAllRowsSelected();
    updateSelectionUI();
  }
}

function clearSelection() {
  selectedIds.clear();
  _syncAllRowsSelected();
  updateSelectionUI();
}

function updateSelectionUI() {
  const count = selectedIds.size;
  document.getElementById('sel-count').textContent = count;
  document.getElementById('selection-info').classList.toggle('d-none', count === 0);
  _updateTranscodeButton();
}

// ── Status modal ──────────────────────────────────────────────────────────────
async function showStatusModal() {
  getModal('statusModal').show();
  try {
    const s = await api('GET', '/api/status');
    const dups    = [...(s.duplicate_movies || []), ...(s.duplicate_series || [])];
    const missing = s.missing_episodes || [];

    document.getElementById('status-modal-body').innerHTML = `
      <!-- Stat cards -->
      <div class="row g-3 mb-4">
        <div class="col-6 col-md-3">
          <div class="stat-card border-success stat-clickable" onclick="statusModalFilter('library', 'movie')" title="Show library movies">
            <div class="stat-num">${s.library_movies || 0}</div>
            <div class="stat-label">Library Movies</div>
          </div>
        </div>
        <div class="col-6 col-md-3">
          <div class="stat-card border-success stat-clickable" onclick="statusModalFilter('library', 'series')" title="Show library series">
            <div class="stat-num">${s.library_series || 0}</div>
            <div class="stat-label">Library Series</div>
            <div class="stat-sub">${s.total_library_episodes || 0} episodes</div>
          </div>
        </div>
        <div class="col-6 col-md-3">
          <div class="stat-card border-warning stat-clickable" onclick="statusModalFilter('download', 'movie')" title="Show download movies">
            <div class="stat-num">${s.download_movies || 0}</div>
            <div class="stat-label">Download Movies</div>
          </div>
        </div>
        <div class="col-6 col-md-3">
          <div class="stat-card border-warning stat-clickable" onclick="statusModalFilter('download', 'series')" title="Show download series">
            <div class="stat-num">${s.download_series || 0}</div>
            <div class="stat-label">Download Series</div>
            <div class="stat-sub">${s.total_download_episodes || 0} episodes</div>
          </div>
        </div>
      </div>

      ${dups.length ? `
      <h6 class="text-danger mb-2">
        <i class="bi bi-copy me-1"></i>Potential Duplicates (${dups.length})
      </h6>
      <div class="mb-4">
        ${dups.map(d => `
          <div class="alert alert-danger py-2 mb-1">
            <div class="fw-semibold">${esc(d.title)}</div>
            ${d.items.map(i =>
              `<div class="small ms-2 text-muted">
                 <i class="bi bi-folder me-1"></i>${esc(i.title)}
                 <span class="badge bg-secondary ms-1">${i.location}</span>
                 <span class="text-muted ms-1 font-monospace">${esc(i.path)}</span>
               </div>`
            ).join('')}
          </div>`).join('')}
      </div>` : '<p class="text-muted small">No duplicates detected.</p>'}

      ${missing.length ? `
      <h6 class="text-warning mb-2">
        <i class="bi bi-exclamation-circle me-1"></i>Missing Episodes (${missing.length} series)
      </h6>
      <div>
        ${missing.map(m => {
          const seasons = Object.entries(m.missing || {});
          return `
            <div class="alert alert-warning py-2 mb-1">
              <div class="fw-semibold">${esc(m.title)}</div>
              ${seasons.map(([s, eps]) =>
                `<div class="small ms-2">
                   Season ${esc(s)}: missing
                   ${eps.map(e =>
                     `<span class="badge bg-danger ms-1">E${String(e).padStart(2,'0')}</span>`
                   ).join('')}
                 </div>`
              ).join('')}
            </div>`;
        }).join('')}
      </div>` : '<p class="text-muted small">No missing episodes detected.</p>'}`;
  } catch (e) {
    document.getElementById('status-modal-body').innerHTML =
      `<div class="text-danger">Failed to load status: ${esc(e.message)}</div>`;
  }
}
