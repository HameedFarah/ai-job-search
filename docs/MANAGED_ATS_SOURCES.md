# Managed ATS source reuse

Career Engine reuses portal-specific integrations from the maintained MIT-licensed
[`Fighter90/career-ops-ui`](https://github.com/Fighter90/career-ops-ui) project instead of
reimplementing every ATS.

## Boundary

Career Engine still owns candidate/job normalization, provenance, dedupe, scoring,
no-send policy and official-source promotion. `career-ops-ui` owns the provider-specific
HTTP/parsing code. The external checkout is pinned to the reviewed SHA in
`career_engine/sources/managed_providers.py`.

A weekly GitHub Action compares that lock to upstream `main` and updates one rolling
issue. It **does not automatically update production**. Review source changes, run
regressions, then deliberately bump the lock.

## Minimal VPS checkout

The checkout is source code only; it is not career-document storage. Keep it sparse and
pinned. Do not run `npm install`: the upstream package has a `prepare` lifecycle script,
and the selected source modules do not require the web UI's Express dependencies.

```bash
mkdir -p /home/hameedo/projects
cd /home/hameedo/projects
rm -rf career-ops-ui
git clone --filter=blob:none --no-checkout https://github.com/Fighter90/career-ops-ui.git career-ops-ui
cd career-ops-ui
git sparse-checkout init --cone
git sparse-checkout set server/lib
git checkout --detach 308722f2cc8be3b5dd591d5566ee97b56b90cf44
```

The Python bridge verifies `git rev-parse HEAD` against the reviewed lock before any
provider code is executed. A newer checkout fails closed until the lock is reviewed and
updated.

## Providers

First-priority generic ATS for GCC/Jordan/global employer coverage:

- Workday
- SAP SuccessFactors
- Oracle Recruiting Cloud
- iCIMS
- Avature
- Eightfold

Additional global coverage:

- Jobvite
- JibeApply
- BambooHR
- Breezy
- Comeet
- Teamtailor

Greenhouse, Lever, Ashby and SmartRecruiters are also available through the managed
bridge for comparison with the existing native Career Engine adapters. Native adapters
remain available as fallback until live evidence shows the managed implementation is
strictly better for the Career Engine's use cases.

## Probe examples

```bash
python3 -m career_engine.sources.managed_cli providers

python3 -m career_engine.sources.managed_cli probe \
  --provider workday \
  --company 'Parsons|https://parsons.wd5.myworkdayjobs.com/en-US/Search'

python3 -m career_engine.sources.managed_cli probe \
  --provider oraclecloud \
  --company '{"name":"Employer","careers_url":"https://tenant.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/jobs"}'
```

A company spec may be a careers URL, `Company|URL`, a JSON object, or `@file.json`.
Output uses the standard Career Engine discovery shape and always carries
`send_or_submit=false`.

## Upgrade procedure

1. Read the rolling **Managed ATS source sync watch** issue.
2. Compare the reviewed SHA to the new upstream SHA.
3. Focus review on `server/lib/portals/adapters/`, `server/lib/sources/` and their shared helpers.
4. Run managed source offline tests plus bounded live probes for representative GCC/Jordan employers.
5. Bump `UPSTREAM_REF` only after acceptance.
6. Move the sparse VPS checkout to the accepted SHA.
7. Never make an upstream update automatically change a production scan.
