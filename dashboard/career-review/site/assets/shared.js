/* shared.js — Career Engine dashboard shared helpers (loaded before app.js / detail.js) */
'use strict';

const STAGES = [
  { id: 'found', label: 'Found jobs', next: 'ready_review', nextLabel: 'Prepare for review' },
  { id: 'manual_review_needed', label: 'Needs review', next: 'ready_review', nextLabel: 'Move to ready review' },
  { id: 'ready_review', label: 'Ready for review', next: 'applied', nextLabel: 'Confirm applied / sent' },
  { id: 'applied', label: 'Applied / sent', next: 'ready_review', nextLabel: 'Reopen review' },
  { id: 'inactive', label: 'Closed / inactive', next: 'found', nextLabel: 'Reopen' }
];

const APPLIED_STATUS_VALUES = new Set([
  'applied', 'submitted', 'sent', 'application_submitted', 'email_sent',
  'submitted_pending_response', 'email_sent_owner_confirmed'
]);
const SUBMISSION_HISTORY_EVENTS = new Set(['application_submitted', 'email_sent_owner_confirmed']);
const SUBMISSION_RETRACTION_EVENTS = new Set(['application_submission_retracted']);
const READY_PROCESSING_STATUS_VALUES = new Set([
  'awaiting_owner_approval', 'owner_review_ready', 'ready_for_review',
  'generated_content_valid', 'rendered', 'render_complete', 'packaged'
]);

const THEMES = [
  { id: 'executive-navy', label: 'Executive Navy' },
  { id: 'compact-slate', label: 'Compact Slate' },
  { id: 'warm-paper', label: 'Warm Paper' },
  { id: 'high-contrast', label: 'High Contrast' },
  { id: 'minimal-grid', label: 'Minimal Grid' }
];

const COMMENT_TYPES = [
  { id: 'edit_request', label: 'Edit request' },
  { id: 'question', label: 'Question' },
  { id: 'decision', label: 'Decision' },
  { id: 'note', label: 'Note' }
];

const AI_REQUEST_TYPES = [
  { id: 'review_application', label: 'Review application package' },
  { id: 'improve_cover_letter', label: 'Improve cover letter' },
  { id: 'ats_check', label: 'ATS / keyword check' },
  { id: 'questions', label: 'Answer questions' },
  { id: 'other', label: 'Other request' }
];

const GMAIL_ACCOUNT = 'hameedo@gmail.com';
const OUTWARD_EMAIL = 'hameedfarah@gmail.com';
const THEME_PREF_KEY = 'dashboard_theme';

/* A recipient only counts for email actions when it is a genuine address. */
function hasGenuineRecipient(role) {
  return /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(String((role && role.recipient) || '').trim());
}

/* Drafts live in hameedo@gmail.com, but employer-facing material must expose
   only hameedfarah@gmail.com. Sanitize any legacy outward address in body text. */
function sanitizeAccount(value) {
  return String(value || '').replace(/hameedo@gmail\.com/gi, OUTWARD_EMAIL);
}

/* Per-job resume templates. `ats-linear` is a legacy alias for `ats-classic`
   (the current ATS Linear design is branded ATS Classic in the design gallery). */
const TEMPLATE_OPTIONS = [
  { id: 'ats-classic', label: 'ATS Classic', kind: 'role', roleDoc: 'ats', defaultFor: 'portal' },
  { id: 'ats-executive-line', label: 'Executive Line', kind: 'gallery' },
  { id: 'ats-compact-technical', label: 'Compact Technical', kind: 'gallery' },
  { id: 'ats-minimal-modern', label: 'Minimal Modern', kind: 'gallery' },
  { id: 'ats-project-led', label: 'Project Led', kind: 'gallery' },
  { id: 'modern-executive-sidebar', label: 'Stylized Executive Sidebar', kind: 'role', roleDoc: 'exec', defaultFor: 'email' }
];
const TEMPLATE_ID_ALIASES = { 'ats-linear': 'ats-classic' };

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

