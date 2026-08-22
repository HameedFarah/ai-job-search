/* app.js — Career Engine dashboard board */
'use strict';

const GLOBAL_ROLE_KEY = '__career_engine__';

const state = {
  data: null,
  roles: [],
  workflow: new Map(),
  comments: [],
  history: [],
  aiRequests: [],
  preferences: new Map(),
  lastScanAt: null,
  dragging: false,
  templatesData: null,
  detailShards: new Map(),
  overlayOpen: false,
  overlayKey: '',
  overlayReturnKey: '',
  overlayHydrationPromise: null,
  aiPollTimer: null,
  operationPollTimer: null,
  commentsFull: new Set(),
  inactiveCollapsed: localStorage.getItem('career_inactive_collapsed') !== 'false',
  batchStageOverrides: new Map(),
  batchProgress: null
};

function updateScanClock() {
  if (!state.lastScanAt) return;
  $('#scan-relative').textContent = `Last scan ${relativeTime(state.lastScanAt)}`;
  $('#scan-absolute').textContent = formatDate(state.lastScanAt);
  for (const node of $$('[data-age-time]')) {
    node.textContent = relativeTime(node.dataset.ageTime);
  }
}

async function loadComments() {
  const records = await loadCollection('comments');
  records.sort((a, b) => String(a.createdAt).localeCompare(String(b.createdAt)));
  state.comments = records.map(record => ({ id: record.id, ...dataOf(record), createdAt: record.createdAt, updatedAt: record.updatedAt }));
}

async function loadWorkflow() {
  const records = await loadCollection('workflow');
  records.sort((a, b) => String(a.updatedAt).localeCompare(String(b.updatedAt)));
  for (const record of records) {
    const data = dataOf(record);
    const stage = data.stage === 'approved' ? 'ready_review' : data.stage;
    if (data.role_key && (STAGES.some(item => item.id === stage) || stage === 'processing')) {
      state.workflow.set(data.role_key, { id: record.id, ...data, stage, createdAt: record.createdAt, updatedAt: record.updatedAt });
    }
  }
}

async function loadHistory() {
  const records = await loadCollection('history', 500);
  records.sort((a, b) => String(a.createdAt).localeCompare(String(b.createdAt)));
  state.history = records.map(record => ({
    id: record.id,
    ...dataOf(record),
    createdAt: record.createdAt,
    updatedAt: record.updatedAt
  }));
}

async function loadPreferences() {
  const records = await loadCollection('preferences');
  for (const record of records) {
    const data = dataOf(record);
    if (data.key) state.preferences.set(data.key, { record, value: data.value });
  }
}

function decisionRank(role) {
  return ({ pursue: 0, selective: 1, stretch: 2, do_not_pursue: 3 })[normalizeDecision(role.decision)] ?? 4;
}

function sortedRoles(roles) {
  const order = $('#sort-order')?.value || 'recommended';
  return [...roles].sort((a, b) => {
    if (order === 'score_desc') return (b.score || 0) - (a.score || 0);
    if (order === 'score_asc') return (a.score || 0) - (b.score || 0);
    if (order === 'posted_newest') return postedRank(b, false) - postedRank(a, false) || new Date(roleFoundTime(b, 0)) - new Date(roleFoundTime(a, 0));
    if (order === 'posted_oldest') return postedRank(a, true) - postedRank(b, true) || new Date(roleFoundTime(a, 0)) - new Date(roleFoundTime(b, 0));
    if (order === 'activity_desc') return new Date(activityTime(b)) - new Date(activityTime(a));
    if (order === 'found_desc') return new Date(roleFoundTime(b, 0)) - new Date(roleFoundTime(a, 0));
    if (order === 'company_asc') return String(a.company).localeCompare(String(b.company));
    return decisionRank(a) - decisionRank(b) || (b.score || 0) - (a.score || 0);
  });
}

function filteredRoles() {
  const query = ($('#search-filter')?.value || '').trim().toLowerCase();
  const decision = $('#decision-filter')?.value || 'all';
  return state.roles.filter(role => {
    const haystack = `${role.role} ${role.company}`.toLowerCase();
    const matchesQuery = !query || haystack.includes(query);
    const matchesDecision = decision === 'all' || normalizeDecision(role.decision) === decision;
    return matchesQuery && matchesDecision;
  });
}

function makeStageMenu(role) {
  const wrapper = document.createElement('label');
  wrapper.className = 'card-stage-select-wrap';
  const caption = document.createElement('span');
  caption.className = 'card-stage-select-label';
  caption.textContent = 'Status';
  const select = document.createElement('select');
  select.className = 'card-stage-select';
  select.setAttribute('aria-label', `Change status for ${role.role}`);
  const current = stageFor(role);
  for (const stage of STAGES) {
    const option = document.createElement('option');
    option.value = stage.id;
    option.textContent = stage.label;
    option.selected = stage.id === current;
    select.append(option);
  }
  select.addEventListener('click', event => event.stopPropagation());
  select.addEventListener('change', async event => {
    event.stopPropagation();
    const nextStage = select.value;
    if (nextStage === current) return;
    await moveRole(role, nextStage, nextStage === 'applied');
    if (stageFor(role) === current) select.value = current;
  });
  wrapper.append(caption, select);
  return wrapper;
}

function makeCompactConfirmButton(label, handler) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'card-button success confirm-submission';
  button.setAttribute('aria-label', label);
  button.title = label;
  button.innerHTML = '<svg class="icon-svg" viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12 4 4L19 6"></path></svg>';
  button.addEventListener('click', handler);
  return button;
}

function buildStageActions(role, target) {
  target.replaceChildren();
  const stageId = stageFor(role);
  const stage = STAGES.find(item => item.id === stageId);
  const batchStage = state.batchStageOverrides.get(role.key);
  if (batchStage === 'processing') {
    const status = document.createElement('button');
    status.type = 'button';
    status.className = 'card-button primary batch-processing-button';
    status.disabled = true;
    status.textContent = 'Processing CV…';
    target.append(status);
    return;
  }
  target.append(makeStageMenu(role));
  if (!stage) return;
  if (stage.id === 'ready_review') {
    if (role.route === 'email') {
      if (hasGenuineRecipient(role)) target.append(makeButton('Open Gmail', () => openGmailCompose(role), 'email'));
      if (role.application_url) target.append(makeLink('Recruitment route', role.application_url));
    } else if (role.application_url) {
      target.append(makeButton('Open application portal', () => openTrackedPortal(role, 'kanban_card'), 'primary'));
    }
    target.append(makeCompactConfirmButton(
      role.route === 'email' ? 'Confirm sent' : 'Confirm submitted',
      () => moveRole(role, 'applied', true)
    ));
    return;
  }
  if (stage.id === 'applied') {
    const status = document.createElement('span');
    status.className = 'card-applied-status';
    status.textContent = 'Applied';
    status.title = 'Use the always-visible Status dropdown to reopen or change this application state.';
    target.append(status);
    return;
  }
  target.append(makeButton(stage.nextLabel, () => moveRole(role, stage.next), 'primary'));
}

async function appendHistoryEvent(role, event, fromStage, toStage, note = '') {
  try {
    await createRecord('history', {
      role_key: role.key,
      event,
      from_stage: fromStage || '',
      to_stage: toStage || '',
      note
    }, `history-${role.key}-${event}-${Date.now()}`);
  } catch (error) {
    console.warn('history event failed', error);
  }
}

async function undoAppliedMark(role, priorStage, confirmation) {
  const targetStage = STAGES.some(stage => stage.id === priorStage) && priorStage !== 'applied' ? priorStage : 'ready_review';
  const retractedAt = new Date().toISOString();
  const saved = await createRecord('history', {
    role_key: role.key,
    event: 'application_submission_retracted',
    from_stage: 'applied',
    to_stage: targetStage,
    actor: 'owner',
    ui_source: 'applied_toast_undo',
    evidence_type: 'explicit_owner_correction',
    note: JSON.stringify({
      reason: 'Owner used Undo after an accidental applied mark.',
      retracted_event_id: confirmation?.record?.id || '',
      retracted_at: retractedAt
    })
  }, `submission-retract-${role.key}-${Date.now()}`);
  state.history.push({
    id: saved.id,
    ...(dataOf(saved) || {}),
    createdAt: saved.createdAt || retractedAt,
    updatedAt: saved.updatedAt || retractedAt
  });

  const current = state.workflow.get(role.key);
  const templateId = current?.template_id || selectedTemplateFor(role);
  try {
    const record = current?.id
      ? await patchRecord('workflow', current.id, { stage: targetStage, role_key: role.key, route: role.route || '', company: role.company, role: role.role, template_id: templateId })
      : await createRecord('workflow', {
          role_key: role.key,
          stage: targetStage,
          route: role.route || '',
          company: role.company,
          role: role.role,
          template_id: templateId
        }, `${role.key}-${targetStage}-${Date.now()}`);
    state.workflow.set(role.key, {
      id: record.id || current?.id,
      ...(dataOf(record) || {}),
      role_key: role.key,
      stage: targetStage,
      template_id: templateId,
      createdAt: record.createdAt || current?.createdAt,
      updatedAt: record.updatedAt || new Date().toISOString()
    });
  } catch (error) {
    console.warn('Applied undo workflow projection could not be updated immediately', error);
  }

  if (typeof refreshRoleSurfaces === 'function') refreshRoleSurfaces(role);
  else {
    renderBoard();
    renderOverlayIfOpen();
  }
}

function showAppliedSuccess(role, priorStage, confirmation) {
  if (state.overlayOpen && state.overlayKey === role.key) closeOverlay();
  launchApplicationConfetti();
  showActionToast('Job applied', 'Undo', () => undoAppliedMark(role, priorStage, confirmation), { success: true, duration: 7000 });
}

