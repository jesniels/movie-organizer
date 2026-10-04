'use strict';
// Settings modal: loading/saving settings, paths and local shares, registered not-duplicate
// pairs, transcode settings and naming settings.

// ── Init ──────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  // Load settings whenever the settings modal opens
  document.getElementById('settingsModal').addEventListener('show.bs.modal', loadSettings);

  // The in-dialog confirmation of “Remove all” is cancelled by closing the dialog
  document.getElementById('settingsModal').addEventListener('hidden.bs.modal', () =>
    resetConfirm(document.getElementById('notdup-remove-all-btn'), document.getElementById('notdup-confirm-note')));
});

// ── Settings ──────────────────────────────────────────────────────────────────
async function loadSettings() {
  try {
    const cfg = await api('GET', '/api/config');
    configCache = cfg;
    renderLocationFilters();
    document.getElementById('server-mode-check').checked = !!cfg.server_mode;
    const shares = cfg.local_shares || {};
    renderPathList('locations-list', 'locations', (cfg.locations || []).map(p => ({ path: p, share: _shareFor(shares, p) })));
    renderPathList('downloads-list', 'downloads', (cfg.downloads || []).map(p => ({ path: p, share: _shareFor(shares, p) })));
    _fillTranscodeSettings(cfg.transcode);
    _fillNamingSettings(cfg.naming);
    document.getElementById('tc-output-share').value = _shareFor(shares, ((cfg.transcode || {}).output_root || ''));
    updateServerModeUI();
    fillMoveTargets(cfg);
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
    if (!confirmInDialog(document.getElementById('notdup-remove-all-btn'), document.getElementById('notdup-confirm-note'),
      `All ${notDupPairs.length} registered pair(s) will be removed and flagged as potential duplicates again.`,
      '<i class="bi bi-exclamation-triangle-fill me-1"></i>Confirm remove all')) return;
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

// Local share stored for a configured path (exact path match, case/trailing-slash insensitive)
function _shareFor(shares, path) {
  if (!path) return '';
  const n = _normRoot(path);
  for (const [k, v] of Object.entries(shares || {})) {
    if (_normRoot(k) === n) return v || '';
  }
  return '';
}

// Show/hide the local-share inputs to match the "Running as server" checkbox
function updateServerModeUI() {
  const on = document.getElementById('server-mode-check').checked;
  document.querySelectorAll('#settingsModal .local-share-row')
    .forEach(el => el.classList.toggle('d-none', !on));
}

function renderPathList(containerId, type, rows) {
  const serverMode = document.getElementById('server-mode-check').checked;
  document.getElementById(containerId).innerHTML = rows.map((r, i) => `
    <div class="mb-2">
      <div class="input-group input-group-sm">
        <input type="text" class="form-control bg-dark text-light border-secondary font-monospace path-input"
               data-type="${type}" data-index="${i}" value="${esc(r.path)}">
        <button class="btn btn-outline-danger" type="button" onclick="removePath('${type}',${i})">
          <i class="bi bi-x"></i>
        </button>
      </div>
      <div class="input-group input-group-sm mt-1 local-share-row${serverMode ? '' : ' d-none'}">
        <span class="input-group-text bg-dark text-info border-secondary"><i class="bi bi-pc-display me-1"></i>local share</span>
        <input type="text" class="form-control bg-dark text-light border-secondary font-monospace local-share-input"
               data-share-type="${type}" data-index="${i}" value="${esc(r.share || '')}"
               placeholder="empty → playing disabled for this path"
               title="This folder as seen from your machine — used by the Play buttons in server mode">
      </div>
    </div>`).join('');
}

// Current {path, share} rows of a path list, read from the DOM
function _readPathRows(type) {
  return [...document.querySelectorAll(`.path-input[data-type="${type}"]`)].map(inp => ({
    path:  inp.value,
    share: document.querySelector(`.local-share-input[data-share-type="${type}"][data-index="${inp.dataset.index}"]`)?.value || '',
  }));
}

function addPath(type) {
  const rows = _readPathRows(type);
  rows.push({ path: '', share: '' });
  renderPathList(type + '-list', type, rows);
  // focus new input
  const all = document.querySelectorAll(`.path-input[data-type="${type}"]`);
  all[all.length - 1]?.focus();
}

function removePath(type, index) {
  const rows = _readPathRows(type).filter((_, i) => i !== index);
  renderPathList(type + '-list', type, rows);
}

async function saveSettings(rescan) {
  const locRows = _readPathRows('locations');
  const dlRows  = _readPathRows('downloads');
  const locations = locRows.map(r => r.path.trim()).filter(Boolean);
  const downloads = dlRows.map(r => r.path.trim()).filter(Boolean);
  const transcode = _readTranscodeSettings();
  const server_mode = document.getElementById('server-mode-check').checked;
  const local_shares = {};
  for (const r of [...locRows, ...dlRows]) {
    const p = r.path.trim(), s = (r.share || '').trim();
    if (p && s) local_shares[p] = s;
  }
  const tcShare = _cleanPath(document.getElementById('tc-output-share').value);
  if (transcode.output_root && tcShare) local_shares[transcode.output_root] = tcShare;
  if (!Number.isInteger(transcode.qp) || transcode.qp < 0 || transcode.qp > 51) {
    showToast('Transcoding quality (QP) must be a whole number between 0 and 51.', 'warning');
    return;
  }
  if (!transcode.audio_languages.length) {
    showToast('Enter at least one preferred audio language (e.g. eng).', 'warning');
    return;
  }
  const naming = _readNamingSettings();
  const namingErr = _namingError(naming);
  if (namingErr) {
    showToast('Naming: ' + namingErr, 'warning');
    return;
  }
  try {
    const res = await api('POST', '/api/config', { locations, downloads, transcode, naming, server_mode, local_shares });
    if (!res.transcode || !res.naming) {
      showToast('Paths saved, but the server did NOT save all settings — the running server is older than this page. Restart it and save again.', 'danger');
      return;
    }
    configCache = { locations, downloads, transcode: res.transcode, naming: res.naming, server_mode, local_shares };
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
    showToast('Save failed: ' + _apiErr(e), 'danger');
  }
}

// ── Transcode settings ────────────────────────────────────────────────────────
function _fillTranscodeSettings(tc) {
  tc = tc || {};
  document.getElementById('tc-ffmpeg').value  = tc.ffmpeg_path  || '';
  document.getElementById('tc-ffprobe').value = tc.ffprobe_path || '';
  document.getElementById('tc-output').value  = tc.output_root  || '';
  document.getElementById('tc-encoder').value = tc.encoder || 'hevc_amf';
  document.getElementById('tc-qp').value      = tc.qp ?? 22;
  document.getElementById('tc-audio-langs').value  = (tc.audio_languages || ['eng']).join(', ');
  document.getElementById('tc-audio-reject').value = (tc.audio_reject_words || []).join(', ');
  document.getElementById('tc-audio-prefer').value = tc.audio_prefer || 'first';
  document.getElementById('tc-detect-result').textContent = '';
  updateEncoderHelp();
}

const _ENCODER_HELP = {
  hevc_amf:   'AMD GPU, fixed quantizer (QP). Fast. Recommended 20–24 — <strong>22 is a good default</strong> (same as your sample script). 18–20 = near-lossless but big files; 26+ = visibly softer.',
  hevc_nvenc: 'NVIDIA GPU, fixed quantizer (QP). Fast. Recommended 20–25 — <strong>22 is a good default</strong>. 18–20 = near-lossless but big files; 26+ = visibly softer.',
  libx265:    'CPU, constant rate factor (CRF). Smallest files for the same quality but very slow. Recommended 20–24 — <strong>22 is a good default</strong> for high quality (x265\'s own default of 28 is smaller but softer).',
};

function updateEncoderHelp() {
  const enc = document.getElementById('tc-encoder').value;
  document.getElementById('tc-encoder-help').innerHTML =
    `<i class="bi bi-lightbulb me-1 text-warning"></i>${_ENCODER_HELP[enc] || ''}
     <a href="#" class="ms-1" onclick="document.getElementById('tc-qp').value = 22; return false;">Use 22</a>`;
  document.getElementById('tc-test-result').innerHTML = '';
}

async function testEncoders(all) {
  const s = _readTranscodeSettings();
  const out = document.getElementById('tc-test-result');
  if (!Number.isInteger(s.qp) || s.qp < 0 || s.qp > 51) {
    out.innerHTML = '<span class="text-warning">Quality must be a whole number between 0 and 51.</span>';
    return;
  }
  const encoders = all
    ? [...document.getElementById('tc-encoder').options].map(o => o.value)
    : [s.encoder];
  const btns = ['tc-test-btn', 'tc-test-all-btn'].map(id => document.getElementById(id));
  btns.forEach(b => b.disabled = true);
  out.innerHTML = `<span class="text-muted"><i class="bi bi-arrow-repeat spin me-1"></i>Testing ${encoders.join(', ')}…</span>`;
  try {
    const r = await api('POST', '/api/transcode/test-encoder', { ffmpeg_path: s.ffmpeg_path, encoders, qp: s.qp });
    out.innerHTML = r.results.map(t => t.ok
      ? `<div><span class="text-success">✔ ${esc(t.encoder)} works on this machine</span>
           <span class="text-muted">(${t.seconds}s)</span></div>`
      : `<div><span class="text-danger">✖ ${esc(t.encoder)} does not work</span>
           <span class="text-warning">— ${esc(t.reason || '')}</span>
           <pre class="small text-light bg-black rounded p-2 mt-1 mb-1" style="white-space:pre-wrap;word-break:break-all">${esc(t.error)}</pre></div>`
    ).join('');
  } catch (e) {
    out.innerHTML = `<span class="text-danger">Test failed: ${esc(_apiErr(e))}</span>`;
  } finally {
    btns.forEach(b => b.disabled = false);
  }
}

// ── Naming settings ────────────────────────────────────────────────────────────────────────────
// Mirrors settings.naming_error on the server, which re-validates on save.
const _NAMING_FIELDS = [
  ['movie_folder',  'nm-movie-folder',  'Movie folder format',  'Alien', 1979, ''],
  ['movie_file',    'nm-movie-file',    'Movie file format',    'Alien', 1979, '.mkv'],
  ['series_folder', 'nm-series-folder', 'Series folder format', 'Breaking Bad', 2008, ''],
];

function _fillNamingSettings(nm) {
  nm = nm || {};
  document.getElementById('nm-movie-folder').value  = nm.movie_folder  ?? '{title} ({year})';
  document.getElementById('nm-movie-file').value    = nm.movie_file    ?? '{title} ({year})';
  document.getElementById('nm-series-folder').value = nm.series_folder ?? '{title} ({year})';
  document.getElementById('nm-file-eq-folder').checked = nm.file_equals_folder ?? true;
  document.getElementById('nm-rename-files').checked   = nm.rename_files  ?? true;
  document.getElementById('nm-resolve-nfos').checked   = nm.resolve_nfos  ?? true;
  document.getElementById('nm-delete-images').checked  = nm.delete_images ?? true;
  document.getElementById('nm-sanitize-names').checked = nm.sanitize_names ?? true;
  updateNamingUI();
}

function _readNamingSettings() {
  const v = id => document.getElementById(id).value;
  const c = id => document.getElementById(id).checked;
  const eq = c('nm-file-eq-folder');
  return {
    movie_folder:       v('nm-movie-folder'),
    movie_file:         eq ? v('nm-movie-folder') : v('nm-movie-file'),
    series_folder:      v('nm-series-folder'),
    file_equals_folder: eq,
    rename_files:       c('nm-rename-files'),
    resolve_nfos:       c('nm-resolve-nfos'),
    delete_images:      c('nm-delete-images'),
    sanitize_names:     c('nm-sanitize-names'),
  };
}

const _expandNaming = (tpl, title, year) =>
  tpl.replace(/\{(title|year)\}/g, (_, k) => k === 'title' ? title : String(year));

function _namingTemplateError(label, tpl) {
  if (!tpl.includes('{title}')) return `${label} must contain {title}`;
  const unknown = (tpl.match(/\{[^{}]*\}/g) || []).filter(p => p !== '{title}' && p !== '{year}');
  if (unknown.length) return `${label}: unknown placeholder ${unknown[0]} — only {title} and {year} are supported`;
  const sample = _expandNaming(tpl, 'Alien', 1979);
  if (/[{}]/.test(sample)) return `${label}: unmatched { or }`;
  if (/[\\/:*?"<>|\x00-\x1f]/.test(sample)) return `${label}: characters not allowed in file names (\\ / : * ? " < > |)`;
  if (/[. ]$/.test(sample)) return `${label}: must not end with a dot or space`;
  return null;
}

function _namingError(nm) {
  for (const [key, , label] of _NAMING_FIELDS) {
    const err = _namingTemplateError(label, nm[key] || '');
    if (err) return err;
  }
  return null;
}

// Sync/disable the file field, refresh the live previews and the error line
function updateNamingUI() {
  const eq = document.getElementById('nm-file-eq-folder').checked;
  const renameFiles = document.getElementById('nm-rename-files').checked;
  const fileInput = document.getElementById('nm-movie-file');
  if (eq) fileInput.value = document.getElementById('nm-movie-folder').value;
  fileInput.disabled = eq || !renameFiles;
  document.getElementById('nm-file-eq-folder').disabled = !renameFiles;
  for (const [, id, label, title, year, ext] of _NAMING_FIELDS) {
    const tpl = document.getElementById(id).value;
    const err = _namingTemplateError(label, tpl);
    const prev = document.getElementById(id + '-preview');
    prev.textContent = err ? '—' : _expandNaming(tpl, title, year) + ext;
    document.getElementById(id).classList.toggle('is-invalid', !!err);
  }
  document.getElementById('nm-error').textContent = _namingError(_readNamingSettings()) || '';
}

const _cleanPath = p => p.trim().replace(/^"+|"+$/g, '').trim();
const _csvList = s => s.split(',').map(w => w.trim().toLowerCase()).filter(Boolean);

function _readTranscodeSettings() {
  return {
    ffmpeg_path:  _cleanPath(document.getElementById('tc-ffmpeg').value),
    ffprobe_path: _cleanPath(document.getElementById('tc-ffprobe').value),
    output_root:  _cleanPath(document.getElementById('tc-output').value),
    encoder:      document.getElementById('tc-encoder').value,
    qp:           Number(document.getElementById('tc-qp').value),
    audio_languages:    _csvList(document.getElementById('tc-audio-langs').value),
    audio_reject_words: _csvList(document.getElementById('tc-audio-reject').value),
    audio_prefer:       document.getElementById('tc-audio-prefer').value,
  };
}

async function detectTranscodeTools() {
  const btn = document.getElementById('tc-detect-btn');
  const out = document.getElementById('tc-detect-result');
  btn.disabled = true;
  out.textContent = 'Checking…';
  const entered = _readTranscodeSettings();
  try {
    const r = await api('POST', '/api/transcode/tools', {
      ffmpeg_path: entered.ffmpeg_path, ffprobe_path: entered.ffprobe_path,
    });
    const lines = [];
    let changed = false;
    for (const [name, inputId] of [['ffmpeg', 'tc-ffmpeg'], ['ffprobe', 'tc-ffprobe']]) {
      const t = r[name] || {};
      const input = document.getElementById(inputId);
      if (t.source === 'verified') {
        if (input.value !== t.path) { input.value = t.path; changed = true; }
        lines.push(`<span class="text-success">✔ ${esc(name)} verified</span>: ${esc(t.version)}`);
      } else if (t.source === 'detected') {
        input.value = t.path;
        changed = true;
        const why = t.entered_invalid ? 'entered path does not work — ' : '';
        lines.push(`<span class="text-warning">✔ ${esc(name)}</span>: ${why}found ${esc(t.path)} (${esc(t.version)})`);
      } else {
        const why = t.entered_invalid ? 'entered path does not work and ' : '';
        lines.push(`<span class="text-danger">✖ ${esc(name)}</span>: ${why}not found in PATH or common folders`);
      }
    }
    if (changed) lines.push('<span class="text-warning">Paths updated — remember to Save.</span>');
    out.innerHTML = lines.join('<br>');
  } catch (e) {
    out.textContent = 'Check failed: ' + _apiErr(e);
  } finally {
    btn.disabled = false;
  }
}
