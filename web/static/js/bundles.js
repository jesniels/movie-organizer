'use strict';
// Bundles: the "Possible bundles" special filter, the Bundle dialog (create / add to / merge bundles,
// and Bundle… for the selected movies), the Bundles overview (show movies, rename) and the Settings
// "Not related" tab (its code lives here, not in settings.js — ADR 0004). Detection runs on the server
// with every scan; nothing moves without a run here.

// ── Bundle state ──────────────────────────────────────────────────────────────
let bundleData        = { proposals: [], bundles: [] };   // GET /api/bundles
let bundleProposalOf  = new Map();   // item id → index into bundleData.proposals
let bundleShowPath    = null;        // bundle whose movies the list shows ('Show movies'), or null
let bundleDialog      = null;        // what the Bundle dialog acts on (openBundleProposal / openBundleManual)
let bundleBusy        = false;       // real moves are running — the Bundle dialog and page stay open
let boRenaming        = null;        // index of the bundle being renamed in the overview, or null
let notRelatedRecords = [];          // Settings → Not related

const _BUNDLE_TYPE = { new: 'New bundle', add: 'Add to bundle', merge: 'Merge bundles' };

// ── Init ──────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('item-list').addEventListener('click', e => {
    const btn = e.target.closest('[data-bundle-review]');
    if (btn) openBundleProposal(+btn.dataset.bundleReview);
  });

  const modal = document.getElementById('bundleModal');
  modal.addEventListener('hide.bs.modal', e => { if (bundleBusy) e.preventDefault(); });
  modal.addEventListener('hidden.bs.modal', bundleCancelConfirm);
  window.addEventListener('beforeunload', e => {
    if (bundleBusy) { e.preventDefault(); e.returnValue = ''; }
  });
  const body = document.getElementById('bd-body');
  body.addEventListener('click', e => {
    const mark = e.target.closest('[data-bd-mark]');
    if (mark) _bundleMark(+mark.dataset.bdMark);
  });
  body.addEventListener('change', _bundleBodyChange);
  body.addEventListener('input', bundleCancelConfirm);

  document.getElementById('bo-list').addEventListener('click', _bundlesOverviewClick);
  document.getElementById('bo-list').addEventListener('input', bundleOverviewCancelConfirm);
  document.getElementById('bundlesModal').addEventListener('hidden.bs.modal', bundleOverviewCancelConfirm);

  document.getElementById('settingsModal').addEventListener('show.bs.modal', loadNotRelated);
  document.getElementById('settingsModal').addEventListener('hidden.bs.modal', () =>
    resetConfirm(document.getElementById('nr-remove-all-btn'), document.getElementById('nr-confirm-note')));
});

// ── Fetch ─────────────────────────────────────────────────────────────────────
/** Load the proposals and existing bundles (called with every /api/status fetch). */
async function fetchBundles() {
  try {
    bundleData = await api('GET', '/api/bundles');
  } catch (_) {
    return;
  }
  bundleProposalOf = new Map();
  bundleData.proposals.forEach((p, idx) => p.items.forEach(id => bundleProposalOf.set(id, idx)));
  if (activeSpecialFilter === 'bundles' || activeSpecialFilter === 'bundle-members') applyFilters();
  else if (lastStatus) renderStatusPanel(lastStatus);
}

const _bundleItem = id => allItems.find(i => i.id === id);
const _bundleBaseName = p => (p || '').split(/[\\/]/).pop();

/** Display label of one movie (its copy or its parts) in a bundle dialog. */
function _bundleMovieLabel(ids) {
  const item = _bundleItem(ids[0]);
  if (!item) return _bundleBaseName(ids[0]);
  return `${item.title}${item.year ? ` (${item.year})` : ''}${ids.length > 1 ? ` — ${ids.length} parts` : ''}`;
}

