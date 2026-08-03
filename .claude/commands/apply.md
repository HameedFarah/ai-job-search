# /apply - Central Career Engine Application

Use the centralized Career Engine. Do not independently recreate its career facts, policies, scoring framework or templates.

Input may be a live job URL, pasted job description, screenshot, PDF or saved text file.

## Workflow

1. Verify the live vacancy and save the full job description to a UTF-8 file.
2. Run:

```bash
./career-engine bundle build
./career-engine prepare --jd-file <file> --company <company> --role <role> --source <source> --source-url <posting-url> --application-url <official-apply-url> --live-status live --live-verified-at <timestamp> --live-verification-source <source> [--recipient <verified-email> --recipient-source <evidence>]
```

3. Present the deterministic fit score, strongest matches, material gaps and application route.
4. When the role is credible and the owner requested an application package, use the returned `generation_packet.json` for one structured LLM writing pass.
5. Write original, coherent, persuasive vacancy-specific prose. Every factual statement and achievement bullet must cite approved claim IDs. Return JSON matching `projects/job-automation/config/generated_application.schema.json`.
6. Import and validate:

```bash
./career-engine generate import --job-id <job-id> --file <generated-json>
```

7. Render the validated content using the approved versioned DOCX template. Never recreate the layout or silently switch templates.
8. Inspect DOCX and PDF page images, verify the PDF text layer and confirm exactly two CV pages.
9. When the route is `email`, create an unsent Gmail draft with the verified real recipient and final applicant-facing PDF filename. When the route is `portal`, provide the official application link and create no email draft.
10. Show the complete package and unresolved screening questions. Sending or submission requires explicit owner approval.

A second LLM review is exceptional and permitted only after deterministic validation identifies a problem, evidence is materially ambiguous, or the owner explicitly requests another review.
