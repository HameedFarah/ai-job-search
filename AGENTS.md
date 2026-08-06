---
framework_version: 1.2.4
---

# Agent Guidelines: AI Job Search

## Authority model

The Obsidian Vault is the canonical career truth. This repository is the executable build only: code, schemas and the compiled runtime bundle. Candidate facts and career governance are not stored in agent prompts.

Use these shared authorities in this order:

1. **Central machine policy and settings** — `projects/job-automation/config/career-engine.v1.json`. This is the single editable source for thresholds, route defaults, mailbox identity, generation limits and no-send controls.
2. **Canonical operating contract** — `/home/hameedo/obsidian/HermesOpsVault/projects/job-automation/playbooks/career-engine-operations-contract.md`. This is the single procedural rule set for Hermes, interactive ChatGPT sessions and the daily ChatGPT review.
3. **Canonical Vault career truth and governance** — `/home/hameedo/obsidian/HermesOpsVault/projects/job-automation/` plus `governance/north-star.md`.
4. **Repository code and schemas** — `career_engine/` and `projects/job-automation/config/`.
5. **Compiled runtime bundle** — `projects/job-automation/config/runtime-bundle.v1.json`, built and validated through `./career-engine bundle ...`; every client consumes the same versioned bundle.

Before material work, read the central machine policy, operating contract, current Vault `index.md` and `status.md`, then verify the compiled bundle is current. Never rely on an old chat summary or a review-diff narrative when it conflicts with these authorities.

## Required operating rules

- Before material work, read `projects/job-automation/config/central-rules.README.md`, `projects/job-automation/config/career-engine.v1.json`, the current Vault operating contract/index/status, then run `./career-engine doctor` and `./career-engine bundle status`.
- ChatGPT, the daily scanner, Hermes and repository skills use the shared tracker and the central engine only. Treat `.claude/` commands, scanners and skills as thin clients that must not duplicate candidate facts, scoring rules or application policy.
- Record vacancy verification status and source as confidence metadata; it is not a generation prerequisite. Explicitly closed roles remain blocked.
- Preserve the posting date precision and source and the found date; never invent dates.
- Before regenerating any package, read unresolved dashboard comments and pending AI requests (see `read_feedback` below) and version outputs while preserving history.
- Never invent roles, projects, clients, metrics, dates, qualifications or contact addresses.
- Never send email, contact a recruiter or submit an application without explicit owner approval.
- Template defaults: a portal role renders `ats-classic` ATS Linear without photo; a verified direct-email/human-review role renders `modern-executive-sidebar` with photo; a Simplify import renders comprehensive `ats-compact-technical` with photo and is never submitted unchanged; owner overrides are respected. Route-specific rules supersede blanket headshot wording.
- Use `hameedo@gmail.com` for outward career identity and Gmail draft ownership. Email applications attach exactly one selected CV: sidebar by default for email, ATS Linear by default for portals, with a persisted per-job owner override. Approval is internal only; availability is internal-only.
- A verified-recipient email role may receive a normal unsent Gmail draft in `hameedo@gmail.com`. Fill To and Subject from the vacancy instructions; otherwise use the verified recipient and `Abdelhamid Farah - <Post Name>`. Attach exactly one selected CV PDF. Portal-only roles remain dashboard packages with the official link and no email draft unless a genuine email route exists. Never guess a recipient or create a self-addressed review draft.
- Do not commit runtime tracker data, generated application packages, mailbox data, live scan inputs/results, prompts, caches or secrets.

## Primary commands

```bash
./career-engine doctor
./career-engine bundle status
./career-engine bundle build
./career-engine bundle validate
./career-engine bundle rebuild
./career-engine validate-config
./career-engine list-jobs [--status <s>] [--min-score N] [--max-score N] [--company <c>] [--role <r>]
./career-engine show-job --job-id <id>
./career-engine dashboard [--sync]
./career-engine review
./career-engine reconcile
./career-engine run
./career-engine validate [--job-id <id>]
./career-engine record-review [--file <review.json>]
./career-engine status [--job-id <id>]
./career-engine scanner ingest --file <jobs.json> --scanner-id chatgpt_scanner
./career-engine scanner ingest --file <jobs.json> --scanner-id hermes_scanner
./career-engine scan --file <jobs.json> --scanner-id chatgpt_scanner
./career-engine prepare --jd-file <job.txt> --company <company> --role <role> --application-url <url> --live-status live --live-verified-at <timestamp> --live-verification-source <source>
./career-engine score --job-id <id>
./career-engine route --job-id <id>
./career-engine generate export --job-id <id>
./career-engine generate import --job-id <id> --file <json>
./career-engine render --job-id <id>
./career-engine render-ats --job-id <id>
./career-engine render-ats-options --job-id <id> [--out-dir <dir>]
./career-engine package --job-id <id>
node /home/hameedo/websites/career-review/scripts/read_feedback.js [role-key] [--pending-only]
```

`validate-config` validates the central config, required files, bundle
currency/validity and tracker schema (nonzero exit on errors). `run` is the
deterministic batch orchestration: rebuilds/validates the bundle as needed,
reconciles tracker statuses against the centralized threshold and persisted
owner decisions, prepares/generates only eligible records through the no-send
pipeline (up to `daily_scanner.maximum_generation_packets_per_scan`), syncs the
local dashboard data and emits a structured report. `reconcile` is idempotent
and never sends or submits. `dashboard` is read-only by default; `--sync` only
writes the local dashboard data export — live deployment of the career-review
site is an external action requiring explicit owner approval. `record-review`
accepts `--file` and defaults to `runtime/review-diffs/latest.json` when it
validates. `scan` wraps `scanner ingest` with an explicit input file and
scanner id.

See `docs/CAREER_ENGINE_V1.md` and `CAREER_ENGINE_V1_IMPLEMENTATION.md` for the production architecture and recovery procedures.