async function moveRole(role, nextStage, requireConfirmation = false) {
  const current = state.workflow.get(role.key);
  const priorStage = stageFor(role);
  if (priorStage === nextStage) return;
  let submissionConfirmation = null;
  if (requireConfirmation) {
    try {
      submissionConfirmation = await confirmApplicationSubmitted(role, 'kanban_stage_change');
      if (!submissionConfirmation) return;
    } catch (error) {
      showToast(`Submission was not marked complete: ${error.message}`, true);
      return;
    }
  }
  const templateId = current?.template_id || selectedTemplateFor(role);
  const optimistic = { ...current, role_key: role.key, stage: nextStage, template_id: templateId, updatedAt: new Date().toISOString() };
  state.workflow.set(role.key, optimistic);
  renderBoard();
  try {
    const record = current?.id
      ? await patchRecord('workflow', current.id, { stage: nextStage, role_key: role.key, route: role.route || '', company: role.company, role: role.role, template_id: templateId })
      : await createRecord('workflow', {
          role_key: role.key,
          stage: nextStage,
          route: role.route || '',
          company: role.company,
          role: role.role,
          template_id: templateId
        }, `${role.key}-${nextStage}`);
    state.workflow.set(role.key, { id: record.id || current?.id, ...(dataOf(record) || optimistic), createdAt: record.createdAt, updatedAt: record.updatedAt || new Date().toISOString() });
    await appendHistoryEvent(role, 'stage_change', priorStage, nextStage);
    renderBoard();
    renderOverlayIfOpen();
    if (nextStage === 'applied' && submissionConfirmation) showAppliedSuccess(role, priorStage, submissionConfirmation);
    else showToast(`Moved to ${STAGES.find(stage => stage.id === nextStage)?.label}.`);
  } catch (error) {
    if (current) state.workflow.set(role.key, current); else state.workflow.delete(role.key);
    renderBoard();
    renderOverlayIfOpen();
    showToast(error.message, true);
  }
}

function humanManualReviewReason(reason) {
  const value = String(reason || '').trim();
  if (!value) return '';
  if (value === 'insufficient_job_description') return 'Insufficient job description';
  return value.replaceAll('_', ' ').replace(/^./, char => char.toUpperCase());
}

function renderRole(role) {
  const fragment = $('#role-template').content.cloneNode(true);
  const card = $('.role-card', fragment);
  card.dataset.roleKey = role.key;
  card.dataset.stage = stageFor(role);
  const decisionValue = normalizeDecision(role.decision);
  const decision = $('.decision-badge', fragment);
  decision.textContent = humanDecision(decisionValue);
  decision.classList.add(decisionValue.replaceAll('_', '-'));
  const scoreChip = $('.score-chip', fragment);
  const numericScore = Number.isFinite(Number(role.score)) ? Math.max(0, Math.min(100, Number(role.score))) : 0;
  scoreChip.style.setProperty('--score-pct', `${numericScore}%`);
  scoreChip.dataset.bucket = scoreBucket(role.score).cls;
  $('.score-value', fragment).textContent = role.score ?? (stageFor(role) === 'manual_review_needed' ? 'Unscored' : '—');
  $('.role-title', fragment).textContent = role.role;
  $('.role-company', fragment).textContent = role.company;
  $('.role-location', fragment).textContent = role.location || 'Location not stated';
  const age = $('.role-age', fragment);
  age.dataset.ageTime = activityTime(role) || roleFoundTime(role);
  age.textContent = `Found ${relativeTime(age.dataset.ageTime)}`;
  const manualReviewText = stageFor(role) === 'manual_review_needed'
    ? (role.manual_review_detail || humanManualReviewReason(role.manual_review_reason) || 'Manual review is required before processing can continue.')
    : '';
  $('.brief', fragment).textContent = manualReviewText ? `Manual review: ${manualReviewText}` : (role.brief || '');

  const recency = recencyBucket(role.posting_date || roleFoundTime(role));
  const recencyTag = $('.tag-recency', fragment);
  recencyTag.textContent = recency.key === 'unknown' ? 'Recency unknown' : `${recency.label} (${role.posting_date ? 'posted' : 'found'} ${relativeTime(role.posting_date || roleFoundTime(role))})`;
  recencyTag.classList.add(`tag-${recency.cls}`);
  recencyTag.title = role.posting_date
    ? `Posted ${formatDay(role.posting_date)} (${role.posting_date_precision || 'precision unknown'})`
    : 'No posting date available; recency uses the found/verified date.';

  const scoreTag = $('.tag-score', fragment);
  const bucket = scoreBucket(role.score);
  scoreTag.textContent = `${bucket.label} fit`;
  scoreTag.classList.add(`tag-${bucket.cls}`);
  scoreTag.title = role.score == null ? 'No score recorded.' : `Fit score ${role.score}/100.`;

  const postedTag = $('.tag-posted', fragment);
  if (role.posting_date) {
    postedTag.hidden = false;
    postedTag.textContent = `Posted ${formatDay(role.posting_date)}`;
    postedTag.title = `Posting date precision: ${role.posting_date_precision}; source: ${role.posting_date_source}`;
  }

  const quickFiles = $('.quick-files', fragment);
  const recommendedResume = role.recommended_resume || (role.route === 'portal' && role.resume_ats?.pdf ? role.resume_ats : role.resume);
  const resumeLabel = normalizeTemplateId(role.recommended_resume_template) === 'ats-classic' || (role.route === 'portal' && role.resume_ats?.pdf)
    ? 'ATS CV PDF'
    : 'Executive CV PDF';
  appendFileLink(quickFiles, resumeLabel, recommendedResume?.pdf, true);
  appendFileLink(quickFiles, 'Cover PDF', role.cover_letter?.pdf);
  if (!role.resume?.pdf && role.application_url) appendFileLink(quickFiles, 'Vacancy', role.application_url);
  buildStageActions(role, $('.stage-actions', fragment));

  /* Card click -> in-page overlay, unless an interactive control or a drag was involved. */
  card.addEventListener('click', event => {
    if (state.dragging) { state.dragging = false; return; }
    const interactive = event.target.closest('button, select, a, input, textarea, summary, .stage-select');
    if (interactive) return;
    openOverlay(role.key);
  });
  card.addEventListener('keydown', event => {
    if (event.key === 'Enter' && !event.target.closest('button, select, a, input, textarea')) {
      openOverlay(role.key);
    }
  });
  card.addEventListener('dragstart', event => {
    state.dragging = true;
    state.draggedKey = role.key;
    event.dataTransfer.effectAllowed = 'move';
    event.dataTransfer.setData('text/plain', role.key);
    card.classList.add('dragging');
  });
  card.addEventListener('dragend', () => {
    state.draggedKey = '';
    card.classList.remove('dragging');
    for (const column of $$('.kanban-column')) column.classList.remove('drag-over');
  });
  return fragment;
}

function scrollToBoardStage(stageId = '') {
  const board = $('#board');
  if (!board) return;
  const target = stageId ? board.querySelector(`.kanban-column[data-stage="${stageId}"]`) : board.querySelector('.kanban-column');
  if (!target) return;
  target.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'start' });
  if (board.scrollWidth > board.clientWidth) board.scrollTo({ left: target.offsetLeft - board.offsetLeft, behavior: 'smooth' });
}

function summaryMetricIcon(stageId) {
  const paths = {
    tracked: '<path d="M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path><rect x="3" y="7" width="18" height="13" rx="2"></rect><path d="M3 12h18"></path>',
    found: '<circle cx="11" cy="11" r="7"></circle><path d="m20 20-4-4"></path>',
    processing: '<circle cx="12" cy="12" r="3"></circle><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6V21h-4v-.1a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9A1.7 1.7 0 0 0 3 14H3v-4h.1a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9L4.2 7 7 4.2l.1.1a1.7 1.7 0 0 0 1.9.3A1.7 1.7 0 0 0 10 3h4a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1H21v4h-.1a1.7 1.7 0 0 0-1.5 1Z"></path>',
    ready_review: '<circle cx="12" cy="12" r="8"></circle><path d="M12 7v5l3 2"></path><path d="M18 4l2-2"></path>',
    applied: '<path d="m22 2-7 20-4-9-9-4Z"></path><path d="M22 2 11 13"></path>'
  };
  return `<span class="summary-icon" aria-hidden="true"><svg class="icon-svg" viewBox="0 0 24 24">${paths[stageId] || paths.tracked}</svg></span>`;
}

function renderSummary() {
  const counts = Object.fromEntries(STAGES.map(stage => [stage.id, 0]));
  for (const role of state.roles) counts[stageFor(role)] += 1;
  const metrics = [
    [state.roles.length, 'Tracked', '', 'tracked'],
    [counts.found, 'Found', 'found', 'found'],
    [counts.ready_review, 'Awaiting action', 'ready_review', 'ready_review'],
    [counts.applied, 'Applied', 'applied', 'applied']
  ];
  const target = $('#summary');
  target.replaceChildren();
  for (const [value, label, stageId, iconKey] of metrics) {
    const card = document.createElement('button');
    card.type = 'button';
    card.className = `summary-card summary-filter summary-${iconKey}${label === 'Awaiting action' ? ' priority-filter' : ''}`;
    card.setAttribute('aria-label', `${label}: ${value}. Jump to ${stageId ? `${label} column` : 'start of board'}.`);
    card.insertAdjacentHTML('beforeend', summaryMetricIcon(iconKey));
    const text = document.createElement('span');
    text.className = 'summary-copy';
    const strong = document.createElement('strong');
    strong.textContent = value;
    const span = document.createElement('span');
    span.textContent = label;
    text.append(strong, span);
    card.append(text);
    card.addEventListener('click', () => scrollToBoardStage(stageId));
    target.append(card);
  }
}

function setInactiveCollapsed(collapsed) {
  state.inactiveCollapsed = Boolean(collapsed);
  localStorage.setItem('career_inactive_collapsed', state.inactiveCollapsed ? 'true' : 'false');
  renderBoard();
}

