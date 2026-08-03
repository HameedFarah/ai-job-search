# Career Engine Agent Entry Point

This repository runs the production multi-job Career Engine. Do not populate candidate facts in this file and do not use the legacy placeholder workflow.

## Canonical authorities

1. **Engine code and schemas:** `career_engine/` and `projects/job-automation/config/`.
2. **Career truth and governance:** `/home/hameedo/obsidian/HermesOpsVault/projects/job-automation/` and `/home/hameedo/obsidian/HermesOpsVault/governance/north-star.md`.
3. **Compiled runtime bundle:** generated and validated through `./career-engine bundle ...`.

Read the current Vault playbook, verified profile, metrics bank, profile JSON, index, status and North Star before making material claims. Prefer the newest canonical evidence when sources conflict.

## Operating contract

- Use `./career-engine` or the `career_engine` package for normalization, evidence matching, scoring, routing, generation packets, validation, rendering and finalization.
- `.claude/commands/`, `.claude/skills/`, ChatGPT scanners, Hermes scanners and future clients are thin wrappers. They must not duplicate candidate facts or policy.
- Verify vacancy live status, timestamp and source before generation.
- Mandatory domain requirements are material gaps unless directly supported by approved claim-level evidence.
- Generated factual statements must cite approved claim IDs and pass deterministic validation.
- Render only through the approved two-page DOCX sidebar template and validate the PDF output.
- Never send email, contact a recruiter or submit an application without explicit owner approval.
- Create or update an unsent Gmail draft only for a verified real recipient. Never guess an address or create a self-addressed draft. Portal-only vacancies remain portal-only.
- Do not include availability in routine external material unless the owner explicitly requests it.
- Do not commit runtime artifacts, generated applications, mailbox data, scanner inputs/results, prompts, caches or secrets.

## Primary commands

```bash
./career-engine doctor
./career-engine bundle status
./career-engine bundle validate
./career-engine scan --scanner-id chatgpt_scanner --input <jobs.json>
./career-engine scan --scanner-id hermes_scanner --input <jobs.json>
./career-engine prepare --input <job.json>
```

See `AGENTS.md`, `docs/CAREER_ENGINE_V1.md`, `CAREER_ENGINE_V1_IMPLEMENTATION.md` and `docs/CAREER_ENGINE_CLOSEOUT_PLAN_2026-08-03.md` for the production design and recovery instructions.
