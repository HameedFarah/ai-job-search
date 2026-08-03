# Career Engine Production Closeout Record

**Date:** 2026-08-04

**Owner:** Abdelhamid Farah

**Status:** Release verification in progress

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
- `git diff --check`: passed before final documentation edits and must be rerun before commit.
- Career Engine doctor: valid; bundle current; template verified; LibreOffice resolved.
- Runtime bundle hash: `995762707774802ea0eef79733b251385d2a31f6bffe3c8b927774584db5b513`.
- Template SHA-256: `fa23aaf25519ef527e52761c2a3c5738639e642cf9e6830593caaf6a8fd8629e`.
- Manifest SHA-256: `b423a1cab2d51f5df3a9901396110a73bdcbfbd6cebdb04429aa77b87ba7151e`.

All tests, guards, hashes and bundle state must be rerun after the final documentation and Vault changes.

## Hermes scheduler

Verified job definition for `edc36e531637`:

- schedule `0 8 * * *`;
- timezone Asia/Riyadh;
- workdir `/home/hameedo/projects/ai-job-search`;
- provider `opencode-go`;
- model `deepseek-v4-flash`;
- skill `career-engine`;
- enabled and non-sending/non-submitting.

The last completed output predates the final repository release. A newer direct durable execution is still recorded as `running` without a corresponding output artifact. The job definition is verified, but a clean post-release scheduled/manual execution is still required before claiming full cron runtime verification. Do not edit the SQLite ledger or restart the gateway merely to improve status display.

## Gmail

- Connected ChatGPT Gmail access is operational for draft inspection.
- Repository-native Gmail/gws authentication reports `invalid_grant` and requires re-authorization.
- Existing legacy duplicate drafts and one self-addressed review draft were observed; no mailbox mutation was performed during closeout.
- The production engine must search existing drafts before create/update and may create only an unsent draft for a verified real recipient.
- Portal-only roles remain portal-only.
- No email was sent and no application was submitted.

## Release checklist

- [x] Remove temporary prompts, caches, scanner reports and application-specific test inputs.
- [x] Harden `.gitignore` without hiding source, configuration, tests, docs or templates.
- [x] Replace stale repository agent guidance with central-engine/Vault pointers.
- [x] Update production implementation documentation.
- [ ] Rerun full tests, guards, doctor, bundle validation and hashes.
- [ ] Review staged file list and confirm no runtime/application/mailbox data.
- [ ] Commit repository using `feat(career): centralize and harden application engine workflows`.
- [ ] Push repository and verify local HEAD equals `origin/master`.
- [ ] Update and separately commit the canonical Vault playbook, index and status.
- [ ] Push Vault and verify both repositories clean.
- [ ] Recheck Hermes durable execution state and record any remaining blocker accurately.

## Completion rule

Closeout is complete only after the scoped repository and Vault commits are pushed, both repositories are clean, final tests and guards pass, and the Hermes/Gmail status is reported without overstating unresolved external authentication or scheduler execution state.