function renderBoard() {
  renderSummary();
  const roles = sortedRoles(filteredRoles());
  const board = $('#board');
  board.replaceChildren();
  board.classList.toggle('inactive-collapsed', state.inactiveCollapsed);
  for (const stage of STAGES) {
    const column = document.createElement('section');
    column.className = 'kanban-column';
    column.dataset.stage = stage.id;
    if (stage.id === 'inactive') {
      column.classList.add('inactive-column');
      column.classList.toggle('is-collapsed', state.inactiveCollapsed);
    }

    const header = document.createElement('header');
    header.className = 'column-header';
    const titleGroup = document.createElement('div');
    titleGroup.className = 'column-title-group';
    const title = document.createElement('h2');
    title.textContent = stage.label;
    const count = document.createElement('span');
    const stageRoles = roles.filter(role => stageFor(role) === stage.id);
    count.textContent = stageRoles.length;
    titleGroup.append(title, count);
    header.append(titleGroup);

    if (stage.id === 'inactive') {
      const toggle = document.createElement('button');
      toggle.type = 'button';
      toggle.className = 'column-collapse';
      toggle.setAttribute('aria-expanded', state.inactiveCollapsed ? 'false' : 'true');
      toggle.setAttribute('aria-label', state.inactiveCollapsed ? 'Expand closed and inactive jobs' : 'Collapse closed and inactive jobs');
      toggle.title = state.inactiveCollapsed ? 'Expand closed / inactive' : 'Collapse closed / inactive';
      toggle.innerHTML = state.inactiveCollapsed
        ? '<svg class="icon-svg" viewBox="0 0 24 24" aria-hidden="true"><path d="m9 18 6-6-6-6"></path></svg>'
        : '<svg class="icon-svg" viewBox="0 0 24 24" aria-hidden="true"><path d="m15 18-6-6 6-6"></path></svg>';
      toggle.addEventListener('click', event => {
        event.stopPropagation();
        setInactiveCollapsed(!state.inactiveCollapsed);
      });
      header.append(toggle);
    }

    const list = document.createElement('div');
    list.className = 'column-list';
    if (stage.id === 'inactive' && state.inactiveCollapsed) list.setAttribute('aria-hidden', 'true');
    for (const role of stageRoles) list.append(renderRole(role));
    if (!stageRoles.length) {
      const empty = document.createElement('p');
      empty.className = 'column-empty';
      empty.textContent = stage.id === 'inactive' ? 'No closed jobs' : 'Drop a job here';
      list.append(empty);
    }
    if (stage.id === 'inactive') {
      column.addEventListener('click', event => {
        if (!state.inactiveCollapsed) return;
        if (event.target.closest('button, a, input, select, textarea, details, summary')) return;
        setInactiveCollapsed(false);
      });
    }
    column.addEventListener('dragover', event => {
      event.preventDefault();
      event.dataTransfer.dropEffect = 'move';
      column.classList.add('drag-over');
    });
    column.addEventListener('dragleave', event => {
      if (!column.contains(event.relatedTarget)) column.classList.remove('drag-over');
    });
    column.addEventListener('drop', event => {
      event.preventDefault();
      column.classList.remove('drag-over');
      const key = event.dataTransfer.getData('text/plain') || state.draggedKey;
      const role = state.roles.find(item => item.key === key);
      if (role) moveRole(role, stage.id, stage.id === 'applied');
    });
    column.append(header, list);
    board.append(column);
  }
  updateScanClock();
}

function mergeRoleCardData(role, generatedAt) {
  const route = role.route || 'portal';
  const cardResume = role.card_resume_pdf || '';
  const cardCover = role.card_cover_pdf || '';
  const resume = { ...(role.resume || {}) };
  const resumeAts = { ...(role.resume_ats || {}) };
  if (cardResume) {
    if (route === 'portal' && !resumeAts.pdf) resumeAts.pdf = cardResume;
    if (route !== 'portal' && !resume.pdf) resume.pdf = cardResume;
  }
  const coverLetter = { ...(role.cover_letter || {}) };
  if (cardCover && !coverLetter.pdf) coverLetter.pdf = cardCover;
  return {
    ...role,
    route,
    application_url: role.application_url || role.card_application_url || '',
    source_url: role.source_url || role.application_url || role.card_application_url || '',
    found_at: role.found_at || role.first_seen || generatedAt,
    resume,
    resume_ats: resumeAts,
    cover_letter: coverLetter
  };
}

function mergeRoles(data) {
  const generatedAt = data.generated_at || new Date().toISOString();
  const applications = (data.applications || []).map(role => ({
    ...mergeRoleCardData(role, generatedAt),
    kind: 'application'
  }));
  const reviewed = (data.reviewed || []).map(role => ({
    ...mergeRoleCardData(role, generatedAt),
    kind: 'reviewed',
    email_subject: role.email_subject || `Abdelhamid Farah - ${role.role}`,
    email_body: role.email_body || `Dear Recruitment Team,\n\nPlease find my application for the ${role.role} position at ${role.company}.\n\nKind regards,\nAbdelhamid Farah\n${OUTWARD_EMAIL}`
  }));
  return [...applications, ...reviewed];
}

function syncDirectControl(controlId, value) {
  const items = document.querySelectorAll(`[data-direct-control="${controlId}"] .direct-menu-item[data-value]`);
  for (const item of items) {
    const active = item.dataset.value === value;
    item.classList.toggle('is-active', active);
    item.setAttribute('aria-pressed', active ? 'true' : 'false');
  }
}

function closeDismissibleMenus(except = null) {
  const menus = document.querySelectorAll('.dismissible-menu');
  for (const menu of menus) {
    if (menu !== except) menu.open = false;
  }
}

function globalOperations() {
  return state.aiRequests
    .filter(record => dataOf(record).role_key === GLOBAL_ROLE_KEY)
    .sort((a, b) => String(a.createdAt || '').localeCompare(String(b.createdAt || '')));
}

function parseBatchProgress(value) {
  if (!value || typeof value !== 'string' || value[0] !== '{') return null;
  try {
    const parsed = JSON.parse(value);
    return parsed?.kind === 'batch_progress' ? parsed : null;
  } catch {
    return null;
  }
}

