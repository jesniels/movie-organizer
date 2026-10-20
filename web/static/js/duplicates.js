'use strict';
// Duplicates: compare modal, NFO field-selective copy, marking items as not duplicate and
// replacing an original with its transcoded copy.

// ── Duplicates state ──────────────────────────────────────────────────────────
let compareIds          = [];      // [leftId, rightId] currently shown in compare modal

// ── Init ──────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  // Event delegation for compare modal (play / copy-NFO / not-duplicate buttons)
  document.getElementById('compare-body').addEventListener('click', e => {
    const playBtn = e.target.closest('[data-play-file]');
    if (playBtn) {
      playItem(playBtn.dataset.itemId, playBtn.dataset.playFile || undefined);
      return;
    }
    const copyBtn = e.target.closest('[data-copy-nfo-from]');
    if (copyBtn) {
      e.preventDefault();
      copyNfo(copyBtn.dataset.copyNfoFrom, copyBtn.dataset.copyNfoTo);
      return;
    }
    const notDupBtn = e.target.closest('[data-notdup-item]');
    if (notDupBtn) {
      markNotDuplicateItem(notDupBtn.dataset.notdupItem);
      return;
    }
    const delBtn = e.target.closest('[data-delete-item]');
    if (delBtn) {
      leaveModalFor('compareModal', 'deleteModal');
      openDeleteDialog([delBtn.dataset.deleteItem]);
    }
  });

  // In-dialog confirmations are cancelled by closing the dialog or changing its options
  const utReset = () => resetConfirm(document.getElementById('ut-confirm-btn'), document.getElementById('ut-confirm-note'));
  document.getElementById('useTranscodedModal').addEventListener('hidden.bs.modal', utReset);
  document.getElementById('ut-original').addEventListener('change', utReset);
  document.getElementById('ut-dryrun').addEventListener('change', utReset);
});

// ── Duplicate compare modal ────────────────────────────────────────────────
function _compareColumn(item, others) {
  const n = item.nfo || {};
  const quality = item.nfo_quality || (Object.keys(n).length ? 'partial' : 'none');
  const nfoStatus = nfoStatusHtml(item);

  const locBadge = locBadgeHtml(item.location);

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
            <button type="button" class="btn btn-sm btn-play border-0 py-0 px-1"${_playAttrs(item, 'Play this file')}
                    data-play-file="${esc(f)}" data-item-id="${esc(item.id)}">
              <i class="bi bi-play-circle"></i>
            </button>
            <span class="text-truncate" title="${esc(f)}">${esc(f.split(/[/\\]/).pop())}</span>
            <span class="ms-auto text-nowrap text-muted file-size" data-file="${esc(f)}">…</span>
          </div>`).join('')}
      </div>
      <div class="d-flex gap-2 mt-3">
        <button type="button" class="btn btn-sm btn-outline-light"${_playAttrs(item, 'Play in your local player')} data-play-file="" data-item-id="${esc(item.id)}">
          <i class="bi bi-play-fill me-1"></i>Play
        </button>
        ${others.length === 1 ? `
        <button type="button" class="btn btn-sm btn-outline-info"${copyDisabled}
                data-copy-nfo-from="${esc(item.id)}" data-copy-nfo-to="${esc(others[0].id)}"
                title="Copy this NFO to the other item">
          <i class="bi bi-arrow-left-right me-1"></i>Copy NFO to other side
        </button>` : `
        <div class="btn-group">
          <button type="button" class="btn btn-sm btn-outline-info dropdown-toggle"${copyDisabled}
                  data-bs-toggle="dropdown" title="Copy this NFO to another copy">
            <i class="bi bi-arrow-left-right me-1"></i>Copy NFO to…
          </button>
          <ul class="dropdown-menu dropdown-menu-dark">
            ${others.map(o => `
            <li><a class="dropdown-item small" href="#"
                   data-copy-nfo-from="${esc(item.id)}" data-copy-nfo-to="${esc(o.id)}">
              ${locBadgeHtml(o.location, null, 'me-1')}
              <span class="font-monospace" style="font-size:.8em">${esc(o.path)}</span>
            </a></li>`).join('')}
          </ul>
        </div>
        <button type="button" class="btn btn-sm btn-outline-success ms-auto" data-notdup-item="${esc(item.id)}"
                title="This copy is a different title — stop flagging it against the others">
          <i class="bi bi-check2-circle"></i>
        </button>`}
        <button type="button" class="btn btn-sm btn-outline-danger${others.length === 1 ? ' ms-auto' : ''}"
                data-delete-item="${esc(item.id)}" title="Delete this copy (opens the Delete dialog, dry run first)">
          <i class="bi bi-trash"></i>
        </button>
      </div>
    </div>`;
}

