/* bulk-table.js — bulk status editing, table view, and non-disruptive stage persistence. */
'use strict';

const BULK_VIEW_KEY = 'career_dashboard_view';
const selectedRoleKeys = new Set();
const workflowRecordIds = new Map();
const stageWriteQueues = new Map();
const stageMutationVersions = new Map();
let stageMutationCounter = 0;
let bulkUpdating = false;
let tableViewMode = localStorage.getItem(BULK_VIEW_KEY) === 'table' ? 'table' : 'kanban';
let boardRefreshQueued = false;
let boardMutationObserver = null;

/* Engine-side rejected roles are terminal non-target roles and belong in the
   existing Closed / inactive lane rather than Manual Review Needed. */
const baseRoleIsInactive = roleIsInactive;
roleIsInactive = function careerRoleIsInactive(role) {
  return normalizedStatus(role?.processing_status) === 'rejected' || baseRoleIsInactive(role);
};

function safeSelectorValue(value) {
  if (window.CSS?.escape) return CSS.escape(String(value));
  return String(value).replace(/(["\\])/g, '\\$1');
}

function visibleRolesForBulk() {
  return sortedRoles(filteredRoles());
}

function ensureWorkflowRecordIds() {
  for (const [key, record] of state.workflow.entries()) {
    if (record?.id) workflowRecordIds.set(key, record.id);
  }
}

function updateColumnCountsAndEmptyStates() {
  for (const column of $$('.kanban-column')) {
    const list = $('.column-list', column);
    if (!list) continue;
    const cards = $$('.role-card', list);
    const count = $('.column-header .column-title-group span', column);
    if (count) count.textContent = String(cards.length);
    for (const empty of $$('.column-empty', list)) empty.remove();
    if (!cards.length) {
      const empty = document.createElement('p');
      empty.className = 'column-empty';
      empty.textContent = column.dataset.stage === 'inactive' ? 'No closed jobs' : 'Drop a job here';
      list.append(empty);
    }
  }
}

function checkboxForRole(role) {
  const label = document.createElement('label');
  label.className = 'card-select-control';
  label.title = `Select ${role.role}`;
  const input = document.createElement('input');
  input.type = 'checkbox';
  input.className = 'role-bulk-checkbox';
  input.checked = selectedRoleKeys.has(role.key);
  input.setAttribute('aria-label', `Select ${role.company} — ${role.role}`);
  input.addEventListener('click', event => event.stopPropagation());
  input.addEventListener('change', event => {
    event.stopPropagation();
    setRoleSelected(role.key, input.checked);
  });
  label.addEventListener('click', event => event.stopPropagation());
  label.append(input);
  return label;
}

function decorateCards() {
  for (const card of $$('.role-card[data-role-key]')) {
    const key = card.dataset.roleKey;
    const role = state.roles.find(item => item.key === key);
    if (!role) continue;
    card.classList.add('bulk-selectable');
    card.classList.toggle('bulk-selected', selectedRoleKeys.has(key));
    let control = $('.card-select-control', card);
    if (!control) {
      control = checkboxForRole(role);
      card.prepend(control);
    } else {
      const input = $('input', control);
      if (input) input.checked = selectedRoleKeys.has(key);
    }
  }
}

function setRoleSelected(key, selected) {
  if (selected) selectedRoleKeys.add(key);
  else selectedRoleKeys.delete(key);
  const selector = safeSelectorValue(key);
  const card = $(`.role-card[data-role-key="${selector}"]`);
  card?.classList.toggle('bulk-selected', selected);
  const cardBox = card ? $('.role-bulk-checkbox', card) : null;
  if (cardBox) cardBox.checked = selected;
  const row = $(`.career-table tbody tr[data-role-key="${selector}"]`);
  row?.classList.toggle('bulk-selected', selected);
  const rowBox = row ? $('.table-role-checkbox', row) : null;
  if (rowBox) rowBox.checked = selected;
  updateBulkBar();
}

function clearBulkSelection() {
  selectedRoleKeys.clear();
  for (const input of $$('.role-bulk-checkbox, .table-role-checkbox')) input.checked = false;
  for (const node of $$('.bulk-selected')) node.classList.remove('bulk-selected');
  updateBulkBar();
}

function updateBulkBar() {
  const bar = $('#bulk-actions');
  if (!bar) return;
  const count = selectedRoleKeys.size;
  bar.hidden = count === 0;
  const label = $('#bulk-selected-count');
  if (label) label.textContent = `${count} selected`;
}

function refreshRoleCard(role) {
  const selector = safeSelectorValue(role.key);
  const oldCard = $(`.role-card[data-role-key="${selector}"]`);
  if (!oldCard) return;
  const board = $('#board');
  if (boardMutationObserver) boardMutationObserver.disconnect();
  try {
    const fragment = renderRole(role);
    const newCard = $('.role-card', fragment);
    oldCard.replaceWith(fragment);
    const targetList = $(`.kanban-column[data-stage="${stageFor(role)}"] .column-list`);
    if (targetList && newCard) targetList.append(newCard);
    decorateCards();
    updateColumnCountsAndEmptyStates();
  } finally {
    if (boardMutationObserver && board) {
      boardMutationObserver.observe(board, { childList: true, subtree: true });
    }
  }
}

function refreshTableRow(role) {
  const table = $('#career-table');
  if (!table) return;
  const selector = safeSelectorValue(role.key);
  const current = $(`tbody tr[data-role-key="${selector}"]`, table);
  if (!current) return;
  const replacement = buildTableRow(role);
  current.replaceWith(replacement);
}

function refreshRoleSurfaces(role, { overlay = true } = {}) {
  refreshRoleCard(role);
  refreshTableRow(role);
  renderSummary();
  if (overlay && state.overlayOpen && state.overlayKey === role.key) renderOverlayIfOpen();
}

async function persistStageChange(role, nextStage, priorStage, templateId, version, priorRecord) {
  const roleKey = role.key;
  const payload = {
    stage: nextStage,
    role_key: roleKey,
    route: role.route || '',
    company: role.company,
    role: role.role,
    template_id: templateId
  };
  const knownId = workflowRecordIds.get(roleKey) || priorRecord?.id || '';
  const record = knownId
    ? await patchRecord('workflow', knownId, payload)
    : await createRecord('workflow', payload, `${roleKey}-workflow`);
  const recordId = record.id || knownId;
  if (recordId) workflowRecordIds.set(roleKey, recordId);

  const latest = state.workflow.get(roleKey) || {};
  if (stageMutationVersions.get(roleKey) === version) {
    state.workflow.set(roleKey, {
      ...latest,
      id: recordId,
      ...(dataOf(record) || {}),
      role_key: roleKey,
      stage: nextStage,
      template_id: templateId,
      createdAt: record.createdAt || latest.createdAt,
      updatedAt: record.updatedAt || new Date().toISOString()
    });
    refreshRoleSurfaces(role);
  } else if (recordId && latest) {
    /* A newer optimistic status is already on screen. Preserve it while still
       retaining the durable Site Data record id returned by this earlier save. */
    state.workflow.set(roleKey, { ...latest, id: recordId });
  }
  await appendHistoryEvent(role, 'stage_change', priorStage, nextStage);
}

/* Replace the board's full-rerender status mutation with an optimistic,
   per-role update. Other open selects remain mounted while this network write
   completes. Per-role writes are serialized so rapid edits cannot create two
   workflow records or let an older response overwrite a newer choice. */
moveRole = async function moveRoleInBackground(role, nextStage, requireConfirmation = false) {
  const roleKey = role.key;
  const priorRecord = state.workflow.get(roleKey);
  const priorStage = stageFor(role);
  if (priorStage === nextStage) return true;

  if (requireConfirmation) {
    try {
      if (!await confirmApplicationSubmitted(role, 'kanban_stage_change')) return false;
    } catch (error) {
      showToast(`Submission was not marked complete: ${error.message}`, true);
      return false;
    }
  }

  ensureWorkflowRecordIds();
  const templateId = priorRecord?.template_id || selectedTemplateFor(role);
  const version = ++stageMutationCounter;
  stageMutationVersions.set(roleKey, version);
  state.workflow.set(roleKey, {
    ...priorRecord,
    role_key: roleKey,
    stage: nextStage,
    template_id: templateId,
    updatedAt: new Date().toISOString()
  });
  refreshRoleSurfaces(role);

  const previousWrite = stageWriteQueues.get(roleKey) || Promise.resolve();
  const write = previousWrite
    .catch(() => undefined)
    .then(() => persistStageChange(role, nextStage, priorStage, templateId, version, priorRecord));
  stageWriteQueues.set(roleKey, write);

  try {
    await write;
    if (!bulkUpdating && stageMutationVersions.get(roleKey) === version) {
      showToast(`Moved to ${STAGES.find(stage => stage.id === nextStage)?.label || nextStage}.`);
    }
    return true;
  } catch (error) {
    if (stageMutationVersions.get(roleKey) === version) {
      if (priorRecord) state.workflow.set(roleKey, priorRecord);
      else state.workflow.delete(roleKey);
      refreshRoleSurfaces(role);
      showToast(error.message, true);
    }
    return false;
  }
};

function buildStageSelect(role, className = '') {
  const select = document.createElement('select');
  select.className = `card-stage-select ${className}`.trim();
  select.setAttribute('aria-label', `Change status for ${role.company} — ${role.role}`);
  const current = stageFor(role);
  for (const stage of STAGES) {
    const option = document.createElement('option');
    option.value = stage.id;
    option.textContent = stage.label;
    option.selected = stage.id === current;
    select.append(option);
  }
  select.addEventListener('change', async () => {
    const target = select.value;
    const ok = await moveRole(role, target, target === 'applied');
    if (!ok) select.value = stageFor(role);
  });
  return select;
}

function buildTableRow(role) {
  const row = document.createElement('tr');
  row.dataset.roleKey = role.key;
  row.classList.toggle('bulk-selected', selectedRoleKeys.has(role.key));

  const checkCell = document.createElement('td');
  checkCell.className = 'table-check';
  const checkbox = document.createElement('input');
  checkbox.type = 'checkbox';
  checkbox.className = 'table-role-checkbox';
  checkbox.checked = selectedRoleKeys.has(role.key);
  checkbox.setAttribute('aria-label', `Select ${role.company} — ${role.role}`);
  checkbox.addEventListener('change', () => setRoleSelected(role.key, checkbox.checked));
  checkCell.append(checkbox);

  const score = document.createElement('td');
  score.className = 'table-score';
  score.textContent = role.score == null ? '—' : `${role.score}/100`;

  const roleCell = document.createElement('td');
  roleCell.className = 'table-role';
  const title = document.createElement('span');
  title.className = 'table-role-title';
  title.textContent = role.role;
  const decision = document.createElement('span');
  decision.className = 'table-role-meta';
  decision.textContent = humanDecision(normalizeDecision(role.decision));
  roleCell.append(title, decision);

  const company = document.createElement('td');
  company.className = 'table-company';
  company.textContent = role.company;

  const location = document.createElement('td');
  location.className = 'table-location';
  location.textContent = role.location || '—';

  const posted = document.createElement('td');
  posted.className = 'table-date';
  posted.textContent = parseDate(role.posting_date) ? formatDay(role.posting_date) : 'Unknown';

  const found = document.createElement('td');
  found.className = 'table-date';
  found.textContent = parseDate(roleFoundTime(role)) ? formatDay(roleFoundTime(role)) : 'Unknown';

  const status = document.createElement('td');
  status.className = 'table-status';
  status.append(buildStageSelect(role, 'table-stage-select'));

  const actions = document.createElement('td');
  actions.className = 'table-actions';
  const open = document.createElement('button');
  open.type = 'button';
  open.className = 'card-button';
  open.textContent = 'Open';
  open.addEventListener('click', () => openOverlay(role.key));
  actions.append(open);

  row.append(checkCell, score, roleCell, company, location, posted, found, status, actions);
  return row;
}

function renderBulkTable() {
  const host = $('#career-table-view');
  if (!host) return;
  const roles = visibleRolesForBulk();
  const shell = document.createElement('div');
  shell.className = 'career-table-shell';
  const table = document.createElement('table');
  table.id = 'career-table';
  table.className = 'career-table';
  table.innerHTML = `
    <thead><tr>
      <th class="table-check"><input id="table-select-visible" type="checkbox" aria-label="Select all visible jobs"></th>
      <th class="table-score">Score</th><th>Role</th><th>Company</th><th>Location</th>
      <th>Posted</th><th>Found</th><th>Status</th><th>Action</th>
    </tr></thead><tbody></tbody>`;
  const body = $('tbody', table);
  for (const role of roles) body.append(buildTableRow(role));
  shell.append(table);
  host.replaceChildren(shell);

  const selectVisible = $('#table-select-visible', table);
  const visibleKeys = roles.map(role => role.key);
  const selectedVisible = visibleKeys.filter(key => selectedRoleKeys.has(key)).length;
  selectVisible.checked = Boolean(visibleKeys.length) && selectedVisible === visibleKeys.length;
  selectVisible.indeterminate = selectedVisible > 0 && selectedVisible < visibleKeys.length;
  selectVisible.addEventListener('change', () => {
    for (const key of visibleKeys) setRoleSelected(key, selectVisible.checked);
    renderBulkTable();
  });
}

function setViewMode(mode) {
  tableViewMode = mode === 'table' ? 'table' : 'kanban';
  localStorage.setItem(BULK_VIEW_KEY, tableViewMode);
  const board = $('#board');
  const table = $('#career-table-view');
  if (board) board.hidden = tableViewMode === 'table';
  if (table) table.hidden = tableViewMode !== 'table';
  for (const button of $$('[data-career-view]')) {
    const active = button.dataset.careerView === tableViewMode;
    button.classList.toggle('is-active', active);
    button.setAttribute('aria-pressed', active ? 'true' : 'false');
  }
  if (tableViewMode === 'table') renderBulkTable();
}

async function applyBulkStage() {
  const select = $('#bulk-stage-select');
  const targetStage = select?.value || '';
  if (!targetStage) return;
  if (targetStage === 'applied') {
    showToast('Applied / sent requires individual submission confirmation and cannot be bulk-marked.', true);
    return;
  }
  const roles = [...selectedRoleKeys]
    .map(key => state.roles.find(role => role.key === key))
    .filter(Boolean)
    .filter(role => stageFor(role) !== targetStage);
  if (!roles.length) {
    showToast('Selected jobs already have that status.');
    return;
  }

  bulkUpdating = true;
  let succeeded = 0;
  let failed = 0;
  try {
    for (let offset = 0; offset < roles.length; offset += 6) {
      const chunk = roles.slice(offset, offset + 6);
      const results = await Promise.all(chunk.map(role => moveRole(role, targetStage, false)));
      succeeded += results.filter(Boolean).length;
      failed += results.filter(result => !result).length;
    }
  } finally {
    bulkUpdating = false;
  }
  renderBulkTable();
  updateBulkBar();
  showToast(`${succeeded} job${succeeded === 1 ? '' : 's'} updated${failed ? ` · ${failed} failed` : ''}.`, Boolean(failed));
}

function ensureBulkUi() {
  if ($('#bulk-actions')) return;
  ensureWorkflowRecordIds();

  const displayMenu = $('.tool-menu summary[aria-label="Display options"]')?.closest('.tool-menu');
  const popover = displayMenu ? $('.menu-popover', displayMenu) : null;
  if (popover && !$('.view-switch-group', popover)) {
    const group = document.createElement('div');
    group.className = 'view-switch-group';
    const heading = document.createElement('span');
    heading.className = 'menu-heading';
    heading.textContent = 'View';
    const list = document.createElement('div');
    list.className = 'direct-menu-list';
    for (const [id, label] of [['kanban', 'Kanban'], ['table', 'Table']]) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'direct-menu-item';
      button.dataset.careerView = id;
      button.textContent = label;
      button.addEventListener('click', () => {
        setViewMode(id);
        if (displayMenu) displayMenu.open = false;
      });
      list.append(button);
    }
    group.append(heading, list);
    popover.append(group);
  }

  const toolbar = $('.toolbar');
  const bulk = document.createElement('div');
  bulk.id = 'bulk-actions';
  bulk.className = 'bulk-actions';
  bulk.hidden = true;
  const count = document.createElement('strong');
  count.id = 'bulk-selected-count';
  count.textContent = '0 selected';
  const selectVisible = document.createElement('button');
  selectVisible.type = 'button';
  selectVisible.className = 'card-button';
  selectVisible.textContent = 'Select visible';
  selectVisible.addEventListener('click', () => {
    for (const role of visibleRolesForBulk()) setRoleSelected(role.key, true);
    renderBulkTable();
  });
  const status = document.createElement('select');
  status.id = 'bulk-stage-select';
  status.setAttribute('aria-label', 'Bulk status');
  const placeholder = document.createElement('option');
  placeholder.value = '';
  placeholder.textContent = 'Change status…';
  status.append(placeholder);
  for (const stage of STAGES) {
    const option = document.createElement('option');
    option.value = stage.id;
    option.textContent = stage.id === 'applied' ? `${stage.label} — confirm individually` : stage.label;
    option.disabled = stage.id === 'applied';
    status.append(option);
  }
  const apply = document.createElement('button');
  apply.type = 'button';
  apply.className = 'card-button primary';
  apply.textContent = 'Apply to selected';
  apply.addEventListener('click', applyBulkStage);
  const clear = document.createElement('button');
  clear.type = 'button';
  clear.className = 'card-button';
  clear.textContent = 'Clear selection';
  clear.addEventListener('click', clearBulkSelection);
  bulk.append(count, selectVisible, status, apply, clear);
  toolbar?.insertAdjacentElement('afterend', bulk);

  const board = $('#board');
  const table = document.createElement('section');
  table.id = 'career-table-view';
  table.className = 'career-table-view';
  table.setAttribute('aria-label', 'Career application table view');
  table.hidden = true;
  board?.insertAdjacentElement('afterend', table);

  setViewMode(tableViewMode);
  updateBulkBar();
}

function ensureOverlayStageSelect() {
  if (!state.overlayOpen || !state.overlayKey) return;
  const role = state.roles.find(item => item.key === state.overlayKey);
  const strip = $('.overlay-meta-strip');
  if (!role || !strip) return;

  const hiddenStageGroup = $('#ov-stage-options')?.closest('.menu-group');
  hiddenStageGroup?.classList.add('bulk-hidden-stage-menu');

  let label = $('.detail-stage-inline', strip);
  if (!label) {
    label = document.createElement('label');
    label.className = 'detail-stage-inline';
    const text = document.createElement('strong');
    text.textContent = 'Status';
    label.append(text);
    const toolMenu = $('.detail-tool-menu', strip);
    strip.insertBefore(label, toolMenu || null);
  }
  const old = $('select', label);
  const focused = old === document.activeElement;
  if (old && old.value === stageFor(role)) return;
  const select = buildStageSelect(role, 'detail-stage-select');
  if (old) old.replaceWith(select); else label.append(select);
  if (focused) select.focus();
}

function scheduleBoardDecoration() {
  if (boardRefreshQueued) return;
  boardRefreshQueued = true;
  queueMicrotask(() => {
    boardRefreshQueued = false;
    decorateCards();
    if (tableViewMode === 'table') renderBulkTable();
  });
}

function initializeBulkTable() {
  ensureBulkUi();
  const board = $('#board');
  if (board) {
    boardMutationObserver = new MutationObserver(scheduleBoardDecoration);
    boardMutationObserver.observe(board, { childList: true, subtree: true });
  }
  const overlay = $('#overlay-content');
  if (overlay) new MutationObserver(() => queueMicrotask(ensureOverlayStageSelect)).observe(overlay, { childList: true, subtree: true });
  scheduleBoardDecoration();
  ensureOverlayStageSelect();
}

initializeBulkTable();
