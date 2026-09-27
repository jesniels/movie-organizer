'use strict';

// ── State ─────────────────────────────────────────────────────────────────────
let allItems            = [];
let filteredItems       = [];
let selectedIds         = new Set();
let currentDetail       = null;    // item currently shown in detail modal
let lastScanTime        = null;
let lastClickedId       = null;    // for shift-click range selection
let configCache         = { locations: [], downloads: [] };
let _pollId             = null;    // setInterval id for scan polling
let activeSpecialFilter = null;    // null | 'duplicates' | 'missing-nfo' | 'missing-eps'
let activeQuickFilter   = null;    // null | 'location:type' — sidebar summary row currently driving the filters
let duplicateMap        = new Map(); // item.id → [{ id, title, location, path }, ...]
let duplicateGroups     = [];      // [{ title, items: [...] }, ...] from /api/status
let dupGroupOf          = new Map(); // item.id → index into duplicateGroups
let lastStatus          = null;    // cached last /api/status response
let lastScanStatus      = null;    // cached last /api/scan/status response
let compareIds          = [];      // [leftId, rightId] currently shown in compare modal

// Location filter state: per group, configured root path → checked
const locState    = { library: {}, download: {} };
const locExpanded = { library: false, download: false };
const locTopEmpty = { library: true, download: true };  // top state when group has no roots

// ── Bootstrap modal helpers ───────────────────────────────────────────────────
const getModal = id => bootstrap.Modal.getOrCreateInstance(document.getElementById(id));

// ── Init ──────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  // Load settings whenever the settings modal opens
  document.getElementById('settingsModal').addEventListener('show.bs.modal', loadSettings);

  renderLocationFilters();   // render top-level rows before config arrives
  fetchConfig();
  pollScanStatus();   // immediate check on load; starts polling if scan is running
  fetchLibrary();
  fetchStatus();

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

  // Event delegation for compare modal (play / copy-NFO buttons)
  document.getElementById('compare-body').addEventListener('click', e => {
    const playBtn = e.target.closest('[data-play-file]');
    if (playBtn) {
      playItem(playBtn.dataset.itemId, playBtn.dataset.playFile || undefined);
      return;
    }
    const copyBtn = e.target.closest('[data-copy-nfo-from]');
    if (copyBtn) {
      copyNfo(copyBtn.dataset.copyNfoFrom, copyBtn.dataset.copyNfoTo);
      return;
    }
    const peerSel = e.target.closest('[data-peer-id]');
    if (peerSel) {
      showCompareModal(compareIds[0], peerSel.dataset.peerId);
    }
  });

  // Event delegation for delete modal (play buttons + label click toggles checkbox)
  document.getElementById('delete-list').addEventListener('click', e => {
    const playBtn = e.target.closest('[data-play-item]');
    if (playBtn) { playItem(playBtn.dataset.playItem); return; }
    const label = e.target.closest('.delete-item-label');
    if (label) {
      const cb = label.parentElement.querySelector('.delete-check');
      if (cb) { cb.checked = !cb.checked; updateDeleteCount(); }
    }
  });

  // Global delegation: NFO view buttons (detail + compare + delete modals)
  document.addEventListener('click', e => {
    const nfoBtn = e.target.closest('[data-view-nfo]');
    if (nfoBtn) showNfoView(nfoBtn.dataset.viewNfo);
  });
});

// ── API helper ────────────────────────────────────────────────────────────────
async function api(method, path, body) {
  const opts = { method, headers: { 'Content-Type': 'application/json' } };
  if (body !== undefined) opts.body = JSON.stringify(body);
  const res = await fetch(path, opts);
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`${res.status}: ${text}`);
  }
  return res.json();
}

