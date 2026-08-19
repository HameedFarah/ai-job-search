/* add-job.js — owner-supplied vacancy intake for the Career Engine dashboard */
'use strict';

const ADD_JOB_ROLE_KEY = '__career_engine_add_job__';
const ADD_JOB_PROMPT_MAX_LENGTH = 8000;
const ADD_JOB_REQUEST_PARAM = 'add_job_request';
const ADD_JOB_SESSION_KEY = 'career-add-job-active';
let addJobPollTimer = null;
let activeAddJobRequestId = '';
let activeAddJobJobKey = '';
let activeAddJobStartedAt = 0;
let activeAddJobMeta = {};

function addJobProgressPayload(value) {
  if (!value || typeof value !== 'string' || !value.trim().startsWith('{')) return null;
  try {
    const parsed = JSON.parse(value);
    return parsed?.kind === 'add_job_progress' ? parsed : null;
  } catch {
    return null;
  }
}

function addJobProgressText(value) {
  const parsed = addJobProgressPayload(value);
  if (parsed) return parsed.message || humanDecision(parsed.phase || 'processing');
  return typeof value === 'string' ? value : '';
}

function setAddJobStatus(message, isError = false) {
  const status = $('#add-job-status');
  if (!status) return;
  status.textContent = message || '';
  status.classList.toggle('error', Boolean(isError));
}

function addJobElapsedText() {
  if (!activeAddJobStartedAt) return '';
  const seconds = Math.max(0, Math.round((Date.now() - activeAddJobStartedAt) / 1000));
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return minutes ? `${minutes}:${String(remainder).padStart(2, '0')}` : `${seconds}s`;
}

function addJobPhaseIndex(requestState, phase) {
  if (requestState === 'done') return 5;
  if (requestState === 'failed') return -1;
  if (requestState === 'pending') return 0;
  return ({ reading: 1, scoring: 2, created: 2, generating: 3, publishing: 4, blocked: 4 })[phase] ?? 1;
}

function escapeAddJobHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

function rememberAddJobRequest(requestId, meta = {}) {
  activeAddJobRequestId = requestId;
  activeAddJobMeta = { ...activeAddJobMeta, ...meta };
  if (!activeAddJobStartedAt) activeAddJobStartedAt = Date.now();
  sessionStorage.setItem(ADD_JOB_SESSION_KEY, JSON.stringify({
    requestId,
    jobKey: activeAddJobJobKey,
    startedAt: activeAddJobStartedAt,
    meta: activeAddJobMeta
  }));
}

function restoreAddJobRequest() {
  const requestId = new URLSearchParams(window.location.search).get(ADD_JOB_REQUEST_PARAM) || '';
  if (!requestId) return '';
  try {
    const saved = JSON.parse(sessionStorage.getItem(ADD_JOB_SESSION_KEY) || '{}');
    if (saved.requestId === requestId) {
      activeAddJobJobKey = saved.jobKey || '';
      activeAddJobStartedAt = Number(saved.startedAt || Date.now());
      activeAddJobMeta = saved.meta && typeof saved.meta === 'object' ? saved.meta : {};
    }
  } catch {
    activeAddJobStartedAt = Date.now();
  }
  if (!activeAddJobStartedAt) activeAddJobStartedAt = Date.now();
  activeAddJobRequestId = requestId;
  return requestId;
}

function syncAddJobUrl(requestId, jobKey = '') {
  const url = new URL(window.location.href);
  if (requestId) url.searchParams.set(ADD_JOB_REQUEST_PARAM, requestId);
  else url.searchParams.delete(ADD_JOB_REQUEST_PARAM);
  if (jobKey) url.searchParams.set('job', jobKey);
  history.replaceState({ addJobRequest: requestId, job: jobKey || undefined }, '', `${url.pathname}${url.search}${url.hash}`);
}