function addList(list, values, emptyText) {
  list.replaceChildren();
  const items = values?.length ? values : [emptyText];
  for (const value of items) {
    const li = document.createElement('li');
    li.textContent = value;
    list.append(li);
  }
}

function populateSelect(select, options, selectedValue) {
  select.replaceChildren();
  for (const option of options) {
    const el = document.createElement('option');
    el.value = option.value;
    el.textContent = option.label;
    if (option.value === selectedValue) el.selected = true;
    select.append(el);
  }
  return select;
}

function commentTypeLabel(id) {
  return COMMENT_TYPES.find(item => item.id === id)?.label || humanDecision(id);
}

function aiTypeLabel(id) {
  return AI_REQUEST_TYPES.find(item => item.id === id)?.label || humanDecision(id);
}

function humanDecision(value = '') {
  return String(value).replaceAll('_', ' ').replace(/\b\w/g, c => c.toUpperCase());
}

function normalizeDecision(value = '') {
  return String(value).replaceAll('-', '_') || 'selective';
}

function dataOf(record) {
  return (record && record.data) || record || {};
}

function relativeTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return 'time unavailable';
  const seconds = Math.round((Date.now() - date.getTime()) / 1000);
  if (seconds < 60) return 'just now';
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} hr${hours === 1 ? '' : 's'} ago`;
  const days = Math.floor(hours / 24);
  return `${days} day${days === 1 ? '' : 's'} ago`;
}

function formatDate(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });
}

function formatDay(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return 'unknown';
  return date.toLocaleDateString([], { dateStyle: 'medium' });
}

function parseDate(value) {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

/* Recency bucket: fresh <=2d, recent <=7d, aging <=30d, old >30d, unknown. */
function recencyBucket(value) {
  const date = parseDate(value);
  if (!date) return { key: 'unknown', label: 'Recency unknown', cls: 'unknown' };
  const days = (Date.now() - date.getTime()) / 86400000;
  if (days <= 2) return { key: 'fresh', label: 'Fresh', cls: 'fresh' };
  if (days <= 7) return { key: 'recent', label: 'Recent', cls: 'recent' };
  if (days <= 30) return { key: 'aging', label: 'Aging', cls: 'aging' };
  return { key: 'old', label: 'Old', cls: 'old' };
}

/* Score bucket: high >=85, good 70-84, marginal 55-69, low <55. */
function scoreBucket(score) {
  if (score == null || Number.isNaN(Number(score))) return { key: 'unscored', label: 'No score', cls: 'unscored' };
  const value = Number(score);
  if (value >= 85) return { key: 'high', label: 'High', cls: 'high' };
  if (value >= 70) return { key: 'good', label: 'Good', cls: 'good' };
  if (value >= 55) return { key: 'marginal', label: 'Marginal', cls: 'marginal' };
  return { key: 'low', label: 'Low', cls: 'low' };
}

/* Posted-date rank for sorting; unknown dates always sink to the end. */
function postedRank(role, ascending) {
  const date = parseDate(role.posting_date);
  if (!date) return ascending ? Number.POSITIVE_INFINITY : Number.NEGATIVE_INFINITY;
  return date.getTime();
}

function roleFoundTime(role, fallback = '') {
  return role.found_at || role.first_seen || role.scanned_at || fallback || '';
}

/* ---- Site Data (here.now) ---- */

const COLLECTION_CACHE = new Map();

function invalidateCollectionCache(name) {
  for (const key of COLLECTION_CACHE.keys()) {
    if (key.startsWith(`${name}:`)) COLLECTION_CACHE.delete(key);
  }
}

async function loadCollection(name, limit = 300, fresh = false, throwOnError = false) {
  const cacheKey = `${name}:${limit}`;
  const cacheable = !fresh && name !== 'ai_requests';
  if (cacheable && COLLECTION_CACHE.has(cacheKey)) return COLLECTION_CACHE.get(cacheKey);
  const request = (async () => {
    try {
      const response = await fetch(`./.herenow/data/${name}?limit=${limit}`, { cache: 'no-store' });
      if (!response.ok) throw new Error(`${name} unavailable (${response.status})`);
      const payload = await response.json();
      return Array.isArray(payload.records) ? payload.records : [];
    } catch (error) {
      console.warn(`loadCollection(${name})`, error);
      if (throwOnError) throw error;
      return [];
    }
  })();
  if (cacheable) COLLECTION_CACHE.set(cacheKey, request);
  return request;
}

/* Site Data lists are cursor-paginated. Workflow persistence must read the
   complete collection because an old record that is PATCHed does not become a
   newly-created row and can therefore sit beyond the first page after reload. */
async function loadCollectionAll(name, pageSize = 300, fresh = false, maxRecords = 25000) {
  const cacheKey = `${name}:all:${pageSize}:${maxRecords}`;
  const cacheable = !fresh && name !== 'ai_requests';
  if (cacheable && COLLECTION_CACHE.has(cacheKey)) return COLLECTION_CACHE.get(cacheKey);
  const request = (async () => {
    try {
      const records = [];
      let cursor = '';
      while (records.length < maxRecords) {
        const params = new URLSearchParams({ limit: String(Math.min(pageSize, maxRecords - records.length)) });
        if (cursor) params.set('cursor', cursor);
        const response = await fetch(`./.herenow/data/${name}?${params.toString()}`, { cache: 'no-store' });
        if (!response.ok) throw new Error(`${name} unavailable (${response.status})`);
        const payload = await response.json();
        const page = Array.isArray(payload.records) ? payload.records : [];
        records.push(...page);
        const nextCursor = String(payload.nextCursor || '');
        if (!nextCursor || page.length === 0) break;
        cursor = nextCursor;
      }
      return records;
    } catch (error) {
      console.warn(`loadCollectionAll(${name})`, error);
      return [];
    }
  })();
  if (cacheable) COLLECTION_CACHE.set(cacheKey, request);
  return request;
}

async function createRecord(collection, payload, idempotencyKey) {
  const response = await fetch(`./.herenow/data/${collection}`, {
    method: 'POST',
    headers: { 'content-type': 'application/json', 'Idempotency-Key': idempotencyKey || crypto.randomUUID() },
    body: JSON.stringify(payload)
  });
  if (!response.ok) throw new Error(await responseError(response, `Save to ${collection} failed`));
  const result = await response.json();
  invalidateCollectionCache(collection);
  return result.record || result;
}

async function patchRecord(collection, recordId, fields) {
  const response = await fetch(`./.herenow/data/${collection}/${encodeURIComponent(recordId)}`, {
    method: 'PATCH',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(fields)
  });
  if (!response.ok) throw new Error(await responseError(response, `Update ${collection} failed`));
  const result = await response.json();
  invalidateCollectionCache(collection);
  return result.record || result;
}

async function responseError(response, fallback) {
  try {
    const body = await response.json();
    return `${fallback} (${response.status}): ${body.message || body.error || ''}`.trim();
  } catch {
    return `${fallback} (${response.status})`;
  }
}

/* ---- Gmail: always a new tab, never the current tab, no mailto ---- */

function cleanedEmailBody(role) {
  // Prefer the generated cover-letter text when a legacy portal package has a
  // valid cover-letter artifact but no separate email_body field. This keeps the
  // job detail useful without inventing generic application text.
  const sourceText = role.email_body || role.cover_letter?.text || '';
  const lines = String(sourceText).split(/\r?\n/);
  while (lines.length && (!lines[0].trim() || /^(subject|to):/i.test(lines[0].trim()))) lines.shift();
  let body = sanitizeAccount(lines.join('\n').trim());
  if (role.route !== 'email' && role.application_url && !body.includes(role.application_url)) {
    body += `\n\nOfficial submission link: ${role.application_url}`;
  }
  return body;
}

function gmailComposeUrl(role) {
  const params = new URLSearchParams({
    authuser: GMAIL_ACCOUNT,
    from: OUTWARD_EMAIL,
    view: 'cm',
    fs: '1',
    tf: '1',
    su: role.email_subject || `Abdelhamid Farah - ${role.role}`,
    body: cleanedEmailBody(role)
  });
  if (role.recipient) params.set('to', role.recipient);
  return `https://mail.google.com/mail/?${params.toString()}`;
}