function formatEta(seconds) {
  const value = Number(seconds);
  if (!Number.isFinite(value) || value <= 0) return '';
  if (value < 60) return '<1 min';
  const minutes = Math.max(1, Math.round(value / 60));
  if (minutes < 60) return `~${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const remainder = minutes % 60;
  return remainder ? `~${hours}h ${remainder}m` : `~${hours}h`;
}

function applyBatchProgress(progress, requestState) {
  const previous = JSON.stringify(state.batchProgress || {});
  state.batchProgress = requestState === 'processing' ? progress : null;
  state.batchStageOverrides.clear();
  if (state.batchProgress) {
    for (const key of state.batchProgress.completed_role_keys || []) {
      state.batchStageOverrides.set(key, 'ready_review');
    }
    // The active batch job stays in its canonical workflow lane. Background
    // processing is shown through the progress/status UI, not a workflow stage.
  }
  if (previous !== JSON.stringify(state.batchProgress || {})) renderBoard();
}

function renderGlobalOperationStatus() {
  const target = $('#operation-status');
  if (!target) return;
  const latest = globalOperations().at(-1);
  if (!latest) { target.textContent = ''; return; }
  const data = dataOf(latest);
  const requestState = data.state || 'pending';
  const label = data.request_type === 'refresh_jobs'
    ? 'Hermes job scan'
    : `Score ≥ ${Number(data.min_score || 70)} processing`;
  const progress = data.request_type === 'process_jobs' ? parseBatchProgress(data.answer) : null;
  applyBatchProgress(progress, requestState);
  if (requestState === 'pending') {
    target.textContent = `${label}: queued…`;
  } else if (requestState === 'processing' && progress) {
    const done = Number(progress.done || 0);
    const succeeded = Number(progress.succeeded || Math.max(0, done - Number(progress.failed || 0)));
    const failed = Number(progress.failed || 0);
    const preserved = Number(progress.preserved || 0);
    const total = Number(progress.total || 0);
    const remaining = Number(progress.remaining || Math.max(0, total - done));
    const eta = formatEta(progress.eta_seconds);
    const current = [progress.current_company, progress.current_role].filter(Boolean).join(' — ');
    const phase = progress.phase === 'publishing' ? 'publishing dashboard…' : (current ? `processing ${current}` : 'preparing batch…');
    target.textContent = `${label}: ${succeeded} done${preserved ? ` · ${preserved} preserved` : ''}${failed ? ` · ${failed} failed` : ''} · ${remaining} remaining (${total} total)${eta ? ` · ETA ${eta}` : ''} · ${phase}`;
    const latestFailure = Array.isArray(progress.recent_failures) ? progress.recent_failures.at(-1) : null;
    target.title = latestFailure?.error ? `Latest failure: ${latestFailure.error}` : '';
  } else if (requestState === 'processing') {
    target.textContent = `${label}: running…`;
  } else if (requestState === 'failed') {
    target.textContent = `${label}: failed — ${data.answer || 'see request history'}`;
  } else {
    target.textContent = `${label}: ${data.answer || 'complete'}`;
  }

  const active = ['pending', 'processing'].includes(requestState);
  if ($('#refresh-board')) $('#refresh-board').disabled = active;
  if ($('#process-jobs')) $('#process-jobs').disabled = active;
  if (!active && requestState === 'done' && latest.id && sessionStorage.getItem('career-operation-owned') === latest.id && sessionStorage.getItem('career-operation-reloaded') !== latest.id) {
    sessionStorage.setItem('career-operation-reloaded', latest.id);
    window.setTimeout(() => window.location.reload(), 1200);
  }
}

function setupGlobalOperationPolling() {
  if (state.operationPollTimer) clearInterval(state.operationPollTimer);
  renderGlobalOperationStatus();
  const active = globalOperations().some(record => ['pending', 'processing'].includes(dataOf(record).state || 'pending'));
  if (!active) return;
  state.operationPollTimer = setInterval(async () => {
    try {
      const records = await loadCollection('ai_requests', 300, true, true);
      state.aiRequests = records.map(record => ({ id: record.id, ...dataOf(record), createdAt: record.createdAt, updatedAt: record.updatedAt }));
      renderGlobalOperationStatus();
      if (!globalOperations().some(record => ['pending', 'processing'].includes(dataOf(record).state || 'pending'))) {
        clearInterval(state.operationPollTimer);
        state.operationPollTimer = null;
      }
    } catch (error) {
      const target = $('#operation-status');
      if (target) target.textContent = `Operation status unavailable: ${error.message}`;
    }
  }, 3000);
}

async function queueGlobalOperation(requestType, fields = {}) {
  const active = globalOperations().find(record => ['pending', 'processing'].includes(dataOf(record).state || 'pending'));
  if (active) {
    showToast('A Career Engine operation is already running.', true);
    return;
  }
  const prompt = requestType === 'refresh_jobs'
    ? 'Run a fresh Hermes Career Engine scan, then rebuild and republish the private dashboard. Do not send or submit anything.'
    : `Process every Career Engine-eligible live job scoring at least ${fields.min_score}. Generate and render validated application packages only; do not send or submit anything.`;
  try {
    const record = await createRecord('ai_requests', {
      role_key: GLOBAL_ROLE_KEY,
      request_type: requestType,
      prompt,
      state: 'pending',
      ...fields
    }, `career-global-${requestType}-${Date.now()}`);
    state.aiRequests.push({ id: record.id, ...dataOf(record), createdAt: record.createdAt, updatedAt: record.updatedAt });
    if (record.id) sessionStorage.setItem('career-operation-owned', record.id);
    renderGlobalOperationStatus();
    setupGlobalOperationPolling();
  } catch (error) {
    showToast(error.message, true);
  }
}

function setupControls() {
  const search = $('#search-filter');
  const searchShell = $('.desktop-search-control');
  const mobileSearchToggle = $('#mobile-search-toggle');
  search.addEventListener('input', renderBoard);
  mobileSearchToggle?.addEventListener('click', event => {
    event.stopPropagation();
    const open = !searchShell.classList.contains('mobile-open');
    searchShell.classList.toggle('mobile-open', open);
    mobileSearchToggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (open) requestAnimationFrame(() => search.focus());
  });
  search.addEventListener('keydown', event => {
    if (event.key === 'Escape' && searchShell.classList.contains('mobile-open')) {
      searchShell.classList.remove('mobile-open');
      mobileSearchToggle?.setAttribute('aria-expanded', 'false');
      mobileSearchToggle?.focus();
    }
  });
  $('#decision-filter').addEventListener('change', renderBoard);
  $('#sort-order').addEventListener('change', renderBoard);
  $('#theme-select').addEventListener('change', async event => {
    const theme = applyTheme(event.target.value);
    syncDirectControl('theme-select', theme.id);
    try {
      await savePreference(THEME_PREF_KEY, theme.id);
      showToast(`Design: ${theme.label}`);
    } catch (error) {
      showToast(`Design applied but not saved: ${error.message}`, true);
    }
  });
  $('#refresh-board').addEventListener('click', () => queueGlobalOperation('refresh_jobs'));
  $('#process-jobs').addEventListener('click', () => {
    const score = Number($('#process-score').value || 70);
    if (!Number.isInteger(score) || score < 0 || score > 100) {
      showToast('Score limit must be an integer from 0 to 100.', true);
      return;
    }
    queueGlobalOperation('process_jobs', { min_score: score });
  });

  document.addEventListener('click', event => {
    const item = event.target.closest('.direct-menu-item[data-value]');
    if (!item) return;
    const list = item.closest('[data-direct-control]');
    const controlId = list?.dataset.directControl;
    const control = controlId ? $(`#${controlId}`) : null;
    if (!control) return;
    control.value = item.dataset.value || '';
    syncDirectControl(controlId, control.value);
    control.dispatchEvent(new Event('change', { bubbles: true }));
    const menu = item.closest('.dismissible-menu');
    if (menu) menu.open = false;
  });

  document.addEventListener('toggle', event => {
    const menu = event.target.closest?.('.dismissible-menu');
    if (menu?.open) closeDismissibleMenus(menu);
  }, true);

  document.addEventListener('pointerdown', event => {
    if (!event.target.closest('.desktop-search-control, #mobile-search-toggle') && searchShell.classList.contains('mobile-open')) {
      searchShell.classList.remove('mobile-open');
      mobileSearchToggle?.setAttribute('aria-expanded', 'false');
    }
    if (event.target.closest('.dismissible-menu')) return;
    closeDismissibleMenus();
  });

  syncDirectControl('decision-filter', $('#decision-filter').value);
  syncDirectControl('sort-order', $('#sort-order').value);
  syncDirectControl('theme-select', $('#theme-select').value);
}

/* ================= Job detail overlay (in-page modal) ================= */

function commentsForRole(key) {
  return state.comments
    .filter(record => record.role_key === key)
    .sort((a, b) => String(a.createdAt).localeCompare(String(b.createdAt)));
}

function aiRequestsForRole(key) {
  return state.aiRequests
    .filter(record => record.role_key === key)
    .sort((a, b) => String(b.createdAt).localeCompare(String(a.createdAt)));
}

function renderOverlayIfOpen() {
  if (!state.overlayOpen) return;
  const role = state.roles.find(item => item.key === state.overlayKey);
  if (role) renderOverlayContent(role);
}

function openOverlay(key, syncUrl = true) {
  const role = state.roles.find(item => item.key === key);
  if (!role) return;
  state.overlayKey = key;
  state.overlayReturnKey = key;
  state.overlayOpen = true;
  if (syncUrl) {
    const url = new URL(window.location.href);
    url.searchParams.set('job', key);
    history.pushState({ job: key }, '', url);
  }
  renderOverlayContent(role);
  loadRoleDetails(role);
  ensureOverlayData(role).catch(error => console.warn('overlay data unavailable', error));
  const overlay = $('#job-overlay');
  overlay.hidden = false;
  overlay.setAttribute('aria-hidden', 'false');
  document.documentElement.classList.add('overlay-open');
  document.body.classList.add('overlay-open');
  requestAnimationFrame(() => {
    const first = $('.overlay-panel [data-focus-first]') || $('.overlay-panel .overlay-close');
    if (first) first.focus();
  });
}

function closeOverlay() {
  if (!state.overlayOpen) return;
  const returnKey = state.overlayReturnKey || state.overlayKey;
  state.overlayOpen = false;
  state.overlayKey = '';
  state.overlayReturnKey = '';
  const url = new URL(window.location.href);
  url.searchParams.delete('job');
  history.replaceState({}, '', `${url.pathname}${url.search}${url.hash}`);
  if (state.aiPollTimer) { clearInterval(state.aiPollTimer); state.aiPollTimer = null; }
  const overlay = $('#job-overlay');
  overlay.hidden = true;
  overlay.setAttribute('aria-hidden', 'true');
  document.documentElement.classList.remove('overlay-open');
  document.body.classList.remove('overlay-open');
  if (returnKey) {
    requestAnimationFrame(() => {
      const card = $(`.role-card[data-role-key="${returnKey}"]`);
      if (card) card.focus();
    });
  }
}

async function loadRoleDetails(role) {
  if (role.detailsLoaded || !role.key) return;
  try {
    const shardId = Number.isInteger(Number(role.detail_shard)) ? Number(role.detail_shard) : null;
    let detail = null;
    if (shardId !== null) {
      if (!state.detailShards.has(shardId)) {
        const response = await fetch(`data/job-details/${shardId}.json`, { cache: 'force-cache' });
        if (!response.ok) return;
        state.detailShards.set(shardId, await response.json());
      }
      detail = state.detailShards.get(shardId)?.[role.key] || null;
    } else {
      // Backward-compatible fallback for an older generated dashboard payload.
      const response = await fetch(`data/job-details/${encodeURIComponent(role.key)}.json`, { cache: 'force-cache' });
      if (!response.ok) return;
      detail = await response.json();
    }
    if (!detail) return;
    Object.assign(role, detail, { detailsLoaded: true });
    if (state.overlayOpen && state.overlayKey === role.key) renderOverlayContent(role);
  } catch (error) { console.warn('job detail unavailable', error); }
}

function setupOverlay() {
  const overlay = $('#job-overlay');
  if (!overlay) return;
  overlay.addEventListener('click', event => {
    if (event.target.closest('[data-overlay-close]')) closeOverlay();
  });
  document.addEventListener('keydown', event => {
    if (!state.overlayOpen) return;
    if (event.key === 'Escape') {
      event.preventDefault();
      closeOverlay();
      return;
    }
    if (event.key === 'Tab') trapOverlayFocus(event);
  });
}