function clearAddJobProcessingView() {
  if (addJobPollTimer) {
    clearInterval(addJobPollTimer);
    addJobPollTimer = null;
  }
  activeAddJobRequestId = '';
  activeAddJobJobKey = '';
  activeAddJobStartedAt = 0;
  activeAddJobMeta = {};
  sessionStorage.removeItem(ADD_JOB_SESSION_KEY);
  const url = new URL(window.location.href);
  url.searchParams.delete(ADD_JOB_REQUEST_PARAM);
  if (!state?.overlayOpen) url.searchParams.delete('job');
  history.replaceState({}, '', `${url.pathname}${url.search}${url.hash}`);
  const overlay = $('#job-overlay');
  if (overlay && !state?.overlayOpen) {
    overlay.hidden = true;
    overlay.setAttribute('aria-hidden', 'true');
    document.documentElement.classList.remove('overlay-open');
    document.body.classList.remove('overlay-open');
  }
}

function renderAddJobProcessingOverlay(requestState, progress = null, answer = '') {
  const overlay = $('#job-overlay');
  const content = $('#overlay-content');
  if (!overlay || !content) return;
  const phase = progress?.phase || (requestState === 'pending' ? 'queued' : 'processing');
  const current = addJobPhaseIndex(requestState, phase);
  const message = progress?.message || addJobProgressText(answer) || (requestState === 'pending' ? 'Job intake queued…' : 'Processing job…');
  const score = Number.isFinite(Number(progress?.score)) ? `${Number(progress.score)}/100` : '';
  const company = progress?.company || activeAddJobMeta.company || '';
  const role = progress?.role || activeAddJobMeta.role || 'New job';
  const location = progress?.location || activeAddJobMeta.location || '';
  const failed = requestState === 'failed';
  const blocked = phase === 'blocked';
  const steps = [
    ['Queued', 'Request accepted'],
    ['Read vacancy', 'Extract job details'],
    ['Score & dedupe', 'Create tracker record'],
    ['Generate documents', 'CV and cover letter'],
    ['Publish', 'Refresh dashboard'],
    ['Ready', 'Open generated package']
  ];
  const stepHtml = steps.map(([label, detail], index) => {
    const status = failed ? (index === Math.max(0, current) ? 'is-error' : index < Math.max(0, current) ? 'is-done' : '')
      : index < current ? 'is-done' : index === current ? 'is-current' : '';
    return `<li class="add-job-progress-step ${status}"><span class="add-job-progress-marker">${index < current ? '✓' : index + 1}</span><span><strong>${escapeAddJobHtml(label)}</strong><small>${escapeAddJobHtml(detail)}</small></span></li>`;
  }).join('');
  content.innerHTML = `
    <div class="add-job-processing-detail">
      <header class="add-job-processing-header">
        <div>
          <span class="add-job-processing-kicker">Career Engine · live processing</span>
          <h2 id="ov-role">${escapeAddJobHtml(role)}</h2>
          <p>${escapeAddJobHtml([company, location].filter(Boolean).join(' · ') || 'Vacancy details are being extracted')}</p>
        </div>
        ${score ? `<span class="score-chip"><strong>${escapeAddJobHtml(score.replace('/100', ''))}</strong><small>/100</small></span>` : ''}
      </header>
      <section class="add-job-processing-card ${failed ? 'is-error' : blocked ? 'is-warning' : ''}" aria-live="polite">
        <div class="add-job-processing-spinner" aria-hidden="true"></div>
        <div class="add-job-processing-copy">
          <strong>${escapeAddJobHtml(failed ? 'Processing failed' : blocked ? 'Generation blocked' : message)}</strong>
          <span>${failed ? escapeAddJobHtml(message) : `Elapsed ${escapeAddJobHtml(addJobElapsedText())} · this page updates automatically`}</span>
        </div>
      </section>
      <ol class="add-job-progress-list">${stepHtml}</ol>
      <footer class="add-job-processing-footer">
        <span>Nothing is sent or submitted automatically.</span>
        <button id="add-job-processing-dismiss" class="card-button quiet" type="button">Back to board</button>
      </footer>
    </div>`;
  overlay.hidden = false;
  overlay.setAttribute('aria-hidden', 'false');
  document.documentElement.classList.add('overlay-open');
  document.body.classList.add('overlay-open');
  $('#add-job-processing-dismiss')?.addEventListener('click', clearAddJobProcessingView);
}

