'use strict';
// Transcoding: the transcode dialog (probe, audio choice, start), status polling, badge and console.

// ── Transcode state ───────────────────────────────────────────────────────────
let tcStatus            = null;    // cached last /api/transcode/status response
let _tcPollId           = null;    // setInterval id for transcode polling
let tcWasRunning        = false;
let tcItems             = [];      // movies shown in the open transcode dialog
let tcProbe             = null;    // probe response for the open transcode dialog
let tcConsoleOpen       = false;
let tcLogSeq            = 0;       // last log sequence number rendered in the console
let tcLogJob            = null;    // `started` of the job whose log is shown

// ── Init ──────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  const tcConsole = document.getElementById('transcodeConsoleModal');
  tcConsole.addEventListener('shown.bs.modal', () => { tcConsoleOpen = true; });
  tcConsole.addEventListener('hidden.bs.modal', () => {
    tcConsoleOpen = false;
    resetConfirm(document.getElementById('tc-kill-btn'), document.getElementById('tc-kill-note'));
  });

  document.getElementById('tc-files').addEventListener('change', e => {
    if (e.target.closest('.tc-audio')) updateTranscodeStartBtn();
  });
});

// ── Transcode dialog ──────────────────────────────────────────────────────────
function _selectedItems() {
  return [...selectedIds].map(id => allItems.find(i => i.id === id)).filter(Boolean);
}

function _updateTranscodeButton() {
  const btn = document.getElementById('btn-transcode');
  const items = _selectedItems();
  const running   = !!(tcStatus && tcStatus.running);
  const hasSeries = items.some(i => i.type === 'series');
  const hasTransc = items.some(i => i.location === 'transcoded');
  btn.disabled = running || hasSeries || hasTransc || !items.length;
  btn.innerHTML = running
    ? '<i class="bi bi-arrow-repeat spin me-1"></i>Transcoding in progress…'
    : hasSeries
      ? '<i class="bi bi-cpu me-1"></i>Transcode (movies only)'
      : hasTransc
        ? '<i class="bi bi-cpu me-1"></i>Transcode (already transcoded)'
        : '<i class="bi bi-cpu me-1"></i>Transcode';
}

