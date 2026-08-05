# Career Engine v1

## Purpose

Career Engine v1 is the centralized application pipeline for Abdelhamid Farah. It is used by ChatGPT, Hermes, the daily job scanner and future API or SaaS clients.

The engine reduces repeated LLM work without reducing writing quality. Deterministic code handles facts, evidence, scoring, routing, caching and policy checks. One bounded LLM pass writes original, coherent and persuasive vacancy-specific CV content and the cover email.

## Authorities

Career Engine separates three authorities:

1. Code and schemas in this repository.
2. Personal career truth and governance in the Obsidian Vault.
3. A compiled versioned runtime bundle consumed by all clients.

The Vault sources are:

- `projects/job-automation/career-engine-profile.v1.json`
- `projects/job-automation/playbooks/career-engine-application-playbook.md`
- `projects/job-automation/verified-career-profile-2026-08-01.md`
- `projects/job-automation/career-metrics-bank-2026-08-03.md`
- `governance/north-star.md`

The bundle records every source hash. When a source changes, the bundle becomes stale and is rebuilt automatically. This makes a correction or new verified achievement available to future ChatGPT threads, Hermes runs, daily scans and SaaS consumers without copying the rule into each client.

## Application flow

1. Verify and save the complete job description.
2. Build or validate the runtime bundle.
3. Normalize the job description deterministically.
4. Extract responsibilities, mandatory requirements and preferred requirements.
5. Match requirements to verified claims using tags and aliases.
6. Calculate the initial fit score and material gaps.
7. Resolve a verified email route, official portal route or route blocker.
8. Produce one bounded `generation_packet.json`.
9. Use one LLM pass to write original structured prose.
10. Import the structured result and reject unsupported facts or policy violations.
11. Produce renderer input for the approved DOCX template.
12. Verify the DOCX, PDF, page count and PDF text layer.
13. Create a normal unsent Gmail draft when a verified actual recipient exists. For a portal-only role, create a clearly labelled portal preparation draft with an empty recipient, attached application package and official submission link; it is never an outreach email and cannot be sent without a verified recipient.
14. Present the package for owner approval.
15. Send or submit only after explicit approval.

## Vacancy-status confidence

Every normalized job carries a `live_status` contract: `live`, `closed` or
`unverified` (missing status defaults to `unverified`), plus optional
`live_verified_at` and `live_verification_source`.

- Verification is recorded as source-confidence metadata and is not required
  for scoring or application preparation.
- `unverified` jobs can receive a `generation_packet.json` and become generation
  candidates when they meet the score threshold and have a usable email or
  portal route. The warning remains visible for owner review.
- A `live` job missing verification metadata is prepared with an
  `invalid_live_metadata` warning rather than a generation blocker.
- A role explicitly marked `closed` remains blocked and unresolved.
- Manual, pasted, alert and aggregator jobs default to `unverified`; neither
  ChatGPT nor Hermes silently upgrades them to verified.

## Scoring calibration and generation threshold

Generation eligibility is **threshold 80 (high priority)**. Verification raises
confidence but is not a prerequisite. Below 80 a role stays trackable but never
receives a generation packet unless the owner explicitly forces one
(`--force-weak` on prepare or an owner score override).

Two calibration controls stop scoring false positives for materially mismatched
roles while preserving adjacent senior design-management roles:

1. **Specialization/seniority title gate** (`taxonomy.specialization`).
   Role titles are classified into: adjacent design-management (Design Manager,
   Design Director, Design Governance, Technical Design Director, Delivery
   Project Director, ... - multiplier 1.0, never suppressed), out-of-lane
   functions (planner, project engineering, strategy, theming, ... - multiplier
   0.35), production individual-contributor roles (architect, senior architect,
   landscape architect, design architect, ... - multiplier 0.6) and junior
   titles (multiplier 0.4). The multiplier is applied to the total after all
   subscore evidence is computed; subscores remain preserved.
2. **Requirement-coverage honesty.** When a JD has no explicit mandatory
   bucket, mandatory coverage uses the overall requirement match quality as a
   proxy instead of a fixed default, so a strongly matched design-management JD
   can still reach 80 while an unmatched JD cannot inflate itself. An absent
   preferred section contributes 0.5 (not 1.0).

Raw score and human override evidence are preserved on every job record:

- `scoring.raw_total` always holds the deterministic engine score;
- `scoring.human_override` records any owner override with score, raw score,
  reason, actor and timestamp;
