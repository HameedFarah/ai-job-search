# Career Engine Shared Tracker

This directory is the single operational job tracker used by ChatGPT and Hermes. The Obsidian Vault remains the authority for career evidence, governance and canonical resume decisions.

## Canonical structure

```text
projects/job-automation/
├── data/
│   ├── jobs.csv
│   └── jobs/<job-id>.json
├── logs/events.jsonl
├── artifacts/<job-id>/
└── tracker.py
```

- `jobs.csv` is the concise operational index.
- Each per-job JSON record retains the full JD, requirements, provenance, scoring, evidence matches, processing state, artifacts, Gmail draft reference and complete history.
- `events.jsonl` is append-only. Never truncate, replace or reorder it.
- Every material edit requires a non-empty comment and a before/after event.

## Ownership

- Pasted JDs, screenshots, recruiter messages and ad-hoc URLs start with `owner=chatgpt`.
- Recurring portal discovery and email scans are owned by Hermes.
- A ChatGPT handoff uses `owner=hermes` and `processing_status=queued_for_hermes`.
- `jd_hash`, external IDs and source URLs prevent duplicate work.

## External action gate

The tracker may record and prepare applications, generate PDFs and create Gmail drafts. It must never send email, submit an application, answer sensitive screening questions or commit salary expectations without explicit owner approval.

## CLI examples

Central engine commands (verified against `career_engine/cli.py`; run from the repository root). Scanners, ChatGPT, Hermes, the daily scanner and skills must use the central engine and shared tracker only:

```bash
./career-engine doctor
./career-engine bundle status
./career-engine bundle build
./career-engine bundle validate
./career-engine scanner ingest --file <jobs.json> --scanner-id chatgpt_scanner
./career-engine scanner ingest --file <jobs.json> --scanner-id hermes_scanner
./career-engine prepare --jd-file <job.txt> --company <company> --role <role> --application-url <url> --live-status live --live-verified-at <timestamp> --live-verification-source <source>
./career-engine status --job-id <id>
./career-engine score --job-id <id>
./career-engine route --job-id <id>
./career-engine generate export --job-id <id>
./career-engine generate import --job-id <id> --file <json>
./career-engine validate --job-id <id>
./career-engine render --job-id <id>
./career-engine render-ats --job-id <id>
./career-engine render-ats-options --job-id <id> [--out-dir <dir>]
./career-engine package --job-id <id>
```

Before regenerating any package, read unresolved dashboard comments and pending AI requests:

```bash
node dashboard/career-review/scripts/read_feedback.js [role-key] [--pending-only]
```

Tracker CLI:

```bash
python3 projects/job-automation/tracker.py ingest \
  --actor chatgpt \
  --comment "Ingested from a verified employer posting" \
  --payload '{"source":"workable","external_job_id":"123","source_url":"https://example.invalid/job/123","company":"Example","role":"Design Manager","location":"Riyadh","full_job_description":"Full text..."}'

python3 projects/job-automation/tracker.py update \
  --job-id <job-id> \
  --actor chatgpt \
  --action generated \
  --comment "Generated evidence-constrained resume and PDF" \
  --changes '{"resume_status":"generated","pdf_status":"validated"}'

python3 projects/job-automation/tracker.py queue-hermes \
  --job-id <job-id> \
  --actor chatgpt \
  --comment "Queued for recurring Hermes follow-up"
```

## Validation

```bash
python3 -m unittest tests.test_career_tracker
python3 tools/lint_skills.py
python3 tools/check_framework_version.py
python3 tools/security_guards.py
```
