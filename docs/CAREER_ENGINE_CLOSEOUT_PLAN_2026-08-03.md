# Career Engine Production Closeout Record

**Date:** 2026-08-04

**Owner:** Abdelhamid Farah

**Status:** Production release complete; repository-native Gmail re-authorization remains an external integration task

## Objective

Close out one reusable Career Engine for direct ChatGPT requests, ChatGPT and Hermes scanners, the repository CLI, scheduled execution and future API/SaaS clients. The release is not a single-application package.

## Verified implementation state

- [x] Central importable `career_engine/` package implemented.
- [x] Repository configuration, schemas and requirement taxonomy implemented.
- [x] One compiled runtime bundle used by every entry point.
- [x] Deterministic normalization, evidence matching, scoring, route selection, caching and tracker integration implemented.
- [x] Verified-live vacancy gate implemented.
- [x] Mandatory-domain gap gate implemented.
- [x] One-pass structured generation contract implemented with claim citations.
- [x] Unsupported claims, altered chronology, prohibited names, availability wording and invalid filenames rejected.
- [x] Approved two-page sidebar/headshot DOCX template installed and hash-locked.
- [x] LibreOffice resolver supports environment override, PATH, executable permission-bit and user-local fallback paths.
- [x] Renderer, PDF conversion, page/text-layer and outward filename checks implemented.
- [x] `chatgpt_scanner` and `hermes_scanner` wrappers retain distinct identities and call the central engine.
- [x] Daily scanner calls the same central pipeline and cannot send or submit.
- [x] `.claude` commands/skills and repository Hermes skill are thin clients.
- [x] Owner approval and `external_action_allowed=false` gates preserved.
- [x] Runtime/generated/mailbox/prompt/cache/secret artifacts excluded from release scope.
- [x] Application-specific test dependencies replaced with sanitized fictional fixtures.

## Verified technical evidence

- Full final repository suite after sanitization and documentation updates: **226 passed**.
- Sanitized live/domain/generation gate suite: **33 passed**.
- Framework-version guard: passed.
- Skill lint: passed.
- Security guards: passed.
- `git diff --check` and staged diff checks: passed.
- Career Engine doctor: valid; bundle current; template verified; LibreOffice resolved.
- Runtime bundle hash after final Vault closeout: `67995a223e3362c5ed3bc7501d393294e6575b945eb2706ac4bc452598f27972`.
- Template SHA-256: `fa23aaf25519ef527e52761c2a3c5738639e642cf9e6830593caaf6a8fd8629e`.
- Manifest SHA-256: `b423a1cab2d51f5df3a9901396110a73bdcbfbd6cebdb04429aa77b87ba7151e`.
- Post-release Hermes execution `67f7fafef682486988cc5852adc8590c`: completed successfully on 4 August 2026.
- Post-release scan: seven roles ingested; all seven remained unverified and were blocked from generation; no send, recruiter contact or submission occurred.

## Hermes scheduler

Verified job definition for `edc36e531637`:

- schedule `0 8 * * *`;
- timezone Asia/Riyadh;
- workdir `/home/hameedo/projects/ai-job-search`;
- provider `opencode-go`;
- model `deepseek-v4-flash`;
- skill `career-engine`;
- enabled and non-sending/non-submitting.

A clean post-release manual execution completed successfully as durable run `67f7fafef682486988cc5852adc8590c` and produced `cron/output/edc36e531637/2026-08-04_02-38-50.md`. It validated the released engine and policy using the immediately preceding bundle, discovered current roles, invoked only the central Hermes scanner, respected all live-vacancy gates and performed no external action. After removing a circular documentation self-reference from the playbook, a no-agent cron context check confirmed that the production pre-run script now resolves the stable final bundle `67995a223e3362c5ed3bc7501d393294e6575b945eb2706ac4bc452598f27972`.

Two earlier direct attempts were safely reconciled to `unknown` after their foreground owners were terminated by connector time limits. Recovery used Hermes' built-in execution recovery through the managed runtime; the SQLite ledger was not edited directly and the gateway was not restarted.

## Gmail

- `hameedo@gmail.com` is the sole approved application identity and Gmail draft account. The superseded `hameedfarah@gmail.com` address must not be used.
- Repository-native Gmail/gws authentication for the approved identity reports `invalid_grant` and requires re-authorization.
- Existing legacy duplicate drafts and one self-addressed review draft were observed in the legacy mailbox; no mailbox mutation was performed during closeout.
- The production engine must search existing drafts before create/update and may create only an unsent draft for a verified real recipient.
- Portal-only roles remain portal-only.
- No email was sent and no application was submitted.

## Release checklist

- [x] Remove temporary prompts, caches, scanner reports and application-specific test inputs.
- [x] Harden `.gitignore` without hiding source, configuration, tests, docs or templates.
- [x] Replace stale repository agent guidance with central-engine/Vault pointers.
- [x] Update production implementation documentation.
- [x] Rerun full tests, guards, doctor, bundle validation and hashes.
- [x] Review staged file list and confirm no runtime/application/mailbox data.
- [x] Commit repository using `feat(career): centralize and harden application engine workflows`.
- [x] Push repository and verify local HEAD equals `origin/master`.
- [x] Update and separately commit the canonical Vault playbook, index and status.
- [x] Push Vault and verify both repositories clean.
- [x] Recheck Hermes durable execution state and complete a clean post-release run.

## Completion rule

Closeout is complete only after the scoped repository and Vault commits are pushed, both repositories are clean, final tests and guards pass, and the Hermes/Gmail status is reported without overstating unresolved external authentication or scheduler execution state.
