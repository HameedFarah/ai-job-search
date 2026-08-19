/* irrelevant-feedback.js — owner relevance feedback extension for Career Engine */
'use strict';

(() => {
  if (typeof STAGES === 'undefined' || typeof stageFor !== 'function' || typeof moveRole !== 'function') return;

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
      role_key: role.key,
      job_id: role.job_id || '',
      company: role.company || '',
      role: role.role || '',
      event,
      actor: 'owner',
      ui_source: 'status_dropdown',
      evidence_type: 'explicit_owner_relevance_feedback',
      from_stage: fromStage || '',
      to_stage: toStage || '',
      recorded_at: at,
      note: JSON.stringify({
        reason: event === 'role_marked_irrelevant'
          ? 'Owner explicitly marked this vacancy Irrelevant for future fit calibration.'
          : 'Owner explicitly restored this vacancy from Irrelevant to the normal workflow.',
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

  const originalMoveRole = moveRole;
  moveRole = async function moveRoleWithOwnerRelevance(role, nextStage, requireConfirmation = false) {
    const priorStage = stageFor(role);
    await originalMoveRole(role, nextStage, requireConfirmation);
    const workflowStage = normalizedWorkflowStage(state.workflow.get(role?.key)?.stage, role);

    try {
      if (nextStage === 'irrelevant' && priorStage !== 'irrelevant' && workflowStage === 'irrelevant') {
        await saveOwnerRelevanceEvent(role, 'role_marked_irrelevant', priorStage, 'irrelevant');
        showToast('Marked Irrelevant. This will calibrate future job scoring.');
      } else if (priorStage === 'irrelevant' && nextStage !== 'irrelevant' && workflowStage === nextStage) {
        await saveOwnerRelevanceEvent(role, 'role_irrelevant_retracted', 'irrelevant', nextStage);
        showToast('Irrelevant label removed. The role is back in the normal workflow.');
      }
    } catch (error) {
      showToast(`Status changed, but owner-feedback evidence could not be saved: ${error.message}`, true);
    }
  };
})();
