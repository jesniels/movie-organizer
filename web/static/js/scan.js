'use strict';
// Library scan: starting a scan, polling its status, the scan badge and the scan status popover.

// ── Scan state ────────────────────────────────────────────────────────────────
let lastScanTime        = null;
let _pollId             = null;    // setInterval id for scan polling
let lastScanStatus      = null;    // cached last /api/scan/status response

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
        roExecuted = false;   // the library has the names Reorganize changed — no rescan prompt needed
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
  // Reorganize would analyse a cache that is being rebuilt — not while scanning
  const scanning = state === 'scanning';
  document.getElementById('btn-reorganize').disabled = scanning;
  document.getElementById('btn-reorganize-wrap').title = scanning
    ? 'Reorganize is disabled while a scan is running'
    : 'Suggest standard names for the items shown by the filters — nothing changes until you execute';
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
    const typeIcon = {
      download:   '<i class="bi bi-download text-muted" title="Download folder"></i>',
      transcoded: '<i class="bi bi-cpu text-muted" title="Transcoded folder"></i>',
    }[l.location_type] || '<i class="bi bi-collection-play text-muted" title="Library"></i>';
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