async function showCompareModal(itemId) {
  const item = allItems.find(i => i.id === itemId);
  const peers = duplicateMap.get(itemId) || [];
  if (!item || peers.length === 0) { showDetail(itemId); return; }

  const group = [item, ...peers.map(p => allItems.find(i => i.id === p.id)).filter(Boolean)];
  if (group.length < 2) { showDetail(itemId); return; }
  compareIds = group.map(i => i.id);

  const wide   = group.length > 3;   // 4+ copies scroll horizontally
  const colCls = group.length === 2 ? 'col-md-6' : 'col-md-4';
  const columns = group.map(it => {
    const others = group.filter(o => o.id !== it.id);
    const col = _compareColumn(it, others);
    return wide ? `<div class="compare-col-fixed">${col}</div>` : `<div class="${colCls}">${col}</div>`;
  }).join('');

  const groupHint = group.length > 2
    ? `, or <i class="bi bi-check2-circle text-success"></i> on a copy if only that one is a different title`
    : '';
  document.getElementById('compare-body').innerHTML = `
    ${group.length > 2 ? `<div class="text-muted small mb-2"><i class="bi bi-copy me-1"></i>${group.length} copies in this group</div>` : ''}
    ${wide
      ? `<div class="d-flex flex-nowrap gap-3 overflow-auto pb-2">${columns}</div>`
      : `<div class="row g-3">${columns}</div>`}
    <div class="text-muted small mt-3">
      <i class="bi bi-lightbulb me-1"></i>
      Use <strong>Play</strong> to check the videos, <strong>Copy NFO</strong> to transfer metadata,
      or <strong>Not a duplicate</strong> if these are all different titles${groupHint}.
      <i class="bi bi-trash text-danger"></i> deletes a copy you do not need.
    </div>`;
  const trCopies = group.filter(i => i.location === 'transcoded');
  const useBtn = document.getElementById('compare-use-transcoded-btn');
  const canUse = trCopies.length === 1 && group.some(i => i.location !== 'transcoded' && i.type === 'movie');
  useBtn.classList.toggle('d-none', !canUse);
  getModal('compareModal').show();

  // Fill in file sizes asynchronously
  for (const it of group) {
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

  const locBadge = i => locBadgeHtml(i.location);

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

  leaveModalFor('compareModal', 'nfoCopyModal');
  getModal('nfoCopyModal').show();
}

function selectNfoFields(on) {
  document.querySelectorAll('.nfo-field-check:not(:disabled)').forEach(cb => cb.checked = on);
}

function cancelNfoCopy() {
  getModal('nfoCopyModal').hide();
  nfoCopyCtx = null;
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
    closeModalDone('nfoCopyModal');
    nfoCopyCtx = null;
    showToast(`Copied ${fields.length} NFO field(s) → ${res.to}`, 'success');
    await fetchLibrary();
    if (compareIds.length >= 2) showCompareModal(compareIds[0]);
  } catch (e) {
    showToast('NFO copy failed: ' + e.message, 'danger');
  }
}

