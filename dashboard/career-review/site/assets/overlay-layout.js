/* overlay-layout.js — owner-approved job-detail layout and scroll behavior */
'use strict';

(() => {
  let overlayOpenScrollY = 0;
  let overlayOpenScrollX = 0;

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

    window.scrollTo({ left: overlayOpenScrollX, top: overlayOpenScrollY, behavior: 'instant' });
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

  function buildVisibleStatusControl(role) {
    const meta = document.querySelector('.overlay-meta-strip');
    if (!meta) return;

    meta.querySelector('.owner-overlay-status-control')?.remove();
    const control = document.createElement('label');
    control.className = 'owner-overlay-status-control';
    const caption = document.createElement('span');
    caption.textContent = 'Status';
    const select = document.createElement('select');
    select.className = 'owner-overlay-stage-select card-stage-select';
    select.setAttribute('aria-label', `Change status for ${role.role}`);

    const current = stageFor(role);
    for (const stage of STAGES) {
      const option = document.createElement('option');
      option.value = stage.id;
      option.textContent = stage.label;
      option.selected = stage.id === current;
      select.append(option);
    }

    select.addEventListener('change', async () => {
      const nextStage = select.value;
      if (nextStage === stageFor(role)) return;
      select.disabled = true;
      const ok = await moveRole(role, nextStage, nextStage === 'applied');
      if (ok === false && select.isConnected) select.value = stageFor(role);
      if (select.isConnected) select.disabled = false;
    });

    control.append(caption, select);
    meta.append(control);
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

    /* Stage is now the always-visible right-aligned select and Submission CV is
       expanded above the preview, so the old three-dot popover is redundant. */
    menu.remove();
  }

  const baseRenderOverlayContent = renderOverlayContent;
  renderOverlayContent = function ownerLayoutRenderOverlayContent(role) {
    baseRenderOverlayContent(role);
    buildVisibleStatusControl(role);
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

    .owner-overlay-status-control {
      margin-left: auto;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      flex: 0 0 auto;
      color: var(--muted);
      font-size: .64rem;
      font-weight: 800;
    }
    .owner-overlay-status-control > span {
      text-transform: uppercase;
      letter-spacing: .04em;
    }
    .owner-overlay-stage-select {
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
    .owner-overlay-stage-select:focus {
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

    @media (max-width: 1050px) {
      .owner-resume-selector #ov-template-options { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .owner-overlay-status-control { width: 100%; margin-left: 0; justify-content: flex-end; }
      .overlay-meta-strip { flex-wrap: wrap !important; }
    }
    @media (max-width: 680px) {
      .owner-resume-selector #ov-template-options { grid-template-columns: 1fr; }
      .owner-overlay-status-control { justify-content: stretch; }
      .owner-overlay-stage-select { flex: 1 1 auto; max-width: none; }
    }
  `;
  document.head.append(style);
})();