/* Opens Gmail compose in a new tab only. On popup block, reports a copyable URL. */
function openGmailCompose(role) {
  const url = gmailComposeUrl(role);
  let opened = null;
  try {
    opened = window.open(url, '_blank', 'noopener,noreferrer');
  } catch {
    opened = null;
  }
  if (!opened) showGmailBlocked(url);
  return opened;
}

function showGmailBlocked(url) {
  const panel = document.createElement('div');
  panel.className = 'gmail-blocked';
  panel.innerHTML = `
    <strong>Gmail popup was blocked.</strong>
    <span>The dashboard tab was not replaced. Copy the compose link below and open it manually.</span>
    <div class="gmail-blocked-url">${escapeHtml(url)}</div>
    <div class="gmail-blocked-actions">
      <button type="button" class="card-button primary">Copy link</button>
      <button type="button" class="card-button">Dismiss</button>
    </div>`;
  const copyButton = $('.card-button.primary', panel);
  copyButton.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(url);
      copyButton.textContent = 'Copied';
    } catch {
      const input = document.createElement('input');
      input.value = url;
      document.body.append(input);
      input.select();
      document.execCommand('copy');
      input.remove();
      copyButton.textContent = 'Copied';
    }
  });
  $('.card-button:not(.primary)', panel).addEventListener('click', () => panel.remove());
  const old = $('.gmail-blocked');
  if (old) old.remove();
  document.body.append(panel);
}

