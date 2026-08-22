# Career Engine scheduled-review evidence projection

This file documents the derived runtime evidence consumed by the repeating ChatGPT Career Engine review. It is not a second tracker and it never overrides CareerTracker, owner decisions, the Career Engine config/runtime bundle, or the Vault operating contract.

## Publication contract

The production Hermes daily scanner publishes a bounded review projection to the dedicated Git branch:

- branch: `career-review-runtime`
- current projection: `projects/job-automation/review-runtime/latest.json`
- dated projections: `projects/job-automation/review-runtime/daily/YYYY-MM-DD.json`
- retention in the branch working tree: 14 dated projections

`projects/job-automation/daily_scanner.py --scanner-id hermes_scanner` publishes by default after discovery and post-scan reconciliation. `--no-review-publish` is a manual/testing escape hatch. Publication failure is reported in the normal scan report under `review_bundle_publication` and does not silently convert a completed discovery scan into success for the scheduled review.

The publisher uses the existing Git remote/authentication path. It fetches the runtime branch into an isolated detached worktree, changes only `projects/job-automation/review-runtime/`, commits the dated/latest projection there, and pushes only `career-review-runtime`. It does not stage, reset, clean, or modify the main Career Engine worktree.

## Authority and privacy

The projection is read-only evidence for a reviewer that lacks direct VPS access. It is deliberately derived and non-canonical. CareerTracker remains the single operational authority for vacancy identity, lifecycle/application state, submission evidence, and dashboard counts.

The projection may contain public vacancy metadata needed for review: job ID, company, role, fit score, workflow/application status, source-path label, route type, blocker count, selected resume variant, and whether current packet/package evidence exists. It also contains aggregate scan/source/tracker/reconciliation statistics and the source/bundle identities required to detect stale or drifted evidence.

The projection must not contain:

- full job descriptions;
- source/application URLs;
- email addresses, Gmail message IDs, subjects, bodies, recipients, or sender data;
- owner comments, notes, prompts, or AI-request text;
- CV, cover-letter, or application-document contents;
- credential/token/secret values.

Every projection declares the privacy exclusions and `contains_secret_values=false`.

## Scheduled-review acceptance

The scheduled ChatGPT review must read current Vault/repo canon first, then read `latest.json` explicitly from branch `career-review-runtime`.

At the normal 10:00 Asia/Riyadh review, a projection counts as the completed natural morning scan only when all of the following are true:

1. `operation_date` is the expected current Riyadh review date;
2. `scan.scanner_id` is `hermes_scanner`;
3. `scan.scanned_at`, converted to `Asia/Riyadh`, is on the current date and falls in the bounded morning acceptance window `08:45 <= scanned_at <= 10:00`;
4. the recorded bundle hash is consistent with the accepted Career Engine state used for that scan;
5. `source.head_matches_origin_master=true` at projection-publication time unless current canon documents a deliberate exception;
6. `privacy.contains_secret_values=false` and all exclusion flags remain false as defined above;
7. Gmail/reconciliation and source-path statistics are internally consistent.

A same-date projection created before 08:45 is a prior controlled/manual run and must not be accepted as proof that the natural 09:00 run occurred. The reviewer may use it as historical context only.

`scan.scan_source_sha` records the source HEAD on which the scan actually ran. `scan.current_source_sha`/`source.head` record source state at review-projection publication time. Source may legitimately advance after a scan. A mismatch between scan and current source is therefore reported as drift and is not, by itself, evidence that the completed scan is invalid. The identities must never be conflated.

A missing, stale, malformed, privacy-unsafe projection, or a projection outside the morning acceptance window, must be reported as PARTIAL/UNVERIFIED. The reviewer must distinguish `09:00 scan evidence missing` from `09:00 review publication missing` when connected evidence permits that distinction, and must never manufacture zero counts from missing runtime evidence.

Gmail is independently inspected by the scheduled ChatGPT task for alerts, recruiter messages, confirmations, and replies. Gmail evidence may corroborate or challenge the projection, but it does not replace CareerTracker and cannot silently override an owner decision.