function finishAddJobNavigation(requestId, jobKey, message) {
  if (addJobPollTimer) {
    clearInterval(addJobPollTimer);
    addJobPollTimer = null;
  }
  setAddJobStatus(message || 'CV generated. Opening job details…');
  renderAddJobProcessingOverlay('done', { phase: 'ready', message: message || 'CV generated. Opening job details…' }, message);
  sessionStorage.removeItem(ADD_JOB_SESSION_KEY);
  const url = new URL(window.location.href);
  url.searchParams.delete(ADD_JOB_REQUEST_PARAM);
  if (jobKey) url.searchParams.set('job', jobKey);
  window.setTimeout(() => window.location.replace(`${url.pathname}${url.search}${url.hash}`), 700);
}

async function pollAddJobRequest() {
  if (!activeAddJobRequestId) return;
  try {
    const records = await loadCollection('ai_requests');
    const record = records.find(item => item.id === activeAddJobRequestId);
    if (!record) return;
    const data = dataOf(record);
    const requestState = data.state || 'pending';
    const progress = addJobProgressPayload(data.answer || '');
    const answer = addJobProgressText(data.answer || '');
    if (progress?.job_id) {
      activeAddJobJobKey = `tracker-${progress.job_id}`;
      activeAddJobMeta = {
        ...activeAddJobMeta,
        company: progress.company || activeAddJobMeta.company || '',
        role: progress.role || activeAddJobMeta.role || '',
        location: progress.location || activeAddJobMeta.location || ''
      };
      rememberAddJobRequest(activeAddJobRequestId, activeAddJobMeta);
      syncAddJobUrl(activeAddJobRequestId, activeAddJobJobKey);
    }
    if (requestState === 'pending') {
      setAddJobStatus('Job intake queued…');
      renderAddJobProcessingOverlay(requestState, progress, answer);
      return;
    }
    if (requestState === 'processing') {
      setAddJobStatus(answer || 'Processing job…');
      renderAddJobProcessingOverlay(requestState, progress, answer);
      return;
    }
    if (requestState === 'failed') {
      if (addJobPollTimer) {
        clearInterval(addJobPollTimer);
        addJobPollTimer = null;
      }
      setAddJobStatus(answer || 'Job intake failed. Review the input and retry.', true);
      renderAddJobProcessingOverlay(requestState, progress, answer || 'Job intake failed. Review the input and retry.');
      $('#add-job-open')?.removeAttribute('disabled');
      return;
    }
    finishAddJobNavigation(activeAddJobRequestId, activeAddJobJobKey, answer || 'CV and cover letter are ready. Opening job details…');
  } catch (error) {
    setAddJobStatus(`Job intake status unavailable: ${error.message}`, true);
    renderAddJobProcessingOverlay('processing', null, `Status temporarily unavailable: ${error.message}`);
  }
}

function startAddJobPolling(requestId, meta = {}) {
  rememberAddJobRequest(requestId, meta);
  syncAddJobUrl(requestId, activeAddJobJobKey);
  renderAddJobProcessingOverlay('pending', { phase: 'queued', message: 'Job intake queued…', ...meta });
  if (addJobPollTimer) clearInterval(addJobPollTimer);
  pollAddJobRequest();
  addJobPollTimer = window.setInterval(pollAddJobRequest, 2500);
}

