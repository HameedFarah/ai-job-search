---
framework_version: 1.2.0
---

# Agent Guidelines: AI Job Search

## Authority model

The Obsidian Vault is the canonical career truth. This repository is the executable build only: code, schemas and the compiled runtime bundle. Candidate facts and career governance are not stored in agent prompts.

Use these three authorities:

1. **Canonical Vault career truth and governance** — `/home/hameedo/obsidian/HermesOpsVault/projects/job-automation/` plus `governance/north-star.md`. Read the current Vault `index.md`, `status.md` and the current playbook before material work.
2. **Repository code and schemas (executable build)** — `career_engine/` and `projects/job-automation/config/`.
3. **Compiled runtime bundle** — build and validate it through `./career-engine bundle ...`; every client consumes the same versioned bundle.

## Required operating rules

- Before material work, read the current Vault index/status/playbook, then run `./career-engine doctor` and `./career-engine bundle status`.
- ChatGPT, the daily scanner, Hermes and repository skills use the shared tracker and the central engine only. Treat `.claude/` commands, scanners and skills as thin clients that must not duplicate candidate facts, scoring rules or application policy.
- Verify vacancy live status and source before generation.
- Preserve the posting date precision and source and the found date; never invent dates.
- Before regenerating any package, read unresolved dashboard comments and pending AI requests (see `read_feedback` below) and version outputs while preserving history.
- Never invent roles, projects, clients, metrics, dates, qualifications or contact addresses.
- Never send email, contact a recruiter or submit an application without explicit owner approval.
- Template defaults: a portal role renders `ats-classic` ATS Linear without photo; a verified direct-email/human-review role renders `modern-executive-sidebar` with photo; a Simplify import renders comprehensive `ats-compact-technical` with photo and is never submitted unchanged; owner overrides are respected. Route-specific rules supersede blanket headshot wording.
- Outward email is `hameedfarah@gmail.com`; draft/auth account is `hameedo@gmail.com`; approval is internal only; availability is internal-only.
- A verified-recipient role may receive a normal unsent Gmail draft. A portal-only role may receive a clearly labelled portal preparation draft with an empty recipient, tailored subject/body, attached application documents and the official submission link. It is not an outreach email and must never be sent until a real recipient is verified. Never guess a recipient or create a self-addressed review draft.
- Do not commit runtime tracker data, generated application packages, mailbox data, live scan inputs/results, prompts, caches or secrets.

## Primary commands

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
node /home/hameedo/websites/career-review/scripts/read_feedback.js [role-key] [--pending-only]
```

See `docs/CAREER_ENGINE_V1.md` and `CAREER_ENGINE_V1_IMPLEMENTATION.md` for the production architecture and recovery procedures.
