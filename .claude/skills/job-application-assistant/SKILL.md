---
name: job-application-assistant
description: Use the centralized Career Engine to evaluate a vacancy and create an evidence-grounded tailored CV and cover email.
framework_version: 1.2.0
---

# Job Application Assistant

This skill is a thin client of the centralized Career Engine. It does not maintain independent career facts, scoring rules, writing rules or application policy.

## Authorities

- Engine code and schemas: repository root and `career_engine/`
- Career truth and governance: `/home/hameedo/obsidian/HermesOpsVault/projects/job-automation/`
- Compiled runtime bundle: `projects/job-automation/config/runtime-bundle.v1.json`
- Canonical tracker: `projects/job-automation/tracker.py`

## Required flow

1. Save the complete verified job description to a UTF-8 file.
2. Run `./career-engine bundle build`.
3. Run `./career-engine prepare` with the real company, role, source, official application URL, `live_status=live`, verification timestamp/source and verified recipient details when available. Closed, unverified or incompletely verified vacancies must remain blocked.
4. Read the generated `generation_packet.json` from the returned artifact path.
5. Perform one high-quality LLM generation pass using the packet's system instruction and evidence claims. Write original, coherent and persuasive prose. Do not use rigid fill-in-the-blank sentences.
6. Return JSON matching `projects/job-automation/config/generated_application.schema.json`.
7. Import the result with `./career-engine generate import --job-id <id> --file <json>`.
8. Render through the approved versioned DOCX template only after deterministic validation passes.
9. Create an unsent Gmail draft only when the Career Engine route is `email` and the verified real recipient is present. For portal-only roles, provide the official application link and create no application email draft.
10. Never send, submit or answer sensitive declarations without explicit owner approval.

## LLM boundary

The LLM writes vacancy-specific prose. It must not re-evaluate career facts independently. Every factual sentence or bullet cites approved claim IDs. A second LLM review is used only when deterministic validation fails, evidence is materially ambiguous, or the owner asks for another review.

## Required reporting

Always include the job ID, bundle hash, fit score, material gaps, application route, artifact paths, validation result and explicit external-action status.
