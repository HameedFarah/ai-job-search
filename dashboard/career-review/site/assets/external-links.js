/* external-links.js — final owner-acceptance layer for Career Engine.
 *
 * Loaded after app.js/bulk-table.js so it can preserve the current board while
 * applying the remaining owner-requested runtime behavior without forking the
 * Career Engine data/workflow authority. Nothing here sends or submits an
 * application; external actions remain owner-controlled.
 */
'use strict';

const OWNER_INITIAL_LANE_LIMIT = 15;
const OWNER_LANE_INCREMENT = 15;
const ownerLaneLimits = new Map(STAGES.map(stage => [stage.id, OWNER_INITIAL_LANE_LIMIT]));
let ownerLazyObserver = null;

/* ---------- External tabs: no false popup-blocked warning ---------- */
function openCareerExternalTab(url) {
  if (!url) return null;
  try {
    // With noopener/noreferrer browsers may return null even when the tab opens.
    // Never interpret the return value as proof that the popup was blocked.
    return window.open(url, '_blank', 'noopener,noreferrer');
  } catch {
    return null;
  }
}

openInNewTab = openCareerExternalTab;
openGmailCompose = role => openCareerExternalTab(gmailComposeUrl(role));
showGmailBlocked = () => {};

/* Never surface the old generic placeholder as a real cover letter. */
const ownerBaseCleanedEmailBody = cleanedEmailBody;
cleanedEmailBody = function ownerCleanedEmailBody(role) {
  const text = ownerBaseCleanedEmailBody(role);
  const normalized = String(text || '').replace(/\s+/g, ' ').trim();
  const generic = /^Dear Recruitment Team, Please find my application for the .+ position at .+\. Kind regards, Abdelhamid Farah/i;
  return generic.test(normalized) ? '' : text;
};

/* Exact user-facing resume label. */
const compactTechnical = TEMPLATE_OPTIONS.find(option => option.id === 'ats-compact-technical');
if (compactTechnical) compactTechnical.label = 'Compact Technical with photo';

/* ---------- Fresh scan / Process all prompts ---------- */
const ownerBaseQueueGlobalOperation = queueGlobalOperation;
queueGlobalOperation = function ownerQueueGlobalOperation(requestType, fields = {}) {
  if (requestType === 'refresh_jobs') {
    return ownerBaseQueueGlobalOperation(requestType, {
      ...fields,
      prompt: [
        'Run a fresh Hermes Career Engine job scan, then rebuild and republish the private dashboard.',
        'Also perform a read-only Gmail reconciliation for jobs already marked applied/sent: look for employer replies, interview/assessment/offer/rejection signals, and submission confirmations; match only with evidence and preserve conflicting/uncertain cases for owner review.',
        'Do not send email, create outward drafts, contact recruiters, open submission portals, or submit applications.'
      ].join(' ')
    });
  }
  if (requestType === 'process_jobs') {
    const score = Number(fields.min_score || 70);
    return ownerBaseQueueGlobalOperation(requestType, {
      ...fields,
      prompt: [
        `Process every Career Engine-eligible live job scoring at least ${score}.`,
        'For every applicable role, ensure the package has a real role-specific CV and a real evidence-grounded cover letter; never substitute generic placeholder application text.',
        'Preserve already-valid current artifacts, repair missing/invalid artifacts, render and validate the package, publish batch progress with done/remaining/current job/ETA when available, then rebuild and republish the dashboard.',
        'Do not send email, contact recruiters, open submission portals, or submit applications.'
      ].join(' ')
    });
  }
  return ownerBaseQueueGlobalOperation(requestType, fields);
};

/* ---------- Kanban incremental rendering / lazy load ---------- */
const ownerBaseRenderBoard = renderBoard;

function ownerAllFilteredStageRoles() {
  const roles = sortedRoles(filteredRoles());
  return new Map(STAGES.map(stage => [stage.id, roles.filter(role => stageFor(role) === stage.id)]));
}

function ownerSetTrueColumnCounts(stageRoles) {
  for (const stage of STAGES) {
    const column = document.querySelector(`.kanban-column[data-stage="${stage.id}"]`);
    const count = column?.querySelector('.column-title-group span');
    if (count) count.textContent = String((stageRoles.get(stage.id) || []).length);
  }
}