function trapOverlayFocus(event) {
  const panel = $('.overlay-panel', $('#job-overlay'));
  if (!panel) return;
  const focusables = $$('button:not([disabled]), [href], select, textarea, input, [tabindex]:not([tabindex="-1"])', panel)
    .filter(el => el.offsetParent !== null || el === document.activeElement);
  if (!focusables.length) return;
  const first = focusables[0];
  const last = focusables[focusables.length - 1];
  if (event.shiftKey && (document.activeElement === first || document.activeElement === panel)) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

function renderOverlayContent(role) {
  const content = $('#overlay-content');
  const decisionValue = normalizeDecision(role.decision);
  const scoreBucketValue = scoreBucket(role.score);
  content.innerHTML = `
    <header class="overlay-header compact-detail-header">
      <span class="overlay-job-icon" aria-hidden="true">
        <svg class="icon-svg" viewBox="0 0 24 24"><path d="M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path><rect x="3" y="7" width="18" height="13" rx="2"></rect><path d="M3 12h18"></path></svg>
      </span>
      <div class="overlay-title-block">
        <h2 id="ov-role">${escapeHtml(role.role)}</h2>
        <p><span id="ov-company">${escapeHtml(role.company)}</span><span class="meta-separator">·</span><span id="ov-location">${escapeHtml(role.location || 'Location not stated')}</span></p>
      </div>
      <div class="overlay-badges">
        <span id="ov-decision" class="decision-badge ${decisionValue.replaceAll('_', '-')}">${escapeHtml(humanDecision(decisionValue))}</span>
        <span class="score-chip"><strong id="ov-score">${escapeHtml(role.score ?? '—')}</strong><small>/100</small></span>
        <span id="ov-score-tag" class="tag tag-${scoreBucketValue.cls}">${escapeHtml(scoreBucketValue.label)}</span>
      </div>
      <button type="button" class="overlay-close" data-overlay-close aria-label="Close job detail" data-focus-first>×</button>
    </header>

    <div class="overlay-action-bar">
      <div class="overlay-action-copy">
        <span class="overlay-action-icon" aria-hidden="true">
          <svg class="icon-svg" viewBox="0 0 24 24"><path d="m22 2-7 20-4-9-9-4Z"></path><path d="M22 2 11 13"></path></svg>
        </span>
        <div>
          <strong>Take the next step</strong>
          <span id="ov-action-message">Review this opportunity and continue when the package is ready.</span>
        </div>
      </div>
      <div class="overlay-action-buttons">
        <button id="ov-main-action" class="card-button primary action-main primary-next-action" type="button"></button>
        <button id="ov-job-applied" class="card-button overlay-applied-button" type="button">Job applied</button>
        <div id="ov-secondary" class="doc-links compact-secondary-actions"></div>
      </div>
    </div>

    <div class="overlay-meta-strip" aria-label="Job metadata">
      <span><strong>Posted</strong> <span id="ov-posted"></span></span>
      <span><strong>Found</strong> <span id="ov-found"></span></span>
      <span><strong>Scan</strong> <span id="ov-scan"></span></span>
      <details class="detail-tool-menu dismissible-menu">
        <summary title="Stage and resume options" aria-label="Stage and resume options">
          <svg class="icon-svg" viewBox="0 0 24 24" aria-hidden="true"><circle cx="5" cy="12" r="1"></circle><circle cx="12" cy="12" r="1"></circle><circle cx="19" cy="12" r="1"></circle></svg>
        </summary>
        <div class="detail-tool-popover menu-popover">
          <div class="menu-group">
            <span class="menu-heading">Stage</span>
            <div id="ov-stage-options" class="direct-menu-list"></div>
            <div id="ov-stage-action" class="stage-action"></div>
          </div>
          <div class="menu-group">
            <span class="menu-heading">Submission CV</span>
            <div id="ov-template-options" class="direct-menu-list"></div>
            <p id="ov-template-default" class="help-note"></p>
            <div id="ov-template-files" class="doc-links"></div>
            <p id="ov-template-note" class="missing-note"></p>
          </div>
        </div>
      </details>
    </div>

    <div class="overlay-workspace">
      <section class="resume-workspace" aria-label="Resume viewer">
        <div class="resume-viewer-head">
          <strong>Resume</strong>
          <div class="resume-file-actions">
            <a id="ov-resume-open" class="card-button" href="#" target="_blank" rel="noopener noreferrer" hidden>View PDF</a>
            <a id="ov-resume-download" class="card-button" href="#" download hidden>Download PDF</a>
            <a id="ov-resume-docx" class="card-button" href="#" download hidden>Download DOCX</a>
          </div>
        </div>
        <div id="ov-resume-viewer" class="resume-viewer">
          <iframe id="ov-resume-frame" title="Selected resume preview" loading="eager" scrolling="yes"></iframe>
          <div id="ov-resume-empty" class="resume-empty" hidden>Selected resume PDF is not available for this job.</div>
        </div>
      </section>

      <aside class="detail-utility" aria-label="Application details">

        <section class="compact-detail-section assistant-block assistant-block-top overlay-assistant-top">
          <h3 class="assistant-heading">Career Engine assistant</h3>
          <div class="compact-detail-body">
            <p class="help-note">Job-specific replies stay in Site Data. Unsupported factual or personal details are marked Owner input needed.</p>
            <div class="assistant-quick-actions" aria-label="Assistant quick actions">
              <button type="button" class="card-button quick-action" data-overlay-assistant-action="headline">Headline</button>
              <button type="button" class="card-button quick-action" data-overlay-assistant-action="summary">Summary</button>
              <button type="button" class="card-button quick-action" data-overlay-assistant-action="skills">Skills</button>
              <button type="button" class="card-button quick-action" data-overlay-assistant-action="current_role">Current role</button>
              <button type="button" class="card-button quick-action" data-overlay-assistant-action="experience">Experience</button>
              <button type="button" class="card-button quick-action" data-overlay-assistant-action="cover_letter">Cover letter</button>
              <button type="button" class="card-button quick-action" data-overlay-assistant-action="application_question">Application question</button>
              <button type="button" class="card-button quick-action" data-overlay-assistant-action="edit_cv">Edit CV</button>
            </div>
            <div id="ov-ai" class="ai-list overlay-scroll"></div>
            <form id="ov-ai-form" class="panel-form">
              <textarea id="ov-ai-prompt" rows="2" placeholder="Ask about this job or edit the selected request…"></textarea>
              <div class="form-row">
                <button type="submit" class="card-button primary">Send / Ask</button>
                <span id="ov-ai-status" class="comment-status"></span>
              </div>
            </form>
          </div>
        </section>

        <section class="overlay-block cover-box">
          <div class="cover-box-head">
            <h3>Cover letter / email</h3>
            <div class="cover-box-actions">
              <button id="ov-cover-copy" class="card-button" type="button">Copy text</button>
              <a id="ov-cover-pdf-download" class="card-button" href="#" download hidden>Download PDF</a>
            </div>
          </div>
          <p id="ov-email-meta" class="email-meta"></p>
          <textarea id="ov-email-body" class="cover-textbox" readonly aria-label="Cover letter or application email text"></textarea>
        </section>

        <details id="ov-submission-record-section" class="compact-detail-section" hidden>
          <summary>Submitted package</summary>
          <div id="ov-submission-record" class="compact-detail-body submission-record"></div>
        </details>

        <details class="compact-detail-section">
          <summary>Fit & metadata</summary>
          <div class="compact-detail-body">
            <p id="ov-brief" class="detail-brief"></p>
            <h4>Why it fits</h4>
            <ul id="ov-strengths" class="overlay-tight-list"></ul>
            <h4>Material gaps / risks</h4>
            <ul id="ov-gaps" class="overlay-tight-list"></ul>
          </div>
        </details>

        <details class="compact-detail-section">
          <summary>Documents</summary>
          <div class="compact-detail-body">
            <h4>Cover letter files</h4>
            <div id="ov-cover" class="doc-links"></div>
            <h4>Email draft package</h4>
            <div id="ov-eml" class="doc-links"></div>
          </div>
        </details>

        <details class="compact-detail-section">
          <summary>Comments <span id="ov-comment-count" class="tag"></span></summary>
          <div class="compact-detail-body">
            <div id="ov-comments" class="comment-list overlay-scroll"></div>
            <form id="ov-comment-form" class="panel-form">
              <label>Type<select id="ov-comment-type"></select></label>
              <textarea id="ov-comment-text" rows="2" placeholder="Decision, edit request, question or note…"></textarea>
              <div class="form-row">
                <button type="submit" class="card-button primary">Save comment</button>
                <span id="ov-comment-status" class="comment-status"></span>
              </div>
            </form>
          </div>
        </details>


      </aside>
    </div>`;

  renderOverlayHeader(role);
  renderOverlayDates(role);
  renderOverlayReview(role);
  renderOverlayStage(role);
  renderOverlayTemplate(role);
  renderOverlayResumePreview(role);
  renderOverlayDocuments(role);
  renderOverlayActions(role);
  renderOverlayEmail(role);
  renderOverlaySubmissionRecord(role);
  renderOverlayComments(role);
  renderOverlayAi(role);
  setupOverlayForms(role);
}

function renderOverlayHeader(role) {
  setTextSafe('ov-role', role.role);
  setTextSafe('ov-company', role.company);
  setTextSafe('ov-location', role.location || 'Location not stated');
  setTextSafe('ov-score', role.score ?? '—');
  const decisionValue = normalizeDecision(role.decision);
  const decision = $('#ov-decision');
  decision.textContent = humanDecision(decisionValue);
  decision.className = `decision-badge ${decisionValue.replaceAll('_', '-')}`;
}

function renderOverlayDates(role) {
  const posted = $('#ov-posted');
  if (parseDate(role.posting_date)) {
    posted.textContent = formatDay(role.posting_date);
    posted.title = `Precision: ${role.posting_date_precision || 'unknown'} · Source: ${role.posting_date_source || 'unknown'}`;
  } else {
    posted.textContent = 'Unknown';
    posted.title = 'No posting date was present in the source data.';
  }
  setTextSafe('ov-found', parseDate(role.found_at) ? formatDate(role.found_at) : 'Unknown');
  setTextSafe('ov-scan', parseDate(role.scanned_at || state.data?.generated_at) ? formatDate(role.scanned_at || state.data?.generated_at) : 'Unknown');
}

function renderOverlayReview(role) {
  setTextSafe('ov-brief', role.brief || 'No summary recorded.');
  addList($('#ov-strengths'), role.strengths, 'No specific strengths recorded.');
  addList($('#ov-gaps'), role.gaps, 'No material gap recorded.');
}

function renderOverlayStage(role) {
  const target = $('#ov-stage-options');
  target.replaceChildren();
  const currentStage = stageFor(role);
  for (const stageOption of STAGES) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = `direct-menu-item${stageOption.id === currentStage ? ' is-active' : ''}`;
    button.textContent = stageOption.label;
    button.setAttribute('aria-pressed', stageOption.id === currentStage ? 'true' : 'false');
    button.addEventListener('click', async () => {
      const menu = button.closest('.dismissible-menu');
      if (menu) menu.open = false;
      await moveRole(role, stageOption.id, stageOption.id === 'applied');
    });
    target.append(button);
  }

  const action = $('#ov-stage-action');
  action.replaceChildren();
  const stage = STAGES.find(item => item.id === currentStage);
  if (!stage) return;
  if (stage.id === 'ready_review') {
    if (role.route === 'email') {
      if (hasGenuineRecipient(role)) action.append(makeButton('Open Gmail', () => openGmailCompose(role), 'email'));
      if (role.application_url) action.append(makeLink('Recruitment route', role.application_url));
    } else if (role.application_url) {
      action.append(makeButton('Open application portal', () => openTrackedPortal(role, 'detail_overlay'), 'primary'));
    }
    action.append(makeButton(role.route === 'email' ? 'Confirm sent' : 'Confirm applied', () => moveRole(role, 'applied', true), 'success'));
  } else if (stage.id === 'applied') {
    action.append(makeButton('Reopen review', () => moveRole(role, 'ready_review')));
  } else {
    action.append(makeButton(stage.nextLabel, () => moveRole(role, stage.next), 'primary'));
  }
}