/* Opens any external URL in a new tab only. On popup block, shows a copyable link panel. */
function openInNewTab(url) {
  if (!url) return null;
  let opened = null;
  try {
    opened = window.open(url, '_blank', 'noopener,noreferrer');
  } catch {
    opened = null;
  }
  if (!opened) showGmailBlocked(url);
  return opened;
}

function submissionDocumentEvidence(role, templateId = '') {
  const selected = normalizeTemplateId(templateId) || defaultTemplateFor(role);
  const docs = selected === 'modern-executive-sidebar'
    ? (role.resume || {})
    : (role.resume_ats?.pdf || role.resume_ats?.docx ? role.resume_ats : role.resume || {});
  const cover = role.cover_letter || {};
  return {
    job_id: role.job_id || '',
    company: role.company || '',
    role: role.role || '',
    route: role.route || '',
    application_url: role.application_url || '',
    template_id: selected,
    document_pdf: docs.pdf || '',
    document_docx: docs.docx || '',
    document_sha256: docs.sha256 || docs.pdf_sha256 || role.resume_sha256 || '',
    document_text: docs.text || '',
    cover_letter_pdf: cover.pdf || '',
    cover_letter_docx: cover.docx || '',
    cover_letter_sha256: cover.sha256 || '',
    cover_letter_text: cover.text || cleanedEmailBody(role) || '',
    package_version: role.package_version || role.version || role.submission_package?.package_version || ''
  };
}

function submissionHistoryFields(evidence) {
  return {
    actor: evidence.actor || '',
    ui_source: evidence.ui_source || '',
    evidence_level: evidence.evidence_level || '',
    evidence_type: evidence.evidence_type || '',
    url: evidence.url || evidence.application_url || '',
    recipient: evidence.recipient || '',
    confirmation_reference: evidence.confirmation_reference || '',
    opened_at: evidence.opened_at || '',
    submitted_at: evidence.submitted_at || '',
    template_id: evidence.template_id || '',
    document_pdf: evidence.document_pdf || '',
    document_docx: evidence.document_docx || '',
    document_sha256: evidence.document_sha256 || '',
    package_version: evidence.package_version || ''
  };
}