function ownerAttachLazyLoad(stageRoles) {
  if (ownerLazyObserver) ownerLazyObserver.disconnect();
  ownerLazyObserver = 'IntersectionObserver' in window
    ? new IntersectionObserver(entries => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          const button = entry.target;
          const stageId = button.dataset.stage;
          if (!stageId) continue;
          ownerLazyObserver.unobserve(button);
          ownerLaneLimits.set(stageId, (ownerLaneLimits.get(stageId) || OWNER_INITIAL_LANE_LIMIT) + OWNER_LANE_INCREMENT);
          renderBoard();
          break;
        }
      }, { root: null, rootMargin: '500px 0px', threshold: 0.01 })
    : null;

  for (const stage of STAGES) {
    const all = stageRoles.get(stage.id) || [];
    const limit = ownerLaneLimits.get(stage.id) || OWNER_INITIAL_LANE_LIMIT;
    const remaining = Math.max(0, all.length - limit);
    if (!remaining) continue;
    const list = document.querySelector(`.kanban-column[data-stage="${stage.id}"] .column-list`);
    if (!list) continue;
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'column-load-more';
    button.dataset.stage = stage.id;
    button.textContent = `Load ${Math.min(OWNER_LANE_INCREMENT, remaining)} more · ${remaining} remaining`;
    button.addEventListener('click', event => {
      event.stopPropagation();
      ownerLaneLimits.set(stage.id, limit + OWNER_LANE_INCREMENT);
      renderBoard();
    });
    list.append(button);
    ownerLazyObserver?.observe(button);
  }
}

renderBoard = function ownerRenderBoard() {
  const fullRoles = state.roles;
  const stageRoles = ownerAllFilteredStageRoles();
  const visibleKeys = new Set();
  for (const stage of STAGES) {
    const limit = ownerLaneLimits.get(stage.id) || OWNER_INITIAL_LANE_LIMIT;
    for (const role of (stageRoles.get(stage.id) || []).slice(0, limit)) visibleKeys.add(role.key);
  }
  try {
    state.roles = fullRoles.filter(role => visibleKeys.has(role.key));
    ownerBaseRenderBoard();
  } finally {
    state.roles = fullRoles;
  }
  // Summary and lane counts always reflect the full filtered/tracked set, not
  // only the DOM slice currently rendered.
  renderSummary();
  ownerSetTrueColumnCounts(stageRoles);
  ownerAttachLazyLoad(stageRoles);
};

/* bulk-table.js can update counts after a single status mutation; keep the
 * displayed lane count tied to the full filtered data set. */
if (typeof updateColumnCountsAndEmptyStates === 'function') {
  const ownerBaseUpdateColumnCounts = updateColumnCountsAndEmptyStates;
  updateColumnCountsAndEmptyStates = function ownerUpdateColumnCounts() {
    ownerBaseUpdateColumnCounts();
    ownerSetTrueColumnCounts(ownerAllFilteredStageRoles());
  };
}

/* ---------- Resume fallback + generate-on-demand ---------- */
function ownerTemplateInfo(role, templateId) {
  return templateAvailability(role, state.templatesData || {})[templateId] || { generated: false };
}

function ownerPreviewTemplateFor(role) {
  const selected = selectedTemplateFor(role);
  const selectedInfo = ownerTemplateInfo(role, selected);
  if (selectedInfo.pdf || selectedInfo.docx) return selected;
  const candidates = [
    defaultTemplateFor(role),
    'modern-executive-sidebar',
    'ats-classic',
    ...TEMPLATE_OPTIONS.map(option => option.id)
  ];
  for (const id of [...new Set(candidates.map(normalizeTemplateId).filter(Boolean))]) {
    const info = ownerTemplateInfo(role, id);
    if (info.pdf || info.docx) return id;
  }
  return selected;
}

function ownerActivePackageRequest(role) {
  return aiRequestsForRole(role.key).find(record => {
    const data = dataOf(record);
    return data.request_type === 'edit_cv' && ['pending', 'processing'].includes(data.state || 'pending');
  }) || null;
}