function renderOverlayTemplate(role) {
  const current = selectedTemplateFor(role);
  const avail = templateAvailability(role, state.templatesData || {});
  const options = $('#ov-template-options');
  options.replaceChildren();
  for (const option of TEMPLATE_OPTIONS) {
    const info = avail[option.id] || { generated: false };
    const button = document.createElement('button');
    button.type = 'button';
    button.className = `direct-menu-item${option.id === current ? ' is-active' : ''}`;
    button.setAttribute('aria-pressed', option.id === current ? 'true' : 'false');
    const label = document.createElement('span');
    label.textContent = option.label;
    const meta = document.createElement('small');
    meta.className = 'menu-item-meta';
    meta.textContent = info.generated ? 'Available' : 'Not generated';
    button.append(label, meta);
    button.addEventListener('click', async () => {
      const menu = button.closest('.dismissible-menu');
      if (menu) menu.open = false;
      await saveTemplateOverride(role, option.id);
    });
    options.append(button);
  }

  const rule = $('#ov-template-default');
  rule.textContent = `Default: ${templateLabel(defaultTemplateFor(role))}. One CV only; your selection persists for this job.`;
  const files = $('#ov-template-files');
  const note = $('#ov-template-note');
  files.replaceChildren();
  note.textContent = '';
  const info = avail[current] || { generated: false, label: current, note: 'Template file not available.' };
  if (info.generated) {
    if (info.pdf) {
      files.append(makeLink('View PDF', info.pdf, 'primary'));
      const downloadPdf = makeLink('Download PDF', info.pdf);
      downloadPdf.download = '';
      files.append(downloadPdf);
    }
    if (info.docx) {
      const downloadDocx = makeLink('Download DOCX', info.docx);
      downloadDocx.download = '';
      files.append(downloadDocx);
    }
    if (info.note) note.textContent = info.note;
  } else {
    const msg = document.createElement('span');
    msg.className = 'missing-note warn-note';
    msg.textContent = info.note || 'Selected template is not generated for this job yet.';
    files.append(msg);
  }
}

function renderOverlayResumePreview(role) {
  const frame = $('#ov-resume-frame');
  const empty = $('#ov-resume-empty');
  const open = $('#ov-resume-open');
  const download = $('#ov-resume-download');
  const docx = $('#ov-resume-docx');
  if (!frame || !empty || !open || !download || !docx) return;
  const current = selectedTemplateFor(role);
  const available = templateAvailability(role, state.templatesData || {});
  const info = available[current] || {};
  const pdf = info.pdf || '';
  const docxHref = info.docx || '';

  docx.hidden = !docxHref;
  if (docxHref) docx.href = docxHref;
  else docx.removeAttribute('href');

  if (!pdf) {
    frame.hidden = true;
    frame.removeAttribute('src');
    empty.hidden = false;
    empty.textContent = docxHref
      ? 'PDF preview is not available yet. Use Download DOCX or generate the PDF for this selected resume.'
      : 'Selected resume files are not available for this job.';
    open.hidden = true;
    open.removeAttribute('href');
    download.hidden = true;
    download.removeAttribute('href');
    return;
  }
  empty.hidden = true;
  frame.hidden = false;
  frame.src = pdf;
  open.href = pdf;
  open.hidden = false;
  download.href = pdf;
  download.hidden = false;
}

async function saveTemplateOverride(role, templateId) {
  const current = state.workflow.get(role.key);
  const previousTemplate = normalizeTemplateId(current?.template_id) || defaultTemplateFor(role);
  try {
    let record;
    if (current?.id) {
      record = await patchRecord('workflow', current.id, { template_id: templateId });
    } else {
      record = await createRecord('workflow', {
        role_key: role.key,
        stage: stageFor(role),
        route: role.route || '',
        company: role.company,
        role: role.role,
        template_id: templateId
      }, `workflow-${role.key}-${Date.now()}`);
    }
    state.workflow.set(role.key, { id: record.id || current?.id, ...(dataOf(record) || {}), createdAt: record.createdAt, updatedAt: record.updatedAt || new Date().toISOString() });
    await appendHistoryEvent(
      role,
      'resume_variant_override',
      previousTemplate,
      templateId,
      `Selected submission CV: ${templateLabel(templateId)}; route default: ${templateLabel(defaultTemplateFor(role))}`
    );
    showToast(`Submission CV set to ${templateLabel(templateId)}. Exactly one CV will be attached or uploaded.`);
    renderOverlayTemplate(role);
    renderOverlayResumePreview(role);
  } catch (error) {
    showToast(error.message, true);
  }
}

function renderOverlayDocuments(role) {
  const cover = $('#ov-cover');
  cover.replaceChildren();
  if (role.cover_letter?.pdf || role.cover_letter?.docx) {
    if (role.cover_letter.pdf) cover.append(makeLink('Cover PDF', role.cover_letter.pdf, 'primary'));
    if (role.cover_letter.docx) cover.append(makeLink('Cover DOCX', role.cover_letter.docx));
  } else {
    const note = document.createElement('span');
    note.className = 'missing-note';
    note.textContent = 'No cover letter generated for this role.';
    cover.append(note);
  }
  const eml = $('#ov-eml');
  eml.replaceChildren();
  if (role.email_file) {
    eml.append(makeLink('Download draft (.eml)', role.email_file, 'primary'));
    const note = document.createElement('span');
    note.className = 'missing-note';
    note.textContent = 'Browsers cannot auto-attach files to Gmail; download this draft and attach it in Gmail.';
    eml.append(note);
  } else {
    const note = document.createElement('span');
    note.className = 'missing-note';
    note.textContent = 'No email draft package for this role.';
    eml.append(note);
  }
}

function renderOverlayActions(role) {
  const main = $('#ov-main-action');
  const appliedButton = $('#ov-job-applied');
  const message = $('#ov-action-message');
  const currentStage = stageFor(role);
  main.classList.remove('disabled');
  if (appliedButton) {
    const evidence = submissionDocumentEvidence(role, selectedTemplateFor(role));
    const canRecordSubmission = Boolean(evidence.document_pdf && evidence.document_sha256);
    appliedButton.disabled = currentStage === 'applied' || !canRecordSubmission;
    appliedButton.textContent = currentStage === 'applied' ? 'Applied ✓' : 'Job applied';
    appliedButton.title = currentStage === 'applied'
      ? 'This job already has an owner-confirmed applied/sent state.'
      : canRecordSubmission
        ? `Record this job as applied using the selected ${templateLabel(evidence.template_id)} resume and save its immutable submission evidence.`
        : 'Generate/select a resume PDF before recording this job as applied.';
    appliedButton.onclick = appliedButton.disabled ? null : () => moveRole(role, 'applied', true);
  }
  if (message) {
    message.textContent = currentStage === 'ready_review'
      ? 'Your application package is ready for review. Open the verified route when you are satisfied.'
      : currentStage === 'manual_review_needed'
        ? 'This role needs owner review before the package can move to Ready for review.'
        : currentStage === 'applied'
          ? 'This application is recorded as sent or submitted. Reopen review if you need to revise the package.'
          : currentStage === 'inactive'
            ? 'This role is closed or inactive. Reopen it only if the vacancy becomes actionable again.'
            : 'Review the fit, evidence and route before asking Career Engine to prepare the application package.';
  }
  const emailWithRecipient = role.route === 'email' && hasGenuineRecipient(role);
  if (emailWithRecipient) {
    main.textContent = 'Open Gmail';
    main.title = 'Opens Gmail using the hameedo@gmail.com draft mailbox and requests hameedfarah@gmail.com as the employer-facing From identity. Nothing is sent automatically.';
    main.onclick = () => openGmailCompose(role);
  } else if (role.application_url) {
    main.textContent = 'Open application portal';
    main.title = 'Opens the official application page in a new tab and records only that the portal was opened. It does not mark the application submitted.';
    main.onclick = () => openTrackedPortal(role, 'kanban_overlay');
  } else if (role.route === 'email') {
    main.textContent = 'No verified recipient';
    main.classList.add('disabled');
    main.title = 'This email-route job has no verified recipient; no draft can be prepared.';
    main.onclick = null;
  } else {
    main.textContent = 'No application link';
    main.classList.add('disabled');
    main.title = 'No official application link is recorded for this job.';
    main.onclick = null;
  }
  const secondary = $('#ov-secondary');
  secondary.replaceChildren();
  if (role.route !== 'email' && hasGenuineRecipient(role)) {
    secondary.append(makeButton('Open Gmail draft (new tab)', () => openGmailCompose(role), 'email'));
  }
  if (role.email_file) secondary.append(makeLink('Download draft (.eml)', role.email_file));
  if (role.source_url && role.source_url !== role.application_url) secondary.append(makeLink('Vacancy source', role.source_url));
}

