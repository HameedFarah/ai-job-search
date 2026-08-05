# Career Engine Agent Entry Point

This repository is the executable build for the production multi-job Career Engine; the Obsidian Vault is the canonical career truth. Do not populate candidate facts in this file and do not use the legacy placeholder workflow.

## Canonical authorities

1. **Career truth and governance (canonical):** `/home/hameedo/obsidian/HermesOpsVault/projects/job-automation/` and `/home/hameedo/obsidian/HermesOpsVault/governance/north-star.md`.
2. **Engine code and schemas (executable build):** `career_engine/` and `projects/job-automation/config/`.
3. **Compiled runtime bundle:** generated and validated through `./career-engine bundle ...`.

Read the current Vault index, status and playbook, the verified profile, metrics bank, profile JSON and North Star before making material claims. Prefer the newest canonical evidence when sources conflict.

## Operating contract

- Before material work, read the current Vault index/status/playbook, then run `./career-engine doctor` and `./career-engine bundle status`.
- Use `./career-engine` or the `career_engine` package for normalization, evidence matching, scoring, routing, generation packets, validation, rendering and finalization.
- ChatGPT, the daily scanner, Hermes and `.claude/commands/`, `.claude/skills/` and future clients are thin wrappers over the shared tracker and the central engine only. They must not duplicate candidate facts or policy.
- Verify vacancy live status, timestamp and source before generation.
- Preserve the posting date precision and source and the found date; never invent dates.
- Read unresolved dashboard comments and pending AI requests before regenerating any package; version outputs and preserve history.
- Mandatory domain requirements are material gaps unless directly supported by approved claim-level evidence.
- Generated factual statements must cite approved claim IDs and pass deterministic validation.
- Render only through the approved templates and validate the PDF output: portal roles use `ats-classic` ATS Linear (no photo); verified direct-email/human-review roles use `modern-executive-sidebar` (with photo); Simplify imports use comprehensive `ats-compact-technical` (with photo) and are never submitted unchanged. Owner overrides are respected; route-specific rules supersede blanket headshot wording.
- Never send email, contact a recruiter or submit an application without explicit owner approval.
- Use `hameedo@gmail.com` for outward career identity and Gmail draft ownership. Email applications attach exactly one selected CV: sidebar by default for email, ATS Linear by default for portals, with a persisted per-job owner override. Approval is internal only; availability is internal-only.
- Create or update a normal unsent Gmail draft in `hameedo@gmail.com` only for a verified real recipient. Fill To and Subject from the vacancy instructions; otherwise use the verified recipient and `Abdelhamid Farah - <Post Name>`. Attach exactly one selected CV PDF. Portal-only vacancies remain dashboard packages with their official links and receive no email draft unless a genuine email route exists. Never guess an address or create a self-addressed draft.
- Do not include availability in routine external material unless the owner explicitly requests it.
- Do not commit runtime artifacts, generated applications, mailbox data, scanner inputs/results, prompts, caches or secrets.

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

See `AGENTS.md`, `docs/CAREER_ENGINE_V1.md`, `CAREER_ENGINE_V1_IMPLEMENTATION.md` and `docs/CAREER_ENGINE_CLOSEOUT_PLAN_2026-08-03.md` for the production design and recovery instructions.