- `scoring.calibration` records the title signals and multiplier used;
- `./career-engine score --job-id JOB_ID --override N --reason "..."` records
  the override and appends an append-only event with before/after values.

## LLM boundary

The LLM is responsible for high-value communication work:

- vacancy-aligned headline;
- leadership profile;
- concise achievement bullets;
- selection and ordering of relevant evidence;
- coherent cover-email subject and body;
- honest framing of material gaps.

The LLM is not allowed to decide career facts. Every factual sentence and achievement bullet cites approved claim IDs from the generation packet. The importer rejects:

- unknown claim IDs;
- unsupported metrics;
- changed employers, titles or dates;
- prohibited clients, projects or terms;
- availability wording;
- more than two numeric figures in an achievement bullet;
- placeholder text;
- stale bundle hashes;
- missing requirement coverage without an acknowledged gap.

A second LLM review is not routine. It is used only after a policy failure, a material evidence ambiguity or an explicit owner request.

## Runtime bundle

Default path:

`projects/job-automation/config/runtime-bundle.v1.json`

The bundle includes:

- engine and schema versions;
- source paths and SHA-256 hashes;
- identity and contact rules;
- career chronology;
- verified evidence claims;
- requirement aliases and taxonomy;
- writing rules;
- confidentiality and prohibited-content rules;
- template identity and version;
- generation policy;
- daily scanner thresholds.

## Tracker and artifacts

The existing tracker remains canonical:

- `projects/job-automation/data/jobs.csv`
- `projects/job-automation/data/jobs/<job-id>.json`
- `projects/job-automation/logs/events.jsonl`
- `projects/job-automation/artifacts/<job-id>/`

Each stage records its input hash and output. Re-running an unchanged application reuses cached stages. A changed Vault bundle invalidates the dependent stages.

## Commands

```bash
./career-engine doctor
./career-engine bundle build
./career-engine bundle status
./career-engine prepare --jd-file JOB.txt --company COMPANY --role ROLE --application-url OFFICIAL_URL --live-status live --live-verified-at TIMESTAMP --live-verification-source SOURCE
./career-engine status --job-id JOB_ID
./career-engine score --job-id JOB_ID [--override N --reason REASON]
./career-engine route --job-id JOB_ID
./career-engine generate export --job-id JOB_ID
./career-engine generate run --job-id JOB_ID --adapter manual
./career-engine generate import --job-id JOB_ID --file generated_application.json
./career-engine validate --job-id JOB_ID
./career-engine render --job-id JOB_ID
./career-engine render-ats --job-id JOB_ID
./career-engine package --job-id JOB_ID
./career-engine scanner ingest --file jobs.json --scanner-id hermes_scanner
```

The `manual` generation adapter allows ChatGPT or another approved model to consume and return the structured packet. Provider-specific adapters are optional and do not change the core application logic.

## Exit codes

| Code | Meaning |
|---:|---|
| 0 | Ready or successful |
| 10 | Owner input required |
| 20 | Weak fit stop gate |
| 30 | Policy or generated-content failure |
| 40 | Application route unresolved |
| 50 | Approved template missing |
| 70 | System or configuration failure |

## Daily scanner

Repository entry point:

`projects/job-automation/daily_scanner.py`

Discovery connectors provide full structured job records. The scanner sends each record through the same Career Engine pipeline. It does not maintain its own career facts, scoring rules or writing prompt.

The scanner:

- rebuilds the central bundle when needed;
- deduplicates through the canonical tracker;
- stops roles below the 80-point generation threshold after scoring;
- respects the live-vacancy gate: only verified-live jobs can produce generation packets or appear as generation candidates;
- never sends email or submits applications;
- produces one structured report.

The live Hermes cron contract is job `edc36e531637`, schedule `0 8 * * *`, timezone Asia/Riyadh, repository workdir `/home/hameedo/projects/ai-job-search`, provider `opencode-go`, model `deepseek-v4-flash` and skill `career-engine`. It remains non-sending and non-submitting.

Verify the job definition, durable execution history and latest output outside the repository before claiming scheduler success. A completed historical run does not prove the current release, and a durable execution recorded as running without a matching output must be reported as unresolved rather than silently treated as successful.

## ChatGPT and Hermes integration

The following are thin clients of the same engine:

- `.claude/commands/apply.md`
- `.claude/commands/career-engine.md`
- `.claude/skills/job-application-assistant/SKILL.md`
- `skills/career-engine/SKILL.md`

They must not duplicate evidence or policy. They call the CLI, use its generation packet and report its job ID and bundle hash.

## Gmail integration