async function ownerQueuePackageGeneration(role, templateId = selectedTemplateFor(role)) {
  const active = ownerActivePackageRequest(role);
  if (active) {
    showToast('This job package is already being generated.');
    setupAiPolling(role);
    return active;
  }
  const prompt = [
    `Regenerate and validate the Career Engine package for this exact job using the selected submission CV design: ${templateLabel(templateId)} (${templateId}).`,
    'Generate the selected role-specific CV PDF/DOCX and a real evidence-grounded cover letter PDF/DOCX when applicable.',
    'Do not invent claims, do not use generic placeholder cover text, preserve the current job/workflow identity, and do not send or submit anything.',
    'Rebuild and republish the private dashboard when the generated files pass validation.'
  ].join(' ');
  const record = await createRecord('ai_requests', {
    role_key: role.key,
    request_type: 'edit_cv',
    prompt,
    state: 'pending',
    template_id: templateId
  }, `generate-package-${role.key}-${templateId}-${Date.now()}`);
  const normalized = { id: record.id, ...dataOf(record), createdAt: record.createdAt, updatedAt: record.updatedAt };
  state.aiRequests.push(normalized);
  if (record.id) sessionStorage.setItem(`career-generation-owned:${role.key}`, record.id);
  await createRecord('history', {
    role_key: role.key,
    event: 'package_generation_requested',
    note: `Generate selected CV and package: ${templateLabel(templateId)}`
  }, `history-generate-${role.key}-${Date.now()}`);
  renderOverlayAi(role);
  renderOverlayTemplate(role);
  renderOverlayResumePreview(role);
  setupAiPolling(role);
  return normalized;
}

const ownerBaseRenderOverlayTemplate = renderOverlayTemplate;
renderOverlayTemplate = function ownerRenderOverlayTemplate(role) {
  ownerBaseRenderOverlayTemplate(role);
  const selected = selectedTemplateFor(role);
  const info = ownerTemplateInfo(role, selected);
  const files = document.querySelector('#ov-template-files');
  const note = document.querySelector('#ov-template-note');
  if (!files || info.generated) return;
  const active = ownerActivePackageRequest(role);
  const button = document.createElement('button');
  button.type = 'button';
  button.className = `card-button primary generate-selected-resume${active ? ' is-loading' : ''}`;
  button.disabled = Boolean(active);
  button.innerHTML = active
    ? '<span class="owner-spinner" aria-hidden="true"></span> Generating selected CV…'
    : `Generate ${escapeHtml(templateLabel(selected))}`;
  button.addEventListener('click', () => ownerQueuePackageGeneration(role, selected).catch(error => showToast(error.message, true)));
  files.append(button);
  const fallbackId = ownerPreviewTemplateFor(role);
  if (fallbackId !== selected && note) {
    note.textContent = `${templateLabel(selected)} is not generated yet. Showing the available ${templateLabel(fallbackId)} CV until generation completes.`;
  }
};

renderOverlayResumePreview = function ownerRenderOverlayResumePreview(role) {
  const frame = document.querySelector('#ov-resume-frame');
  const empty = document.querySelector('#ov-resume-empty');
  const open = document.querySelector('#ov-resume-open');
  const download = document.querySelector('#ov-resume-download');
  const docx = document.querySelector('#ov-resume-docx');
  if (!frame || !empty || !open || !download || !docx) return;

  const selected = selectedTemplateFor(role);
  const previewId = ownerPreviewTemplateFor(role);
  const info = ownerTemplateInfo(role, previewId);
  const pdf = info.pdf || '';
  const docxHref = info.docx || '';

  docx.hidden = !docxHref;
  if (docxHref) docx.href = docxHref;
  else docx.removeAttribute('href');

  if (!pdf) {
    frame.hidden = true;
    frame.removeAttribute('src');
    empty.hidden = false;
    const active = ownerActivePackageRequest(role);
    empty.textContent = active
      ? 'Generating this job package… the resume will appear here after validation and dashboard refresh.'
      : 'No generated resume is available for this job yet. Use Generate selected CV.';
    open.hidden = true;
    open.removeAttribute('href');
    download.hidden = true;
    download.removeAttribute('href');
    return;
  }

  empty.hidden = true;
  frame.hidden = false;
  const pageOne = `${pdf}${pdf.includes('#') ? '&' : '#'}page=1&view=FitH&toolbar=0&navpanes=0`;
  if (frame.getAttribute('src') !== pageOne) frame.src = pageOne;
  open.href = pdf;
  open.hidden = false;
  download.href = pdf;
  download.hidden = false;
  frame.title = previewId === selected
    ? `Selected ${templateLabel(selected)} resume preview, page one`
    : `Available ${templateLabel(previewId)} resume preview while ${templateLabel(selected)} is generated`;
};

