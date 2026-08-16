/* add-job.js — owner-supplied vacancy intake for the Career Engine dashboard */
'use strict';

const ADD_JOB_ROLE_KEY = '__career_engine_add_job__';
let addJobPollTimer = null;
let activeAddJobRequestId = '';

function addJobProgressText(value) {
  if (!value || typeof value !== 'string') return '';
  if (!value.trim().startsWith('{')) return value;
  try {
    const parsed = JSON.parse(value);
    return parsed?.kind === 'add_job_progress' ? (parsed.message || humanDecision(parsed.phase || 'processing')) : value;
  } catch {
    return value;
  }
}

function setAddJobStatus(message, isError = false) {
  const status = $('#add-job-status');
  if (!status) return;
  status.textContent = message || '';
  status.classList.toggle('error', Boolean(isError));
}

async function pollAddJobRequest() {
  if (!activeAddJobRequestId) return;
  try {
    const records = await loadCollection('ai_requests');
    const record = records.find(item => item.id === activeAddJobRequestId);
    if (!record) return;
    const data = dataOf(record);
    const requestState = data.state || 'pending';
    const answer = addJobProgressText(data.answer || '');
    if (requestState === 'pending') {
      setAddJobStatus('Job intake queued…');
      return;
    }
    if (requestState === 'processing') {
      setAddJobStatus(answer || 'Processing job…');
      return;
    }
    if (addJobPollTimer) {
      clearInterval(addJobPollTimer);
      addJobPollTimer = null;
    }
    if (requestState === 'failed') {
      setAddJobStatus(answer || 'Job intake failed. Review the input and retry.', true);
      $('#add-job-open')?.removeAttribute('disabled');
      return;
    }
    setAddJobStatus(answer || 'Job added and processed. Reloading dashboard…');
    sessionStorage.setItem('career-add-job-complete', activeAddJobRequestId);
    window.setTimeout(() => window.location.reload(), 1200);
  } catch (error) {
    setAddJobStatus(`Job intake status unavailable: ${error.message}`, true);
  }
}

function startAddJobPolling(requestId) {
  activeAddJobRequestId = requestId;
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
      const record = await createRecord('ai_requests', {
        role_key: ADD_JOB_ROLE_KEY,
        request_type: 'add_job',
        prompt: 'Add this owner-supplied vacancy to Career Engine and process this job immediately when eligible. Do not send or submit anything.',
        job_url: jobUrl,
        job_description: jobDescription,
        company,
        role,
        location,
        state: 'pending'
      }, `career-add-job-${Date.now()}`);
      dialog.close();
      openButton.disabled = true;
      setAddJobStatus('Job intake queued…');
      startAddJobPolling(record.id);
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
