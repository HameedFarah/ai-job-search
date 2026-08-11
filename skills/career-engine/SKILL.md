---
name: career-engine
description: Centralized job evaluation and evidence-grounded application pipeline for Abdelhamid Farah.
---

# Career Engine

## Central authorities

Before any scan, review, generation or correction, read:

1. `/home/hameedo/obsidian/HermesOpsVault/projects/agent-ops/model-routing.md` for inference routing only.
2. `projects/job-automation/config/central-rules.README.md`
3. `projects/job-automation/config/career-engine.v1.json`
4. `/home/hameedo/obsidian/HermesOpsVault/projects/job-automation/playbooks/career-engine-operations-contract.md`
5. Current Vault `projects/job-automation/index.md` and `status.md`

Then run `./career-engine doctor` and `./career-engine bundle status`. The routing authority, central config and operating contract override stale chat summaries, installed skill copies or review-diff narratives.

Always invoke the repository CLI and compiled runtime bundle. This skill contains no independent career facts or policy.

Repository: `/home/hameedo/projects/ai-job-search`

## Entry points

```bash
cd /home/hameedo/projects/ai-job-search
./career-engine bundle build
./career-engine validate-config
./career-engine run
./career-engine reconcile
./career-engine list-jobs --status generation_ready
./career-engine validate
./career-engine record-review [--file <review.json>]
./career-engine prepare --jd-file <file> --company <company> --role <role> --live-status live --live-verified-at <timestamp> --live-verification-source <source> [route metadata]
```

For credible roles, use the generated packet for one structured free-prose generation pass, then import it through the engine. Do not draft independently from this skill text.

`validate-config` fails closed on config/bundle/tracker errors; `run` is the
idempotent no-send orchestration (bundle + reconcile + prepare eligible +
local dashboard data sync); `reconcile` enforces the centralized threshold and
persisted owner decisions; `record-review` accepts `--file` and defaults to
`runtime/review-diffs/latest.json` when it validates.

Never send, submit or contact externally. A Gmail draft requires a verified real recipient. Portal-only roles receive the official application link.