function compactSubmissionNote(evidence) {
  return {
    job_id: evidence.job_id || '',
    company: evidence.company || '',
    role: evidence.role || '',
    route: evidence.route || '',
    application_url: evidence.application_url || evidence.url || '',
    submitted_at: evidence.submitted_at || '',
    confirmation_reference: evidence.confirmation_reference || '',
    template_id: evidence.template_id || '',
    document_pdf: evidence.document_pdf || '',
    document_docx: evidence.document_docx || '',
    document_sha256: evidence.document_sha256 || '',
    cover_letter_pdf: evidence.cover_letter_pdf || '',
    cover_letter_docx: evidence.cover_letter_docx || '',
    cover_letter_sha256: evidence.cover_letter_sha256 || '',
    package_version: evidence.package_version || ''
  };
}

/* A portal click proves only that the recorded application URL was opened. It
   never changes the workflow to Applied and never claims submission. The popup
   is opened synchronously to avoid browser blocking; the append-only evidence
   record is then saved in the background. */
function openTrackedPortal(role, uiSource = 'dashboard') {
  if (!role?.application_url) return null;
  const opened = openInNewTab(role.application_url);
  const workflow = typeof state !== 'undefined' && state.workflow ? (state.workflow.get(role.key) || {}) : {};
  const currentStage = workflow.stage || stageFor(role);
  const evidence = {
    actor: 'owner_ui',
    ui_source: uiSource,
    evidence_level: 'portal_open_only_not_submission',
    url: role.application_url,
    opened_at: new Date().toISOString(),
    ...submissionDocumentEvidence(role, workflow.template_id)
  };
  createRecord('history', {
    role_key: role.key,
    event: 'portal_opened',
    from_stage: currentStage,
    to_stage: currentStage,
    ...submissionHistoryFields(evidence),
    note: JSON.stringify({
      application_url: evidence.application_url || evidence.url || '',
      opened_at: evidence.opened_at || '',
      template_id: evidence.template_id || '',
      document_sha256: evidence.document_sha256 || ''
    })
  }, `portal-open-${role.key}-${Date.now()}`).catch(error => {
    console.warn('portal-open evidence failed', error);
    showToast('Portal opened, but the click evidence could not be saved.', true);
  });
  return opened;
}

/* Clicking the private dashboard's Job applied / Confirm submitted action is
   the explicit owner confirmation. Opening the destination alone still never
   counts as submission evidence. Exact selected-package evidence is preserved. */
const SUBMISSION_CONFIRMATION_PENDING = new Set();
async function confirmApplicationSubmitted(role, uiSource = 'dashboard') {
  if (!role?.key || SUBMISSION_CONFIRMATION_PENDING.has(role.key)) return false;
  SUBMISSION_CONFIRMATION_PENDING.add(role.key);
  try {
    const workflow = typeof state !== 'undefined' && state.workflow ? (state.workflow.get(role.key) || {}) : {};
    const submittedAt = new Date().toISOString();
    const currentStage = workflow.stage || stageFor(role);
    const evidence = {
      actor: 'owner',
      ui_source: uiSource,
      evidence_type: 'explicit_owner_confirmation',
      url: role.application_url || '',
      recipient: role.recipient || '',
      confirmation_reference: '',
      submitted_at: submittedAt,
      ...submissionDocumentEvidence(role, workflow.template_id)
    };
    const saved = await createRecord('history', {
      role_key: role.key,
      event: role.route === 'email' ? 'email_sent_owner_confirmed' : 'application_submitted',
      from_stage: currentStage,
      to_stage: 'applied',
      ...submissionHistoryFields(evidence),
      note: JSON.stringify(compactSubmissionNote(evidence))
    }, `submission-confirm-${role.key}-${Date.now()}`);
    const savedHistory = {
      id: saved.id,
      ...(dataOf(saved) || {}),
      createdAt: saved.createdAt || submittedAt,
      updatedAt: saved.updatedAt || submittedAt
    };
    if (typeof state !== 'undefined' && Array.isArray(state.history)) {
      state.history.push(savedHistory);
    }
    return { record: savedHistory, evidence, priorStage: currentStage };
  } finally {
    SUBMISSION_CONFIRMATION_PENDING.delete(role.key);
  }
}

