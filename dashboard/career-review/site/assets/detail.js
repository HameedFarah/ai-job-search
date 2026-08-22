/* detail.js — Career Engine job detail view (detail.html?job=<key>) */
'use strict';

const state = {
  data: null,
  role: null,
  workflow: new Map(),
  comments: [],
  history: [],
  aiRequests: [],
  preferences: new Map(),
  templatesData: null,
  aiPollTimer: null
};

function roleKey() {
  return new URLSearchParams(window.location.search).get('job') || '';
}

function showError(message) {
  $('#detail-error').hidden = false;
  $('#detail-error').textContent = message;
  $('#detail').hidden = true;
}

function setText(id, value) {
  const node = $(`#${id}`);
  if (node) node.textContent = value == null || value === '' ? '—' : value;
}

function setKvDate(id, value, suffix = '') {
  const node = $(`#${id}`);
  if (!node) return;
  if (parseDate(value)) {
    node.textContent = `${formatDate(value)}${suffix}`;
  } else {
    node.textContent = 'Unknown';
  }
}

function buildDocLinks(target, docs, kindLabel) {
  target.replaceChildren();
  if (!docs || (!docs.pdf && !docs.docx)) {
    const note = document.createElement('span');
    note.className = 'missing-note';
    note.textContent = `No ${kindLabel} generated for this role.`;
    target.append(note);
    return;
  }
  appendFileLink(target, `${kindLabel} PDF`, docs.pdf, true);
  appendFileLink(target, `${kindLabel} DOCX`, docs.docx);
}

function renderHeader() {
  const role = state.role;
  setText('d-role', role.role);
  setText('d-company', role.company);
  setText('d-location', role.location || 'Location not stated');

  const decisionValue = normalizeDecision(role.decision);
  const decision = $('#d-decision');
  decision.textContent = humanDecision(decisionValue);
  decision.classList.add(decisionValue.replaceAll('_', '-'));

  const scoreNode = $('#d-score strong');
  scoreNode.textContent = role.score ?? '—';

  const bucket = scoreBucket(role.score);
  const scoreTag = $('#d-score-tag');
  scoreTag.textContent = `${bucket.label} fit`;
  scoreTag.className = `tag tag-${bucket.cls}`;
  scoreTag.title = role.score == null ? 'No score recorded.' : `Fit score ${role.score}/100.`;
}

function renderDates() {
  const role = state.role;
  const posted = $('#d-posted');
  if (parseDate(role.posting_date)) {
    posted.textContent = formatDay(role.posting_date);
    posted.title = `Precision: ${role.posting_date_precision || 'unknown'} · Source: ${role.posting_date_source || 'unknown'}`;
  } else {
    posted.textContent = 'Unknown (not stated in source data)';
    posted.title = 'No posting date was present in the scan data; the engine does not invent dates.';
  }
  setKvDate('d-found', role.found_at);
  setKvDate('d-scan', role.scanned_at || state.data?.generated_at);
}

function renderSummary() {
  const role = state.role;
  setText('d-brief', role.brief || 'No summary recorded.');
  addList($('#d-strengths'), role.strengths, 'No specific strengths recorded.');
  addList($('#d-gaps'), role.gaps, 'No material gap recorded.');
  setText('d-jd', role.full_job_description || 'Full job description not captured in the scan data.');
}

