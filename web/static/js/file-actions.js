'use strict';
// File actions on selected items: Delete, Move and Rename (dry run by default).

// ── Init ──────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
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

  // While a move runs the dialog cannot be closed (Esc, backdrop, ×, Cancel)
  document.getElementById('moveModal').addEventListener('hide.bs.modal', e => {
    if (moveInProgress) e.preventDefault();
  });

  // A running move must not be interrupted by leaving the page
  window.addEventListener('beforeunload', e => {
    if (moveInProgress) { e.preventDefault(); e.returnValue = ''; }
  });

  // Move dialog: common-folder choice and Unselect buttons
  const groupsEl = document.getElementById('move-groups');
  groupsEl.addEventListener('change', e => {
    if (e.target.name !== 'move-keep') return;
    moveKeepGroups = e.target.value === '1';
    _renderMoveGroups();
  });
  groupsEl.addEventListener('click', e => {
    const btn = e.target.closest('[data-unselect-group]');
    if (btn) _unselectMoveGroup(btn.dataset.unselectGroup);
  });
});

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
        <div class="text-truncate">${esc(_isLooseFile(i) ? i.raw_name || i.title : i.folder)}</div>
        <div class="small text-muted text-truncate font-monospace" title="${esc(_isLooseFile(i) ? i.id : i.path)}">
          ${_isLooseFile(i) ? '<i class="bi bi-file-earmark-play me-1"></i>' : '<i class="bi bi-folder me-1"></i>'}${esc(_isLooseFile(i) ? i.id : i.path)}</div>
        ${i.type === 'movie' && (i.files || []).length > 1 ? `
        <div class="small text-danger-emphasis"><i class="bi bi-exclamation-triangle-fill me-1"></i>
          This movie folder contains ${i.files.length} video files — check it is not a collection of several movies</div>` : ''}
      </div>
      ${nfoIconHtml(i)}${viewBtn}
      ${locBadgeHtml(i.location)}
      <button type="button" class="btn btn-sm btn-play border-0 py-0 px-1 flex-shrink-0"
              ${_playAttrs(i, 'Play — check which copy this is before deleting')} data-play-item="${esc(i.id)}">
        <i class="bi bi-play-circle"></i>
      </button>
    </div>`;
}

// True when the Delete dialog was opened from the selection (a real delete then clears it)
let deleteFromSelection = true;

/** Open the Delete dialog for `ids` (default: the selected items). */
function openDeleteDialog(ids = null) {
  deleteFromSelection = !ids;
  ids = ids || [...selectedIds];
  if (ids.length === 0) return;
  const items = ids.map(id => allItems.find(i => i.id === id)).filter(Boolean);

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
    if (dryRun) {
      // Nothing can be deleted: the toast explains it and the dialog stays open
      if (!results.some(r => r.ok)) return handleActionResults(results, true, 'delete', 'deleted');
      swapModal('deleteModal', () => handleActionResults(results, true, 'delete', 'deleted', {
        closeText: 'Back to Delete', onClose: () => getModal('deleteModal').show(),
      }));
      return;
    }
    closeModalDone('deleteModal');
    handleActionResults(results, false, 'delete', 'deleted');
    // Drop deleted items now so a copy left alone in its group stops showing as a duplicate
    const gone = new Set(results.filter(r => r.ok).map(r => r.id));
    allItems = allItems.filter(i => !gone.has(i.id));
    if (deleteFromSelection) clearSelection();
    await fetchStatus();
    applyFilters();
    triggerScan();
  } catch (e) {
    showToast('Delete failed: ' + e.message, 'danger');
  }
}

// ── Move ──────────────────────────────────────────────────────────────────────
/**
 * Populate the Move destination dropdown with the configured locations and downloads,
 * preselecting the last used destination if it is still configured.
 */
function fillMoveTargets(cfg) {
  const all = [...(cfg.locations || []), ...(cfg.downloads || [])].filter(p => p && pathEnabled(p));
  const sel = document.getElementById('move-target-select');
  sel.innerHTML = '<option value="">— select a configured path —</option>'
    + all.map(p => `<option value="${esc(p)}">${esc(p)}</option>`).join('');
  sel.value = all.includes(cfg.last_move_target) ? cfg.last_move_target : '';
}

function openMoveDialog() {
  if (selectedIds.size === 0) return;
  fillMoveTargets(configCache || {});
  _renderMovePreview();
  document.getElementById('move-dryrun').checked = true;
  document.getElementById('move-target-custom').value = '';
  moveGroups = [];
  moveKeepGroups = true;
  _renderMoveGroups();
  setMoveBusy(false);
  getModal('moveModal').show();
  _loadMoveGroups();
}

function _renderMovePreview() {
  const items = [...selectedIds].map(id => allItems.find(i => i.id === id)).filter(Boolean);
  document.getElementById('move-items-preview').innerHTML =
    `Moving <strong>${items.length}</strong> item(s): `
    + items.map(i => `<span class="badge bg-secondary ms-1">${esc(i.title)}</span>`).join('');
}

// Selected items in a common folder below their location (from /api/move/groups), and the user's answer
let moveGroups = [];
let moveKeepGroups = true;

/** Ask the server which selected items share a common folder, then show the question. */
async function _loadMoveGroups() {
  // No Move before the question is known — the items would silently lose their folder
  const btn = document.getElementById('move-confirm-btn');
  btn.disabled = true;
  try {
    moveGroups = await api('POST', '/api/move/groups', { item_ids: [...selectedIds] });
  } catch (e) {
    moveGroups = [];
    showToast('Could not check for common folders: ' + _apiErr(e), 'warning');
  } finally {
    btn.disabled = moveInProgress;
  }
  _renderMoveGroups();
}

/** The common-folder question: move them together (keep the folder) or not — then offer to unselect them. */
function _renderMoveGroups() {
  const el = document.getElementById('move-groups');
  el.classList.toggle('d-none', !moveGroups.length);
  if (!moveGroups.length) { el.innerHTML = ''; return; }
  const n = moveGroups.reduce((s, g) => s + g.ids.length, 0);
  el.innerHTML = `
    <div class="alert alert-warning py-2 mb-0">
      <div class="mb-1"><i class="bi bi-folder2-open me-1"></i>
        <strong>${n} selected item(s) are in a common folder:</strong></div>
      ${moveGroups.map((g, i) => `
        <div class="d-flex align-items-center gap-2 py-1">
          <i class="bi bi-folder"></i>
          <span class="font-monospace text-truncate" title="${esc(g.folder)}">${esc(g.group)}</span>
          <span class="text-nowrap">${g.ids.length} of ${g.total} item(s) selected</span>
          <button type="button" class="btn btn-sm btn-outline-dark py-0 ms-auto text-nowrap" data-unselect-group="${i}">
            Unselect them</button>
        </div>`).join('')}
      <div class="mt-2 fw-semibold">Move them together?</div>
      <div class="form-check">
        <input class="form-check-input" type="radio" name="move-keep" id="move-keep-yes" value="1" ${moveKeepGroups ? 'checked' : ''}>
        <label class="form-check-label" for="move-keep-yes">Yes — keep the folder: they go into the same folder in the
          destination (created if needed); a folder left empty here is removed</label>
      </div>
      <div class="form-check">
        <input class="form-check-input" type="radio" name="move-keep" id="move-keep-no" value="0" ${moveKeepGroups ? '' : 'checked'}>
        <label class="form-check-label" for="move-keep-no">No — move each one straight into the destination</label>
      </div>
      ${moveKeepGroups ? '' : `
      <div class="d-flex align-items-center gap-2 mt-2">
        <span>They will lose their common folder. Leave them where they are instead?</span>
        <button type="button" class="btn btn-sm btn-dark py-0 ms-auto text-nowrap" data-unselect-group="all">
          Unselect all ${n}</button>
      </div>`}
    </div>`;
  if (moveInProgress) el.querySelectorAll('input, button').forEach(c => { c.disabled = true; });
}

/** Take the items of one common folder (index, or 'all') out of the selection. */
function _unselectMoveGroup(which) {
  const gone = which === 'all' ? moveGroups : [moveGroups[+which]];
  gone.forEach(g => g.ids.forEach(id => selectedIds.delete(id)));
  moveGroups = moveGroups.filter(g => !gone.includes(g));
  _syncAllRowsSelected();
  updateSelectionUI();
  if (!selectedIds.size) {
    getModal('moveModal').hide();
    showToast('Nothing left to move — all items were unselected.', 'info');
    return;
  }
  _renderMovePreview();
  _renderMoveGroups();
}

// True while real moves are running — blocks closing the Move dialog and the page.
let moveInProgress = false;

/** Lock/unlock the Move dialog and show/hide its progress area. */
function setMoveBusy(busy) {
  moveInProgress = busy;
  document.getElementById('move-progress').classList.toggle('d-none', !busy);
  for (const id of ['move-confirm-btn', 'move-cancel-btn', 'move-dryrun',
                    'move-target-select', 'move-target-custom']) {
    document.getElementById(id).disabled = busy;
  }
  document.querySelector('#moveModal .btn-close').disabled = busy;
  document.querySelectorAll('#move-groups input, #move-groups button').forEach(c => { c.disabled = busy; });
  const btn = document.getElementById('move-confirm-btn');
  btn.innerHTML = busy
    ? '<span class="spinner-border spinner-border-sm me-1"></span>Moving…'
    : '<i class="bi bi-folder-symlink me-1"></i>Move';
}

/** Update the progress bar: `done` of `total` finished, `current` is being moved now. */
function setMoveProgress(done, total, current) {
  const bar = document.getElementById('move-progress-bar');
  bar.style.width = `${total ? Math.round(done / total * 100) : 0}%`;
  bar.textContent = `${done} / ${total}`;
  document.getElementById('move-progress-text').textContent = current ? `Moving: ${current}` : '';
}

/** Hide the Move dialog, then run `fn` once it is fully closed (avoids stacked modals). */
function afterMoveModalHidden(fn) {
  document.getElementById('moveModal').addEventListener('hidden.bs.modal', fn, { once: true });
  getModal('moveModal').hide();
}

/** Format move results as lines for the result modal (failures first). */
function _moveResultLines(results) {
  const titleOf = id => (allItems.find(i => i.id === id) || {}).title || id;
  const fail = results.filter(r => !r.ok).map(r => `✗ ${titleOf(r.id)}\n  ${r.error}`);
  const ok   = results.filter(r => r.ok).map(r =>
    `✓ ${r.from}\n  → ${r.to}${(r.also || []).map(a => `\n  + ${a}`).join('')}`
    + `${r.cross_drive ? `\n  (other drive: copies ${fmtSize(r.size)})` : ''}${r.warning ? `\n  ⚠ ${r.warning}` : ''}`
    + (r.removed_folders || []).map(f => `\n  − empty folder removed: ${f}`).join(''));
  return [...fail, ...ok];
}

/** True when items in a common folder should keep it in the destination. */
const _moveKeep = () => moveGroups.length > 0 && moveKeepGroups;

async function confirmMove() {
  const dryRun = document.getElementById('move-dryrun').checked;
  const custom = document.getElementById('move-target-custom').value.trim();
  const sel    = document.getElementById('move-target-select').value;
  const target = custom || sel;
  if (!target) {
    showToast('Please select or enter a destination path.', 'warning');
    return;
  }
  const ids = [...selectedIds];
  if (dryRun) return dryRunMove(ids, target);

  // Real move: one request per item so progress can be shown
  setMoveBusy(true);
  const results = [];
  let aborted = '';
  for (let n = 0; n < ids.length; n++) {
    const item = allItems.find(i => i.id === ids[n]);
    setMoveProgress(n, ids.length, item ? item.title : ids[n]);
    try {
      const res = await api('POST', '/api/move', {
        item_ids: [ids[n]], target_base: target, dry_run: false, keep_groups: _moveKeep(),
      });
      results.push(...res);
    } catch (e) {
      // Destination problem (or lost connection) — stop, the remaining items would fail too
      aborted = _apiErr(e);
      break;
    }
  }
  setMoveProgress(results.length, ids.length, '');
  setMoveBusy(false);

  const ok   = results.filter(r => r.ok).length;
  const fail = results.filter(r => !r.ok).length;
  if (ok) {
    // Mirror the server: remember a configured destination for the next Move
    if ([...(configCache.locations || []), ...(configCache.downloads || [])].filter(pathEnabled).includes(target)) {
      configCache.last_move_target = target;
    }
    // Moved items no longer exist at their listed path — drop them now so they
    // cannot be selected/moved again; the rescan adds them back at their new location.
    const moved = new Set(results.filter(r => r.ok).map(r => r.id));
    allItems = allItems.filter(i => !moved.has(i.id));
    selectedIds.clear();
    applyFilters();
    updateSelectionUI();
    triggerScan();
  }
  const warned = results.filter(r => r.warning).length;
  if (!fail && !aborted && !warned) {
    afterMoveModalHidden(() => showToast(`moved: ${ok} item(s)`, 'success'));
    return;
  }
  let status = `Moved ${ok} of ${ids.length} item(s). ${fail} failed.`;
  if (warned) status += `<br>${warned} item(s) moved, but some related files stayed behind.`;
  if (aborted) status += `<br>Stopped: ${esc(aborted)} — ${ids.length - results.length} item(s) not attempted.`;
  afterMoveModalHidden(() => showResultModal({
    title: 'Move Result', good: false, status, lines: _moveResultLines(results),
  }));
}

/** Run the move as a dry run, show the result, then return to the Move dialog. */
async function dryRunMove(ids, target) {
  let results = [], error = '';
  const btn = document.getElementById('move-confirm-btn');
  btn.disabled = true;
  try {
    results = await api('POST', '/api/move', { item_ids: ids, target_base: target, dry_run: true, keep_groups: _moveKeep() });
  } catch (e) {
    error = _apiErr(e);
  } finally {
    btn.disabled = false;
  }
  const fail = results.filter(r => !r.ok).length;
  const good = !error && !fail && results.length > 0;
  const status = error
    ? `Nothing can be moved to <strong>${esc(target)}</strong>:<br>${esc(error)}`
    : good
      ? `All ${results.length} item(s) can be moved to <strong>${esc(target)}</strong> (destination is writable).`
      : `${fail} of ${results.length} item(s) cannot be moved — they will be skipped.`;
  const backToMove = () => getModal('moveModal').show();
  afterMoveModalHidden(() => showResultModal({
    good, status, lines: error ? [] : _moveResultLines(results),
    closeText: 'Back to Move', onClose: backToMove,
  }));
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
  leaveModalFor('detailModal', 'renameModal');

  const item   = currentDetail;
  const isFile = _isLooseFile(item);

  // Loose files can only rename the file; series only the folder (episode names carry SxxEyy)
  document.getElementById('rename-target-group').classList.toggle('d-none', isFile || item.type === 'series');

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
    if (dryRun) {
      const lines = (result.changes || []).map(c =>
        `[${c.type}]  ${c.from}\n       → ${c.to}`);
      // Back reopens the Rename dialog with its inputs kept, so dry run can be turned off
      swapModal('renameModal', () => showResultModal({
        lines, closeText: 'Back to Rename', onClose: () => getModal('renameModal').show(),
      }));
    } else {
      closeModalDone('renameModal');
      showToast(`Renamed successfully`, 'success');
      triggerScan();
    }
  } catch (e) {
    showToast('Rename failed: ' + _apiErr(e), 'danger');
  }
}