function renderOverlayEmail(role) {
  setTextSafe('ov-email-meta', role.route === 'email'
    ? `To: ${role.recipient || 'No verified recipient'} · ${role.email_subject || ''}`
    : 'Portal application · cover text retained for reference');
  const body = $('#ov-email-body');
  if (body) body.value = cleanedEmailBody(role) || 'Cover text unavailable.';
  const copy = $('#ov-cover-copy');
  if (copy && body) {
    copy.onclick = async () => {
      body.focus();
      body.select();
      body.setSelectionRange(0, body.value.length);
      try {
        await navigator.clipboard.writeText(body.value);
        copy.textContent = 'Copied';
      } catch {
        document.execCommand('copy');
        copy.textContent = 'Copied';
      }
      setTimeout(() => { if (copy.isConnected) copy.textContent = 'Copy text'; }, 1400);
    };
  }
  const coverPdf = $('#ov-cover-pdf-download');
  if (coverPdf) {
    coverPdf.hidden = !role.cover_letter?.pdf;
    if (role.cover_letter?.pdf) coverPdf.href = role.cover_letter.pdf;
    else coverPdf.removeAttribute('href');
  }
}

function submissionRecordsForRole(roleKey) {
  const lifecycle = state.history
    .filter(record => record.role_key === roleKey
      && (SUBMISSION_HISTORY_EVENTS.has(normalizedStatus(record.event))
        || SUBMISSION_RETRACTION_EVENTS.has(normalizedStatus(record.event))))
    .sort((a, b) => String(a.retracted_at || a.submitted_at || a.createdAt || '').localeCompare(String(b.retracted_at || b.submitted_at || b.createdAt || '')));
  const lastRetraction = lifecycle.map(record => normalizedStatus(record.event)).lastIndexOf('application_submission_retracted');
  return lifecycle.slice(lastRetraction + 1)
    .filter(record => SUBMISSION_HISTORY_EVENTS.has(normalizedStatus(record.event)));
}

function submissionEvidenceFromRecord(record) {
  let note = {};
  try { note = record.note ? JSON.parse(record.note) : {}; } catch { note = {}; }
  return { ...record, ...note };
}

function renderOverlaySubmissionRecord(role) {
  const section = $('#ov-submission-record-section');
  const target = $('#ov-submission-record');
  if (!section || !target) return;
  const records = submissionRecordsForRole(role.key);
  const archived = latestSubmissionHistoryState(role) === 'retracted' ? null : (role.submitted_package || null);
  const latest = archived
    ? {
        history_event_id: archived.history_event_id || '',
        submitted_at: archived.submitted_at || '',
        company: archived.company || role.company,
        role: archived.role || role.role,
        route: archived.route || role.route,
        application_url: archived.application_url || role.application_url,
        confirmation_reference: archived.confirmation_reference || '',
        template_id: archived.resume?.template_id || '',
        document_pdf: archived.resume?.pdf || '',
        document_docx: archived.resume?.docx || '',
        document_sha256: archived.resume?.sha256 || '',
        document_text: archived.resume?.text || '',
        cover_letter_pdf: archived.cover_letter?.pdf || '',
        cover_letter_docx: archived.cover_letter?.docx || '',
        cover_letter_sha256: archived.cover_letter?.sha256 || '',
        cover_letter_text: archived.cover_letter?.text || '',
        evidence_source: 'career_engine_submission_archive'
      }
    : (records.length ? submissionEvidenceFromRecord(records.at(-1)) : null);
  section.hidden = !latest;
  target.replaceChildren();
  if (!latest) return;

  const meta = document.createElement('div');
  meta.className = 'submission-record-grid';
  const rows = [
    ['Applied', formatDate(latest.submitted_at || latest.createdAt)],
    ['Company', latest.company || role.company],
    ['Job', latest.role || role.role],
    ['Resume', templateLabel(latest.template_id || selectedTemplateFor(role))],
    ['Resume SHA-256', latest.document_sha256 || 'Not recorded'],
    ['Cover SHA-256', latest.cover_letter_sha256 || 'Not recorded']
  ];
  for (const [label, value] of rows) {
    const row = document.createElement('div');
    const term = document.createElement('strong');
    term.textContent = label;
    const detail = document.createElement('span');
    detail.textContent = value || '—';
    row.append(term, detail);
    meta.append(row);
  }
  target.append(meta);

  const links = document.createElement('div');
  links.className = 'doc-links submission-record-links';
  if (latest.document_pdf) {
    const resume = makeLink('Submitted CV PDF', latest.document_pdf, 'primary');
    resume.download = '';
    links.append(resume);
  }
  if (latest.cover_letter_pdf) {
    const cover = makeLink('Submitted cover PDF', latest.cover_letter_pdf);
    cover.download = '';
    links.append(cover);
  }
  if (links.childElementCount) target.append(links);

  if (latest.document_text) {
    const label = document.createElement('strong');
    label.className = 'submission-text-label';
    label.textContent = 'Submitted resume text snapshot';
    const text = document.createElement('textarea');
    text.className = 'submission-text-snapshot';
    text.readOnly = true;
    text.value = latest.document_text;
    text.setAttribute('aria-label', 'Exact submitted resume text snapshot');
    const copy = makeButton('Copy resume text', async () => {
      text.focus();
      text.select();
      text.setSelectionRange(0, text.value.length);
      try { await navigator.clipboard.writeText(text.value); }
      catch { document.execCommand('copy'); }
      showToast('Submitted resume text copied.');
    });
    target.append(label, text, copy);
  }

  const note = document.createElement('p');
  note.className = 'help-note';
  note.textContent = archived
    ? `Exact submitted package archived permanently by Career Engine · ${records.length || 1} confirmed submission record${(records.length || 1) === 1 ? '' : 's'} for this job.`
    : `Historical submission confirmation exists, but the exact submitted file archive is not available yet. Recorded hashes are shown above; do not substitute the current CV.`;
  target.append(note);
}

function renderOverlayComments(role) {
  const list = commentsForRole(role.key);
  const unresolved = list.filter(item => item.resolved !== true).length;
  const count = $('#ov-comment-count');
  if (count) {
    count.textContent = `${unresolved} unresolved · ${list.length} total`;
    count.className = `tag ${unresolved ? 'tag-open' : 'tag-resolved'}`;
    count.title = unresolved ? 'Unresolved comments need attention.' : 'All comments resolved.';
  }
  const target = $('#ov-comments');
  target.replaceChildren();
  if (!list.length) {
    const empty = document.createElement('p');
    empty.className = 'empty-note';
    empty.textContent = 'No comments yet.';
    target.append(empty);
    return;
  }
  const showAll = state.commentsFull.has(role.key);
  const recent = showAll ? list : list.slice(-5);
  for (const record of recent) {
    const item = document.createElement('div');
    item.className = 'comment-item';
    const head = document.createElement('div');
    head.className = 'comment-head';
    const typeBadge = document.createElement('span');
    typeBadge.className = 'tag tag-type';
    typeBadge.textContent = commentTypeLabel(record.comment_type || 'note');
    const time = document.createElement('span');
    time.className = 'comment-time';
    time.textContent = formatDate(record.createdAt);
    head.append(typeBadge, time);
    const body = document.createElement('p');
    body.className = 'comment-body';
    body.textContent = record.comment || '';
    const foot = document.createElement('div');
    foot.className = 'comment-foot';
    const stateChip = document.createElement('span');
    stateChip.className = `tag tag-${record.resolved ? 'resolved' : 'open'}`;
    stateChip.textContent = record.resolved ? 'Resolved' : 'Unresolved';
    const toggle = makeButton(record.resolved ? 'Mark unresolved' : 'Mark resolved', () => toggleOverlayCommentResolution(role, record), 'quiet');
    foot.append(stateChip, toggle);
    item.append(head, body, foot);
    target.append(item);
  }
  if (list.length > 5 && !showAll) {
    target.append(makeButton(`Show all ${list.length} comments`, () => {
      state.commentsFull.add(role.key);
      renderOverlayComments(role);
    }, 'quiet'));
  }
}

async function toggleOverlayCommentResolution(role, record) {
  const next = !record.resolved;
  try {
    const updated = await patchRecord('comments', record.id, { resolved: next });
    const index = state.comments.findIndex(item => item.id === record.id);
    if (index >= 0) state.comments[index] = { ...state.comments[index], ...dataOf(updated), id: record.id, updatedAt: updated.updatedAt || state.comments[index].updatedAt };
    renderOverlayComments(role);
    showToast(`Comment marked ${next ? 'resolved' : 'unresolved'}.`);
  } catch (error) {
    showToast(error.message, true);
  }
}

