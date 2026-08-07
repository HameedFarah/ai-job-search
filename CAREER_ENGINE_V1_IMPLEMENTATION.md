# Career Engine v1 production implementation

**Status:** Implemented and validated for reusable multi-job operation.

**Engine version:** 1.0.0

**Release scope:** Central pipeline, CLI, scanners, schemas, deterministic gates, approved renderer template, thin client skills and tests.

## Production architecture

Career Engine uses three authorities and does not permit a fourth:

1. **Code and schemas** in this repository.
2. **Career truth and governance** in `/home/hameedo/obsidian/HermesOpsVault`.
3. **One compiled, versioned runtime bundle** consumed by every client.

ChatGPT, Hermes, repository CLI, scheduled scanners and future API/SaaS clients are thin entry points. They do not carry independent career facts, evidence, scoring rules or application policy.

## Implemented components

The importable package is `career_engine/` and includes configuration, bundle compilation, normalized models, deterministic matching/scoring/routing, generation packet handling, Gmail draft preparation, pipeline orchestration, document rendering, scanner integration, service functions and CLI commands.

Repository entry points include:

- `./career-engine`
- `projects/job-automation/chatgpt_scanner.py`
- `projects/job-automation/hermes_scanner.py`
- `projects/job-automation/daily_scanner.py`
- `.claude/commands/apply.md`
- `.claude/commands/career-engine.md`
- `.claude/skills/job-application-assistant/SKILL.md`
- `skills/career-engine/SKILL.md`

## Deterministic stage flow

1. Ingest and save the complete vacancy record.
2. Record `live_status`, timestamp and verification source as confidence metadata.
3. Build or validate the runtime bundle.
4. Normalize the job description.
5. Extract mandatory and preferred requirements.
6. Match only approved evidence claims.
7. Calculate deterministic fit, strengths, gaps and recommendation.
8. Resolve a verified-email, official-portal or blocked route.
9. Produce one bounded generation packet.
10. Import one structured vacancy-specific prose result.
11. Reject unsupported claims, altered chronology, prohibited content and policy violations.
12. Render through the approved DOCX template and convert to PDF.
13. Verify page count, text layer, contact data, prohibited content and outward filename.
14. Create an unsent Gmail draft only for a verified recipient, or provide the official portal route.
15. Set `awaiting_owner_approval` with `external_action_allowed=false`.
16. Send or submit only after explicit owner approval.

Repeated unchanged runs reuse stage outputs by input hash. A changed runtime bundle invalidates only dependent stages.

## Production gates

### Live-vacancy gate

Verification is retained as confidence metadata and is not required for generation. `unverified` or incomplete-live records may be scored and prepared when they meet the threshold and have a usable route; records explicitly marked `closed` remain blocked.

### Mandatory-domain gate

A mandatory sector or project-type requirement is treated as a material gap unless directly supported by approved claim-level evidence. Generic portfolio or retail wording cannot prove explicit major-mall, stadium, hospitality, defence or similar mandatory experience.

### Generation contract

One bounded LLM pass may write coherent vacancy-specific prose. Every factual statement must cite approved claim IDs. Import rejects unsupported or fabricated claims, changed employers/titles/dates, stale bundle hashes, prohibited names, availability wording, excessive numeric density and invalid role attribution. A second LLM review is exceptional, not routine.

### Route and external-action controls

- Email requires an actual verified recipient and source.
- Email patterns and recipients are never guessed.
- Self-addressed review drafts are prohibited.
- Portal-only roles create no Gmail application draft.
- No email is sent and no portal is submitted without explicit owner approval.

## Approved renderer

Template:

`templates/cv/2026-08-03_Abdelhamid_Farah_CV_Template_Modern_Executive_Sidebar_v1.0.docx`

Verified SHA-256:

`0abf9c85e492ad258bf9289b3fef8a1e8d86d84887396dea6fce404f85646123`

Manifest SHA-256:

`b423a1cab2d51f5df3a9901396110a73bdcbfbd6cebdb04429aa77b87ba7151e`

The renderer fails closed if the approved template is missing or invalid. LibreOffice resolution preserves explicit environment override precedence, then PATH precedence, supports executable permission bits on `noexec` mounts, and retains the user-local fallback. The resolver tests must not be weakened.