The repository-native Gmail/gws authentication path and the connected ChatGPT Gmail connector are separate execution paths. The native path may report `invalid_grant` until re-authorized even when the ChatGPT connector can inspect drafts.

Before draft creation the client must search existing drafts. A normal unsent draft requires a verified real recipient and must verify To, subject, body, attachment filename/hash and DRAFT status. A portal-only vacancy may instead receive a clearly labelled portal preparation draft with an empty To field, tailored body, attached files and official submission link; it is not sendable until a real recipient is verified. Duplicate drafts, guessed recipients and self-addressed review drafts are prohibited. Mailbox-derived data is runtime data and must not be committed.

## Future SaaS integration

`career_engine/service.py` provides JSON-serializable functions for a future API layer. Core normalization, matching, scoring and validation functions accept regular Python dictionaries and do not depend on a frontend.

A future SaaS application must consume the same runtime bundle and schemas. It must not copy rules into browser or frontend code.

## Template rule

The approved versioned template is:

`2026-08-03_Abdelhamid_Farah_CV_Template_Modern_Executive_Sidebar_v1.0.docx`

The renderer must stop with a clear blocker when this binary is absent or fails its manifest check. It must not silently generate a different layout. Material layout changes create a new ISO-dated template version.

Verified hashes:

- Template SHA-256: `fa23aaf25519ef527e52761c2a3c5738639e642cf9e6830593caaf6a8fd8629e`
- Manifest SHA-256: `b423a1cab2d51f5df3a9901396110a73bdcbfbd6cebdb04429aa77b87ba7151e`

LibreOffice discovery preserves explicit environment override precedence, then PATH precedence, supports executable permission-bit checks for binaries on `noexec` mounts and retains the user-local fallback. These resolver tests are release guards.

Outward application filenames remain practical and unversioned, for example:

`Abdelhamid_Farah_CV_Design_Governance_Manager.pdf`

## ATS Linear template

A second Career Engine resume template, `ats-linear`, is generated for every
application alongside the approved Modern Executive Sidebar template. It is
optimized for ATS parsing and is the recommended/default CV for **portal
routes**; the email route (direct human review) keeps the Modern Executive
Sidebar as recommended. Both templates are always generated and retained for
every application.

ATS Linear properties:

- one column; no sidebar; no headshot; no tables; no text boxes; no floating
  objects; no images;
- conventional section headings: PROFESSIONAL SUMMARY, PROFESSIONAL EXPERIENCE,
  EDUCATION, PROFESSIONAL CREDENTIALS, LANGUAGES;
- standard bullets and readable Calibri fonts; every word is selectable and
  searchable text;
- factual claim-cited content only: the headline and profile come from
  `generated_application.json`, every bullet is claim-cited, the full verified
  six-role chronology comes from the generation packet, and education and
  credentials are the verified facts already carried by the approved executive
  template and the canonical Vault profile;
- deliberate ATS-optimization deviations from the global writing rules:
  left-aligned body text (instead of justified) and no headshot (explicit
  per-application override) - both are documented in the template spec;
- 2-page target; ATS integrity and complete relevant chronology take precedence
  over forcing the page count, so a 3-page result is accepted with a warning
  finding instead of an error.

The template spec lives in a **non-bundle** config file:

`projects/job-automation/config/ats-linear-template.v1.json`

It is deliberately excluded from the compiled runtime bundle inputs so that
existing generation packets keep their `bundle_hash` valid when the ATS layout
evolves. The renderer builds the DOCX programmatically (reusable code, not
per-application documents) and verifies it:

- DOCX structure: zero tables, zero images, no text boxes, no floating
  objects, one section, one column, non-empty selectable text;
- PDF: page count, non-empty text layer, word count, no embedded raster
  images (`pdfimages -list`), single-column layout (word x-coordinate cluster
  analysis from `pdftotext -bbox`), and the same policy checks as the
  executive template (prohibited names/terms, availability wording, forbidden
  characters, required identity text).

Render the ATS variant for a job with:

```bash
./career-engine render-ats --job-id JOB_ID
```

Outward ATS filenames include the template marker, for example:

`Abdelhamid_Farah_CV_Senior_Design_Manager_ATS.pdf`

## Failure recovery

- Never claim a stage succeeded without a verified artifact.
- Preserve the last verified document.
- Do not create duplicate Gmail drafts before checking existing drafts.
- Do not guess a recipient when route resolution fails.
- Record failures and retries in the append-only event log.
- Rebuild the bundle after a Vault change.
- Re-run only the stages whose input hashes changed.