// ── Sidebar and special filter ────────────────────────────────────────────────
/** Sidebar rows of the special filters: Possible bundles (N), and the bundle shown by "Show movies". */
function bundleFilterRowsHtml() {
  const n = bundleData.proposals.length;
  const on = activeSpecialFilter === 'bundles';
  const showing = activeSpecialFilter === 'bundle-members' && bundleShowPath;
  return `
      <div class="special-filter-row${on ? ' sf-active' : ''}" onclick="toggleSpecialFilter('bundles')"
           title="Movies that could be bundled, grouped per proposal">
        <i class="bi bi-${on ? 'check-square-fill text-info' : 'square'} me-2"></i>
        <span>Possible bundles</span>
        <span class="ms-auto badge ${n ? 'bg-info-subtle text-info-emphasis' : 'bg-secondary-subtle text-secondary-emphasis'}">${n}</span>
      </div>${showing ? `
      <div class="special-filter-row sf-active" onclick="toggleSpecialFilter('bundle-members')" title="Click to show everything again">
        <i class="bi bi-check-square-fill text-info me-2"></i>
        <span class="text-truncate">Bundle: ${esc(_bundleBaseName(bundleShowPath))}</span>
      </div>` : ''}`;
}

/** Sidebar row below the special filters that opens the Bundles overview. */
function bundlesOverviewRowHtml() {
  const n = bundleData.bundles.length;
  return `
    <div class="special-filter-row mt-2" onclick="openBundlesOverview()" title="Every bundle — show its movies or rename it">
      <i class="bi bi-collection me-2"></i>
      <span>Bundles</span>
      <span class="ms-auto badge bg-secondary-subtle text-secondary-emphasis">${n}</span>
    </div>`;
}

/** True when the item passes the active bundle filter ('bundles' or 'bundle-members'). */
function bundleFilterMatch(item) {
  if (activeSpecialFilter === 'bundles') return bundleProposalOf.has(item.id);
  return !!item.bundle && !!bundleShowPath && _normRoot(item.bundle) === _normRoot(bundleShowPath);
}

/** The Possible bundles view: one card per proposal with its movies and a Review… button. */
function renderBundleProposalGroups() {
  const shown = new Map(filteredItems.map(i => [i.id, i]));
  return bundleData.proposals.map((p, idx) => {
    const members = p.items.filter(id => shown.has(id)).map(id => shown.get(id));
    if (!members.length) return '';
    const note = members.length < p.items.length
      ? `${members.length} of ${p.items.length} shown (others hidden by filters)`
      : `${p.movies.length} movie(s)`;
    const where = p.type === 'new' ? '' : ` → ${esc(p.bundle)}`;
    return `
    <div class="dup-group mx-3 my-3 border border-info-subtle rounded">
      <div class="dup-group-header d-flex align-items-center gap-2 px-3 py-2">
        <i class="bi bi-collection text-info"></i>
        <span class="fw-semibold text-info-emphasis text-truncate">${esc(p.name)}</span>
        <span class="badge bg-info-subtle text-info-emphasis">${_BUNDLE_TYPE[p.type]}</span>
        <span class="text-muted small text-truncate" title="${esc(p.reason)}">${esc(p.reason)} · ${note}${where}</span>
        <div class="ms-auto flex-shrink-0">
          <button type="button" class="btn btn-sm btn-outline-info py-0" data-bundle-review="${idx}">
            <i class="bi bi-eye me-1"></i>Review…
          </button>
        </div>
      </div>
      ${members.map(i => renderRow(i)).join('')}
    </div>`;
  }).join('');
}

// ── Bundle dialog ─────────────────────────────────────────────────────────────
/** Open the Bundle dialog for proposal `idx` (new bundle, add to bundle or merge bundles). */
function openBundleProposal(idx) {
  const p = bundleData.proposals[idx];
  if (!p) return;
  bundleDialog = { mode: p.type, proposal: p, movies: p.movies.map(m => ({ ids: m.ids, why: m.why || '', on: true })) };
  if (p.type === 'merge') Object.assign(bundleDialog, { source: p.source, target: p.target });
  _openBundleDialog();
}

/** Open the Bundle dialog for the selected movies (Bundle… in the selection bar). */
function openBundleManual() {
  if (!selectedIds.size) return;
  const action = bundleData.bundles.length ? 'existing' : 'new';
  bundleDialog = { mode: 'manual', action, movies: [...selectedIds].map(id => ({ ids: [id], on: true })) };
  _openBundleDialog();
}

