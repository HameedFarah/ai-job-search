# Portal and career-ops reuse plan

## Current boundary

Career Engine remains the owner-specific execution layer for GCC/Jordan discovery, evidence-grounded scoring, CV/application generation, tracker/Kanban state, submission provenance and no-send/no-submit controls.

Maintained external projects are preferred for generic portal/search capabilities where they are actively maintained and compatible with this boundary.

## Current maintained inputs

- `MadsLorentzen/ai-job-search`: framework/upstream changes reviewed weekly.
- `Fighter90/career-ops-ui`: maintained ATS/source implementations reused through the managed-source bridge where they are stronger than native code.
- `ever-jobs/ever-jobs`: specialist ATS reference/reuse source for providers not adequately covered elsewhere, currently Taleo.

## Fighter90/career-ops follow-up

Do not port the whole project during the owner's active search. Evaluate individual capabilities after the current source/scan closeout, in this order:

1. CV and cover-letter factual-claim validation gate.
2. Canonical vacancy URL normalization/deduplication.
3. Company application history and responsiveness signals.
4. Funnel/velocity analytics and interview silence/follow-up detection.
5. JD-to-CV skill-gap analysis and seniority pre-filtering.
6. Interview/story-bank and outcome-learning utilities.

For each capability, reuse the maintained implementation only when it can sit behind a small stable boundary and does not duplicate or weaken Career Engine's canonical Vault facts, evidence controls or owner approval gates.

Do not open a broad `career-ops` migration/refactor until one of these bounded capabilities proves materially useful in the live owner workflow.
