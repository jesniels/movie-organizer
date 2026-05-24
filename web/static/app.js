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
let activeSpecialFilter = null;    // null | 'duplicates' | 'missing-nfo'
let duplicateMap        = new Map(); // item.id → [{ id, title, location, path }, ...]
let lastStatus          = null;    // cached last /api/status response

// ── Bootstrap modal helpers ───────────────────────────────────────────────────
const getModal = id => bootstrap.Modal.getOrCreateInstance(document.getElementById(id));

// ── Init ──────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  // Load settings whenever the settings modal opens
  document.getElementById('settingsModal').addEventListener('show.bs.modal', loadSettings);

  pollScanStatus();   // immediate check on load; starts polling if scan is running
  fetchLibrary();
  fetchStatus();

  // Event delegation for item list
  const list = document.getElementById('item-list');
  list.addEventListener('click', e => {
    const row = e.target.closest('.item-row');
    if (!row) return;
    const id = row.dataset.id;
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
    const row = e.target.closest('.item-row');
    if (row) showDetail(row.dataset.id);
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

// Build a map of item.id → array of duplicate peers for fast lookup
function _buildDuplicateMap(status) {
  duplicateMap.clear();
  const allDups = [...(status.duplicate_movies || []), ...(status.duplicate_series || [])];
  for (const group of allDups) {
    for (const item of group.items) {
      const others = group.items.filter(i => i.id !== item.id);
      if (others.length > 0) duplicateMap.set(item.id, others);
    }
  }
}

// ── Special filters (duplicates / incomplete NFO) ─────────────────────────────
function toggleSpecialFilter(filter) {
  activeSpecialFilter = (activeSpecialFilter === filter) ? null : filter;
  if (lastStatus) renderStatusPanel(lastStatus);
  applyFilters();
}

// ── Filters ───────────────────────────────────────────────────────────────────
function applyFilters() {
  const showLib  = document.getElementById('f-library').checked;
  const showDl   = document.getElementById('f-download').checked;
  const showMov  = document.getElementById('f-movies').checked;
  const showSer  = document.getElementById('f-series').checked;
  const q        = document.getElementById('search-input').value.toLowerCase().trim();

  filteredItems = allItems.filter(item => {
    if (!showLib && item.location === 'library')  return false;
    if (!showDl  && item.location === 'download') return false;
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

  list.innerHTML = filteredItems.map(renderRow).join('');
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

  // NFO icon — 3 states: full (teal), partial/handicapped (orange + strikethrough), absent (dimmed)
  const nfoQuality = item.nfo_quality || (item.nfo && Object.keys(item.nfo).length ? 'partial' : 'none');
  let nfoIcon;
  if (nfoQuality === 'full') {
    nfoIcon = '<i class="bi bi-file-earmark-text nfo-icon nfo-present" title="NFO complete (year + ID present)"></i>';
  } else if (nfoQuality === 'partial') {
    nfoIcon = '<i class="bi bi-file-earmark-text nfo-icon nfo-partial" title="NFO incomplete — missing year or external ID"></i>';
  } else {
    nfoIcon = '<i class="bi bi-file-earmark nfo-icon nfo-absent" title="No NFO file"></i>';
  }

  // Duplicate info lines (only shown in duplicates filter mode)
  let dupLines = '';
  if (activeSpecialFilter === 'duplicates') {
    const peers = duplicateMap.get(item.id) || [];
    dupLines = peers.map(p => {
      const locCls = p.location === 'library' ? 'success' : 'warning';
      return `<div class="small text-danger-emphasis mt-1">
        <i class="bi bi-copy me-1"></i>Potential duplicate:
        <span class="badge bg-${locCls}-subtle text-${locCls}-emphasis ms-1">${esc(p.location)}</span>
        ${esc(p.title)}
        <span class="text-muted font-monospace ms-1" style="font-size:.75em">${esc(p.path)}</span>
      </div>`;
    }).join('');
  }

  return `
  <div class="item-row d-flex align-items-center gap-2 px-3 py-2 border-bottom border-secondary-subtle${selected}"
       data-id="${esc(item.id)}">
    <div class="flex-grow-1 min-w-0">
      <div class="fw-semibold text-truncate">
        ${esc(item.title)}${yearStr}${epStr}${missBadge}
      </div>
      <div class="small text-muted text-truncate" title="${esc(item.path)}">${esc(item.path)}</div>
      ${dupLines}
    </div>
    <div class="d-flex align-items-center gap-1 flex-shrink-0">
      ${nfoIcon} ${locBadge} ${typeBadge}
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

  const sfDup = activeSpecialFilter === 'duplicates';
  const sfNfo = activeSpecialFilter === 'missing-nfo';

  document.getElementById('status-panel').innerHTML = `
    <div class="status-grid">
      <div class="status-row">
        <span class="dot dot-success"></span>
        <span>${status.library_movies || 0} movies</span>
      </div>
      <div class="status-row">
        <span class="dot dot-success"></span>
        <span>${status.library_series || 0} series
          <span class="text-muted">(${status.total_library_episodes || 0} ep)</span>
        </span>
      </div>
      <div class="status-row">
        <span class="dot dot-warning"></span>
        <span>${status.download_movies || 0} Download movies</span>
      </div>
      <div class="status-row">
        <span class="dot dot-warning"></span>
        <span>${status.download_series || 0} Download series
          <span class="text-muted">(${status.total_download_episodes || 0} ep)</span>
        </span>
      </div>
      ${missCount > 0 ? `
      <div class="status-row status-link text-warning-emphasis" onclick="showStatusModal()">
        <i class="bi bi-exclamation-circle text-warning"></i>
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

function renderMovieDetail(item) {
  const n = item.nfo || {};
  const hasNfo = Object.keys(n).length > 0;
  const quality = item.nfo_quality || (hasNfo ? 'partial' : 'none');

  const nfoStatus = quality === 'full'
    ? '<i class="bi bi-file-earmark-text text-info me-1"></i>Complete'
    : quality === 'partial'
    ? '<span class="text-warning"><i class="bi bi-file-earmark-text me-1"></i>Incomplete <span class="text-muted small">(missing year or ID — Jellyfin may not recognise)</span></span>'
    : '<span class="text-secondary"><i class="bi bi-file-earmark me-1"></i>Not found</span>';

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
  const hasNfo = Object.keys(n).length > 0;
  const quality = item.nfo_quality || (hasNfo ? 'partial' : 'none');

  const nfoStatus = quality === 'full'
    ? '<i class="bi bi-file-earmark-text text-info me-1"></i>Complete'
    : quality === 'partial'
    ? '<span class="text-warning"><i class="bi bi-file-earmark-text me-1"></i>Incomplete <span class="text-muted small">(missing year or ID — Jellyfin may not recognise)</span></span>'
    : '<span class="text-secondary"><i class="bi bi-file-earmark me-1"></i>Not found</span>';

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

// ── Settings ──────────────────────────────────────────────────────────────────
async function loadSettings() {
  try {
    const cfg = await api('GET', '/api/config');
    configCache = cfg;
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
function openDeleteDialog() {
  if (selectedIds.size === 0) return;
  const items = [...selectedIds].map(id => allItems.find(i => i.id === id)).filter(Boolean);
  document.getElementById('delete-list').innerHTML = items.map(i =>
    `<div class="py-1 border-bottom border-secondary-subtle d-flex align-items-center gap-2">
       <i class="bi bi-folder2 text-muted"></i>
       <span class="text-truncate">${esc(i.folder)}</span>
       <span class="ms-auto">${i.location === 'library'
         ? '<span class="badge bg-success-subtle text-success-emphasis">Library</span>'
         : '<span class="badge bg-warning-subtle text-warning-emphasis">Downloads</span>'}</span>
     </div>`
  ).join('');
  document.getElementById('delete-dryrun').checked = true;
  getModal('deleteModal').show();
}

async function confirmDelete() {
  const dryRun = document.getElementById('delete-dryrun').checked;
  try {
    const results = await api('POST', '/api/delete', {
      item_ids: [...selectedIds],
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
          <div class="stat-card border-success">
            <div class="stat-num">${s.library_movies || 0}</div>
            <div class="stat-label">Library Movies</div>
          </div>
        </div>
        <div class="col-6 col-md-3">
          <div class="stat-card border-success">
            <div class="stat-num">${s.library_series || 0}</div>
            <div class="stat-label">Library Series</div>
            <div class="stat-sub">${s.total_library_episodes || 0} episodes</div>
          </div>
        </div>
        <div class="col-6 col-md-3">
          <div class="stat-card border-warning">
            <div class="stat-num">${s.download_movies || 0}</div>
            <div class="stat-label">Download Movies</div>
          </div>
        </div>
        <div class="col-6 col-md-3">
          <div class="stat-card border-warning">
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