function _openBundleDialog() {
  document.getElementById('bd-dryrun').checked = true;
  _renderBundleDialog();
  getModal('bundleModal').show();
}

/** Enabled Library Locations as <option>s, `selected` pre-selected. */
function _bundleLocationOptions(selected) {
  return (configCache.locations || []).filter(p => p && pathEnabled(p))
    .map(p => `<option value="${esc(p)}"${p === selected ? ' selected' : ''}>${esc(p)}</option>`).join('');
}

/** One movie row: tick, title, where it is, and an Outside / Not related button (`markLabel`). */
function _bundleMovieRow(m, i, markLabel) {
  const item = _bundleItem(m.ids[0]);
  const path = item ? item.path : '';
  const mark = !markLabel ? ''
    : m.marked ? '<span class="text-warning text-nowrap small">remembered — not related</span>'
      : `<button type="button" class="btn btn-sm btn-outline-secondary py-0 text-nowrap" data-bd-mark="${i}"
           title="Remember that this movie does not belong here (Settings → Not related)">${markLabel}</button>`;
  return `
    <div class="d-flex align-items-center gap-2 py-1 border-bottom border-secondary-subtle${m.marked ? ' opacity-50' : ''}">
      <input class="form-check-input mt-0 bd-tick" type="checkbox" data-idx="${i}"
             ${m.on && !m.marked ? 'checked' : ''} ${m.marked ? 'disabled' : ''}>
      <span class="text-truncate" title="${esc(m.ids.join('\n'))}">${esc(_bundleMovieLabel(m.ids))}</span>
      ${m.why ? `<span class="text-muted text-nowrap">${esc(m.why)}</span>` : ''}
      <span class="ms-auto text-muted font-monospace text-truncate" title="${esc(path)}">${esc(path)}</span>
      ${mark}
    </div>`;
}

/** Members of an existing bundle as movie rows (one per item), for Merge. */
function _bundleMembers(path) {
  const b = bundleData.bundles.find(x => _normRoot(x.path) === _normRoot(path));
  return b ? b.items.map(id => ({ ids: [id], on: true })) : [];
}

