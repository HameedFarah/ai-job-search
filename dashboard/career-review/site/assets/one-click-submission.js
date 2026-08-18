/* One-click owner submission confirmation for the private Career Engine UI.
 * The button click itself is the explicit owner confirmation. No browser
 * confirm/prompt dialogs are shown; optional reference text can be added later
 * as an append-only note if needed. */
'use strict';

(function installOneClickSubmission() {
  const pending = new Set();

  window.confirmApplicationSubmitted = async function confirmApplicationSubmittedOneClick(role, uiSource = 'dashboard') {
    if (!role?.key || pending.has(role.key)) return false;
    pending.add(role.key);
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
      if (typeof state !== 'undefined' && Array.isArray(state.history)) state.history.push(savedHistory);
      return { record: savedHistory, evidence, priorStage: currentStage };
    } finally {
      pending.delete(role.key);
    }
  };
})();
