'use strict';
// Detail modal, NFO status/completeness reasons, NFO file viewer and playing items.

// ── Init ──────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  // Global delegation: NFO view buttons (detail + compare + delete modals)
  document.addEventListener('click', e => {
    const nfoBtn = e.target.closest('[data-view-nfo]');
    if (nfoBtn) showNfoView(nfoBtn.dataset.viewNfo);
  });
});

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
    ['Location', _locLabel(item.location)],
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
    ['Location', _locLabel(item.location)],
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
// Longest configured local share (Settings → Paths) covering the path, or null
function _localShareFor(path) {
  const shares = configCache.local_shares || {};
  const ip = (path || '').toLowerCase();
  let best = null, bestLen = -1;
  for (const [root, share] of Object.entries(shares)) {
    if (!root || !(share || '').trim()) continue;
    const n = _normRoot(root);
    if ((ip === n || ip.startsWith(n + '\\') || ip.startsWith(n + '/')) && n.length > bestLen) {
      best = share.trim();
      bestLen = n.length;
    }
  }
  return best;
}

// In server mode playing needs a local share for the item's location
const canPlay = item => !configCache.server_mode || !!_localShareFor(item.path || item.id);
const PLAY_DISABLED_TITLE = 'Playing disabled — no local share configured for this location (Settings → Paths)';
const _playAttrs = (item, title) => canPlay(item)
  ? ` title="${esc(title)}"`
  : ` disabled title="${PLAY_DISABLED_TITLE}"`;

// Browsers can't hand a share path straight to a player — serve it as a one-line playlist
function _downloadPlaylist(path) {
  const name = (path.split(/[/\\]/).pop() || 'play').replace(/\.[^.]+$/, '') + '.m3u';
  const blob = new Blob(['#EXTM3U\r\n' + path + '\r\n'], { type: 'audio/x-mpegurl' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 10000);
}

async function playItem(itemId, file) {
  try {
    const body = { item_id: itemId };
    if (file) body.file = file;
    const res = await api('POST', '/api/play', body);
    if (res.server_mode && res.local_file) {
      _downloadPlaylist(res.local_file);
      showToast('Playlist downloaded — open it to play ' + res.local_file.split(/[/\\]/).pop(), 'info');
    } else {
      showToast('Launching player: ' + res.file.split(/[/\\]/).pop(), 'info');
    }
  } catch (e) {
    showToast('Play failed: ' + e.message, 'danger');
  }
}