function _renderBundleDialog() {
  const d = bundleDialog;
  const p = d.proposal;
  const title = d.mode === 'manual' ? 'Bundle…' : `${_BUNDLE_TYPE[d.mode]} — ${p.reason}`;
  document.getElementById('bd-title').textContent = title;
  const rows = (label) => d.movies.map((m, i) => _bundleMovieRow(m, i, label)).join('');
  let html = '';
  if (d.mode === 'new') {
    html = `
      <div class="row g-2 mb-3">
        <div class="col-7"><label class="form-label mb-1" for="bd-name">Name</label>
          <input id="bd-name" class="form-control form-control-sm bg-dark text-light border-secondary" value="${esc(d.name ?? p.name)}"></div>
        <div class="col-5"><label class="form-label mb-1" for="bd-location">in</label>
          <select id="bd-location" class="form-select form-select-sm bg-dark text-light border-secondary">
            ${_bundleLocationOptions(d.location ?? p.location)}</select></div>
      </div>
      ${rows('Outside')}`;
  } else if (d.mode === 'add') {
    const ctx = p.context.map(id => (_bundleItem(id) || {}).title || _bundleBaseName(id));
    html = `
      <div class="mb-1">Bundle: <span class="font-monospace">${esc(p.bundle)}\\</span></div>
      <div class="mb-3 text-muted">Already in the bundle: ${esc(ctx.join(', ') || '—')}</div>
      ${rows('Not related')}`;
  } else if (d.mode === 'merge') {
    const count = path => (bundleData.bundles.find(b => _normRoot(b.path) === _normRoot(path)) || {}).count || 0;
    html = `
      <div class="d-flex align-items-center gap-2 mb-1">
        <span class="text-nowrap">Into:</span>
        <select id="bd-target" class="form-select form-select-sm bg-dark text-light border-secondary font-monospace">
          ${[p.target, p.source].map(x => `<option value="${esc(x)}"${x === d.target ? ' selected' : ''}>${esc(x)}\\</option>`).join('')}
        </select>
        <span class="text-nowrap">${count(d.target)} movie(s)</span>
      </div>
      <div class="mb-3">From: <span class="font-monospace">${esc(d.source)}\\</span> — ${count(d.source)} movie(s).
        A movie whose folder already exists in the target is refused (a Duplicates matter).</div>
      ${rows('')}`;
  } else {
    const opt = (v, label) => `
      <div class="form-check">
        <input class="form-check-input" type="radio" name="bd-action" id="bd-action-${v}" value="${v}" ${d.action === v ? 'checked' : ''}>
        <label class="form-check-label" for="bd-action-${v}">${label}</label>
      </div>`;
    const bundles = bundleData.bundles.map(b =>
      `<option value="${esc(b.path)}"${b.path === d.bundle ? ' selected' : ''}>${esc(b.name)} — ${esc(b.location)}</option>`).join('');
    html = `
      ${opt('existing', 'Into an existing bundle')}
      <select id="bd-bundle" class="form-select form-select-sm bg-dark text-light border-secondary ms-4 mb-2 w-auto"
              ${d.action === 'existing' ? '' : 'disabled'}>${bundles || '<option value="">— no bundles found —</option>'}</select>
      ${opt('new', 'Into a new bundle')}
      <div class="row g-2 ms-3 mb-2">
        <div class="col-7"><input id="bd-name" class="form-control form-control-sm bg-dark text-light border-secondary"
             placeholder="Bundle name" value="${esc(d.name || '')}" ${d.action === 'new' ? '' : 'disabled'}></div>
        <div class="col-5"><select id="bd-location" class="form-select form-select-sm bg-dark text-light border-secondary"
             ${d.action === 'new' ? '' : 'disabled'}>${_bundleLocationOptions(d.location)}</select></div>
      </div>
      ${opt('out', 'Out of bundle — each movie to the root of its bundle\'s Library Location')}
      <div class="mt-3">${rows('')}</div>`;
  }
  document.getElementById('bd-body').innerHTML = html;
  const nr = document.getElementById('bd-notrelated-btn');
  nr.classList.toggle('d-none', !(d.mode === 'new' || d.mode === 'merge'));
  nr.title = d.mode === 'merge' ? 'Remember that these two bundles are not to be merged'
    : 'Remember that these movies are unrelated (Settings → Not related)';
  _bundleRunLabel();
}

/** The run button's label for the dialog's action. */
function _bundleRunLabel() {
  const d = bundleDialog;
  const action = d.mode === 'manual' ? d.action : d.mode;
  const label = { new: 'Create bundle', add: 'Add to bundle', merge: 'Merge', existing: 'Add to bundle', out: 'Out of bundle' }[action];
  document.getElementById('bd-run-btn').innerHTML = `<i class="bi bi-collection me-1"></i>${label}`;
}

/** Keep the dialog's inputs in bundleDialog, so a re-render (and Back from a dry run) keeps them. */
function _bundleBodyChange(e) {
  const d = bundleDialog;
  bundleCancelConfirm();
  if (e.target.classList.contains('bd-tick')) {
    d.movies[+e.target.dataset.idx].on = e.target.checked;
  } else if (e.target.name === 'bd-action') {
    _bundleReadInputs();
    d.action = e.target.value;
    _renderBundleDialog();
  } else if (e.target.id === 'bd-target') {
    // Switching the target swaps which bundle's movies move
    [d.source, d.target] = [d.target, e.target.value];
    d.movies = _bundleMembers(d.source);
    _renderBundleDialog();
  }
}

/** Copy the name/location/bundle inputs of the manual dialog into bundleDialog. */
function _bundleReadInputs() {
  const d = bundleDialog;
  const v = id => document.getElementById(id)?.value;
  if (v('bd-name') !== undefined) d.name = v('bd-name');
  if (v('bd-location') !== undefined) d.location = v('bd-location');
  if (v('bd-bundle') !== undefined) d.bundle = v('bd-bundle');
}

/** Cancel a pending in-dialog confirmation of the run button. */
function bundleCancelConfirm() {
  resetConfirm(document.getElementById('bd-run-btn'), document.getElementById('bd-confirm-note'));
}