/* ---- Resume template selection ---- */

function normalizeTemplateId(value) {
  const id = TEMPLATE_ID_ALIASES[value] || value || '';
  return TEMPLATE_OPTIONS.some(option => option.id === id) ? id : '';
}

function defaultTemplateFor(role) {
  return role.route === 'portal' ? 'ats-classic' : 'modern-executive-sidebar';
}

function selectedTemplateFor(role) {
  // Respect an explicit owner override even if its artifact is not generated yet.
  const ownerChoice = normalizeTemplateId(state.workflow.get(role.key)?.template_id);
  if (ownerChoice) return ownerChoice;

  const preferred = normalizeTemplateId(role.recommended_resume_template) || defaultTemplateFor(role);
  // Legacy portal packages sometimes predate the role-specific ATS renderer and
  // therefore have a valid executive PDF but an empty ATS slot. Do not show a
  // blank resume pane in that case; use the generated role-specific resume until
  // an ATS version is explicitly generated or selected by the owner.
  if (preferred === 'ats-classic'
      && !(role.resume_ats?.pdf || role.resume_ats?.docx)
      && (role.resume?.pdf || role.resume?.docx)) {
    return 'modern-executive-sidebar';
  }
  return preferred;
}

/* Maps every template id to the files that exist for this exact role.
   Role-specific templates (ATS Classic, Stylized Executive Sidebar) use the
   role's own generated files. The four gallery ATS alternatives are comparison
   samples only and are only linked for the sample job they were built from;
   for every other role they report an explicit not-generated state so a wrong
   role's file is never silently substituted. */
function templateAvailability(role, designOptions = {}) {
  const exec = role.resume || {};
  const ats = role.resume_ats || {};
  const available = {
    'ats-classic': {
      label: 'ATS Classic',
      pdf: ats.pdf || '', docx: ats.docx || '',
      generated: Boolean(ats.pdf || ats.docx),
      note: ats.pdf || ats.docx ? 'Role-specific ATS Classic file.' : 'No role-specific ATS Classic file has been generated for this job yet.'
    },
    'modern-executive-sidebar': {
      label: 'Stylized Executive Sidebar',
      pdf: exec.pdf || '', docx: exec.docx || '',
      generated: Boolean(exec.pdf || exec.docx),
      note: exec.pdf || exec.docx ? 'Role-specific executive resume.' : 'No executive resume file has been generated for this job yet.'
    }
  };
  const sampleJobId = designOptions.sample_job_id || '';
  const isSampleRole = role.job_id && sampleJobId && String(role.job_id) === String(sampleJobId);
  for (const style of designOptions.styles || []) {
    if (style.id === 'ats-classic' || style.id === 'modern-executive-sidebar') continue;
    const styleFiles = isSampleRole ? { pdf: style.pdf || '', docx: style.docx || '' } : { pdf: '', docx: '' };
    available[style.id] = {
      label: style.label || style.id,
      ...styleFiles,
      generated: isSampleRole && Boolean(style.pdf || style.docx),
      note: isSampleRole
        ? 'Comparison sample for this job.'
        : 'This alternative is only a comparison sample for the sample job; a role-specific version has not been generated for this job yet.'
    };
  }
  return available;
}

function templateLabel(id) {
  return TEMPLATE_OPTIONS.find(option => option.id === id)?.label || humanDecision(id);
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[char]);
}

/* ---- Toasts ---- */

function showToast(message, isError = false) {
  let toast = $('#board-toast');
  if (!toast) {
    toast = document.createElement('div');
    toast.id = 'board-toast';
    document.body.append(toast);
  }
  toast.replaceChildren();
  toast.textContent = message;
  toast.className = isError ? 'toast error' : 'toast';
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => toast.classList.add('hidden'), 3200);
  toast.classList.remove('hidden');
}