function renderOverlayAi(role) {
  const target = $('#ov-ai');
  target.replaceChildren();
  const list = aiRequestsForRole(role.key);
  if (!list.length) {
    const empty = document.createElement('p');
    empty.className = 'empty-note';
    empty.textContent = 'No AI requests queued for this job.';
    target.append(empty);
    return;
  }
  for (const record of list.slice(0, 12).reverse()) {
    const data = dataOf(record);
    const item = document.createElement('div');
    item.className = 'ai-item';
    const head = document.createElement('div');
    head.className = 'comment-head';
    const typeBadge = document.createElement('span');
    typeBadge.className = 'tag tag-type';
    typeBadge.textContent = aiTypeLabel(data.request_type);
    const stateChip = document.createElement('span');
    const requestState = data.state || 'pending';
    stateChip.className = `tag tag-state-${requestState}`;
    stateChip.textContent = requestState;
    const time = document.createElement('span');
    time.className = 'comment-time';
    time.textContent = formatDate(record.createdAt);
    head.append(typeBadge, stateChip, time);
    const prompt = document.createElement('p');
    prompt.className = 'comment-body';
    prompt.textContent = data.prompt || '';
    item.setAttribute('data-request-id', record.id || '');
    const answerText = data.answer || data.response || data.result || data.output || '';
    if (answerText) {
      const answer = document.createElement('div');
      answer.className = 'assistant-answer';
      const label = data.validation_status === 'success' ? 'Validation passed' : data.validation_status === 'failure' ? 'Validation failed' : 'Assistant reply';
      answer.innerHTML = `<strong>${label}</strong>`;
      const body = document.createElement('div');
      body.className = 'comment-body';
      body.textContent = answerText;
      answer.append(body);
      if (data.owner_input_needed) {
        const owner = document.createElement('p');
        owner.className = 'owner-input-needed';
        owner.textContent = 'Owner input needed — provide the missing factual or personal detail before using this answer.';
        answer.append(owner);
      }
      const copyAnswer = makeButton('Copy answer', async () => {
        try { await navigator.clipboard.writeText(answerText); showToast('Answer copied.'); }
        catch { showToast('Clipboard unavailable — select and copy the answer manually.', true); }
      }, 'quiet');
      answer.append(copyAnswer);
      item.append(answer);
    } else if (data.owner_input_needed) {
      const owner = document.createElement('p');
      owner.className = 'owner-input-needed';
      owner.textContent = 'Owner input needed — the assistant cannot answer this safely yet.';
      item.append(owner);
    }
    const foot = document.createElement('div');
    foot.className = 'comment-foot';
    const copy = makeButton('Copy handoff', async () => {
      const snippet = [
        'CAREER ENGINE AI REQUEST (handoff)',
        `Role key: ${role.key}`,
        `Company: ${role.company} — ${role.role}`,
        `Request type: ${data.request_type}`,
        `Request id: ${record.id}`,
        'Prompt:',
        data.prompt || ''
      ].join('\n');
      try {
        await navigator.clipboard.writeText(snippet);
        showToast('Handoff copied to clipboard.');
      } catch {
        showToast('Clipboard unavailable — select and copy the text manually.', true);
      }
    }, 'quiet');
    foot.append(copy);
    item.append(head, prompt, foot);
    target.append(item);
  }
}

function setupOverlayForms(role) {
  const commentSelect = $('#ov-comment-type');
  populateSelect(commentSelect, COMMENT_TYPES.map(type => ({ value: type.id, label: type.label })), 'note');
  $('#ov-comment-form').onsubmit = async event => {
    event.preventDefault();
    const text = $('#ov-comment-text').value.trim();
    const status = $('#ov-comment-status');
    if (!text) {
      status.textContent = 'Enter a comment first.';
      return;
    }
    status.textContent = 'Saving…';
    try {
      const record = await createRecord('comments', {
        role_key: role.key,
        comment: text,
        comment_type: commentSelect.value,
        resolved: false
      }, `comment-${role.key}-${Date.now()}`);
      state.comments.push({ id: record.id, ...dataOf(record), createdAt: record.createdAt, updatedAt: record.updatedAt });
      await createRecord('history', { role_key: role.key, event: 'comment_added', note: `${commentTypeLabel(commentSelect.value)}: ${text.slice(0, 80)}` }, `history-c-${Date.now()}`);
      $('#ov-comment-text').value = '';
      status.textContent = 'Saved (append-only).';
      renderOverlayComments(role);
    } catch (error) {
      status.textContent = error.message;
    }
  };
  let requestType = 'other';
  const prompts = { headline: 'Create a concise, evidence-based headline for this job application.', summary: 'Draft a concise application summary using only the existing Career Engine package.', skills: 'Identify the strongest relevant skills from the existing package and job description.', current_role: 'Explain how the current role should be presented for this job. Mark missing facts as Owner input needed.', experience: 'Select relevant experience for this job. Do not invent dates, employers, titles, or achievements.', cover_letter: 'Draft or improve the cover letter using only verified package facts and this job description.', application_question: 'Prepare a copy-ready answer for the application question, or mark it Owner input needed when facts are missing.', edit_cv: 'Regenerate and validate the Career Engine package for this job. External submission remains blocked.' };
  for (const button of document.querySelectorAll('[data-overlay-assistant-action]')) button.addEventListener('click', () => { requestType = button.dataset.overlayAssistantAction; $('#ov-ai-prompt').value = prompts[requestType]; $('#ov-ai-prompt').focus(); });
  $('#ov-ai-form').onsubmit = async event => {
    event.preventDefault();
    const prompt = $('#ov-ai-prompt').value.trim();
    const status = $('#ov-ai-status');
    if (!prompt) {
      status.textContent = 'Describe the request first.';
      return;
    }
    status.textContent = 'Queueing…';
    try {
      const record = await createRecord('ai_requests', {
        role_key: role.key,
        request_type: requestType === 'application_question' ? 'screening_question' : requestType,
        prompt: requestType === 'edit_cv' ? prompt : `FIELD:${requestType === 'application_question' ? 'screening_question' : requestType}\n${prompt}`,
        state: 'pending'
      }, `ai-${role.key}-${Date.now()}`);
      state.aiRequests.push({ id: record.id, ...dataOf(record), createdAt: record.createdAt, updatedAt: record.updatedAt });
      await createRecord('history', { role_key: role.key, event: 'ai_requested', note: `${aiTypeLabel(requestType)}: ${prompt.slice(0, 80)}` }, `history-ai-${Date.now()}`);
      $('#ov-ai-prompt').value = '';
      status.textContent = 'Queued as pending.';
      renderOverlayAi(role);
      setupAiPolling(role);
    } catch (error) {
      status.textContent = error.message;
    }
  };
}

function setupAiPolling(role) {
  if (state.aiPollTimer) clearInterval(state.aiPollTimer);
  state.aiPollTimer = setInterval(async () => {
    if (!state.overlayOpen || state.overlayKey !== role.key) return;
    const pending = aiRequestsForRole(role.key).some(record => ['pending', 'processing'].includes(dataOf(record).state || 'pending'));
    if (!pending) { clearInterval(state.aiPollTimer); state.aiPollTimer = null; return; }
    try {
      const records = await loadCollection('ai_requests', 300, true, true);
      state.aiRequests = records.map(record => ({ id: record.id, ...dataOf(record), createdAt: record.createdAt, updatedAt: record.updatedAt }));
      renderOverlayAi(role);
    } catch (error) { console.warn('AI request refresh unavailable', error); }
  }, 3000);
}

async function ensureOverlayData(role) {
  if (!state.overlayHydrationPromise) {
    state.overlayHydrationPromise = Promise.all([
      loadComments(),
      loadHistory(),
      loadAiRequests(),
      loadTemplates()
    ]).finally(() => {
      // Keep loaded state in memory but allow a future overlay session to
      // refresh dynamic Site Data if the owner or assistant changed it.
      state.overlayHydrationPromise = null;
    });
  }
  await state.overlayHydrationPromise;
  renderOverlayIfOpen();
  if (role && aiRequestsForRole(role.key).some(record => ['pending', 'processing'].includes(dataOf(record).state || 'pending'))) {
    setupAiPolling(role);
  }
}

function setTextSafe(id, value) {
  const node = $(`#${id}`);
  if (node) node.textContent = value == null || value === '' ? '—' : value;
}

async function init() {
  setupControls();
  applyTheme(localStorage.getItem(THEME_PREF_KEY) || 'executive-navy');
  try {
    const response = await fetch('data/jobs.json', { cache: 'no-store' });
    if (!response.ok) throw new Error(`Unable to load dashboard (${response.status})`);
    state.data = await response.json();
    state.lastScanAt = state.data.generated_at || new Date().toISOString();
    state.roles = mergeRoles(state.data);
    renderBoard();
    updateScanClock();
    setupGlobalOperationPolling();
    setupOverlay();
    setInterval(updateScanClock, 60000);
    // Only owner workflow/preferences hydrate after first paint. Comments,
    // history, AI requests and ATS design options stay fully lazy until a job
    // detail is opened, keeping them out of the initial network waterfall.
    const hydrateBoardState = async () => {
      await Promise.all([loadWorkflow(), loadPreferences()]);
      const savedTheme = state.preferences.get(THEME_PREF_KEY)?.value;
      if (savedTheme) applyTheme(savedTheme);
      renderBoard();
    };
    const idle = window.requestIdleCallback || (callback => setTimeout(callback, 50));
    idle(() => hydrateBoardState().catch(error => console.warn('deferred board state unavailable', error)));
    const deepLink = new URLSearchParams(window.location.search).get('job');
    if (deepLink) openOverlay(deepLink, false);
  } catch (error) {
    const board = $('#board');
    const message = document.createElement('div');
    message.className = 'board-error';
    message.textContent = error.message;
    board.replaceChildren(message);
  }
}

async function loadAiRequests() {
  state.aiRequests = (await loadCollection('ai_requests')).map(record => ({ id: record.id, ...dataOf(record), createdAt: record.createdAt, updatedAt: record.updatedAt }));
}

async function loadTemplates() {
  try {
    const response = await fetch('data/ats-design-options.json', { cache: 'no-store' });
    if (response.ok) state.templatesData = await response.json();
  } catch (error) { console.warn('ats-design-options unavailable', error); }
}

init();

window.addEventListener('popstate', () => {
  const key = new URLSearchParams(window.location.search).get('job');
    if (key) openOverlay(key, false);
  else if (state.overlayOpen) closeOverlay();
});