/** Remember one movie as Outside / Not related for this proposal. */
async function _bundleMark(i) {
  const d = bundleDialog;
  const m = d.movies[i];
  const key = d.mode === 'add' ? 'bundle:' + d.proposal.bundle : d.proposal.key;
  try {
    await api('POST', '/api/bundles/not-related', { records: m.ids.map(id => ({ path: id, key })) });
  } catch (e) {
    showToast('Could not remember it: ' + _apiErr(e), 'danger');
    return;
  }
  m.marked = true;
  m.on = false;
  _bundleReadInputs();
  _renderBundleDialog();
  fetchBundles();
  showToast(`${_bundleMovieLabel(m.ids)} will not be proposed here again (Settings → Not related).`, 'info');
}

/** Not related on the whole proposal: every movie (or, for Merge, the two bundles) is remembered. */
async function bundleNotRelated() {
  const d = bundleDialog;
  const p = d.proposal;
  const records = d.mode === 'merge'
    ? [{ path: d.source, key: 'bundle:' + d.target }]
    : d.movies.flatMap(m => m.ids.map(id => ({ path: id, key: p.key })));
  try {
    await api('POST', '/api/bundles/not-related', { records });
  } catch (e) {
    showToast('Could not remember it: ' + _apiErr(e), 'danger');
    return;
  }
  closeModalDone('bundleModal');
  await fetchBundles();
  applyFilters();
  showToast(`“${p.name}” will not be proposed again (Settings → Not related).`, 'info');
}

/** The request for the dialog's action: { url, body } without item_ids/dry_run, or { error }. */
function _bundleRequest() {
  const d = bundleDialog;
  _bundleReadInputs();
  const action = d.mode === 'manual' ? d.action : d.mode;
  if (action === 'new') {
    const name = (d.name ?? '').trim();
    if (!name) return { error: 'Enter a name for the new bundle.' };
    if (!d.location) return { error: 'Choose a Library Location for the new bundle.' };
    return { url: '/api/bundles/move', body: { location: d.location, name, proposal_key: _bundleProposalKey(d) } };
  }
  if (action === 'add') return { url: '/api/bundles/move', body: { bundle: d.proposal.bundle } };
  if (action === 'existing') {
    if (!d.bundle) return { error: 'Choose a bundle.' };
    return { url: '/api/bundles/move', body: { bundle: d.bundle } };
  }
  if (action === 'merge') return { url: '/api/bundles/merge', body: { source: d.source, target: d.target } };
  return { url: '/api/bundles/out', body: {} };
}

/** The proposal key of a new-bundle proposal (its not-related records follow the new folder), or null. */
function _bundleProposalKey(d) {
  return d.proposal && d.proposal.type === 'new' ? d.proposal.key : null;
}

/** Run button: dry run (result with Back), or — after the in-dialog confirmation — the real moves. */
async function runBundleAction() {
  const d = bundleDialog;
  const ids = d.movies.filter(m => m.on && !m.marked).flatMap(m => m.ids);
  if (!ids.length) {
    showToast('Tick at least one movie.', 'warning');
    return;
  }
  const req = _bundleRequest();
  if (req.error) {
    showToast(req.error, 'warning');
    return;
  }
  if (document.getElementById('bd-dryrun').checked) return _bundleDryRun(req, ids);
  const n = d.movies.filter(m => m.on && !m.marked).length;
  if (!confirmInDialog(document.getElementById('bd-run-btn'), document.getElementById('bd-confirm-note'),
    `${n} movie(s) will be moved. Jellyfin may lose their played status.`,
    '<i class="bi bi-exclamation-triangle-fill me-1"></i>Confirm')) return;
  await _bundleRealRun(req, ids);
}

/** Lines for the result dialog: the new bundle folder first, then every movie (failures first). */
function _bundleResultLines(results) {
  const created = (results.find(r => r.created) || {}).created;
  return [...(created ? [`+ new bundle folder: ${created}`] : []), ..._moveResultLines(results)];
}