const ownerBaseRenderOverlayDocuments = renderOverlayDocuments;
renderOverlayDocuments = function ownerRenderOverlayDocuments(role) {
  ownerBaseRenderOverlayDocuments(role);
  if (role.cover_letter?.pdf || role.cover_letter?.docx) return;
  const cover = document.querySelector('#ov-cover');
  if (!cover || cover.querySelector('.generate-package-cover')) return;
  const active = ownerActivePackageRequest(role);
  const button = document.createElement('button');
  button.type = 'button';
  button.className = `card-button primary generate-package-cover${active ? ' is-loading' : ''}`;
  button.disabled = Boolean(active);
  button.innerHTML = active
    ? '<span class="owner-spinner" aria-hidden="true"></span> Generating package…'
    : 'Generate CV + cover letter';
  button.addEventListener('click', () => ownerQueuePackageGeneration(role).catch(error => showToast(error.message, true)));
  cover.append(button);
};

/* Reload the same deep-linked job after an owner-triggered package regeneration
 * finishes, so the newly published CV/cover letter replace the loading state. */
const ownerBaseSetupAiPolling = setupAiPolling;
setupAiPolling = function ownerSetupAiPolling(role) {
  ownerBaseSetupAiPolling(role);
  if (!state.aiPollTimer) return;
  const baseTimer = state.aiPollTimer;
  clearInterval(baseTimer);
  state.aiPollTimer = setInterval(async () => {
    if (!state.overlayOpen || state.overlayKey !== role.key) return;
    try {
      const records = await loadCollection('ai_requests');
      state.aiRequests = records.map(record => ({ id: record.id, ...dataOf(record), createdAt: record.createdAt, updatedAt: record.updatedAt }));
      renderOverlayAi(role);
      renderOverlayTemplate(role);
      renderOverlayResumePreview(role);
      renderOverlayDocuments(role);
      const pending = aiRequestsForRole(role.key).some(record => ['pending', 'processing'].includes(dataOf(record).state || 'pending'));
      const ownedId = sessionStorage.getItem(`career-generation-owned:${role.key}`);
      if (!pending) {
        clearInterval(state.aiPollTimer);
        state.aiPollTimer = null;
        if (ownedId && sessionStorage.getItem(`career-generation-reloaded:${role.key}`) !== ownedId) {
          sessionStorage.setItem(`career-generation-reloaded:${role.key}`, ownedId);
          window.setTimeout(() => window.location.reload(), 900);
        }
      }
    } catch (error) {
      console.warn('AI request refresh unavailable', error);
    }
  }, 3000);
};

/* ---------- In-overlay previous / next job navigation ---------- */
function ownerNavigateOverlay(delta) {
  const roles = sortedRoles(filteredRoles());
  const current = roles.findIndex(role => role.key === state.overlayKey);
  const nextIndex = current + delta;
  if (current < 0 || nextIndex < 0 || nextIndex >= roles.length) return;
  const target = roles[nextIndex];
  const url = new URL(window.location.href);
  url.searchParams.set('job', target.key);
  history.replaceState({ job: target.key }, '', url);
  openOverlay(target.key, false);
}

const ownerBaseRenderOverlayContent = renderOverlayContent;
renderOverlayContent = function ownerRenderOverlayContent(role) {
  ownerBaseRenderOverlayContent(role);
  const header = document.querySelector('.compact-detail-header');
  const close = header?.querySelector('.overlay-close');
  if (!header || !close || header.querySelector('.owner-overlay-nav')) return;
  const roles = sortedRoles(filteredRoles());
  const index = roles.findIndex(item => item.key === role.key);
  const nav = document.createElement('div');
  nav.className = 'owner-overlay-nav';
  const prev = document.createElement('button');
  prev.type = 'button';
  prev.className = 'owner-nav-button';
  prev.setAttribute('aria-label', 'Previous job');
  prev.title = 'Previous job';
  prev.textContent = '←';
  prev.disabled = index <= 0;
  prev.addEventListener('click', () => ownerNavigateOverlay(-1));
  const counter = document.createElement('span');
  counter.textContent = index >= 0 ? `${index + 1} / ${roles.length}` : '';
  const next = document.createElement('button');
  next.type = 'button';
  next.className = 'owner-nav-button';
  next.setAttribute('aria-label', 'Next job');
  next.title = 'Next job';
  next.textContent = '→';
  next.disabled = index < 0 || index >= roles.length - 1;
  next.addEventListener('click', () => ownerNavigateOverlay(1));
  nav.append(prev, counter, next);
  header.insertBefore(nav, close);
};

