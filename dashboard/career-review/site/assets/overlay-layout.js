/* overlay-layout.js — owner-approved job-detail layout and scroll behavior */
'use strict';

(() => {
  let overlayOpenScrollY = 0;
  let overlayOpenScrollX = 0;
  let ownerOptimisticPackage = null;

  const baseOpenOverlay = openOverlay;
  openOverlay = function ownerOpenOverlay(key, syncUrl = true) {
    if (!state.overlayOpen) {
      overlayOpenScrollY = window.scrollY;
      overlayOpenScrollX = window.scrollX;
    }
    return baseOpenOverlay(key, syncUrl);
  };

  /* Preserve the exact board position that was visible when the detail opened.
     Focusing the originating card must never scroll the page after dismissal. */
  closeOverlay = function ownerCloseOverlay() {
    if (!state.overlayOpen) return;
    const returnKey = state.overlayReturnKey || state.overlayKey;
    state.overlayOpen = false;
    state.overlayKey = '';
    state.overlayReturnKey = '';
    const url = new URL(window.location.href);
    url.searchParams.delete('job');
    history.replaceState({}, '', `${url.pathname}${url.search}${url.hash}`);
    if (state.aiPollTimer) {
      clearInterval(state.aiPollTimer);
      state.aiPollTimer = null;
    }
    const overlay = $('#job-overlay');
    overlay.hidden = true;
    overlay.setAttribute('aria-hidden', 'true');
    document.documentElement.classList.remove('overlay-open');
    document.body.classList.remove('overlay-open');

    window.scrollTo({ left: overlayOpenScrollX, top: overlayOpenScrollY, behavior: 'auto' });
    if (returnKey) {
      requestAnimationFrame(() => {
        const card = $(`.role-card[data-role-key="${returnKey}"]`);
        if (card) {
          try { card.focus({ preventScroll: true }); }
          catch { card.focus(); window.scrollTo(overlayOpenScrollX, overlayOpenScrollY); }
        }
      });
    }
  };

  function ownerPackageRequests(role) {
    return aiRequestsForRole(role.key)
      .filter(record => ['rebuild_documents', 'edit_cv'].includes(dataOf(record).request_type))
      .sort((left, right) => String(right.updatedAt || right.createdAt || '').localeCompare(String(left.updatedAt || left.createdAt || '')));
  }

  function ownerPackageProgress(record) {
    const answer = dataOf(record).answer;
    if (!answer) return null;
    try {
      const parsed = typeof answer === 'string' ? JSON.parse(answer) : answer;
      return parsed?.kind === 'package_progress' ? parsed : null;
    } catch {
      return null;
    }
  }

  function ownerPackageSnapshot(role, templateId) {
    const requests = ownerPackageRequests(role);
    const active = requests.find(record => ['pending', 'processing'].includes(dataOf(record).state || 'pending'));
    if (active) {
      return {
        status: 'active',
        record: active,
        templateId,
        progress: ownerPackageProgress(active) || {
          kind: 'package_progress',
          phase: 'queued',
          label: 'Queued',
          percent: 0,
          elapsed_seconds: 0
        }
      };
    }
    const latest = requests[0];
    if (latest && dataOf(latest).state === 'failed') {
      return { status: 'failed', record: latest, templateId };
    }
    const ownedId = sessionStorage.getItem(`career-generation-owned:${role.key}`);
    if (latest && dataOf(latest).state === 'done' && ownedId && ownedId === latest.id
        && sessionStorage.getItem(`career-generation-reloaded:${role.key}`) !== ownedId) {
      return {
        status: 'success',
        record: latest,
        templateId,
        progress: { kind: 'package_progress', phase: 'complete', label: `${templateLabel(templateId)} ready`, percent: 100, elapsed_seconds: 0, eta_seconds: 0 }
      };
    }
    if (ownerOptimisticPackage?.roleKey === role.key) {
      return {
        status: 'active',
        templateId: ownerOptimisticPackage.templateId,
        progress: { kind: 'package_progress', phase: 'queued', label: 'Queued', percent: 0, elapsed_seconds: 0 }
      };
    }
    return null;
  }

  function ownerFormatDuration(seconds) {
    const value = Math.max(0, Math.round(Number(seconds) || 0));
    if (value < 60) return `${value}s`;
    return `${Math.floor(value / 60)}m ${String(value % 60).padStart(2, '0')}s`;
  }

  function ownerProgressTiming(snapshot, progress) {
    const now = Date.now();
    const createdMs = Date.parse(snapshot.record?.createdAt || '');
    const updatedMs = Date.parse(snapshot.record?.updatedAt || '');
    const backendElapsed = Math.max(0, Number(progress.elapsed_seconds) || 0);
    const elapsed = snapshot.status === 'active' && Number.isFinite(createdMs)
      ? Math.max(backendElapsed, (now - createdMs) / 1000)
      : backendElapsed;
    let eta = null;
    if (Number.isFinite(Number(progress.eta_seconds))) {
      const sinceProgress = snapshot.status === 'active' && Number.isFinite(updatedMs)
        ? Math.max(0, (now - updatedMs) / 1000)
        : 0;
      eta = Math.max(0, Number(progress.eta_seconds) - sinceProgress);
    }
    return { elapsed, eta };
  }

  function ownerProgressCard(role, templateId, snapshot) {
    const card = document.createElement('div');
    card.className = `package-progress-card package-progress-${snapshot.status}`;
    card.setAttribute('role', snapshot.status === 'failed' ? 'alert' : 'status');
    const title = document.createElement('strong');
    const progress = snapshot.progress || {};
    const phaseLabel = progress.phase === 'rendering'
      ? `Rendering ${templateLabel(templateId)}`
      : progress.label || 'Generating package';
    title.textContent = snapshot.status === 'failed'
      ? 'Package generation failed'
      : snapshot.status === 'success'
        ? `${templateLabel(templateId)} ready`
        : phaseLabel;
    const details = document.createElement('span');
    if (snapshot.status === 'failed') {
      details.textContent = String(dataOf(snapshot.record).answer || 'The package could not be generated.');
      card.append(title, details);
    } else {
      const percent = Math.max(0, Math.min(100, Number(progress.percent) || 0));
      const timing = ownerProgressTiming(snapshot, progress);
      const track = document.createElement('div');
      track.className = 'package-progress-track';
      track.setAttribute('role', 'progressbar');
      track.setAttribute('aria-valuemin', '0');
      track.setAttribute('aria-valuemax', '100');
      track.setAttribute('aria-valuenow', String(Math.round(percent)));
      const fill = document.createElement('span');
      fill.className = 'package-progress-fill';
      fill.style.width = `${percent}%`;
      track.append(fill);
      const eta = timing.eta == null ? 'Estimating ETA…' : `ETA ${ownerFormatDuration(timing.eta)}`;
      details.textContent = `${Math.round(percent)}% · elapsed ${ownerFormatDuration(timing.elapsed)} · ${eta}`;
      card.append(title, track, details);
    }
    if (snapshot.status === 'failed') {
      const retry = document.createElement('button');
      retry.type = 'button';
      retry.className = 'card-button primary package-progress-retry';
      retry.textContent = `Retry ${templateLabel(templateId)}`;
      retry.addEventListener('click', () => ownerQueuePackageGeneration(role, templateId).catch(error => showToast(error.message, true)));
      card.append(retry);
    }
    return card;
  }

  function ownerRenderPackageProgress(role, templateId) {
    const snapshot = ownerPackageSnapshot(role, templateId);
    const group = document.querySelector('.owner-resume-selector')
      || document.querySelector('#ov-template-files')?.closest('.menu-group');
    const selectorCard = group?.querySelector('#ov-package-progress');
    if (snapshot && group) {
      const card = ownerProgressCard(role, templateId, snapshot);
      card.id = 'ov-package-progress';
      if (selectorCard) selectorCard.replaceWith(card);
      else group.append(card);
    } else if (selectorCard) {
      selectorCard.remove();
    }

    const empty = document.querySelector('#ov-resume-empty');
    if (!empty) return;
    const emptyCard = empty.querySelector('#ov-package-progress-empty');
    if (snapshot) {
      empty.replaceChildren();
      const card = ownerProgressCard(role, templateId, snapshot);
      card.id = 'ov-package-progress-empty';
      empty.append(card);
    } else if (emptyCard) {
      empty.replaceChildren('Selected resume PDF is not available for this job.');
    }
  }

  function ownerMarkPackageStarting(role, templateId) {
    ownerOptimisticPackage = { roleKey: role.key, templateId };
    const button = document.querySelector('.generate-selected-resume');
    if (button) {
      button.disabled = true;
      button.classList.add('is-loading');
      button.innerHTML = `<span class="owner-spinner" aria-hidden="true"></span> Generating ${escapeHtml(templateLabel(templateId))}…`;
    }
    ownerRenderPackageProgress(role, templateId);
  }

  /* A generate button is a document-rebuild operation, not a free-form CV edit.
     The rebuild backend owns deterministic render + dashboard republish, so using
     it here prevents the UI from reporting success while still serving stale
     document metadata/assets. Keep legacy edit_cv requests visible as active so a
     pre-fix request cannot be duplicated while it is still processing. */
  ownerActivePackageRequest = function ownerPublishedPackageRequest(role) {
    return ownerPackageRequests(role).find(record => ['pending', 'processing'].includes(dataOf(record).state || 'pending')) || null;
  };

  ownerQueuePackageGeneration = async function ownerPublishedPackageGeneration(role, templateId = selectedTemplateFor(role)) {
    const active = ownerActivePackageRequest(role);
    if (active) {
      showToast('This job package is already being generated.');
      ownerRenderPackageProgress(role, templateId);
      setupAiPolling(role);
      return active;
    }
    ownerMarkPackageStarting(role, templateId);
    const prompt = [
      `Rebuild and validate the Career Engine package for this exact job. The selected submission CV is ${templateLabel(templateId)} (${templateId}).`,
      'Generate/rerender the role-specific CV PDF/DOCX and the evidence-grounded cover letter PDF/DOCX when applicable.',
      'Republish the private dashboard so the generated files and their metadata are immediately available after reload.',
      'Do not invent claims, do not use generic placeholder cover text, and do not send or submit anything.'
    ].join(' ');
    let record;
    try {
      record = await createRecord('ai_requests', {
        role_key: role.key,
        request_type: 'rebuild_documents',
        prompt,
        state: 'pending'
      }, `rebuild-package-${role.key}-${templateId}-${Date.now()}`);
    } catch (error) {
      ownerOptimisticPackage = null;
      renderOverlayTemplate(role);
      renderOverlayResumePreview(role);
      throw error;
    }
    const normalized = { id: record.id, ...dataOf(record), createdAt: record.createdAt, updatedAt: record.updatedAt };
    state.aiRequests.push(normalized);
    ownerOptimisticPackage = null;
    if (record.id) sessionStorage.setItem(`career-generation-owned:${role.key}`, record.id);
    try {
      await createRecord('history', {
        role_key: role.key,
        event: 'package_generation_requested',
        note: `Rebuild selected CV and package: ${templateLabel(templateId)}`
      }, `history-rebuild-${role.key}-${Date.now()}`);
    } catch (error) {
      console.warn('Package generation history unavailable', error);
    }
    renderOverlayAi(role);
    renderOverlayTemplate(role);
    renderOverlayResumePreview(role);
    renderOverlayDocuments(role);
    setupAiPolling(role);
    return normalized;
  };

  const ownerBaseRenderOverlayTemplateWithProgress = renderOverlayTemplate;
  renderOverlayTemplate = function ownerRenderOverlayTemplateWithProgress(role) {
    ownerBaseRenderOverlayTemplateWithProgress(role);
    const selected = selectedTemplateFor(role);
    const snapshot = ownerPackageSnapshot(role, selected);
    const button = document.querySelector('.generate-selected-resume');
    if (button && snapshot?.status === 'active') {
      button.disabled = true;
      button.classList.add('is-loading');
      button.innerHTML = `<span class="owner-spinner" aria-hidden="true"></span> Generating ${escapeHtml(templateLabel(selected))}…`;
    } else if (button && snapshot?.status === 'failed') {
      button.disabled = false;
      button.classList.remove('is-loading');
      button.textContent = `Retry ${templateLabel(selected)}`;
    }
    ownerRenderPackageProgress(role, selected);
  };

  const ownerBaseRenderOverlayResumePreviewWithProgress = renderOverlayResumePreview;
  renderOverlayResumePreview = function ownerRenderOverlayResumePreviewWithProgress(role) {
    ownerBaseRenderOverlayResumePreviewWithProgress(role);
    ownerRenderPackageProgress(role, selectedTemplateFor(role));
  };

  function ownerReloadSameJob(role) {
    const url = new URL(window.location.href);
    url.searchParams.set('job', role.key);
    window.location.replace(`${url.pathname}${url.search}${url.hash}`);
  }

  const ownerBaseSetupAiPollingWithProgress = setupAiPolling;
  setupAiPolling = function ownerSetupAiPollingWithProgress(role) {
    ownerBaseSetupAiPollingWithProgress(role);
    if (!state.aiPollTimer) return;
    clearInterval(state.aiPollTimer);
    state.aiPollTimer = setInterval(async () => {
      if (!state.overlayOpen || state.overlayKey !== role.key) return;
      try {
        const records = await loadCollection('ai_requests', 300, true, true);
        state.aiRequests = records.map(record => ({ id: record.id, ...dataOf(record), createdAt: record.createdAt, updatedAt: record.updatedAt }));
        renderOverlayAi(role);
        renderOverlayTemplate(role);
        renderOverlayResumePreview(role);
        renderOverlayDocuments(role);
        const ownedId = sessionStorage.getItem(`career-generation-owned:${role.key}`);
        const owned = ownedId ? state.aiRequests.find(record => record.id === ownedId) : null;
        const anyPending = aiRequestsForRole(role.key).some(record => ['pending', 'processing'].includes(dataOf(record).state || 'pending'));
        if (owned && dataOf(owned).state === 'done'
            && sessionStorage.getItem(`career-generation-reloaded:${role.key}`) !== ownedId) {
          sessionStorage.setItem(`career-generation-reloaded:${role.key}`, ownedId);
          clearInterval(state.aiPollTimer);
          state.aiPollTimer = null;
          ownerRenderPackageProgress(role, selectedTemplateFor(role));
          window.setTimeout(() => ownerReloadSameJob(role), 900);
          return;
        }
        if (!anyPending) {
          clearInterval(state.aiPollTimer);
          state.aiPollTimer = null;
          ownerRenderPackageProgress(role, selectedTemplateFor(role));
        }
      } catch (error) {
        console.warn('AI request refresh unavailable', error);
      }
    }, 3000);
  };

  /* bulk-table.js already owns the persistent stage select, including the
     special Rebuild CV & cover letter action. Reuse that one source of behavior
     and only relocate it to the far right of the metadata strip. */
  function moveExistingStatusToRight() {
    if (typeof ensureOverlayStageSelect === 'function') ensureOverlayStageSelect();
    const strip = document.querySelector('.overlay-meta-strip');
    const status = strip?.querySelector('.detail-stage-inline');
    if (!strip || !status) return;
    status.classList.add('owner-overlay-status-control');
    strip.append(status);
  }

  function moveResumeSelectorAboveViewer() {
    const resumeWorkspace = document.querySelector('.resume-workspace');
    const menu = document.querySelector('.overlay-meta-strip .detail-tool-menu');
    if (!resumeWorkspace || !menu) return;

    const templateGroup = [...menu.querySelectorAll('.menu-group')]
      .find(group => group.querySelector('#ov-template-options'));
    if (templateGroup) {
      templateGroup.classList.add('owner-resume-selector');
      resumeWorkspace.prepend(templateGroup);
    }

    /* Status is now the existing always-visible right-aligned control and
       Submission CV is expanded above the preview, so the old popover is redundant. */
    menu.remove();
  }

  const baseRenderOverlayContent = renderOverlayContent;
  renderOverlayContent = function ownerLayoutRenderOverlayContent(role) {
    baseRenderOverlayContent(role);
    moveExistingStatusToRight();
    moveResumeSelectorAboveViewer();
  };

  const style = document.createElement('style');
  style.id = 'owner-overlay-layout-style';
  style.textContent = `
    .overlay-meta-strip {
      display: flex !important;
      align-items: center !important;
      gap: 10px !important;
    }

    .detail-stage-inline.owner-overlay-status-control {
      margin-left: auto !important;
      display: inline-flex !important;
      align-items: center;
      gap: 6px;
      flex: 0 0 auto;
      color: var(--muted);
      font-size: .64rem;
      font-weight: 800;
    }
    .detail-stage-inline.owner-overlay-status-control > strong {
      text-transform: uppercase;
      letter-spacing: .04em;
    }
    .detail-stage-inline.owner-overlay-status-control .detail-stage-select {
      min-width: 168px;
      max-width: 220px;
      border: 1px solid var(--line);
      border-radius: 7px;
      background: var(--paper);
      color: var(--ink);
      padding: 5px 28px 5px 8px;
      font-size: .69rem;
      font-weight: 700;
      outline: none;
    }
    .detail-stage-inline.owner-overlay-status-control .detail-stage-select:focus {
      border-color: #2859c8;
      box-shadow: 0 0 0 2px rgba(40,89,200,.12);
    }

    .owner-resume-selector {
      border-bottom: 1px solid var(--line);
      background: #fbfcff;
      padding: 9px 10px 8px;
    }
    .owner-resume-selector > .menu-heading {
      display: block;
      margin: 0 0 6px;
      color: var(--muted);
      font-size: .65rem;
      font-weight: 850;
      letter-spacing: .06em;
      text-transform: uppercase;
    }
    .owner-resume-selector #ov-template-options {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 5px;
    }
    .owner-resume-selector #ov-template-options .direct-menu-item {
      min-height: 38px;
      border: 1px solid var(--line);
      border-radius: 7px;
      background: var(--paper);
      padding: 6px 8px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      text-align: left;
      color: var(--ink);
      font-size: .69rem;
      font-weight: 760;
    }
    .owner-resume-selector #ov-template-options .direct-menu-item:hover,
    .owner-resume-selector #ov-template-options .direct-menu-item:focus-visible {
      border-color: #7fa2f4;
      outline: none;
    }
    .owner-resume-selector #ov-template-options .direct-menu-item.is-active {
      border-color: #2f63ef;
      background: #eef3ff;
      box-shadow: inset 3px 0 0 #2f63ef;
    }
    .owner-resume-selector .menu-item-meta {
      color: var(--muted);
      font-size: .60rem;
      font-weight: 700;
      white-space: nowrap;
    }
    .owner-resume-selector #ov-template-default,
    .owner-resume-selector #ov-template-note {
      margin: 5px 0 0;
      font-size: .62rem;
    }
    .owner-resume-selector #ov-template-files {
      margin-top: 5px;
    }
    .owner-resume-selector + .resume-viewer-head {
      border-top: 0 !important;
    }

    .package-progress-card {
      display: grid;
      gap: 5px;
      width: 100%;
      min-width: 0;
      box-sizing: border-box;
      margin-top: 7px;
      padding: 8px 9px;
      border: 1px solid #b9c9ee;
      border-radius: 8px;
      background: #f4f7ff;
      color: var(--ink);
      font-size: .68rem;
      overflow-wrap: anywhere;
    }
    .package-progress-track {
      width: 100%;
      height: 7px;
      overflow: hidden;
      border-radius: 999px;
      background: #dfe7f8;
    }
    .package-progress-fill {
      display: block;
      height: 100%;
      border-radius: inherit;
      background: #2f63ef;
      transition: width .2s ease-out;
    }
    .package-progress-card > span { color: var(--muted); line-height: 1.35; }
    .package-progress-failed { border-color: #e5b4b4; background: #fff7f7; }
    .package-progress-failed > span { color: #8c3030; }
    .package-progress-success { border-color: #a9d7bd; background: #f2fbf5; }
    .package-progress-retry { justify-self: start; margin-top: 3px; }
    #ov-package-progress-empty { margin: 14px; }

    @media (max-width: 1050px) {
      .owner-resume-selector #ov-template-options { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .detail-stage-inline.owner-overlay-status-control { width: 100%; margin-left: 0 !important; justify-content: flex-end; }
      .overlay-meta-strip { flex-wrap: wrap !important; }
    }

    /* Mobile must keep the selected CV viewer between the selector and the
       application utilities. Force one explicit vertical flow instead of
       relying on overlapping grid/flex rules from the desktop layout. */
    @media (max-width: 900px) {
      .overlay-workspace {
        display: flex !important;
        flex-direction: column !important;
        overflow-y: auto !important;
      }
      .resume-workspace {
        display: flex !important;
        flex-direction: column !important;
        flex: 0 0 auto !important;
        order: 1 !important;
        min-height: auto !important;
        overflow: visible !important;
      }
      .owner-resume-selector,
      .resume-viewer-head,
      .resume-viewer {
        flex: 0 0 auto !important;
      }
      .resume-viewer-head {
        display: flex !important;
      }
      .resume-viewer {
        display: block !important;
        height: 65dvh !important;
        min-height: 65dvh !important;
      }
      #ov-resume-frame:not([hidden]),
      #ov-resume-empty:not([hidden]) {
        display: block !important;
      }
      .detail-utility {
        order: 2 !important;
        flex: 0 0 auto !important;
      }
    }

    @media (max-width: 680px) {
      .owner-resume-selector #ov-template-options { grid-template-columns: 1fr; }
      .detail-stage-inline.owner-overlay-status-control { justify-content: stretch; }
      .detail-stage-inline.owner-overlay-status-control .detail-stage-select { flex: 1 1 auto; max-width: none; }
      .resume-file-actions { flex-wrap: wrap !important; }
      .resume-viewer { height: 62dvh !important; min-height: 62dvh !important; }
    }
  `;
  document.head.append(style);
})();