async function _bundleDryRun(req, ids) {
  const btn = document.getElementById('bd-run-btn');
  btn.disabled = true;
  let results;
  try {
    results = await api('POST', req.url, { ...req.body, item_ids: ids, dry_run: true });
  } catch (e) {
    showToast(_apiErr(e), 'danger');
    return;
  } finally {
    btn.disabled = false;
  }
  const fail = results.filter(r => !r.ok).length;
  const good = !fail && results.length > 0;
  const status = good ? `All ${results.length} item(s) can be moved.`
    : `${fail} of ${results.length} item(s) cannot be moved — they will be skipped.`;
  swapModal('bundleModal', () => showResultModal({
    good, status, lines: _bundleResultLines(results),
    closeText: 'Back to Bundle', onClose: () => getModal('bundleModal').show(),
  }));
}

/** Lock/unlock the Bundle dialog and show/hide its progress area. */
function _setBundleBusy(busy) {
  bundleBusy = busy;
  document.getElementById('bd-progress').classList.toggle('d-none', !busy);
  for (const id of ['bd-run-btn', 'bd-close-btn', 'bd-notrelated-btn', 'bd-dryrun']) {
    document.getElementById(id).disabled = busy;
  }
  document.querySelector('#bundleModal .btn-close').disabled = busy;
  document.querySelectorAll('#bd-body input, #bd-body select, #bd-body button').forEach(c => { c.disabled = busy; });
}

function _setBundleProgress(done, total, current) {
  const bar = document.getElementById('bd-progress-bar');
  bar.style.width = `${total ? Math.round(done / total * 100) : 0}%`;
  bar.textContent = `${done} / ${total}`;
  document.getElementById('bd-progress-text').textContent = current ? `Moving: ${current}` : '';
}

/** Real run: a dry run plans the requests (the parts of one movie move together), then one request per movie. */
async function _bundleRealRun(req, ids) {
  let plan;
  try {
    plan = await api('POST', req.url, { ...req.body, item_ids: ids, dry_run: true });
  } catch (e) {
    showToast('Nothing moved: ' + _apiErr(e), 'danger');
    return;
  }
  const results = plan.filter(r => !r.ok);
  const batches = [];
  const byFolder = new Map();
  for (const r of plan.filter(r => r.ok)) {
    if (!r.folder) { batches.push([r.id]); continue; }
    if (!byFolder.has(r.folder)) { byFolder.set(r.folder, []); batches.push(byFolder.get(r.folder)); }
    byFolder.get(r.folder).push(r.id);
  }
  _setBundleBusy(true);
  let body = { ...req.body };
  let aborted = '';
  for (let n = 0; n < batches.length; n++) {
    _setBundleProgress(n, batches.length, _bundleMovieLabel(batches[n]));
    try {
      const res = await api('POST', req.url, { ...body, item_ids: batches[n], dry_run: false });
      results.push(...res);
      // The new bundle exists after the first move — the next movies are added to it
      const made = res.find(r => r.ok && r.created);
      if (made && body.name) body = { bundle: made.created, proposal_key: body.proposal_key };
    } catch (e) {
      aborted = _apiErr(e);
      break;
    }
  }
  _setBundleProgress(batches.length, batches.length, '');
  _setBundleBusy(false);

  const okIds = new Set(results.filter(r => r.ok).map(r => r.id));
  if (okIds.size) {
    // Moved items no longer exist at their listed path; the rescan adds them back at the new place
    allItems = allItems.filter(i => !okIds.has(i.id));
    okIds.forEach(id => selectedIds.delete(id));
    updateSelectionUI();
    applyFilters();
    triggerScan();
  }
  const fail = results.filter(r => !r.ok).length;
  const warned = results.filter(r => r.warning).length;
  const review = leftoverReviewAction(results);
  const show = () => {
    if (!fail && !aborted && !warned && !review) {
      showToast(`Moved ${okIds.size} item(s) — rescanning…`, 'success');
      return;
    }
    const good = !fail && !aborted && !warned;
    let status = good ? `Moved ${okIds.size} item(s) — rescanning…` : `Moved ${okIds.size} of ${ids.length} item(s). ${fail} not moved.`;
    if (warned) status += `<br>${warned} item(s) moved, but some related files stayed behind.`;
    if (aborted) status += `<br>Stopped: ${esc(aborted)}`;
    showResultModal({ title: 'Bundle Result', good, status, lines: _bundleResultLines(results), action: review });
  };
  document.getElementById('bundleModal').addEventListener('hidden.bs.modal', show, { once: true });
  closeModalDone('bundleModal');
}