/* ---------- Final owner-requested visual adjustments ---------- */
const ownerStyle = document.createElement('style');
ownerStyle.id = 'owner-career-acceptance-style';
ownerStyle.textContent = `
  .gmail-blocked { display: none !important; }

  @media (min-width: 901px) {
    .desktop-search-control {
      flex: 1 1 520px !important;
      min-width: 360px !important;
      width: clamp(360px, 34vw, 620px) !important;
      max-width: 620px !important;
    }
  }

  .kanban-column[data-stage="manual_review_needed"] { background: #fffcf1 !important; }
  .kanban-column[data-stage="manual_review_needed"]::before { background: #e2b53f !important; }
  .kanban-column[data-stage="found"] { background: #f6f9ff !important; }
  .kanban-column[data-stage="processing"] { background: #fff8ef !important; }
  .kanban-column[data-stage="ready_review"] { background: #faf7ff !important; }
  .kanban-column[data-stage="applied"] { background: #f3fbf9 !important; }
  .kanban-column[data-stage="inactive"] { background: #f2f4f7 !important; }

  .role-card { padding: 7px 8px !important; }
  .role-card .brief { margin: 3px 0 4px !important; -webkit-line-clamp: 1 !important; }
  .role-card .quick-files { margin: 3px 0 4px !important; }
  .role-card .tag-row { gap: 3px !important; }
  .role-card .stage-actions { margin-top: 2px !important; }

  .column-load-more {
    width: 100%;
    border: 1px dashed #cfd7e5;
    border-radius: 8px;
    background: rgba(255,255,255,.72);
    color: #53617a;
    padding: 8px;
    font-size: .66rem;
    font-weight: 750;
    cursor: pointer;
  }
  .column-load-more:hover, .column-load-more:focus-visible { border-color: #7fa2f4; color: #2859c8; outline: none; }

  .owner-spinner {
    display: inline-block;
    width: 12px;
    height: 12px;
    border: 2px solid currentColor;
    border-right-color: transparent;
    border-radius: 50%;
    vertical-align: -2px;
    animation: owner-career-spin .75s linear infinite;
  }
  @keyframes owner-career-spin { to { transform: rotate(360deg); } }

  .owner-overlay-nav {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    margin-left: auto;
    font-size: .62rem;
    color: #6c7891;
  }
  .owner-nav-button {
    width: 30px;
    height: 30px;
    border: 1px solid #dfe5ee;
    border-radius: 8px;
    background: #fff;
    color: #253650;
    cursor: pointer;
    font-weight: 800;
  }
  .owner-nav-button:disabled { opacity: .35; cursor: default; }

  @media (min-width: 1200px) {
    .overlay-workspace {
      grid-template-columns: minmax(0, 1.35fr) minmax(620px, 1fr) !important;
      gap: 12px !important;
    }
    .detail-utility {
      display: grid !important;
      grid-template-columns: repeat(2, minmax(0, 1fr)) !important;
      gap: 8px !important;
      align-content: start !important;
      overflow-y: auto !important;
    }
    .detail-utility > .overlay-assistant-top { grid-column: 1; grid-row: 1; }
    .detail-utility > .cover-box { grid-column: 2; grid-row: 1; }
    .detail-utility > details { grid-column: 1 / -1; }
    .resume-viewer { min-height: 610px !important; height: calc(100dvh - 240px) !important; }
  }

  @media (max-width: 1199px) {
    .owner-overlay-nav span { display: none; }
  }
`;
document.head.append(ownerStyle);

document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.gmail-blocked').forEach(panel => panel.remove());
  // app.js init() yields at async setup; if an extremely fast local response
  // completed before this file loaded, redraw once with the final owner layer.
  if (state?.roles?.length) renderBoard();
});