A second renderer, `ats-linear` (see `docs/CAREER_ENGINE_V1.md`), is generated for every application alongside the approved sidebar template. It is built programmatically from `projects/job-automation/config/ats-linear-template.v1.json` (a non-bundle source so generation packets keep their `bundle_hash`), renders via `./career-engine render-ats --job-id JOB_ID`, and is ATS-safe by construction: one column, no tables, no images, no text boxes, no floating objects, selectable text, left-aligned body and no headshot. Both variants are retained for review, but each application selects exactly one CV: ATS Linear by default for portals and Modern Executive Sidebar by default for email. A persisted per-job preview override may change that selection. Email drafts are stored in `hameedo@gmail.com`, expose only `hameedfarah@gmail.com` as the outward sender identity, and attach only the selected CV PDF.

## Runtime bundle

Verified production bundle hash after the canonical Vault closeout:

`67995a223e3362c5ed3bc7501d393294e6575b945eb2706ac4bc452598f27972`

The bundle compiles the canonical Vault profile, playbook, verified profile, metrics bank and North Star together with repository configuration and taxonomy. The full 4 August 2026 post-release Hermes scan validated the same engine and policy using the immediately preceding bundle. A documentation self-reference was then removed from the playbook, producing this stable final hash; a no-agent cron context check confirmed that the production pre-run script resolves this final bundle.

## Scanner behavior

`chatgpt_scanner` and `hermes_scanner` retain distinct source identities while calling the same engine and runtime bundle. They may discover, normalize, track, score and prepare qualifying roles. They never send or submit.

The Hermes daily job contract is:

- Job ID: `edc36e531637`
- Schedule: `0 8 * * *`
- Timezone: Asia/Riyadh
- Workdir: `/home/hameedo/projects/ai-job-search`
- Provider: `opencode-go`
- Model: `deepseek-v4-flash`
- Skill: `career-engine`
- External actions: disabled

Scheduler configuration and the last completed run must be checked outside this repository before final operational sign-off. A stale or in-flight durable execution must not be described as a successful post-release run.

## Gmail behavior

The repository-native Gmail/gws authentication path currently reports `invalid_grant` and must be re-authorized before it can create or verify drafts. A connected ChatGPT Gmail connector is a separate execution path and may remain usable. In either case the engine must search existing drafts before creating another and verify recipient, subject, body, attachment filename/hash and DRAFT status. Mailbox data and draft payloads are runtime data and are never committed.

## Commands

```bash
./career-engine doctor
./career-engine bundle build
./career-engine bundle status
./career-engine bundle validate
./career-engine prepare --jd-file JOB.txt --company COMPANY --role ROLE --application-url OFFICIAL_URL --live-status live --live-verified-at TIMESTAMP --live-verification-source SOURCE
./career-engine scanner ingest --file jobs.json --scanner-id chatgpt_scanner
./career-engine scanner ingest --file jobs.json --scanner-id hermes_scanner
./career-engine generate export --job-id JOB_ID
./career-engine generate import --job-id JOB_ID --file generated_application.json
./career-engine validate --job-id JOB_ID
./career-engine render --job-id JOB_ID
./career-engine package --job-id JOB_ID
```

## Release boundary

Commit source, configuration, schemas, tests, sanitized fixtures, skills, documentation, launcher and the approved template/manifest only.

Never commit:

- tracker runtime data or append-only event contents;
- generated CVs, PDFs, packets or application results;
- live vacancy or scanner inputs/results;
- Gmail/mailbox-derived data;
- temporary prompts;
- caches or test temp directories;
- OAuth data, tokens or secrets.

## Recovery

1. Run `./career-engine doctor`.
2. Run bundle status and validation; rebuild after Vault/config changes.
3. Confirm the approved template and manifest hashes.
4. Re-run the full test and guard suite.
5. Re-run only stages whose input hashes changed.
6. Inspect the canonical tracker and append-only event log; never infer completion from a missing artifact.
7. Check existing Gmail drafts before draft creation.
8. Check the Hermes job definition, durable execution history and latest output before claiming scheduler success.
9. Preserve the owner approval gate and never convert a blocked route into an external action.