// ── Library fetch & render ────────────────────────────────────────────────────
async function fetchLibrary() {
  try {
    allItems = await api('GET', '/api/library');
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
  setLocGroup('library',  !location || location === 'library');
  setLocGroup('download', !location || location === 'download');
  renderLocationFilters();
  document.getElementById('f-movies').checked   = !type || type === 'movie';
  document.getElementById('f-series').checked   = !type || type === 'series';
  document.getElementById('search-input').value = '';
  activeSpecialFilter = null;
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
const _LOC_GROUPS = [
  { key: 'library',  label: 'Library',   badge: 'bg-success-subtle text-success-emphasis', cfg: 'locations' },
  { key: 'download', label: 'Downloads', badge: 'bg-warning-subtle text-warning-emphasis', cfg: 'downloads' },
];

function renderLocationFilters() {
  const el = document.getElementById('location-filters');
  el.innerHTML = _LOC_GROUPS.map(g => {
    // Sync state with configured paths (keep prior choices, default new roots to on)
    const prev = locState[g.key];
    const next = {};
    for (const p of (configCache[g.cfg] || [])) {
      if (p) next[p] = p in prev ? prev[p] : true;
    }
    locState[g.key] = next;
    const roots = Object.keys(next);
    const expanded = locExpanded[g.key];

    const children = roots.map((r, i) => `
      <div class="form-check loc-child">
        <input class="form-check-input" type="checkbox" id="loc-${g.key}-${i}"
               data-group="${g.key}" data-root="${esc(r)}" ${next[r] ? 'checked' : ''}>
        <label class="form-check-label small text-truncate d-block" for="loc-${g.key}-${i}"
               title="${esc(r)}">${esc(r)}</label>
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
  const state = locState[item.location === 'library' ? 'library' : 'download'];
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
  const key   = item.location === 'library' ? 'library' : 'download';
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
  } else if (activeSpecialFilter === 'missing-nfo') {
    filteredItems = filteredItems.filter(item => (item.nfo_quality || 'none') !== 'full');
  } else if (activeSpecialFilter === 'missing-eps') {
    filteredItems = filteredItems.filter(item =>
      item.type === 'series' && Object.keys(item.missing_episodes || {}).length > 0);
  }

  renderItems();
  document.getElementById('item-count').textContent = `${filteredItems.length} item(s)`;
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

  const locBadge = item.location === 'library'
    ? `<span class="badge bg-success-subtle text-success-emphasis" title="${esc(item.path)}">Library</span>`
    : `<span class="badge bg-warning-subtle text-warning-emphasis" title="${esc(item.path)}">Downloads</span>`;

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
      <button type="button" class="btn btn-sm btn-play border-0 py-0 px-1" title="Play in system player">
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

// ── Detail modal ──────────────────────────────────────────────────────────────
function showDetail(itemId) {
  const item = allItems.find(i => i.id === itemId);
  if (!item) return;
  currentDetail = item;
  document.getElementById('detail-title').textContent = item.title;
  document.getElementById('detail-body').innerHTML =
    item.type === 'series' ? renderSeriesDetail(item) : renderMovieDetail(item);
  getModal('detailModal').show();
}

// ── NFO status & completeness reasons ────────────────────────────────────
function nfoIssues(item) {
  const n = item.nfo || {};
  if (item.type === 'series') {
    // Series quality is based on per-episode NFO coverage, not tvshow.nfo
    const total   = (item.files || []).length;
    const withNfo = item.episode_nfo_count || 0;
    const issues  = [];
    if (withNfo < total)
      issues.push({ text: `${total - withNfo} of ${total} episode(s) missing an .nfo file next to the video`, required: true });
    if (!Object.keys(n).length)
      issues.push({ text: 'No series-level tvshow.nfo (optional — Jellyfin matches via episode NFOs)', required: false });
    return issues;
  }
  if (!Object.keys(n).length) {
    return [{ text: 'No .nfo file found (or it could not be parsed as XML)', required: true }];
  }
  const issues = [];
  if (!n.year)
    issues.push({ text: 'Missing <year> — Jellyfin needs it to match the movie', required: true });
  if (!n.uniqueids || !Object.keys(n.uniqueids).length)
    issues.push({ text: 'Missing unique ID (tmdb / imdb / tvdb) — Jellyfin needs it to match', required: true });
  if (!n.title)  issues.push({ text: 'Missing <title>', required: false });
  if (!n.plot)   issues.push({ text: 'Missing <plot>', required: false });
  if (!(n.genre || []).length) issues.push({ text: 'Missing <genre>', required: false });
  if (!n.rating) issues.push({ text: 'Missing <rating>', required: false });
  return issues;
}

// Status label; for incomplete/missing NFO it is clickable and expands the reasons
function nfoStatusHtml(item) {
  const n = item.nfo || {};
  const quality = item.nfo_quality || (Object.keys(n).length ? 'partial' : 'none');
  // For series the eye button opens tvshow.nfo, which only exists when item.nfo is populated
  const hasViewable = item.type === 'series' ? Object.keys(n).length > 0 : quality !== 'none';
  const viewBtn = !hasViewable ? '' : `
    <button type="button" class="btn btn-sm btn-link text-info p-0 ms-2 align-baseline"
            title="View NFO file" data-view-nfo="${esc(item.id)}">
      <i class="bi bi-eye"></i>
    </button>`;
  if (quality === 'full') {
    const label = item.type === 'series' ? 'Complete (all episodes)' : 'Complete';
    return `<span class="text-info"><i class="bi bi-file-earmark-text me-1"></i>${label}</span>${viewBtn}`;
  }
  const label = quality === 'partial'
    ? '<span class="text-warning"><i class="bi bi-file-earmark-text me-1"></i>Incomplete</span>'
    : '<span class="text-secondary"><i class="bi bi-file-earmark me-1"></i>Not found</span>';
  const list = nfoIssues(item).map(i => `
    <div class="${i.required ? 'text-danger-emphasis' : 'text-muted'}">
      <i class="bi bi-${i.required ? 'x-circle' : 'dash-circle'} me-1"></i>${esc(i.text)}
      ${i.required ? '<span class="badge bg-danger-subtle text-danger-emphasis ms-1">required</span>' : ''}
    </div>`).join('');
  return `
    <span role="button" title="Click to see why"
          onclick="this.parentElement.querySelector('.nfo-issues').classList.toggle('d-none')">
      ${label}<i class="bi bi-question-circle ms-1 small text-muted"></i>
    </span>${viewBtn}
    <div class="nfo-issues d-none small mt-1">${list}</div>`;
}

// ── NFO file viewer ─────────────────────────────────────────────────────────
async function showNfoView(itemId) {
  try {
    const res = await api('GET', '/api/nfo?item_id=' + encodeURIComponent(itemId));
    document.getElementById('nfo-view-path').textContent = res.path;
    document.getElementById('nfo-view-content').textContent = res.content;
    getModal('nfoViewModal').show();
  } catch (e) {
    showToast('Could not load NFO: ' + e.message, 'danger');
  }
}

function renderMovieDetail(item) {
  const n = item.nfo || {};
  const nfoStatus = nfoStatusHtml(item);

  const uniqueIds = n.uniqueids || {};
  const uidText = Object.entries(uniqueIds).map(([k, v]) =>
    `<span class="badge bg-secondary-subtle text-secondary-emphasis me-1">${esc(k)}:${esc(v)}</span>`
  ).join('') || '–';

  const actors = (n.actors || []).slice(0, 12);
  const actorText = actors.length
    ? actors.map(a => `<span class="badge bg-dark border border-secondary me-1 mb-1">${esc(a)}</span>`).join('')
    : '–';

  const rows = [
    ['Title',    n.title   || item.title],
    ['Year',     n.year    || item.year    || '–'],
    ['Genre',    (n.genre  || []).join(', ') || '–'],
    ['Studio',   n.studio  || '–'],
    ['Rating',   n.rating  || '–'],
    ['Tagline',  n.tagline || '–'],
    ['NFO',      null],   // rendered via nfoStatus
    ['IDs',      null],   // rendered via uidText
    ['Actors',   null],   // rendered via actorText
    ['Location', item.location === 'library' ? '📚 Library' : '⬇ Downloads'],
    ['Path',     item.path],
    ['Files',    item.files.map(f => f.split(/[/\\]/).pop()).join(', ')],
  ];
  return `
    <table class="table table-sm table-dark table-striped mb-2">
      <tbody>
        ${rows.map(([k, v]) => {
          if (k === 'NFO')    return `<tr><th class="text-muted fw-normal" style="width:90px">${esc(k)}</th><td>${nfoStatus}</td></tr>`;
          if (k === 'IDs')    return `<tr><th class="text-muted fw-normal" style="width:90px">IDs</th><td>${uidText}</td></tr>`;
          if (k === 'Actors') return `<tr><th class="text-muted fw-normal" style="width:90px">Actors</th><td>${actorText}</td></tr>`;
          return `<tr><th class="text-muted fw-normal" style="width:90px">${esc(k)}</th>
                 <td class="text-break">${esc(String(v))}</td></tr>`;
        }).join('')}
      </tbody>
    </table>
    ${n.plot ? `<p class="text-muted small mt-2">${esc(n.plot)}</p>` : ''}`;
}

function renderSeriesDetail(item) {
  const n = item.nfo || {};
  const nfoStatus = nfoStatusHtml(item);

  const uniqueIds = n.uniqueids || {};
  const uidText = Object.entries(uniqueIds).map(([k, v]) =>
    `<span class="badge bg-secondary-subtle text-secondary-emphasis me-1">${esc(k)}:${esc(v)}</span>`
  ).join('') || '–';

  const actors = (n.actors || []).slice(0, 12);
  const actorText = actors.length
    ? actors.map(a => `<span class="badge bg-dark border border-secondary me-1 mb-1">${esc(a)}</span>`).join('')
    : '–';

  const metaRows = [
    ['Title',    n.title   || item.title],
    ['Year',     n.year    || '\u2013'],
    ['Genre',    (n.genre  || []).join(', ') || '\u2013'],
    ['Studio',   n.studio  || '\u2013'],
    ['Rating',   n.rating  || '\u2013'],
    ['Tagline',  n.tagline || '\u2013'],
    ['NFO',      null],
    ['IDs',      null],
    ['Actors',   null],
    ['Location', item.location === 'library' ? '\ud83d\udcda Library' : '\u2b07 Downloads'],
    ['Path',     item.path],
  ];

  const episodes = item.episodes || {};
  const missing  = item.missing_episodes || {};
  const seasons  = Object.keys(episodes).sort((a, b) => +a - +b);

  const seasonBlocks = seasons.map(s => {
    const eps      = episodes[s];
    const missSet  = new Set(missing[s] || []);
    const allEps   = [...new Set([...eps, ...missSet])].sort((a, b) => a - b);
    const badges   = allEps.map(e => {
      const num = String(e).padStart(2, '0');
      return missSet.has(e)
        ? `<span class="badge bg-danger me-1 mb-1" title="Missing">E${num}</span>`
        : `<span class="badge bg-secondary me-1 mb-1">E${num}</span>`;
    }).join('');
    const missTxt = missSet.size
      ? ` · <span class="text-danger">${missSet.size} missing</span>` : '';
    return `
      <div class="mb-3">
        <div class="fw-semibold mb-1">
          Season ${s}
          <span class="text-muted small ms-1">${eps.length} episode(s)${missTxt}</span>
        </div>
        <div>${badges}</div>
      </div>`;
  }).join('');

  return `
    <table class="table table-sm table-dark table-striped mb-3">
      <tbody>
        ${metaRows.map(([k, v]) => {
          if (k === 'NFO')    return `<tr><th class="text-muted fw-normal" style="width:90px">${esc(k)}</th><td>${nfoStatus}</td></tr>`;
          if (k === 'IDs')    return `<tr><th class="text-muted fw-normal" style="width:90px">IDs</th><td>${uidText}</td></tr>`;
          if (k === 'Actors') return `<tr><th class="text-muted fw-normal" style="width:90px">Actors</th><td>${actorText}</td></tr>`;
          return `<tr><th class="text-muted fw-normal" style="width:90px">${esc(k)}</th>
                 <td class="text-break">${esc(String(v))}</td></tr>`;
        }).join('')}
      </tbody>
    </table>
    ${n.plot ? `<p class="text-muted small mb-3">${esc(n.plot)}</p>` : ''}
    ${seasonBlocks || '<div class="text-muted">No episode data found.</div>'}`;
}

// ── Play ───────────────────────────────────────────────────────────────────────
async function playItem(itemId, file) {
  try {
    const body = { item_id: itemId };
    if (file) body.file = file;
    const res = await api('POST', '/api/play', body);
    showToast('Launching player: ' + res.file.split(/[/\\]/).pop(), 'info');
  } catch (e) {
    showToast('Play failed: ' + e.message, 'danger');
  }
}

// ── Duplicate compare modal ────────────────────────────────────────────────
function fmtSize(bytes) {
  if (bytes === null || bytes === undefined) return '–';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let n = bytes, u = 0;
  while (n >= 1024 && u < units.length - 1) { n /= 1024; u++; }
  return n.toFixed(u === 0 ? 0 : 1) + ' ' + units[u];
}

function _compareColumn(item, otherId) {
  const n = item.nfo || {};
  const quality = item.nfo_quality || (Object.keys(n).length ? 'partial' : 'none');
  const nfoStatus = nfoStatusHtml(item);

  const locBadge = item.location === 'library'
    ? '<span class="badge bg-success-subtle text-success-emphasis">Library</span>'
    : '<span class="badge bg-warning-subtle text-warning-emphasis">Downloads</span>';

  const uidText = Object.entries(n.uniqueids || {}).map(([k, v]) =>
    `<span class="badge bg-secondary-subtle text-secondary-emphasis me-1">${esc(k)}:${esc(v)}</span>`
  ).join('') || '–';

  // NFO value, or the scan-derived value marked as "not in the NFO"
  const nfoOr = (nfoVal, scanVal) => nfoVal
    ? esc(nfoVal)
    : scanVal
      ? `<span class="fst-italic opacity-75" title="Not in the NFO — derived from folder/file name">${esc(scanVal)}</span>
         <i class="bi bi-exclamation-circle small text-warning" title="Not in the NFO — derived from folder/file name"></i>`
      : '–';

  const rows = [
    ['Title',  nfoOr(n.title, item.title)],
    ['Year',   nfoOr(n.year, item.year)],
    ['Genre',  esc((n.genre || []).join(', ') || '–')],
    ['Studio', esc(n.studio || '–')],
    ['Rating', esc(n.rating || '–')],
    ['NFO',    nfoStatus],
    ['IDs',    uidText],
    ['Path',   `<span class="font-monospace text-break" style="font-size:.78em">${esc(item.path)}</span>`],
  ];
  if (item.type === 'series') {
    rows.push(['Episodes', esc(String(item.total_episodes || 0))]);
  }

  const copyDisabled = quality === 'none' ? ' disabled' : '';
  return `
    <div class="border border-secondary rounded p-3 h-100 d-flex flex-column">
      <div class="d-flex align-items-center gap-2 mb-2">
        ${locBadge}
        <span class="fw-semibold text-truncate" title="${esc(item.title)}">${esc(item.title)}</span>
      </div>
      <table class="table table-sm table-dark table-striped mb-2">
        <tbody>
          ${rows.map(([k, v]) =>
            `<tr><th class="text-muted fw-normal" style="width:70px">${k}</th><td class="text-break">${v}</td></tr>`
          ).join('')}
        </tbody>
      </table>
      ${n.plot ? `<p class="text-muted small" style="max-height:70px;overflow-y:auto">${esc(n.plot)}</p>` : ''}
      <div class="small text-muted mb-1 mt-auto">Files</div>
      <div class="compare-files small" data-files-for="${esc(item.id)}">
        ${item.files.map(f => `
          <div class="d-flex align-items-center gap-1 py-1 border-top border-secondary-subtle">
            <button type="button" class="btn btn-sm btn-play border-0 py-0 px-1" title="Play this file"
                    data-play-file="${esc(f)}" data-item-id="${esc(item.id)}">
              <i class="bi bi-play-circle"></i>
            </button>
            <span class="text-truncate" title="${esc(f)}">${esc(f.split(/[/\\]/).pop())}</span>
            <span class="ms-auto text-nowrap text-muted file-size" data-file="${esc(f)}">…</span>
          </div>`).join('')}
      </div>
      <div class="d-flex gap-2 mt-3">
        <button type="button" class="btn btn-sm btn-outline-light" data-play-file="" data-item-id="${esc(item.id)}">
          <i class="bi bi-play-fill me-1"></i>Play
        </button>
        <button type="button" class="btn btn-sm btn-outline-info"${copyDisabled}
                data-copy-nfo-from="${esc(item.id)}" data-copy-nfo-to="${esc(otherId)}"
                title="Copy this NFO to the other item">
          <i class="bi bi-arrow-left-right me-1"></i>Copy NFO to other side
        </button>
      </div>
    </div>`;
}

async function showCompareModal(itemId, peerId) {
  const item = allItems.find(i => i.id === itemId);
  const peers = duplicateMap.get(itemId) || [];
  if (!item || peers.length === 0) { showDetail(itemId); return; }

  const peerRef  = peerId ? peers.find(p => p.id === peerId) : peers[0];
  const peerItem = peerRef ? allItems.find(i => i.id === peerRef.id) : null;
  if (!peerItem) { showDetail(itemId); return; }

  compareIds = [item.id, peerItem.id];

  const peerSelector = peers.length > 1 ? `
    <div class="mb-3 d-flex align-items-center gap-2 small">
      <span class="text-muted">Compare with:</span>
      ${peers.map(p => `
        <button type="button" class="btn btn-sm ${p.id === peerItem.id ? 'btn-danger' : 'btn-outline-danger'}"
                data-peer-id="${esc(p.id)}">
          ${esc(p.location)}: ${esc(p.title)}
        </button>`).join('')}
    </div>` : '';

  document.getElementById('compare-body').innerHTML = `
    ${peerSelector}
    <div class="row g-3">
      <div class="col-md-6">${_compareColumn(item, peerItem.id)}</div>
      <div class="col-md-6">${_compareColumn(peerItem, item.id)}</div>
    </div>
    <div class="text-muted small mt-3">
      <i class="bi bi-lightbulb me-1"></i>
      Use <strong>Play</strong> to check both videos, <strong>Copy NFO</strong> to transfer metadata,
      or <strong>Not a duplicate</strong> if these are different titles.
    </div>`;
  getModal('compareModal').show();

  // Fill in file sizes asynchronously
  for (const it of [item, peerItem]) {
    api('GET', `/api/fileinfo?item_id=${encodeURIComponent(it.id)}`).then(infos => {
      const container = document.querySelector(`[data-files-for="${CSS.escape(it.id)}"]`);
      if (!container) return;
      for (const info of infos) {
        const el = [...container.querySelectorAll('.file-size')].find(e => e.dataset.file === info.path);
        if (el) el.textContent = fmtSize(info.size);
      }
    }).catch(() => {});
  }
}

// ── NFO field-selective copy ──────────────────────────────────────────────────
let nfoCopyCtx = null;   // { sourceId, targetId } while the field picker is open

const NFO_COPY_FIELDS = [
  ['title',     'Title',      n => n.title],
  ['year',      'Year',       n => n.year],
  ['rating',    'Rating',     n => n.rating],
  ['tagline',   'Tagline',    n => n.tagline],
  ['studio',    'Studio',     n => n.studio],
  ['genre',     'Genre',      n => (n.genre || []).join(', ')],
  ['plot',      'Plot',       n => n.plot],
  ['uniqueids', 'Unique IDs', n => Object.entries(n.uniqueids || {}).map(([k, v]) => `${k}:${v}`).join(', ')],
  ['actors',    'Actors',     n => (n.actors || []).join(', ')],
];

function copyNfo(sourceId, targetId) {
  const src = allItems.find(i => i.id === sourceId);
  const dst = allItems.find(i => i.id === targetId);
  if (!src || !dst) return;
  nfoCopyCtx = { sourceId, targetId };

  const locBadge = i => i.location === 'library'
    ? '<span class="badge bg-success-subtle text-success-emphasis">Library</span>'
    : '<span class="badge bg-warning-subtle text-warning-emphasis">Downloads</span>';

  document.getElementById('nfo-copy-info').innerHTML = `
    <div class="mb-1"><span class="text-muted">From:</span> ${locBadge(src)} <span class="font-monospace">${esc(src.path)}</span></div>
    <div><span class="text-muted">To:</span> ${locBadge(dst)} <span class="font-monospace">${esc(dst.path)}</span></div>
    <div class="mt-1">Checked fields replace the target's values; unchecked fields are left untouched.</div>`;

  const clip = v => {
    const s = String(v);
    return s.length > 160 ? esc(s.slice(0, 160)) + '…' : esc(s);
  };

  const srcN = src.nfo || {};
  const dstN = dst.nfo || {};
  const rows = NFO_COPY_FIELDS.map(([key, label, get]) => {
    const srcVal = get(srcN);
    const dstVal = get(dstN);
    if (!srcVal && !dstVal) return '';   // nothing on either side
    const copyable = !!srcVal;
    const changed  = copyable && String(srcVal) !== String(dstVal || '');
    return `
      <tr${copyable ? '' : ' class="opacity-50"'}>
        <td><input class="form-check-input nfo-field-check" type="checkbox" id="nfof-${key}" value="${key}"
                   ${copyable ? 'checked' : 'disabled'}></td>
        <td><label class="form-check-label fw-semibold" for="nfof-${key}">${label}</label></td>
        <td class="text-info text-break" title="${srcVal ? esc(String(srcVal)) : ''}">${srcVal ? clip(srcVal) : '<span class="text-muted fst-italic">not in NFO</span>'}</td>
        <td class="text-break ${changed ? 'text-warning' : 'text-muted'}" title="${dstVal ? esc(String(dstVal)) : ''}">${dstVal ? clip(dstVal) : '<span class="fst-italic">not in NFO</span>'}</td>
      </tr>`;
  }).join('');

  document.getElementById('nfo-copy-fields').innerHTML = rows ? `
    <table class="table table-sm table-dark table-striped align-middle small mb-0">
      <thead>
        <tr>
          <th style="width:28px"></th>
          <th style="width:90px">Field</th>
          <th style="width:40%">From ${locBadge(src)} <span class="fw-normal text-muted">${esc(src.title)}</span></th>
          <th>To ${locBadge(dst)} <span class="fw-normal text-muted">${esc(dst.title)} — current value</span></th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>` : '<div class="text-muted small">Neither NFO has copyable fields.</div>';

  getModal('compareModal').hide();
  getModal('nfoCopyModal').show();
}

function selectNfoFields(on) {
  document.querySelectorAll('.nfo-field-check:not(:disabled)').forEach(cb => cb.checked = on);
}

function cancelNfoCopy() {
  getModal('nfoCopyModal').hide();
  nfoCopyCtx = null;
  if (compareIds.length === 2) showCompareModal(compareIds[0], compareIds[1]);
}

async function confirmNfoCopy() {
  if (!nfoCopyCtx) return;
  const fields = [...document.querySelectorAll('.nfo-field-check:checked')].map(cb => cb.value);
  if (fields.length === 0) {
    showToast('Select at least one field to copy.', 'warning');
    return;
  }
  try {
    const res = await api('POST', '/api/nfo/copy', {
      source_id: nfoCopyCtx.sourceId,
      target_id: nfoCopyCtx.targetId,
      fields,
    });
    getModal('nfoCopyModal').hide();
    nfoCopyCtx = null;
    showToast(`Copied ${fields.length} NFO field(s) → ${res.to}`, 'success');
    await fetchLibrary();
    if (compareIds.length === 2) showCompareModal(compareIds[0], compareIds[1]);
  } catch (e) {
    showToast('NFO copy failed: ' + e.message, 'danger');
  }
}

async function markNotDuplicate() {
  if (compareIds.length !== 2) return;
  try {
    await api('POST', '/api/not-duplicate', { ids: compareIds });
    getModal('compareModal').hide();
    showToast('Marked as not duplicates — this pair will no longer be flagged.', 'success');
    await fetchStatus();
    applyFilters();
  } catch (e) {
    showToast('Failed: ' + e.message, 'danger');
  }
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
}

// ── Scan ──────────────────────────────────────────────────────────────────────
function _startPolling() {
  if (_pollId === null) _pollId = setInterval(pollScanStatus, 1500);
}
function _stopPolling() {
  if (_pollId !== null) { clearInterval(_pollId); _pollId = null; }
}

async function triggerScan() {
  setScanBadge('scanning');   // immediate feedback before the request even completes
  _startPolling();
  try {
    await api('POST', '/api/scan');
  } catch (e) {
    setScanBadge('error', e.message);
    showToast('Scan trigger failed: ' + e.message, 'danger');
  }
}

async function pollScanStatus() {
  try {
    const s = await api('GET', '/api/scan/status');
    lastScanStatus = s;
    renderScanPopover();
    if (s.scanning) {
      setScanBadge('scanning', s.current_path);
      if (allItems.length === 0) showScanningPlaceholder();
      _startPolling();
    } else if (s.error) {
      setScanBadge('error', s.error);
      _stopPolling();
    } else {
      // Done (last_scan may be null if no scan has ever run)
      setScanBadge('done', s.last_scan);
      _stopPolling();
      if (s.last_scan && s.last_scan !== lastScanTime) {
        lastScanTime = s.last_scan;
        fetchLibrary();
        fetchStatus();
      }
    }
  } catch (_) {}
}

function showScanningPlaceholder() {
  const list = document.getElementById('item-list');
  // Only replace if the list still shows the initial placeholder (no real rows yet)
  if (!list.querySelector('.item-row')) {
    list.innerHTML = `<div class="text-center text-muted mt-5 pt-5" id="list-placeholder">
      <i class="bi bi-arrow-clockwise fs-1 spin"></i>
      <p class="mt-3">Scanning library…</p>
    </div>`;
  }
}

function setScanBadge(state, extra) {
  const badge = document.getElementById('scan-badge');
  if (state === 'scanning') {
    const name = extra ? extra.replace(/.*[/\\]/, '') : '';
    const pathHtml = name
      ? ` <span class="fw-normal opacity-75" style="font-size:.8em" title="${esc(extra)}">${esc(name)}</span>`
      : '';
    badge.className = 'badge bg-warning text-dark text-nowrap';
    badge.innerHTML = `<i class="bi bi-arrow-clockwise spin me-1"></i>Scanning…${pathHtml}`;
  } else if (state === 'error') {
    badge.className = 'badge bg-danger text-nowrap';
    badge.title = extra || '';
    badge.innerHTML = '<i class="bi bi-exclamation-triangle me-1"></i>Scan error';
  } else {
    const d = extra ? new Date(extra) : null;
    const t = d && !isNaN(d)
      ? d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
      : '';
    badge.className = 'badge bg-success text-nowrap';
    badge.innerHTML = t
      ? `<i class="bi bi-check2 me-1"></i>Scanned ${t}`
      : `<i class="bi bi-check2 me-1"></i>Scanned`;
  }
}

// ── Scan status popover ───────────────────────────────────────────────────────
function toggleScanPopover(e) {
  e.stopPropagation();
  const pop = document.getElementById('scan-popover');
  if (pop.classList.contains('d-none')) {
    pop.classList.remove('d-none');
    renderScanPopover();
    pollScanStatus();   // refresh with latest data right away
  } else {
    pop.classList.add('d-none');
  }
}

document.addEventListener('click', e => {
  const pop = document.getElementById('scan-popover');
  if (pop && !pop.classList.contains('d-none') && !e.target.closest('#scan-popover')) {
    pop.classList.add('d-none');
  }
});

function _fmtScanTime(iso) {
  const d = iso ? new Date(iso) : null;
  return d && !isNaN(d)
    ? d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' })
    : '—';
}

function _fmtDuration(startIso, endIso) {
  const a = new Date(startIso), b = endIso ? new Date(endIso) : new Date();
  if (isNaN(a) || isNaN(b)) return '';
  const secs = Math.max(0, Math.round((b - a) / 1000));
  return secs < 60 ? `${secs}s` : `${Math.floor(secs / 60)}m ${secs % 60}s`;
}

function renderScanPopover() {
  const pop = document.getElementById('scan-popover');
  if (!pop || pop.classList.contains('d-none')) return;

  const s = lastScanStatus;
  const info = s && s.scan_info;
  if (!info) {
    pop.innerHTML = '<div class="text-muted">No scan has run yet in this session.</div>';
    return;
  }

  const running = !!s.scanning;
  const failed  = !!info.error;
  const title = running
    ? '<i class="bi bi-arrow-clockwise spin me-1 text-warning"></i>Scan in progress'
    : failed
      ? '<i class="bi bi-exclamation-triangle me-1 text-danger"></i>Last scan failed'
      : '<i class="bi bi-check2 me-1 text-success"></i>Last scan';

  const rows = (info.locations || []).map(l => {
    let icon, extra = '';
    if (l.status === 'done') {
      icon = '<i class="bi bi-check2 text-success"></i>';
      extra = `<span class="text-muted text-nowrap">${l.items} item${l.items === 1 ? '' : 's'}</span>`;
    } else if (l.status === 'scanning') {
      icon = '<i class="bi bi-arrow-clockwise spin text-warning"></i>';
      extra = '<span class="text-warning text-nowrap">scanning…</span>';
    } else if (l.status === 'missing') {
      icon = '<i class="bi bi-slash-circle text-muted"></i>';
      extra = '<span class="text-muted text-nowrap">not found</span>';
    } else {
      icon = '<i class="bi bi-circle text-muted"></i>';
      extra = '<span class="text-muted text-nowrap">pending</span>';
    }
    const typeIcon = l.location_type === 'download'
      ? '<i class="bi bi-download text-muted" title="Download folder"></i>'
      : '<i class="bi bi-collection-play text-muted" title="Library"></i>';
    return `<div class="scan-loc-row">${icon}${typeIcon}
      <span class="scan-loc-path" title="${esc(l.path)}">${esc(l.path)}</span>${extra}</div>`;
  }).join('');

  const errorHtml = failed
    ? `<div class="text-danger mt-2"><i class="bi bi-exclamation-triangle me-1"></i>${esc(info.error)}</div>`
    : '';

  pop.innerHTML = `
    <div class="fw-bold mb-2">${title}</div>
    <div class="d-flex justify-content-between">
      <span class="text-muted">Started</span><span>${_fmtScanTime(info.started)}</span>
    </div>
    <div class="d-flex justify-content-between">
      <span class="text-muted">${running ? 'Elapsed' : 'Duration'}</span>
      <span>${_fmtDuration(info.started, info.finished)}</span>
    </div>
    ${rows ? `<hr class="my-2"><div>${rows}</div>` : ''}
    ${errorHtml}`;
}

// ── Settings ──────────────────────────────────────────────────────────────────
async function loadSettings() {
  try {
    const cfg = await api('GET', '/api/config');
    configCache = cfg;
    renderLocationFilters();
    renderPathList('locations-list', 'locations', cfg.locations || []);
    renderPathList('downloads-list', 'downloads', cfg.downloads || []);
    // Populate move target dropdown
    const all = [...(cfg.locations || []), ...(cfg.downloads || [])].filter(Boolean);
    const sel = document.getElementById('move-target-select');
    sel.innerHTML = '<option value="">— select a configured path —</option>'
      + all.map(p => `<option value="${esc(p)}">${esc(p)}</option>`).join('');
  } catch (e) {
    showToast('Could not load settings: ' + e.message, 'warning');
  }
  loadNotDuplicates();
}

// ── Registered duplicates (not-duplicate pairs) ──────────────────────────
let notDupPairs = [];

async function loadNotDuplicates() {
  try {
    notDupPairs = await api('GET', '/api/not-duplicates');
  } catch (_) {
    notDupPairs = [];
  }
  renderNotDupList();
}

function renderNotDupList() {
  document.getElementById('notdup-count').textContent = notDupPairs.length;
  const listEl = document.getElementById('notdup-list');
  if (!notDupPairs.length) {
    listEl.innerHTML = '<div class="text-muted small fst-italic">No pairs registered as “not a duplicate”.</div>';
    return;
  }
  listEl.innerHTML = notDupPairs.map((pair, idx) => `
    <div class="form-check py-1 border-bottom border-secondary-subtle">
      <input class="form-check-input notdup-check" type="checkbox" id="nd-${idx}" data-idx="${idx}">
      <label class="form-check-label small w-100" for="nd-${idx}">
        <div class="font-monospace text-truncate" title="${esc(pair[0])}">${esc(pair[0])}</div>
        <div class="font-monospace text-truncate text-muted" title="${esc(pair[1])}">↔ ${esc(pair[1])}</div>
      </label>
    </div>`).join('');
  filterNotDups();
}

function filterNotDups() {
  const q = document.getElementById('notdup-filter').value.toLowerCase().trim();
  if (!q) return;   // clearing the filter keeps the current selection
  document.querySelectorAll('.notdup-check').forEach(cb => {
    const pair = notDupPairs[+cb.dataset.idx];
    cb.checked = pair.some(p => p.toLowerCase().includes(q));
  });
}

function selectNotDups(on) {
  document.querySelectorAll('.notdup-check').forEach(cb => cb.checked = on);
}

async function removeNotDups(all) {
  let pairs;
  if (all) {
    if (!notDupPairs.length) return;
    if (!confirm(`Remove all ${notDupPairs.length} registered pair(s)? They will be flagged as potential duplicates again.`)) return;
    pairs = notDupPairs;
  } else {
    pairs = [...document.querySelectorAll('.notdup-check:checked')].map(cb => notDupPairs[+cb.dataset.idx]);
    if (!pairs.length) {
      showToast('No pairs selected.', 'warning');
      return;
    }
  }
  try {
    const res = await api('POST', '/api/not-duplicates/remove', { pairs });
    showToast(`Removed ${res.removed} registration(s).`, 'success');
    await loadNotDuplicates();
    await fetchStatus();
    applyFilters();
  } catch (e) {
    showToast('Remove failed: ' + e.message, 'danger');
  }
}

function renderPathList(containerId, type, paths) {
  document.getElementById(containerId).innerHTML = paths.map((p, i) => `
    <div class="input-group input-group-sm mb-1">
      <input type="text" class="form-control bg-dark text-light border-secondary font-monospace path-input"
             data-type="${type}" data-index="${i}" value="${esc(p)}">
      <button class="btn btn-outline-danger" type="button" onclick="removePath('${type}',${i})">
        <i class="bi bi-x"></i>
      </button>
    </div>`).join('');
}

function addPath(type) {
  const inputs = [...document.querySelectorAll(`[data-type="${type}"]`)];
  const paths  = inputs.map(i => i.value);
  paths.push('');
  renderPathList(type + '-list', type, paths);
  // focus new input
  const all = document.querySelectorAll(`[data-type="${type}"]`);
  all[all.length - 1]?.focus();
}

function removePath(type, index) {
  const inputs = [...document.querySelectorAll(`[data-type="${type}"]`)];
  const paths  = inputs.map(i => i.value).filter((_, i) => i !== index);
  renderPathList(type + '-list', type, paths);
}

async function saveSettings(rescan) {
  const locations = [...document.querySelectorAll('[data-type="locations"]')]
    .map(i => i.value.trim()).filter(Boolean);
  const downloads = [...document.querySelectorAll('[data-type="downloads"]')]
    .map(i => i.value.trim()).filter(Boolean);
  try {
    await api('POST', '/api/config', { locations, downloads });
    configCache = { locations, downloads };
    renderLocationFilters();
    applyFilters();
    getModal('settingsModal').hide();
    if (rescan) {
      showToast('Settings saved. Starting rescan…', 'success');
      triggerScan();
    } else {
      showToast('Settings saved.', 'success');
    }
  } catch (e) {
    showToast('Save failed: ' + e.message, 'danger');
  }
}

// ── Delete ────────────────────────────────────────────────────────────────────
function _deleteRow(i) {
  const viewable = i.type === 'series'
    ? Object.keys(i.nfo || {}).length > 0
    : (i.nfo_quality || 'none') !== 'none';
  const viewBtn = viewable ? `
    <button type="button" class="btn btn-sm btn-link text-info p-0 flex-shrink-0" title="View NFO file"
            data-view-nfo="${esc(i.id)}"><i class="bi bi-eye"></i></button>` : '';
  return `
    <div class="d-flex align-items-center gap-2 py-1 border-bottom border-secondary-subtle">
      <input type="checkbox" class="form-check-input delete-check m-0 flex-shrink-0"
             data-id="${esc(i.id)}" checked onchange="updateDeleteCount()">
      <div class="flex-grow-1 min-w-0 delete-item-label" role="button">
        <div class="text-truncate">${esc(i.folder)}</div>
        <div class="small text-muted text-truncate font-monospace" title="${esc(i.path)}">${esc(i.path)}</div>
      </div>
      ${nfoIconHtml(i)}${viewBtn}
      ${i.location === 'library'
        ? '<span class="badge bg-success-subtle text-success-emphasis">Library</span>'
        : '<span class="badge bg-warning-subtle text-warning-emphasis">Downloads</span>'}
      <button type="button" class="btn btn-sm btn-play border-0 py-0 px-1 flex-shrink-0"
              title="Play — check which copy this is before deleting" data-play-item="${esc(i.id)}">
        <i class="bi bi-play-circle"></i>
      </button>
    </div>`;
}

function openDeleteDialog() {
  if (selectedIds.size === 0) return;
  const items = [...selectedIds].map(id => allItems.find(i => i.id === id)).filter(Boolean);

  // Partition selection into duplicate groups (≥2 selected copies) and single items
  const byGroup = new Map();
  const singles = [];
  for (const it of items) {
    const g = dupGroupOf.get(it.id);
    if (g === undefined) { singles.push(it); continue; }
    if (!byGroup.has(g)) byGroup.set(g, []);
    byGroup.get(g).push(it);
  }
  const groupBlocks = [];
  for (const [gIdx, members] of byGroup) {
    if (members.length < 2) { singles.push(...members); continue; }
    const total = duplicateGroups[gIdx].items.length;
    const allCopies = members.length >= total;
    groupBlocks.push(`
      <div class="border border-danger rounded p-2 mb-2">
        <div class="small fw-semibold text-danger-emphasis mb-1">
          <i class="bi bi-copy me-1"></i>Duplicate group: ${esc(members[0].title)}
        </div>
        ${allCopies ? `
        <div class="small text-warning mb-1">
          <i class="bi bi-exclamation-triangle-fill me-1"></i>
          All ${total} copies are selected — uncheck the copy you want to keep.
          Use <i class="bi bi-play-circle"></i> to check the videos or <i class="bi bi-eye"></i> to inspect the NFO.
        </div>` : ''}
        ${members.map(_deleteRow).join('')}
      </div>`);
  }

  document.getElementById('delete-list').innerHTML =
    groupBlocks.join('') + singles.map(_deleteRow).join('');
  document.getElementById('delete-dryrun').checked = true;
  updateDeleteCount();
  getModal('deleteModal').show();
}

function updateDeleteCount() {
  const n = document.querySelectorAll('.delete-check:checked').length;
  document.getElementById('delete-count').textContent = n;
  document.getElementById('delete-confirm-btn').disabled = n === 0;
}

async function confirmDelete() {
  const ids = [...document.querySelectorAll('.delete-check:checked')].map(cb => cb.dataset.id);
  if (!ids.length) {
    showToast('No items checked for deletion.', 'warning');
    return;
  }
  const dryRun = document.getElementById('delete-dryrun').checked;
  try {
    const results = await api('POST', '/api/delete', {
      item_ids: ids,
      dry_run: dryRun,
    });
    getModal('deleteModal').hide();
    handleActionResults(results, dryRun, 'delete', 'deleted');
    if (!dryRun) { clearSelection(); triggerScan(); }
  } catch (e) {
    showToast('Delete failed: ' + e.message, 'danger');
  }
}

// ── Move ──────────────────────────────────────────────────────────────────────
function openMoveDialog() {
  if (selectedIds.size === 0) return;
  const items = [...selectedIds].map(id => allItems.find(i => i.id === id)).filter(Boolean);
  document.getElementById('move-items-preview').innerHTML =
    `Moving <strong>${items.length}</strong> item(s): `
    + items.map(i => `<span class="badge bg-secondary ms-1">${esc(i.title)}</span>`).join('');
  document.getElementById('move-dryrun').checked = true;
  document.getElementById('move-target-custom').value = '';
  getModal('moveModal').show();
}

async function confirmMove() {
  const dryRun = document.getElementById('move-dryrun').checked;
  const custom = document.getElementById('move-target-custom').value.trim();
  const sel    = document.getElementById('move-target-select').value;
  const target = custom || sel;
  if (!target) {
    showToast('Please select or enter a destination path.', 'warning');
    return;
  }
  try {
    const results = await api('POST', '/api/move', {
      item_ids: [...selectedIds],
      target_base: target,
      dry_run: dryRun,
    });
    getModal('moveModal').hide();
    handleActionResults(results, dryRun, 'move', 'moved');
    if (!dryRun) { clearSelection(); triggerScan(); }
  } catch (e) {
    showToast('Move failed: ' + e.message, 'danger');
  }
}

// ── Rename ────────────────────────────────────────────────────────────────────
function _isLooseFile(item) {
  // Loose file items have id = file path; folder items have id = folder path = item.path
  return item.id !== item.path;
}

function _renameTarget() {
  return document.querySelector('input[name="rename-target"]:checked').value;
}

function updateRenameTarget() {
  if (!currentDetail) return;
  const target = _renameTarget();
  const item   = currentDetail;
  const isFile = _isLooseFile(item);

  const singleGroup = document.getElementById('rename-single-group');
  const bothGroup   = document.getElementById('rename-both-group');

  if (target === 'both') {
    // Show dual inputs
    singleGroup.classList.add('d-none');
    bothGroup.classList.remove('d-none');

    // Folder section
    document.getElementById('rename-both-current-folder').textContent = item.folder;
    document.getElementById('rename-new-folder').value = item.folder;

    // File section
    const firstFile = (item.files || [])[0] || '';
    const stem = firstFile.split(/[/\\]/).pop().replace(/\.[^.]+$/, '');
    document.getElementById('rename-both-current-file').textContent = stem;
    document.getElementById('rename-new-file').value = stem;
    const count = (item.files || []).length;
    document.getElementById('rename-both-hint').textContent =
      count > 1 ? `${count} files — will be named "name - Part 1.ext", "name - Part 2.ext", …` : 'Extension is preserved automatically.';

  } else {
    // Show single input
    singleGroup.classList.remove('d-none');
    bothGroup.classList.add('d-none');

    if (target === 'files' || isFile) {
      const firstFile = (item.files || [])[0] || '';
      const stem = firstFile.split(/[/\\]/).pop().replace(/\.[^.]+$/, '');
      document.getElementById('rename-current-label').textContent = 'Current file name (stem)';
      document.getElementById('rename-current').textContent = stem;
      document.getElementById('rename-new-label').textContent = 'New file name (stem, no extension)';
      document.getElementById('rename-new-name').value = stem;
      const count = (item.files || []).length;
      document.getElementById('rename-hint').textContent =
        count > 1 ? `${count} files — will be named "name - Part 1.ext", "name - Part 2.ext", …` : 'Extension is preserved automatically.';
    } else {
      document.getElementById('rename-current-label').textContent = 'Current folder name';
      document.getElementById('rename-current').textContent = item.folder;
      document.getElementById('rename-new-label').textContent = 'New folder name';
      document.getElementById('rename-new-name').value = item.folder;
      document.getElementById('rename-hint').textContent = '';
    }
  }
}

function renameFromDetail() {
  if (!currentDetail) return;
  getModal('detailModal').hide();

  const item   = currentDetail;
  const isFile = _isLooseFile(item);

  // Show/hide the target radio group
  document.getElementById('rename-target-group').classList.toggle('d-none', isFile);

  // Pre-select appropriate default
  const defaultTarget = isFile ? 'files' : 'folder';
  document.querySelector(`input[name="rename-target"][value="${defaultTarget}"]`).checked = true;

  document.getElementById('rename-item-id').value = item.id;
  document.getElementById('rename-dryrun').checked = true;

  updateRenameTarget();
  getModal('renameModal').show();
}

async function confirmRename() {
  const itemId       = document.getElementById('rename-item-id').value;
  const dryRun       = document.getElementById('rename-dryrun').checked;
  const renameTarget = _isLooseFile(currentDetail) ? 'files' : _renameTarget();

  let newName, newFolderName;
  if (renameTarget === 'both') {
    newName       = document.getElementById('rename-new-file').value.trim();
    newFolderName = document.getElementById('rename-new-folder').value.trim();
    if (!newName || !newFolderName) return;
  } else {
    newName = document.getElementById('rename-new-name').value.trim();
    if (!newName) return;
  }

  try {
    const payload = { item_id: itemId, new_name: newName, rename_target: renameTarget, dry_run: dryRun };
    if (renameTarget === 'both') payload.new_folder_name = newFolderName;
    const result = await api('POST', '/api/rename', payload);
    getModal('renameModal').hide();
    if (dryRun) {
      const lines = (result.changes || []).map(c =>
        `[${c.type}]  ${c.from}\n       → ${c.to}`);
      showDryRunModal(lines);
    } else {
      showToast(`Renamed successfully`, 'success');
      triggerScan();
    }
  } catch (e) {
    showToast('Rename failed: ' + e.message, 'danger');
  }
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
  document.getElementById('dryrun-content').textContent = lines.join('\n\n');
  getModal('dryrunModal').show();
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
