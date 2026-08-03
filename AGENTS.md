---
framework_version: 1.1.0
---

# Agent Guidelines: AI Job Search

## Authority model

This repository is the executable authority for the Career Engine. Candidate facts and career governance are not stored in agent prompts.

Use these three authorities:

1. **Repository code and schemas** — `career_engine/` and `projects/job-automation/config/`.
2. **Canonical Vault career truth and governance** — `/home/hameedo/obsidian/HermesOpsVault/projects/job-automation/` plus `governance/north-star.md`.
3. **Compiled runtime bundle** — build and validate it through `./career-engine bundle ...`; every client consumes the same versioned bundle.

## Required operating rules

- Read the relevant Vault sources before making material career claims.
- Treat `.claude/` commands, scanners and skills as thin clients only. They must call the central engine and must not duplicate candidate facts, scoring rules or application policy.
- Verify vacancy live status and source before generation.
- Never invent roles, projects, clients, metrics, dates, qualifications or contact addresses.
- Never send email, contact a recruiter or submit an application without explicit owner approval.
- Unsent Gmail drafts are allowed only for a verified real recipient. Portal-only roles remain portal-only. Never create self-addressed review drafts.
- External CVs and routine application material must not state availability unless the owner explicitly requests it.
- Do not commit runtime tracker data, generated application packages, mailbox data, live scan inputs/results, prompts, caches or secrets.

## Primary commands

```bash
./career-engine doctor
./career-engine bundle status
./career-engine bundle validate
./career-engine scan --scanner-id chatgpt_scanner --input <jobs.json>
./career-engine scan --scanner-id hermes_scanner --input <jobs.json>
./career-engine prepare --input <job.json>
```

See `docs/CAREER_ENGINE_V1.md` and `CAREER_ENGINE_V1_IMPLEMENTATION.md` for the production architecture and recovery procedures.