function setupAddJobIntake() {
  const openButton = $('#add-job-open');
  const dialog = $('#add-job-dialog');
  const form = $('#add-job-form');
  const closeButton = $('#add-job-close');
  if (!openButton || !dialog || !form) return;

  const resumedRequestId = restoreAddJobRequest();
  if (resumedRequestId) {
    openButton.disabled = true;
    startAddJobPolling(resumedRequestId, activeAddJobMeta);
  }

  openButton.addEventListener('click', () => {
    form.reset();
    setAddJobStatus('');
    dialog.showModal();
    window.setTimeout(() => $('#add-job-url')?.focus(), 50);
  });
  closeButton?.addEventListener('click', () => dialog.close());
  dialog.addEventListener('click', event => {
    if (event.target === dialog) dialog.close();
  });

  form.addEventListener('submit', async event => {
    event.preventDefault();
    const jobUrl = String($('#add-job-url')?.value || '').trim();
    const jobDescription = String($('#add-job-description')?.value || '').trim();
    const company = String($('#add-job-company')?.value || '').trim();
    const role = String($('#add-job-role')?.value || '').trim();
    const location = String($('#add-job-location')?.value || '').trim();
    if (!jobUrl && !jobDescription) {
      setAddJobStatus('Paste a job description or provide a job link.', true);
      return;
    }
    const submit = $('#add-job-submit');
    if (submit) submit.disabled = true;
    setAddJobStatus('Adding job to Career Engine…');
    try {
      // Keep ai_requests writes schema-compatible. Job-specific intake fields
      // travel inside the existing prompt text field rather than as undeclared
      // collection columns, which here.now rejects with HTTP 400.
      const requestPayload = {
        schema_version: 1,
        kind: 'career_engine_add_job',
        job_url: jobUrl,
        job_description: jobDescription,
        company,
        role,
        location
      };
      const requestPrompt = JSON.stringify(requestPayload);
      if (requestPrompt.length > ADD_JOB_PROMPT_MAX_LENGTH) {
        setAddJobStatus('The pasted job description is too long for the intake queue. Use the job URL only, or shorten the pasted description and retry.', true);
        if (submit) submit.disabled = false;
        return;
      }
      const record = await createRecord('ai_requests', {
        role_key: ADD_JOB_ROLE_KEY,
        request_type: 'add_job',
        prompt: requestPrompt,
        state: 'pending'
      }, `career-add-job-${Date.now()}`);
      dialog.close();
      openButton.disabled = true;
      setAddJobStatus('Job intake queued…');
      activeAddJobStartedAt = Date.now();
      startAddJobPolling(record.id, { company, role, location });
    } catch (error) {
      setAddJobStatus(error.message, true);
      if (submit) submit.disabled = false;
    }
  });
}

/* CareerTracker is the canonical status authority. Site Data workflow is only a
   short-lived owner-write queue: a workflow change newer than the latest site
   build is shown optimistically, then the next reconciliation writes it into
   CareerTracker and patches Site Data back to the canonical stage. */
const CANONICAL_APPLIED_STATUS_VALUES = new Set([
  'applied', 'submitted', 'sent', 'submitted_pending_response',
  'application_submitted', 'email_sent', 'email_sent_owner_confirmed'
]);
const CANONICAL_READY_STATUS_VALUES = new Set([
  'awaiting_owner_approval', 'owner_review_ready', 'ready_for_review',
  'generated_content_valid', 'rendered', 'render_complete', 'packaged'
]);
const baseStageFor = stageFor;

function canonicalTrackerStage(role) {
  const processingStatus = normalizedStatus(role.processing_status);
  const applicationStatus = normalizedStatus(role.application_status);
  if (processingStatus === 'applied' || CANONICAL_APPLIED_STATUS_VALUES.has(applicationStatus)) return 'applied';
  if (roleIsInactive(role)) return 'inactive';
  if (processingStatus === 'manual_review_needed') return 'manual_review_needed';
  if (CANONICAL_READY_STATUS_VALUES.has(processingStatus) || role.kind === 'application') return 'ready_review';
  return 'found';
}

stageFor = function canonicalStageFor(role) {
  const batchOverride = state.batchStageOverrides?.get(role.key);
  if (batchOverride) return normalizedWorkflowStage(batchOverride, role) || canonicalTrackerStage(role);

  const workflow = state.workflow.get(role.key);
  const workflowStage = normalizedWorkflowStage(workflow?.stage, role);
  const workflowUpdated = workflow?.updatedAt ? new Date(workflow.updatedAt).getTime() : 0;
  const buildUpdated = state.data?.generated_at ? new Date(state.data.generated_at).getTime() : 0;
  if (workflowStage && workflowUpdated && (!buildUpdated || workflowUpdated > buildUpdated)) {
    return workflowStage;
  }
  return canonicalTrackerStage(role) || baseStageFor(role);
};

document.addEventListener('DOMContentLoaded', setupAddJobIntake);