// ── Bundles overview ──────────────────────────────────────────────────────────
function openBundlesOverview() {
  boRenaming = null;
  document.getElementById('bo-dryrun').checked = true;
  document.getElementById('bo-confirm-note').innerHTML = '';
  _renderBundlesOverview();
  getModal('bundlesModal').show();
}

function _renderBundlesOverview() {
  const list = document.getElementById('bo-list');
  if (!bundleData.bundles.length) {
    list.innerHTML = '<div class="text-muted fst-italic">No bundles in the Library Locations (a rescan finds them).</div>';
    return;
  }
  const btn = (act, cls, html) => `<button type="button" class="btn btn-sm ${cls} py-0 text-nowrap" data-bo="${act}">${html}</button>`;
  list.innerHTML = bundleData.bundles.map((b, i) => `
    <div class="d-flex align-items-center gap-2 py-1 border-bottom border-secondary-subtle" data-idx="${i}">
      ${boRenaming === i
        ? `<input id="bo-name" class="form-control form-control-sm bg-dark text-light border-secondary w-auto flex-grow-1"
                  value="${esc(b.name)}">`
        : `<span class="fw-semibold text-truncate" title="${esc(b.path)}">${esc(b.name)}</span>`}
      ${b.nested ? `<span class="badge bg-warning-subtle text-warning-emphasis text-nowrap"
          title="Its only movie has the same name — usually an unpacked download, not a real bundle. Out of bundle on the movie fixes it.">
          nested movie folder?</span>` : ''}
      <span class="ms-auto text-muted font-monospace text-truncate">${esc(b.location)}</span>
      <span class="text-nowrap">${b.count} movie(s)</span>
      ${boRenaming === i
        ? btn('do-rename', 'btn-warning', '<i class="bi bi-pencil me-1"></i>Rename') + btn('cancel', 'btn-outline-secondary', 'Cancel')
        : btn('show', 'btn-outline-info', '<i class="bi bi-list-ul me-1"></i>Show movies')
          + btn('rename', 'btn-outline-warning', '<i class="bi bi-pencil me-1"></i>Rename')}
    </div>`).join('');
}

function _bundlesOverviewClick(e) {
  const btn = e.target.closest('[data-bo]');
  if (!btn) return;
  const i = +btn.closest('[data-idx]').dataset.idx;
  const act = btn.dataset.bo;
  if (act === 'show') {
    bundleShowPath = bundleData.bundles[i].path;
    activeSpecialFilter = 'bundle-members';
    closeModalDone('bundlesModal');
    applyFilters();
  } else if (act === 'rename' || act === 'cancel') {
    bundleOverviewCancelConfirm();
    boRenaming = act === 'rename' ? i : null;
    _renderBundlesOverview();
    if (act === 'rename') document.getElementById('bo-name').focus();
  } else if (act === 'do-rename') {
    _bundleRename(i, btn);
  }
}

/** Cancel a pending in-dialog confirmation of the overview's Rename button. */
function bundleOverviewCancelConfirm() {
  resetConfirm(document.querySelector('#bo-list [data-bo="do-rename"]'), document.getElementById('bo-confirm-note'));
}

/** Rename a bundle folder: dry run (result with Back), or — after the in-dialog confirmation — for real. */
async function _bundleRename(i, btn) {
  const b = bundleData.bundles[i];
  const name = document.getElementById('bo-name').value.trim();
  const note = document.getElementById('bo-confirm-note');
  if (!name || name === b.name) {
    showToast('Enter a new name.', 'warning');
    return;
  }
  const dryRun = document.getElementById('bo-dryrun').checked;
  if (!dryRun && !confirmInDialog(btn, note, `The folder ${b.path} will be renamed to “${name}”.`,
    '<i class="bi bi-exclamation-triangle-fill me-1"></i>Confirm rename')) return;
  try {
    const r = await api('POST', '/api/bundles/rename', { path: b.path, new_name: name, dry_run: dryRun });
    if (dryRun) {
      swapModal('bundlesModal', () => showResultModal({
        good: true, status: 'The bundle folder can be renamed.', lines: [`${r.from}\n  → ${r.to}`],
        closeText: 'Back to Bundles', onClose: () => getModal('bundlesModal').show(),
      }));
      return;
    }
    closeModalDone('bundlesModal');
    showToast(`Bundle renamed to “${name}” — rescanning…`, 'success');
    triggerScan();
  } catch (e) {
    showToast('Rename failed: ' + _apiErr(e), 'danger');
  }
}