function renderDocuments() {
  const atsRecommended = normalizeTemplateId(state.role.recommended_resume_template) === 'ats-classic' || (state.role.route === 'portal' && state.role.resume_ats?.pdf);
  $('#d-ats-recommendation').hidden = !atsRecommended;
  $('#d-exec-recommendation').hidden = atsRecommended;
  buildDocLinks($('#d-resume-exec'), state.role.resume, 'Resume');
  buildDocLinks($('#d-resume-ats'), state.role.resume_ats, 'ATS resume');
  buildDocLinks($('#d-cover'), state.role.cover_letter, 'Cover letter');
  const eml = $('#d-eml');
  eml.replaceChildren();
  if (state.role.email_file) {
    eml.append(makeLink('Download draft (.eml)', state.role.email_file, 'primary'));
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

function renderActions() {
  const role = state.role;
  const target = $('#d-actions');
  target.replaceChildren();
  const emailWithRecipient = role.route === 'email' && hasGenuineRecipient(role);
  const main = makeButton(emailWithRecipient ? 'Open Gmail' : 'Open application portal', () => {
    if (emailWithRecipient) openGmailCompose(role);
    else openTrackedPortal(role, 'job_detail_primary');
  }, 'primary');
  if (!emailWithRecipient && !role.application_url) {
    main.disabled = true;
    main.textContent = role.route === 'email' ? 'No verified recipient' : 'No application link';
    main.title = role.route === 'email'
      ? 'No verified recipient is recorded for this email-route job; no draft can be prepared.'
      : 'No official application link is recorded for this job.';
  }
  target.append(main);
  if (emailWithRecipient && role.email_file) target.append(makeLink('Attached draft (.eml)', role.email_file));
  if (!emailWithRecipient && role.application_url) target.append(makeButton('Open portal (tracked)', () => openTrackedPortal(role, 'job_detail_secondary')));
  if (role.source_url && role.source_url !== role.application_url) target.append(makeLink('Vacancy source', role.source_url));
  const subject = role.email_subject || `Abdelhamid Farah - ${role.role}`;
  setText('d-email-meta', `Account: hameedo@gmail.com\nTo: ${role.route === 'email' ? (role.recipient || '(verified recipient required)') : '(not applicable — portal route)'}\nSubject: ${subject}`);
  $('#d-email-body').textContent = cleanedEmailBody(role) || 'Email text unavailable.';
}

function renderTemplate() {
  const role = state.role;
  const select = $('#d-template');
  const current = selectedTemplateFor(role);
  populateSelect(select, TEMPLATE_OPTIONS.map(option => ({ value: option.id, label: option.label })), current);
  select.onchange = () => saveDetailTemplate(role, select.value);
  const rule = $('#d-template-default');
  if (rule) rule.textContent = `Exactly one CV is used. Default for ${role.route === 'email' ? 'email' : 'portal'}: ${templateLabel(defaultTemplateFor(role))}. Your override persists per job and controls the attachment/upload selection.`;
  const avail = templateAvailability(role, state.templatesData || {});
  const files = $('#d-template-files');
  const note = $('#d-template-note');
  files.replaceChildren();
  note.textContent = '';
  const info = avail[current] || { generated: false, label: current, note: 'Template file not available.' };
  if (info.generated) {
    if (info.pdf) files.append(makeLink('Resume PDF', info.pdf, 'primary'));
    if (info.docx) files.append(makeLink('Resume DOCX', info.docx));
    if (info.note) note.textContent = info.note;
  } else {
    const msg = document.createElement('span');
    msg.className = 'missing-note warn-note';
    msg.textContent = `Generate selected template — ${info.note || 'not generated for this job yet.'}`;
    files.append(msg);
  }
}

async function saveDetailTemplate(role, templateId) {
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
    await createRecord('history', {
      role_key: role.key,
      event: 'resume_variant_override',
      from_stage: previousTemplate,
      to_stage: templateId,
      note: `Selected submission CV: ${templateLabel(templateId)}; route default: ${templateLabel(defaultTemplateFor(role))}`
    }, `history-${role.key}-resume-variant-${Date.now()}`);
    showToast(`Submission CV set to ${templateLabel(templateId)}. Exactly one CV will be attached or uploaded.`);
    await refreshTimeline();
    renderTemplate();
  } catch (error) {
    showToast(error.message, true);
  }
}

async function moveToStage(nextStage, requireConfirmation = false) {
  const role = state.role;
  if (stageFor(role) === nextStage) return;
  if (requireConfirmation) {
    try {
      if (!await confirmApplicationSubmitted(role, 'job_detail_stage_change')) return;
    } catch (error) {
      showToast(`Submission was not marked complete: ${error.message}`, true);
      return;
    }
  }
  const priorStage = stageFor(role);
  const current = state.workflow.get(role.key);
  try {
    const record = current?.id
      ? await patchRecord('workflow', current.id, {
          stage: nextStage, role_key: role.key, route: role.route || '', company: role.company, role: role.role
        })
      : await createRecord('workflow', {
          role_key: role.key, stage: nextStage, route: role.route || '', company: role.company, role: role.role
        }, `${role.key}-${nextStage}`);
    state.workflow.set(role.key, { id: record.id || current?.id, ...(dataOf(record) || {}), createdAt: record.createdAt, updatedAt: record.updatedAt || new Date().toISOString() });
    await createRecord('history', { role_key: role.key, event: 'stage_change', from_stage: priorStage, to_stage: nextStage }, `history-${role.key}-${Date.now()}`);
    await refreshTimeline();
    renderStage();
    showToast(`Moved to ${STAGES.find(stage => stage.id === nextStage)?.label}.`);
  } catch (error) {
    showToast(error.message, true);
  }
}

function renderStage() {
  const role = state.role;
  const stageId = stageFor(role);
  const stage = STAGES.find(item => item.id === stageId);
  const select = $('#d-stage-select');
  select.replaceChildren();
  for (const item of STAGES) {
    const option = document.createElement('option');
    option.value = item.id;
    option.textContent = item.label;
    option.selected = item.id === stageId;
    select.append(option);
  }
  select.onchange = () => moveToStage(select.value, select.value === 'applied');
  const action = $('#d-stage-action');
  action.replaceChildren();
  if (!stage) return;
  if (stage.id === 'ready_review') {
    if (role.route === 'email') {
      if (hasGenuineRecipient(role)) action.append(makeButton('Open Gmail', () => openGmailCompose(role), 'email'));
      if (role.application_url) action.append(makeLink('Recruitment route', role.application_url));
    } else if (role.application_url) {
      action.append(makeButton('Open application portal', () => openTrackedPortal(role, 'job_detail_stage'), 'primary'));
    }
    action.append(makeButton(role.route === 'email' ? 'Confirm sent' : 'Confirm submitted', () => moveToStage('applied', true), 'success'));
  } else if (stage.id === 'applied') {
    action.append(makeButton('Reopen review', () => moveToStage('ready_review')));
  } else {
    action.append(makeButton(stage.nextLabel, () => moveToStage(stage.next), 'primary'));
  }
}

function renderTimeline(events) {
  const target = $('#d-timeline');
  target.replaceChildren();
  if (!events.length) {
    const empty = document.createElement('p');
    empty.className = 'empty-note';
    empty.textContent = 'No activity recorded yet.';
    target.append(empty);
    return;
  }
  for (const event of events) {
    const item = document.createElement('div');
    item.className = 'timeline-item';
    const title = document.createElement('strong');
    const labels = {
      stage_change: 'Stage change',
      comment_added: 'Comment added',
      ai_requested: 'AI request queued',
      portal_opened: 'Application portal opened (not submission)',
      application_submitted: 'Application submitted - owner confirmed',
      email_sent_owner_confirmed: 'Application email sent - owner confirmed',
      submission_email_confirmation: 'Submission confirmation email matched',
      recruiter_reply_received: 'Recruiter reply received',
      resume_variant_override: 'Submission CV override'
    };
    title.textContent = labels[event.event] || humanDecision(event.event);
    const meta = document.createElement('span');
    meta.className = 'timeline-meta';
    const from = STAGES.find(s => s.id === event.from_stage)?.label || event.from_stage;
    const to = STAGES.find(s => s.id === event.to_stage)?.label || event.to_stage;
    meta.textContent = `${from || ''}${from && to ? ' → ' : ''}${to || ''} · ${relativeTime(event.createdAt)}`;
    item.append(title, meta);
    target.append(item);
  }
}

function renderComments() {
  const target = $('#d-comments');
  target.replaceChildren();
  const list = [...state.comments].sort((a, b) => String(a.createdAt).localeCompare(String(b.createdAt)));
  if (!list.length) {
    const empty = document.createElement('p');
    empty.className = 'empty-note';
    empty.textContent = 'No comments yet.';
    target.append(empty);
    return;
  }
  for (const record of list) {
    const data = dataOf(record);
    const item = document.createElement('div');
    item.className = 'comment-item';
    const head = document.createElement('div');
    head.className = 'comment-head';
    const typeBadge = document.createElement('span');
    typeBadge.className = 'tag tag-type';
    typeBadge.textContent = commentTypeLabel(data.comment_type || 'note');
    const time = document.createElement('span');
    time.className = 'comment-time';
    time.textContent = formatDate(record.createdAt);
    head.append(typeBadge, time);
    const body = document.createElement('p');
    body.className = 'comment-body';
    body.textContent = data.comment || '';
    const foot = document.createElement('div');
    foot.className = 'comment-foot';
    const stateChip = document.createElement('span');
    stateChip.className = `tag tag-${data.resolved ? 'resolved' : 'open'}`;
    stateChip.textContent = data.resolved ? 'Resolved' : 'Unresolved';
    const toggle = makeButton(data.resolved ? 'Mark unresolved' : 'Mark resolved', () => toggleResolution(record), 'quiet');
    foot.append(stateChip, toggle);
    item.append(head, body, foot);
    target.append(item);
  }
}

async function toggleResolution(record) {
  const data = dataOf(record);
  const next = !data.resolved;
  try {
    const updated = await patchRecord('comments', record.id, { resolved: next });
    const index = state.comments.findIndex(item => item.id === record.id);
    if (index >= 0) state.comments[index] = updated;
    renderComments();
    showToast(`Comment marked ${next ? 'resolved' : 'unresolved'}.`);
  } catch (error) {
    showToast(error.message, true);
  }
}

function setupCommentForm() {
  const select = $('#d-comment-type');
  for (const type of COMMENT_TYPES) {
    const option = document.createElement('option');
    option.value = type.id;
    option.textContent = type.label;
    select.append(option);
  }
  $('#d-comment-form').addEventListener('submit', async event => {
    event.preventDefault();
    const text = $('#d-comment-text').value.trim();
    const status = $('#d-comment-status');
    if (!text) {
      status.textContent = 'Enter a comment first.';
      return;
    }
    status.textContent = 'Saving…';
    try {
      const record = await createRecord('comments', {
        role_key: state.role.key,
        comment: text,
        comment_type: select.value,
        resolved: false
      }, `comment-${state.role.key}-${Date.now()}`);
      state.comments.push(record);
      await createRecord('history', { role_key: state.role.key, event: 'comment_added', note: `${commentTypeLabel(select.value)}: ${text.slice(0, 80)}` }, `history-c-${Date.now()}`);
      $('#d-comment-text').value = '';
      status.textContent = 'Saved (append-only).';
      renderComments();
      refreshTimeline();
    } catch (error) {
      status.textContent = error.message;
    }
  });
}

function renderAiRequests() {
  const target = $('#d-ai-requests');
  target.replaceChildren();
  const list = [...state.aiRequests].sort((a, b) => String(b.createdAt).localeCompare(String(a.createdAt)));
  if (!list.length) {
    const empty = document.createElement('p');
    empty.className = 'empty-note';
    empty.textContent = 'No AI requests queued for this job.';
    target.append(empty);
    return;
  }
  for (const record of list) {
    const data = dataOf(record);
    const item = document.createElement('div');
    item.className = 'ai-item';
    const head = document.createElement('div');
    head.className = 'comment-head';
    const typeBadge = document.createElement('span');
    typeBadge.className = 'tag tag-type';
    typeBadge.textContent = aiTypeLabel(data.request_type);
    const stateChip = document.createElement('span');
    stateChip.className = `tag tag-state-${data.state || 'pending'}`;
    stateChip.textContent = data.state || 'pending';
    const time = document.createElement('span');
    time.className = 'comment-time';
    time.textContent = formatDate(record.createdAt);
    head.append(typeBadge, stateChip, time);
    const prompt = document.createElement('p');
    prompt.className = 'comment-body assistant-prompt';
    prompt.textContent = data.prompt || '';
    item.append(head, prompt);
    const answerText = data.answer || data.response || data.result || data.output || '';
    if (answerText) {
      const answer = document.createElement('div');
      answer.className = 'assistant-answer';
      answer.innerHTML = `<strong>${data.validation_status === 'success' ? 'Validation passed' : data.validation_status === 'failure' ? 'Validation failed' : 'Assistant reply'}</strong>`;
      const body = document.createElement('p');
      body.className = 'comment-body';
      body.textContent = answerText;
      answer.append(body);
      if (data.owner_input_needed) {
        const owner = document.createElement('p');
        owner.className = 'owner-input-needed';
        owner.textContent = 'Owner input needed — this answer contains no invented personal or factual details.';
        answer.append(owner);
      }
      item.append(answer);
    }
    const foot = document.createElement('div');
    foot.className = 'comment-foot';
    const copy = makeButton('Copy handoff', async () => {
      const snippet = [
        'CAREER ENGINE AI REQUEST (handoff)',
        `Role key: ${state.role.key}`,
        `Company: ${state.role.company} — ${state.role.role}`,
        `Request type: ${data.request_type}`,
        `Request id: ${record.id}`,
        `Prompt:`,
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
    item.append(foot);
    target.append(item);
  }
}

async function refreshAiRequests() {
  const records = await loadCollection('ai_requests', 300, true, true);
  state.aiRequests = records.filter(record => dataOf(record).role_key === state.role.key);
  renderAiRequests();
  refreshTimeline();
  return state.aiRequests;
}

function setupAiPolling() {
  if (state.aiPollTimer) clearInterval(state.aiPollTimer);
  state.aiPollTimer = setInterval(async () => {
    if (!state.aiRequests.some(record => (dataOf(record).state || 'pending') === 'pending' || dataOf(record).state === 'processing')) {
      clearInterval(state.aiPollTimer);
      state.aiPollTimer = null;
      return;
    }
    try { await refreshAiRequests(); } catch (error) { console.warn('AI request refresh unavailable', error); }
  }, 5000);
}

function setupAiForm() {
  let requestType = 'other';
  const prompts = {
    headline: 'Create a concise, evidence-based headline for this job application.',
    summary: 'Draft a concise application summary using only the existing Career Engine package.',
    skills: 'Identify the strongest relevant skills from the existing package and job description.',
    current_role: 'Explain how the current role should be presented for this job. Mark missing facts as Owner input needed.',
    experience: 'Select relevant experience for this job. Do not invent dates, employers, titles, or achievements.',
    cover_letter: 'Draft or improve the cover letter using only verified package facts and this job description.',
    application_question: 'Prepare a copy-ready answer for the application question, or mark it Owner input needed when facts are missing.',
    edit_cv: 'Regenerate the Career Engine package for this job, validate the output, and report validation success or failure.'
  };
  for (const button of document.querySelectorAll('[data-assistant-action]')) {
    button.addEventListener('click', () => {
      requestType = button.dataset.assistantAction || 'other';
      $('#d-ai-prompt').value = prompts[requestType] || '';
      $('#d-ai-prompt').focus();
    });
  }
  $('#d-ai-form').addEventListener('submit', async event => {
    event.preventDefault();
    const prompt = $('#d-ai-prompt').value.trim();
    const status = $('#d-ai-status');
    if (!prompt) {
      status.textContent = 'Describe the request first.';
      return;
    }
    status.textContent = 'Queueing…';
    try {
      const backendField = requestType === 'application_question' ? 'screening_question' : requestType;
      const backendPrompt = requestType === 'edit_cv'
        ? prompt
        : `FIELD:${backendField}\n${prompt}`;
      const record = await createRecord('ai_requests', {
        role_key: state.role.key,
        request_type: requestType === 'application_question' ? 'screening_question' : requestType,
        prompt: backendPrompt,
        state: 'pending'
      }, `ai-${state.role.key}-${Date.now()}`);
      state.aiRequests.push(record);
      await createRecord('history', { role_key: state.role.key, event: 'ai_requested', note: `${humanDecision(requestType)}: ${prompt.slice(0, 80)}` }, `history-ai-${Date.now()}`);
      $('#d-ai-prompt').value = '';
      status.textContent = 'Queued as pending.';
      renderAiRequests();
      setupAiPolling();
      refreshTimeline();
    } catch (error) {
      status.textContent = error.message;
    }
  });
}

async function refreshTimeline() {
  const events = (await loadCollection('history')).filter(record => dataOf(record).role_key === state.role.key);
  state.history = events.sort((a, b) => String(a.createdAt).localeCompare(String(b.createdAt))).reverse();
  renderTimeline(state.history);
}

async function loadDetailData() {
  const records = await loadCollection('workflow');
  for (const record of records) {
    const data = dataOf(record);
    const stage = data.stage === 'approved' ? 'ready_review' : data.stage;
    if (data.role_key) state.workflow.set(data.role_key, { id: record.id, ...data, stage, createdAt: record.createdAt, updatedAt: record.updatedAt });
  }
  const comments = await loadCollection('comments');
  state.comments = comments.filter(record => dataOf(record).role_key === state.role.key);
  const aiRequests = await loadCollection('ai_requests');
  state.aiRequests = aiRequests.filter(record => dataOf(record).role_key === state.role.key);
  await refreshTimeline();
}

async function init() {
  await initTheme();
  $('#back-button').addEventListener('click', () => {
    window.location.href = 'index.html';
  });
  const key = roleKey();
  if (!key) {
    showError('No job key provided. Use detail.html?job=<key>.');
    return;
  }
  try {
    const response = await fetch('data/jobs.json', { cache: 'no-store' });
    if (!response.ok) throw new Error(`Unable to load data (${response.status})`);
    state.data = await response.json();
    state.role = [...(state.data.applications || []), ...(state.data.reviewed || [])].find(role => role.key === key);
    if (!state.role) throw new Error(`Job "${key}" not found.`);
    try {
      const templatesResponse = await fetch('data/ats-design-options.json', { cache: 'no-store' });
      if (templatesResponse.ok) state.templatesData = await templatesResponse.json();
    } catch (error) {
      console.warn('ats-design-options unavailable', error);
    }
    await loadDetailData();
    renderHeader();
    renderDates();
    renderSummary();
    renderTemplate();
    renderDocuments();
    renderActions();
    renderStage();
    renderComments();
    setupCommentForm();
    renderAiRequests();
    setupAiForm();
    setupAiPolling();
    $('#detail').hidden = false;
  } catch (error) {
    showError(error.message);
  }
}

init();
