# Career Engine private access migration — baseline

**Date:** 2026-08-18  
**Plan ID:** `career-cloudflare-access-20260818`  
**Status:** accepted remediation baseline

## Incident

The Career Engine application and canonical tracker are healthy, but the owner cannot reliably enter the private dashboard because the current production path depends on the `here.now` restricted-viewer login route before the dashboard is served.

Observed before mutation:

- `career.farahdigital.com` and `gilded-timber-xfj7.here.now` return the expected unauthenticated `401 access_restricted` response from the VPS.
- here.now Site Data owner-API CRUD passed for all five collections: `workflow`, `comments`, `history`, `ai_requests`, and `preferences`.
- `./career-engine doctor` passed with the current runtime bundle.
- the 2026-08-18 no-send scan validated 1,745 CareerTracker jobs and synchronized the dashboard locally, but the external dashboard was not republished; the current external deployment still identifies the 2026-08-16 here.now release.
- the existing `/home/hameedo/projects/ai-job-search` checkout contains an unrelated, valid pending edit in `career_engine/ops.py`; this migration must preserve it.

## Root cause boundary

The failure is at the private viewer/authentication surface, not in CareerTracker, package generation, local dashboard building, or the here.now Site Data owner API. Rebuilding Career Engine alone does not repair the failing login path.

## Chosen target

Move the dashboard's **serving and authentication path** to Cloudflare while keeping the existing CareerTracker authority and, initially, the already-working here.now Site Data projection/evidence API.

Target request path:

`browser -> Cloudflare Access -> career.farahdigital.com -> Cloudflare Worker static assets`

The Worker will be attached as a route in front of the existing proxied `career.farahdigital.com` DNS record. It will serve the generated static dashboard itself and will not fetch the here.now origin for matched requests.

Security requirements:

- Cloudflare Access exact-email allow for `hameedo@gmail.com` only.
- deny-by-default Access behavior; no `Everyone` allow rule.
- `workers.dev = false` and preview URLs disabled so the Worker has no alternate public Cloudflare hostname.
- no secrets committed; Cloudflare credentials remain injected through the canonical Infisical runtime wrapper.
- no change to CareerTracker authority or no-send/no-submit controls.

## Why Worker route instead of a new Pages origin

A Pages migration would create a `*.pages.dev` production origin that needs additional Access/redirect hardening. Cloudflare's current Workers static-assets platform can serve the same generated directory while disabling `workers.dev` and preview URLs. A Worker route can also sit in front of the existing proxied hostname without first deleting the here.now CNAME, giving a materially simpler rollback: remove the Worker route and the previous origin resumes.

## Rollback baseline

Until final acceptance:

- preserve the current here.now slug `gilded-timber-xfj7` and its restricted access configuration;
- preserve the existing DNS record and here.now Site Data;
- do not delete the previous deployment;
- if Cloudflare acceptance fails, remove/disable the Career Engine Worker route and Access application/policy changes made by this plan, then verify the previous `401 access_restricted` behavior returns on the custom domain.

## Acceptance gates

1. Source validation and regression checks pass.
2. Access application exists for exactly `career.farahdigital.com` and permits only `hameedo@gmail.com`.
3. Unauthenticated requests are intercepted by Cloudflare Access and do not expose dashboard HTML or static job data.
4. Authenticated owner browser reaches the current dashboard.
5. Current dashboard counts match CareerTracker after republish.
6. Site Data read/write smoke passes from the Cloudflare-hosted dashboard; no duplicate or lost tracker state.
7. `workers.dev` and preview URLs are disabled.
8. Rollback is proven without deleting the here.now fallback.
9. Canonical job-automation Vault status is updated only after independent acceptance.
