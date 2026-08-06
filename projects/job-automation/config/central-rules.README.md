# Career Engine Central Rules

All Career Engine clients—Hermes, interactive ChatGPT career sessions, repository agents, and the repeating 10:00 ChatGPT review—must read the same authorities before processing jobs. No client may maintain a separate threshold, route rule, mailbox rule or send policy:

1. `projects/job-automation/config/career-engine.v1.json` — machine policy and editable settings.
2. `/home/hameedo/obsidian/HermesOpsVault/projects/job-automation/playbooks/career-engine-operations-contract.md` — procedural operating contract.
3. `projects/job-automation/config/runtime-bundle.v1.json` — compiled validated bundle consumed at runtime.

Startup must fail closed when `./career-engine doctor` or `./career-engine bundle status` reports a stale or invalid bundle; rebuild and validate before processing.

Current mandatory decisions (effective 2026-08-06):

- Generation threshold: `70/100` (`scoring.thresholds.high_priority` and `daily_scanner.minimum_score_for_generation`).
- Roles scoring 70 or above automatically qualify for generation subject to genuine blockers such as duplicates, explicit closure, missing usable route, mandatory qualification failure, or owner rejection.
- Roles below 70 require an evidence-backed owner override recorded in the central tracker and append-only history.
- Preserve raw score and append owner overrides to job history. Later owner decisions supersede earlier reviewer interpretations.
- Generate and retain both Modern Executive Sidebar and ATS Linear variants for each accepted application.
- Select exactly one CV per application: Modern Executive Sidebar by default for verified email routes; ATS Linear by default for portal routes; a persisted owner override wins.
- Use only `hameedo@gmail.com` for Career Engine ingestion, career drafts and outward identity.
- Create an unsent Gmail draft only for a verified real email recipient. Put the cover letter in the body and attach only the selected CV PDF. Never create portal-only self-addressed drafts.
- Never send, contact or submit without explicit owner approval for the specific action.
- Read append-only comments, pending AI requests and persisted dashboard overrides before regeneration.
- `review-diffs/latest.json` provides reusable lessons only; it cannot override the central config, operating contract or a later owner decision. When conflict exists, update the review diff to reflect the later decision before the next run.

FADEN CONTRACTING LTD / Architecture Project Manager, job `4006ecf27038992c28d4`, is accepted at raw score 78 and is eligible for portal-package generation with ATS Linear selected. This later owner decision supersedes the earlier 10:02 review narrative that treated FADEN as watchlist-only. No Gmail draft is permitted because the route is portal-only.
