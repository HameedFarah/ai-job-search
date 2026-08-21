/* irrelevant-feedback.js — owner relevance feedback + dismissal UX extension for Career Engine */
'use strict';

(() => {
  if (typeof STAGES === 'undefined' || typeof stageFor !== 'function') return;

  if (!STAGES.some(stage => stage.id === 'irrelevant')) {
    const inactiveIndex = STAGES.findIndex(stage => stage.id === 'inactive');
    STAGES.splice(inactiveIndex >= 0 ? inactiveIndex : STAGES.length, 0, {
      id: 'irrelevant',
      label: 'Irrelevant',
      next: 'found',
      nextLabel: 'Restore to workflow'
    });
  }

  const originalStageFor = stageFor;
  stageFor = function stageForWithOwnerRelevance(role) {
    if (roleHasActiveSubmissionEvidence(role)) return 'applied';
    const workflowStage = normalizedWorkflowStage(state.workflow.get(role?.key)?.stage, role);
    const ownerRelevance = normalizedStatus(
      role?.outcome
      || role?.owner_relevance
      || role?.processing_state?.owner_relevance
    );
    const exportedIrrelevantMarker = /Owner marked this role Irrelevant/i.test(String(role?.brief || ''));
    if (ownerRelevance === 'irrelevant' || exportedIrrelevantMarker) {
      // A deliberate owner move away from Irrelevant should render immediately,
      // even before the next server-side reconciliation clears the stale export.
      if (workflowStage && !['irrelevant', 'inactive'].includes(workflowStage)) return workflowStage;
      return 'irrelevant';
    }
    return originalStageFor(role);
  };

  async function saveOwnerRelevanceEvent(role, event, fromStage, toStage) {
    const at = new Date().toISOString();
    const saved = await createRecord('history', {
      // Keep the live history payload to the schema-proven fields. Rich metadata
      // remains append-only inside note JSON so Site Data cannot reject an owner
      // relevance update because of an undeclared top-level field.
      role_key: role.key,
      event,
      from_stage: fromStage || '',
      to_stage: toStage || '',
      note: JSON.stringify({
        reason: event === 'role_marked_irrelevant'
          ? 'Owner explicitly marked this vacancy Irrelevant for future fit calibration.'
          : 'Owner explicitly restored this vacancy from Irrelevant to the normal workflow.',
        actor: 'owner',
        ui_source: 'status_dropdown',
        evidence_type: 'explicit_owner_relevance_feedback',
        recorded_at: at,
        company: role.company || '',
        role: role.role || '',
        job_id: role.job_id || ''
      })
    }, `owner-relevance-${role.key}-${event}-${Date.now()}`);
    if (Array.isArray(state.history)) {
      state.history.push({
        id: saved.id,
        ...(dataOf(saved) || {}),
        createdAt: saved.createdAt || at,
        updatedAt: saved.updatedAt || at
      });
    }
    return saved;
  }

  function undoStage(priorStage) {
    const known = STAGES.some(stage => stage.id === priorStage);
    if (!known || ['inactive', 'irrelevant'].includes(priorStage)) return 'found';
    return priorStage;
  }

  async function undoDismissal(role, priorStage, dismissedStage, baseMoveRole) {
    const targetStage = undoStage(priorStage);
    const ok = await baseMoveRole(role, targetStage, false);
    if (ok === false) throw new Error('The previous status could not be restored.');

    const workflowStage = normalizedWorkflowStage(state.workflow.get(role?.key)?.stage, role);
    if (workflowStage !== targetStage) {
      throw new Error('The previous status was not confirmed after Undo.');
    }

    if (dismissedStage === 'irrelevant') {
      await saveOwnerRelevanceEvent(role, 'role_irrelevant_retracted', 'irrelevant', targetStage);
    }
    return true;
  }

  function closeDetailFor(role) {
    if (typeof closeOverlay !== 'function') return;
    if (state.overlayOpen && state.overlayKey === role.key) closeOverlay();
  }

  function showDismissalSuccess(role, priorStage, nextStage, baseMoveRole, feedbackError = null) {
    closeDetailFor(role);
    const irrelevant = nextStage === 'irrelevant';
    const message = irrelevant ? 'Marked Irrelevant' : 'Job closed';
    const submessage = feedbackError
      ? `Status changed, but feedback evidence could not be saved: ${feedbackError.message}`
      : (irrelevant
          ? 'This feedback will calibrate future job scoring.'
          : 'Moved to Closed / inactive.');
    showActionToast(
      message,
      'Undo',
      () => undoDismissal(role, priorStage, nextStage, baseMoveRole),
      { duration: 7000, submessage }
    );
  }

  function installMoveRoleWrapper() {
    if (typeof moveRole !== 'function' || moveRole.__ownerRelevanceWrapped) return;
    const baseMoveRole = moveRole;
    const wrapped = async function moveRoleWithOwnerRelevance(role, nextStage, requireConfirmation = false) {
      const priorStage = stageFor(role);
      const ok = await baseMoveRole(role, nextStage, requireConfirmation);
      if (ok === false) return false;
      const workflowStage = normalizedWorkflowStage(state.workflow.get(role?.key)?.stage, role);
      let feedbackError = null;

      try {
        if (nextStage === 'irrelevant' && priorStage !== 'irrelevant' && workflowStage === 'irrelevant') {
          await saveOwnerRelevanceEvent(role, 'role_marked_irrelevant', priorStage, 'irrelevant');
        } else if (priorStage === 'irrelevant' && nextStage !== 'irrelevant' && workflowStage === nextStage) {
          await saveOwnerRelevanceEvent(role, 'role_irrelevant_retracted', 'irrelevant', nextStage);
        }
      } catch (error) {
        feedbackError = error;
        console.error('Owner relevance evidence could not be saved', error);
      }

      if (['irrelevant', 'inactive'].includes(nextStage) && workflowStage === nextStage) {
        showDismissalSuccess(role, priorStage, nextStage, baseMoveRole, feedbackError);
      } else if (feedbackError) {
        showToast(`Status changed, but owner-feedback evidence could not be saved: ${feedbackError.message}`, true);
      } else if (priorStage === 'irrelevant' && nextStage !== 'irrelevant' && workflowStage === nextStage) {
        showToast('Irrelevant label removed. The role is back in the normal workflow.');
      }
      return ok === undefined ? true : ok;
    };
    wrapped.__ownerRelevanceWrapped = true;
    moveRole = wrapped;
  }

  // bulk-table.js installs the final optimistic moveRole implementation later in
  // document order. Wait until DOMContentLoaded so every card/table/drag/detail
  // status path is wrapped once, while the STAGES entry is available immediately.
  if (document.readyState === 'loading' || document.readyState === 'interactive') {
    document.addEventListener('DOMContentLoaded', installMoveRoleWrapper, { once: true });
  } else {
    installMoveRoleWrapper();
  }
})();