function showActionToast(message, actionLabel, handler, { duration = 7000, success = false } = {}) {
  let toast = $('#board-toast');
  if (!toast) {
    toast = document.createElement('div');
    toast.id = 'board-toast';
    document.body.append(toast);
  }
  toast.replaceChildren();
  const text = document.createElement('div');
  text.className = 'toast-message';
  text.textContent = message;
  const action = document.createElement('button');
  action.type = 'button';
  action.className = 'toast-action';
  action.textContent = actionLabel;
  action.addEventListener('click', async () => {
    action.disabled = true;
    try {
      await handler();
      toast.classList.add('hidden');
    } catch (error) {
      showToast(error?.message || String(error), true);
    }
  });
  toast.append(text, action);
  toast.className = `toast actionable${success ? ' success' : ''}`;
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => toast.classList.add('hidden'), duration);
  toast.classList.remove('hidden');
}

function launchApplicationConfetti() {
  if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return;
  const old = $('.application-confetti');
  if (old) old.remove();
  const layer = document.createElement('div');
  layer.className = 'application-confetti';
  layer.setAttribute('aria-hidden', 'true');
  for (let index = 0; index < 42; index += 1) {
    const piece = document.createElement('i');
    piece.style.setProperty('--confetti-x', `${Math.round(Math.random() * 100)}vw`);
    piece.style.setProperty('--confetti-delay', `${Math.round(Math.random() * 220)}ms`);
    piece.style.setProperty('--confetti-drift', `${Math.round((Math.random() - 0.5) * 180)}px`);
    piece.style.setProperty('--confetti-spin', `${Math.round(300 + Math.random() * 720)}deg`);
    piece.style.setProperty('--confetti-size', `${6 + Math.round(Math.random() * 6)}px`);
    piece.dataset.tone = String(index % 6);
    layer.append(piece);
  }
  document.body.append(layer);
  window.setTimeout(() => layer.remove(), 2100);
}

/* ---- Theme persistence (Site Data preferences) ---- */

async function loadPreference(key) {
  const records = await loadCollection('preferences');
  const match = records.find(record => dataOf(record).key === key);
  return match ? { record: match, value: dataOf(match).value } : null;
}

async function savePreference(key, value) {
  const existing = state.preferences?.get(key);
  if (existing?.record?.id) {
    const record = await patchRecord('preferences', existing.record.id, { value });
    state.preferences.set(key, { record, value });
    return record;
  }
  const record = await createRecord('preferences', { key, value }, `pref-${key}`);
  state.preferences.set(key, { record, value });
  return record;
}

function applyTheme(themeId) {
  const theme = THEMES.find(item => item.id === themeId) || THEMES[0];
  document.documentElement.dataset.theme = theme.id;
  const select = $('#theme-select');
  if (select) select.value = theme.id;
  return theme;
}

async function initTheme() {
  const saved = await loadPreference(THEME_PREF_KEY);
  return applyTheme(saved ? saved.value : 'executive-navy');
}

/* ---- Stage helpers ---- */

const INACTIVE_STATUS_VALUES = new Set([
  'closed', 'deleted', 'expired', 'inactive', 'removed', 'unavailable', 'withdrawn', 'cancelled', 'canceled'
]);

function normalizedStatus(value) {
  return String(value || '').trim().toLowerCase().replace(/[\s-]+/g, '_');
}

function latestSubmissionHistoryState(role) {
  if (typeof state === 'undefined' || !Array.isArray(state.history) || !role?.key) return '';
  const relevant = state.history
    .filter(record => record.role_key === role.key
      && (SUBMISSION_HISTORY_EVENTS.has(normalizedStatus(record.event))
        || SUBMISSION_RETRACTION_EVENTS.has(normalizedStatus(record.event))))
    .sort((a, b) => {
      const aTime = new Date(a.retracted_at || a.submitted_at || a.updatedAt || a.createdAt || 0).getTime();
      const bTime = new Date(b.retracted_at || b.submitted_at || b.updatedAt || b.createdAt || 0).getTime();
      return aTime - bTime;
    });
  const latest = relevant.at(-1);
  if (!latest) return '';
  return SUBMISSION_RETRACTION_EVENTS.has(normalizedStatus(latest.event)) ? 'retracted' : 'submitted';
}