// Footer button: none of the copies are duplicates of each other (all pairs)
async function markNotDuplicate() {
  if (compareIds.length < 2) return;
  try {
    for (let a = 0; a < compareIds.length; a++)
      for (let b = a + 1; b < compareIds.length; b++)
        await api('POST', '/api/not-duplicate', { ids: [compareIds[a], compareIds[b]] });
    getModal('compareModal').hide();
    showToast('Marked as not duplicates — these will no longer be flagged against each other.', 'success');
    await fetchStatus();
    applyFilters();
  } catch (e) {
    showToast('Failed: ' + e.message, 'danger');
  }
}

// Per-copy button: only this copy is a different title (pairs vs all others)
async function markNotDuplicateItem(itemId) {
  const others = compareIds.filter(id => id !== itemId);
  if (!others.length) return;
  try {
    for (const o of others)
      await api('POST', '/api/not-duplicate', { ids: [itemId, o] });
    getModal('compareModal').hide();
    showToast('Copy marked as not a duplicate of the others.', 'success');
    await fetchStatus();
    applyFilters();
  } catch (e) {
    showToast('Failed: ' + e.message, 'danger');
  }
}

// ── Use transcoded video ──────────────────────────────────────────────────────
let useTranscodedId = null;

function openUseTranscoded() {
  const group = compareIds.map(id => allItems.find(i => i.id === id)).filter(Boolean);
  const tr = group.find(i => i.location === 'transcoded');
  const originals = group.filter(i => i.location !== 'transcoded' && i.type === 'movie');
  if (!tr || !originals.length) return;
  useTranscodedId = tr.id;

  // Preselect the original whose file names match the transcoded ones (transcoding keeps the stem)
  const stem = f => f.split(/[/\\]/).pop().replace(/\.[^.]+$/, '').toLowerCase();
  const trStems = tr.files.map(stem);
  const match = originals.find(o => trStems.every(s => o.files.some(f => stem(f) === s))) || originals[0];

  document.getElementById('ut-transcoded').innerHTML =
    tr.files.map(f => esc(f)).join('<br>') || esc(tr.path);
  document.getElementById('ut-original').innerHTML = originals.map(o =>
    `<option value="${esc(o.id)}" ${o === match ? 'selected' : ''}>[${esc(_locInfo(o.location).label)}] ${esc(o.path)}</option>`
  ).join('');
  document.getElementById('ut-dryrun').checked = true;
  leaveModalFor('compareModal', 'useTranscodedModal');
  getModal('useTranscodedModal').show();
}

async function confirmUseTranscoded() {
  if (!useTranscodedId) return;
  const dryRun = document.getElementById('ut-dryrun').checked;
  const originalId = document.getElementById('ut-original').value;
  if (!dryRun && !confirmInDialog(document.getElementById('ut-confirm-btn'), document.getElementById('ut-confirm-note'),
    'The original video will be replaced permanently. This cannot be undone.',
    '<i class="bi bi-exclamation-triangle-fill me-1"></i>Confirm — replace permanently')) return;
  try {
    const res = await api('POST', '/api/use-transcoded', {
      original_id: originalId, transcoded_id: useTranscodedId, dry_run: dryRun,
    });
    const lines = (res.changes || []).map(c => c.type === 'replace'
      ? `[replace]  ${c.from}\n       → ${c.to}`
      : `[${c.type}]  ${c.path}`);
    if (dryRun) {
      swapModal('useTranscodedModal', () => showResultModal({
        lines, closeText: 'Back to Use transcoded', onClose: () => getModal('useTranscodedModal').show(),
      }));
      return;
    }
    closeModalDone('useTranscodedModal');
    useTranscodedId = null;
    clearSelection();
    await fetchStatus();
    await fetchLibrary();
    showResultModal({
      title: 'Use transcoded video', good: true, lines,
      status: 'Original video replaced with the transcoded one. Run a full rescan so all caches are up to date '
        + '— Close keeps the in-memory update only.',
      action: { html: '<i class="bi bi-arrow-clockwise me-1"></i>Rescan now', onClick: triggerScan },
    });
  } catch (e) {
    showToast('Use transcoded failed: ' + _apiErr(e), 'danger');
  }
}
