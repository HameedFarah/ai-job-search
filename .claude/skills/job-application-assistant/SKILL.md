---
name: job-application-assistant
description: Use the centralized Career Engine to evaluate a vacancy and create an evidence-grounded tailored CV and cover email.
framework_version: 1.3.2
---

# Job Application Assistant

This skill is a thin client of the centralized Career Engine. It does not maintain independent career facts, scoring rules, writing rules or application policy.

## Authorities

- Career truth and governance (canonical Vault): `/home/hameedo/obsidian/HermesOpsVault/projects/job-automation/` (index, status, playbook) and `governance/north-star.md`
- Engine code and schemas (executable build): repository root and `career_engine/`
- Compiled runtime bundle: `projects/job-automation/config/runtime-bundle.v1.json`
- Canonical tracker: `projects/job-automation/tracker.py` (shared by ChatGPT, the daily scanner, Hermes and skills)

## Required flow

1. Before material work, read the current Vault index/status/playbook, run `./career-engine doctor` and `./career-engine bundle status`, and read unresolved dashboard comments and pending AI requests with `node /home/hameedo/websites/career-review/scripts/read_feedback.js [role-key] [--pending-only]`.
2. Save the complete verified job description to a UTF-8 file, preserving the posting date precision and source and the found date. Never invent dates.
3. Run `./career-engine prepare` with the real company, role, source, official application URL, `live_status`, verification timestamp/source and verified recipient details when available. Verification is confidence metadata, not a generation prerequisite: `unverified` or incompletely verified vacancies proceed with a visible warning. Only vacancies explicitly marked `closed` remain blocked.
4. Read the generated `generation_packet.json` from the returned artifact path.
5. Perform one high-quality LLM generation pass using the packet's system instruction and evidence claims. Write original, coherent and persuasive prose. Do not use rigid fill-in-the-blank sentences.
6. Return JSON matching `projects/job-automation/config/generated_application.schema.json`.
7. Import the result with `./career-engine generate import --job-id <id> --file <json>`.
8. Render only after deterministic validation passes, using the route-specific template default: portal roles render `ats-classic` ATS Linear without photo; verified direct-email/human-review roles render `modern-executive-sidebar` with photo; Simplify imports render comprehensive `ats-compact-technical` with photo and are never submitted unchanged. Route-specific rules supersede blanket headshot wording; owner overrides are respected. Version outputs and preserve history.
9. Create a normal unsent Gmail draft in `hameedo@gmail.com` only when the Career Engine route is `email` and a genuine verified real recipient exists. Fill `To` and `Subject` from the vacancy instructions when specified; otherwise use the verified recipient and subject `Abdelhamid Farah - <Post Name>`. Attach exactly one selected CV PDF: Modern Executive Sidebar by default for email routes, ATS Linear by default for portal routes, or the persisted per-job preview override. Portal-only roles remain dashboard packages with the official application link and receive no email draft unless a genuine email route exists. Never guess a recipient or create a self-addressed review draft.
10. Never send, submit or answer sensitive declarations without explicit owner approval. Approval is internal only; availability is internal-only.

## LLM boundary

The LLM writes vacancy-specific prose. It must not re-evaluate career facts independently. Every factual sentence or bullet cites approved claim IDs. A second LLM review is used only when deterministic validation fails, evidence is materially ambiguous, or the owner asks for another review.

## Required reporting

Always include the job ID, bundle hash, fit score, material gaps, application route, artifact paths, validation result and explicit external-action status.