function roleHasActiveSubmissionEvidence(role) {
  const historyState = latestSubmissionHistoryState(role);
  if (historyState === 'retracted') return false;
  if (historyState === 'submitted') return true;
  if (role?.submitted_package && !role.submitted_package.submission_retracted_at) return true;
  const applicationStatus = normalizedStatus(role?.application_status);
  const processingStatus = normalizedStatus(role?.processing_status);
  return processingStatus === 'applied' || APPLIED_STATUS_VALUES.has(applicationStatus);
}

function roleIsInactive(role) {
  const liveStatus = normalizedStatus(role.live_status);
  const processingStatus = normalizedStatus(role.processing_status);
  const applicationStatus = normalizedStatus(role.application_status);
  if (INACTIVE_STATUS_VALUES.has(liveStatus)
      || INACTIVE_STATUS_VALUES.has(processingStatus)
      || INACTIVE_STATUS_VALUES.has(applicationStatus)) return true;

  /* A role verified as live stays active even when the posting is old. For
     unverified/non-live roles, reuse the dashboard's existing Old >30d rule
     as the default archival threshold. The owner can always reopen/move it. */
  if (liveStatus === 'live') return false;
  return recencyBucket(role.posting_date || roleFoundTime(role)).key === 'old';
}

function defaultStage(role) {
  if (roleIsInactive(role)) return 'inactive';
  const processingStatus = normalizedStatus(role.processing_status);
  if (processingStatus === 'manual_review_needed') return 'manual_review_needed';
  const hasGeneratedResume = Boolean(
    role.card_resume_pdf
    || role.resume?.pdf || role.resume?.docx
    || role.resume_ats?.pdf || role.resume_ats?.docx
  );
  if (READY_PROCESSING_STATUS_VALUES.has(processingStatus) || hasGeneratedResume) return 'ready_review';
  return 'found';
}

function normalizedWorkflowStage(stage, role) {
  const value = normalizedStatus(stage);
  if (value === 'approved') return 'ready_review';
  if (value === 'processing') {
    return role && (role.kind === 'application' || role.resume || role.cover_letter || role.submission_package)
      ? 'ready_review' : 'found';
  }
  return STAGES.some(item => item.id === value) ? value : '';
}

function stageFor(role) {
  if (roleHasActiveSubmissionEvidence(role)) return 'applied';
  const batchOverride = normalizedWorkflowStage(state.batchStageOverrides?.get(role.key), role);
  const workflowStage = normalizedWorkflowStage(state.workflow.get(role.key)?.stage, role);
  // here.now workflow rows are only a projection. A stale/accidental Applied
  // projection must never outrank CareerTracker status without explicit
  // submission evidence.
  if (workflowStage === 'applied') return batchOverride || defaultStage(role);
  return batchOverride || workflowStage || defaultStage(role);
}

function activityTime(role) {
  return state.workflow.get(role.key)?.updatedAt || roleFoundTime(role) || state.data?.generated_at || '';
}

function makeLink(label, href, className = '') {
  const link = document.createElement('a');
  link.className = `card-button ${className}`.trim();
  link.href = href;
  link.textContent = label;
  if (/^https?:/.test(href)) link.target = '_blank';
  link.rel = 'noopener noreferrer';
  return link;
}

function makeButton(label, handler, className = '') {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = `card-button ${className}`.trim();
  button.textContent = label;
  button.addEventListener('click', handler);
  return button;
}

function appendFileLink(target, label, href, primary = false) {
  if (!href) return;
  target.append(makeLink(label, href, primary ? 'primary' : ''));
}

function tagChip(bucket) {
  const span = document.createElement('span');
  span.className = `tag tag-${bucket.cls}`;
  span.textContent = bucket.label;
  span.title = bucket.title || bucket.label;
  return span;
}