// ── Settings → Not related ────────────────────────────────────────────────────
async function loadNotRelated() {
  try {
    notRelatedRecords = await api('GET', '/api/bundles/not-related');
  } catch (_) {
    notRelatedRecords = [];
  }
  renderNotRelatedList();
}

/** What a record's key says, e.g. "bundle M:\Movies\James Bond" or "similar titles: rocky". */
function _notRelatedKeyLabel(key) {
  if (key.startsWith('bundle:')) return 'bundle ' + key.slice(7);
  if (key.startsWith('collection:')) return 'collection proposal: ' + key.slice(11);
  if (key.startsWith('title:')) return 'similar titles: ' + key.slice(6);
  return key;
}

function renderNotRelatedList() {
  document.getElementById('nr-count').textContent = notRelatedRecords.length;
  const listEl = document.getElementById('nr-list');
  if (!notRelatedRecords.length) {
    listEl.innerHTML = '<div class="text-muted small fst-italic">No movies marked “not related”.</div>';
    return;
  }
  listEl.innerHTML = notRelatedRecords.map((r, idx) => {
    const item = _bundleItem(r.path);
    const name = item ? `${item.title}${item.year ? ` (${item.year})` : ''}` : _bundleBaseName(r.path);
    return `
    <div class="form-check py-1 border-bottom border-secondary-subtle">
      <input class="form-check-input nr-check" type="checkbox" id="nr-${idx}" data-idx="${idx}">
      <label class="form-check-label small w-100" for="nr-${idx}">
        <div class="text-truncate" title="${esc(r.path)}">${esc(name)}
          ${r.found ? '' : '<span class="badge bg-danger-subtle text-danger-emphasis ms-1">not found</span>'}</div>
        <div class="font-monospace text-truncate text-muted" title="${esc(r.key)}">— ${esc(_notRelatedKeyLabel(r.key))}</div>
      </label>
    </div>`;
  }).join('');
  filterNotRelated();
}

function filterNotRelated() {
  const q = document.getElementById('nr-filter').value.toLowerCase().trim();
  if (!q) return;   // clearing the filter keeps the current selection
  document.querySelectorAll('.nr-check').forEach(cb => {
    const r = notRelatedRecords[+cb.dataset.idx];
    cb.checked = r.path.toLowerCase().includes(q) || r.key.toLowerCase().includes(q);
  });
}

function selectNotRelated(on) {
  document.querySelectorAll('.nr-check').forEach(cb => { cb.checked = on; });
}

async function removeNotRelated(all) {
  let records;
  if (all) {
    if (!notRelatedRecords.length) return;
    if (!confirmInDialog(document.getElementById('nr-remove-all-btn'), document.getElementById('nr-confirm-note'),
      `All ${notRelatedRecords.length} record(s) will be removed; those movies can be proposed again.`,
      '<i class="bi bi-exclamation-triangle-fill me-1"></i>Confirm remove all')) return;
    records = notRelatedRecords;
  } else {
    records = [...document.querySelectorAll('.nr-check:checked')].map(cb => notRelatedRecords[+cb.dataset.idx]);
    if (!records.length) {
      showToast('No records selected.', 'warning');
      return;
    }
  }
  try {
    const res = await api('POST', '/api/bundles/not-related/remove',
      { records: records.map(r => ({ path: r.path, key: r.key })) });
    showToast(`Removed ${res.removed} record(s).`, 'success');
    await loadNotRelated();
    await fetchBundles();
  } catch (e) {
    showToast('Remove failed: ' + _apiErr(e), 'danger');
  }
}