function _fmtHms(secs) {
  secs = Math.round(secs || 0);
  const h = Math.floor(secs / 3600), m = Math.floor(secs % 3600 / 60), s = secs % 60;
  return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

const _AUDIO_PREFER_LABEL = { first: 'first track', default: 'default track', channels: 'most channels' };

async function openTranscodeDialog() {
  const items = _selectedItems().filter(i => i.type === 'movie');
  if (!items.length) return;
  if (tcStatus && tcStatus.running) {
    showToast('A transcoding job is already running.', 'warning');
    return;
  }
  try { configCache = await api('GET', '/api/config'); } catch (_) {}
  const tc = configCache.transcode || {};
  tcItems = items;
  tcProbe = null;

  const missing = [];
  if (!tc.ffmpeg_path)  missing.push('ffmpeg path');
  if (!tc.ffprobe_path) missing.push('ffprobe path');
  if (!tc.output_root)  missing.push('output folder');
  const outDir = tc.output_root ? tc.output_root.replace(/[\\/]+$/, '') + '\\Movies' : '—';
  document.getElementById('tc-options').innerHTML = `
    <div class="d-flex flex-wrap gap-3 border border-secondary rounded p-2">
      <div><span class="text-muted">Encoder</span> <code>${esc(tc.encoder || '—')}</code></div>
      <div><span class="text-muted">Quality</span> <code>${esc(tc.qp ?? '—')}</code></div>
      <div class="text-truncate"><span class="text-muted">Output</span>
        <code title="${esc(outDir)}">${esc(outDir)}</code></div>
      <div><span class="text-muted">Audio</span>
        <code>${esc((tc.audio_languages || []).join(' → ') || '—')}</code>
        <span class="text-muted">(${esc(_AUDIO_PREFER_LABEL[tc.audio_prefer] || 'first track')})</span></div>
      <div class="ms-auto text-muted fst-italic">Change in Settings → Transcoding</div>
    </div>
    ${missing.length ? `
    <div class="alert alert-warning py-2 mt-2 mb-0">
      <i class="bi bi-exclamation-triangle-fill me-1"></i>
      Not configured: ${missing.map(esc).join(', ')}. Open Settings → Transcoding first.
    </div>` : ''}`;

  document.getElementById('tc-files').innerHTML = `
    <div class="text-muted mb-2">${items.length} movie(s) selected — probe the files to choose the audio track for each file.</div>
    ${items.map(i => `
      <div class="py-1 border-bottom border-secondary-subtle">
        <div class="text-truncate">${esc(i.title)}</div>
        <div class="text-muted font-monospace text-truncate" title="${esc(i.path)}">${esc(i.path)}</div>
      </div>`).join('')}`;
  document.getElementById('tc-probe-btn').disabled = missing.length > 0;
  document.getElementById('tc-start-btn').disabled = true;
  document.getElementById('tc-start-count').textContent = '';
  document.getElementById('tc-footer-note').textContent = 'Probing is required before transcoding.';
  getModal('transcodeModal').show();
}

async function probeTranscodeFiles() {
  const btn = document.getElementById('tc-probe-btn');
  btn.disabled = true;
  btn.innerHTML = '<i class="bi bi-arrow-repeat spin me-1"></i>Probing…';
  document.getElementById('tc-files').innerHTML =
    `<div class="text-muted"><i class="bi bi-arrow-repeat spin me-1"></i>Probing ${tcItems.length} movie(s)…</div>`;
  try {
    tcProbe = await api('POST', '/api/transcode/probe', { item_ids: tcItems.map(i => i.id) });
    renderTranscodeProbe();
  } catch (e) {
    document.getElementById('tc-files').innerHTML =
      `<div class="text-danger">Probe failed: ${esc(_apiErr(e))}</div>`;
  } finally {
    btn.disabled = false;
    btn.innerHTML = '<i class="bi bi-search me-1"></i>Probe again';
  }
}

function _audioLabel(a) {
  const flags = [
    a.default && 'default', a.comment && 'commentary',
    a.hearing_impaired && 'SDH', a.visual_impaired && 'audio description',
  ].filter(Boolean);
  return [`#${a.index}`, a.language || 'und', a.codec, a.channels ? `${a.channels}ch` : '',
          a.title ? `“${a.title}”` : '', flags.length ? `[${flags.join(', ')}]` : '']
    .filter(Boolean).join(' · ');
}

function renderTranscodeProbe() {
  const rows = tcProbe.files.map((f, idx) => {
    if (f.error) {
      return `<tr><td><div class="text-truncate" title="${esc(f.src || f.name)}">${esc(f.name)}</div></td>
        <td colspan="5" class="text-danger"><i class="bi bi-x-circle me-1"></i>${esc(f.error)}</td></tr>`;
    }
    const v = f.video || {};
    const exists = !!f.dst_exists;
    const noClean = f.suggested_audio === null || f.suggested_audio === undefined;
    const opts = `<option value="">— skip this file —</option>` + (f.audio || []).map(a =>
      `<option value="${a.index}" ${!exists && a.index === f.suggested_audio ? 'selected' : ''}>${esc(_audioLabel(a))}</option>`
    ).join('');
    let note = '';
    if (exists) {
      note = '<div class="text-warning"><i class="bi bi-exclamation-triangle me-1"></i>Destination already exists — skipped</div>';
    } else if (noClean) {
      const langs = ((tcProbe.config || {}).audio_languages || []).join(', ') || '—';
      note = `<div class="text-warning"><i class="bi bi-exclamation-triangle me-1"></i>No clean track in preferred language(s) ${esc(langs)} — pick one manually or the file is skipped</div>`;
    }
    return `<tr>
      <td class="min-w-0">
        <div class="text-truncate" title="${esc(f.src)}">${esc(f.name)}</div>
        <div class="text-muted font-monospace text-truncate" title="${esc(f.dst || '')}">→ ${esc(f.dst || '')}</div>
      </td>
      <td class="text-nowrap">${esc(v.codec || '?')}${v.width ? ` ${v.width}×${v.height}` : ''}</td>
      <td class="text-nowrap">${_fmtHms(f.duration)}</td>
      <td class="text-nowrap">${fmtSize(f.size)}</td>
      <td class="text-nowrap">${f.subtitles}</td>
      <td style="min-width:18rem">
        <select class="form-select form-select-sm bg-dark text-light border-secondary tc-audio"
                data-idx="${idx}" ${exists ? 'disabled' : ''}>${opts}</select>
        ${note}
      </td>
    </tr>`;
  }).join('');
  document.getElementById('tc-files').innerHTML = `
    <table class="table table-dark table-sm align-middle mb-0" style="table-layout:fixed">
      <thead><tr>
        <th>File</th><th style="width:9rem">Video</th><th style="width:5.5rem">Duration</th>
        <th style="width:5.5rem">Size</th><th style="width:3.5rem">Subs</th><th style="width:22rem">Audio track</th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
  document.getElementById('tc-footer-note').textContent =
    'Video is re-encoded; the chosen audio track and all subtitles are copied.';
  updateTranscodeStartBtn();
}

function _chosenTranscodeFiles() {
  if (!tcProbe) return [];
  return [...document.querySelectorAll('.tc-audio')]
    .filter(s => !s.disabled && s.value !== '')
    .map(s => {
      const f = tcProbe.files[+s.dataset.idx];
      return { item_id: f.item_id, src: f.src, audio_index: +s.value };
    });
}

function updateTranscodeStartBtn() {
  const n = _chosenTranscodeFiles().length;
  const running = !!(tcStatus && tcStatus.running);
  document.getElementById('tc-start-btn').disabled = n === 0 || running;
  document.getElementById('tc-start-count').textContent = n ? `(${n})` : '';
}

async function startTranscode() {
  const files = _chosenTranscodeFiles();
  if (!files.length) return;
  try {
    await api('POST', '/api/transcode/start', { files });
    getModal('transcodeModal').hide();
    showToast(`Transcoding started for ${files.length} file(s).`, 'info');
    pollTranscodeStatus();
  } catch (e) {
    const msg = e.message.startsWith('409') ? 'A transcoding job is already running.' : 'Start failed: ' + _apiErr(e);
    showToast(msg, 'danger');
  }
}

// ── Transcode status / console ────────────────────────────────────────────────
const _TC_FINISHED = ['done', 'failed', 'killed', 'cancelled'];

async function pollTranscodeStatus() {
  try {
    tcStatus = await api('GET', '/api/transcode/status');
  } catch (_) { return; }
  renderTranscodeBadge();
  _updateTranscodeButton();
  if (tcConsoleOpen) {
    renderTranscodeConsole();
    fetchTranscodeLog();
  }
  if (tcStatus.running) {
    tcWasRunning = true;
    if (_tcPollId === null) _tcPollId = setInterval(pollTranscodeStatus, 1500);
  } else {
    if (_tcPollId !== null) { clearInterval(_tcPollId); _tcPollId = null; }
    if (tcWasRunning) {
      tcWasRunning = false;
      const files  = tcStatus.files || [];
      const done   = files.filter(f => f.status === 'done').length;
      const failed = files.filter(f => f.status === 'failed').length;
      const type = tcStatus.killed ? 'secondary' : failed ? 'warning' : 'success';
      showToast(`Transcoding ${tcStatus.killed ? 'killed' : 'finished'}: ${done} done`
        + (failed ? `, ${failed} failed` : ''), type);
      if (done) fetchStatus().then(fetchLibrary);   // server refreshed the transcoded folder
    }
  }
}

function renderTranscodeBadge() {
  const badge = document.getElementById('transcode-badge');
  const s = tcStatus;
  if (!s || !s.started) { badge.classList.add('d-none'); return; }
  badge.classList.remove('d-none');
  const active = (s.files || []).filter(f => f.status !== 'skipped');
  const pct = Math.floor((s.overall || 0) * 100);
  if (s.running) {
    const pos = Math.min(active.filter(f => _TC_FINISHED.includes(f.status)).length + 1, active.length);
    badge.className = 'badge bg-info text-dark text-nowrap';
    badge.innerHTML = `<i class="bi bi-arrow-repeat spin me-1"></i>Transcoding ${pct}% (${pos}/${active.length})`;
  } else if (s.killed) {
    badge.className = 'badge bg-secondary text-nowrap';
    badge.innerHTML = '<i class="bi bi-x-octagon me-1"></i>Transcode killed';
  } else if (s.error || active.some(f => f.status === 'failed')) {
    badge.className = 'badge bg-danger text-nowrap';
    badge.innerHTML = '<i class="bi bi-exclamation-triangle me-1"></i>Transcode failed';
  } else {
    badge.className = 'badge bg-success text-nowrap';
    badge.innerHTML = `<i class="bi bi-cpu me-1"></i>Transcoded ${active.length}`;
  }
}

const _TC_ICON = {
  pending:   '<i class="bi bi-circle text-muted" title="Pending"></i>',
  running:   '<i class="bi bi-arrow-repeat spin text-info" title="Running"></i>',
  done:      '<i class="bi bi-check-circle text-success" title="Done"></i>',
  failed:    '<i class="bi bi-x-circle text-danger" title="Failed"></i>',
  killed:    '<i class="bi bi-x-octagon text-danger" title="Killed"></i>',
  cancelled: '<i class="bi bi-slash-circle text-muted" title="Cancelled"></i>',
  skipped:   '<i class="bi bi-skip-forward text-warning" title="Skipped"></i>',
};

function openTranscodeConsole() {
  tcConsoleOpen = true;
  renderTranscodeConsole();
  fetchTranscodeLog();
  getModal('transcodeConsoleModal').show();
}

function renderTranscodeConsole() {
  const s = tcStatus || {};
  document.getElementById('tc-kill-btn').disabled = !s.running;
  if (!s.running) resetConfirm(document.getElementById('tc-kill-btn'), document.getElementById('tc-kill-note'));
  document.getElementById('tc-console-files').innerHTML = (s.files || []).map(f => {
    const pct = Math.floor((f.progress || 0) * 100);
    const name = f.src.replace(/.*[/\\]/, '');
    const right = f.status === 'running'
      ? `${pct}%${f.speed ? ` · ${esc(f.speed)}` : ''}`
      : f.error ? `<span class="text-danger">${esc(f.error)}</span>` : esc(f.status);
    return `
      <div class="d-flex align-items-center gap-2 py-1 border-bottom border-secondary-subtle">
        ${_TC_ICON[f.status] || ''}
        <div class="flex-grow-1 min-w-0">
          <div class="text-truncate" title="${esc(f.src)}">${esc(name)}</div>
          ${f.status === 'running' ? `
          <div class="progress mt-1" style="height:4px">
            <div class="progress-bar bg-info" style="width:${pct}%"></div>
          </div>` : ''}
        </div>
        <span class="text-nowrap small">${right}</span>
      </div>`;
  }).join('') || '<div class="text-muted">No transcoding job has run in this session.</div>';
}

let _tcLogBusy = false;
async function fetchTranscodeLog() {
  if (_tcLogBusy) return;
  _tcLogBusy = true;
  const pre = document.getElementById('tc-console-log');
  try {
    const job = tcStatus && tcStatus.started;
    if (job !== tcLogJob) {   // a new job resets the server-side log
      tcLogJob = job;
      tcLogSeq = 0;
      pre.textContent = '';
    }
    const r = await api('GET', `/api/transcode/log?since=${tcLogSeq}`);
    if (r.lines.length) {
      const atBottom = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 20;
      pre.textContent += r.lines.join('\n') + '\n';
      if (atBottom) pre.scrollTop = pre.scrollHeight;
    }
    tcLogSeq = r.seq;
  } catch (_) {
  } finally {
    _tcLogBusy = false;
  }
}

async function killTranscode() {
  if (!confirmInDialog(document.getElementById('tc-kill-btn'), document.getElementById('tc-kill-note'),
    'The partially written file is deleted and the remaining files are cancelled.',
    '<i class="bi bi-exclamation-triangle-fill me-1"></i>Confirm kill')) return;
  try {
    await api('POST', '/api/transcode/kill');
    showToast('Transcoding killed.', 'secondary');
  } catch (e) {
    showToast('Kill failed: ' + _apiErr(e), 'danger');
  }
  pollTranscodeStatus();
}
