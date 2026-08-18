# Career Cloudflare Access migration — implementation plan

**Plan ID:** `career-cloudflare-access-20260818`  
**Canonical tracker:** `docs/implementation/career-cloudflare-access-20260818-tracker.csv`

## Architecture

```text
CareerTracker (canonical)
  -> dashboard/career-review/scripts/build_site.js
  -> dashboard/career-review/site/
  -> Cloudflare Worker static assets
  -> Worker route career.farahdigital.com/*
  -> Cloudflare Access exact-email policy
  -> owner browser

Dashboard browser mutations
  -> existing here.now Site Data API
  -> canonical reconciliation back into CareerTracker where permitted
```

The existing proxied DNS record remains in place. The Worker route intercepts the hostname and serves static assets. This deliberately preserves the here.now origin as rollback and avoids creating a second public static hostname.

## Source changes

### Worker configuration

Add `dashboard/career-review/wrangler.jsonc` with:

- fixed Worker name `career-engine-private`;
- current compatibility date;
- static asset directory `./site`;
- `workers_dev: false`;
- `preview_urls: false`;
- route `career.farahdigital.com/*` in the `farahdigital.com` zone.

### Access/deployment reconciler

Add `dashboard/career-review/scripts/deploy_cloudflare_access.js` which is idempotent and fail-closed:

1. validate `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_TOKEN`, and `CLOUDFLARE_ZONE_ID` are present without printing values;
2. read existing DNS and prove `career.farahdigital.com` is Cloudflare-proxied before adding a Worker route;
3. list Access applications and find the exact hostname;
4. create the self-hosted Access app if absent, or verify/reconcile its application-local owner policy;
5. permit only `hameedo@gmail.com` through an `allow` policy and reject unsafe `Everyone` rules in application-local policies managed by this script;
6. only after Access is in place, run Wrangler to deploy `dashboard/career-review/site` using `wrangler.jsonc`;
7. re-read Access and Worker/route state and emit only value-free deployment evidence;
8. support `--preflight`, `--deploy`, and `--verify` modes; preflight must perform no mutation.

The script must be invoked through the existing infrastructure wrapper so credentials remain Infisical-only:

```bash
/home/hameedo/vps-infra-dev/scripts/operations/cloudflare-with-infisical-runtime.sh \
  node dashboard/career-review/scripts/deploy_cloudflare_access.js --preflight
```

For deployment replace `--preflight` with `--deploy`; final verification uses `--verify`.

### Security headers

Add `dashboard/career-review/site/_headers` to enforce no-store/noindex and basic browser hardening without interfering with file downloads or the existing browser workflow.

### Documentation

Update the dashboard README after production acceptance to make Cloudflare Access + Worker the primary serving path and here.now the Site Data/rollback dependency, not the viewer authentication authority.

## Production sequence

1. Fast-forward local `master` to GitHub without overwriting the unrelated `career_engine/ops.py` edit.
2. Run `./career-engine doctor`, bundle status, focused dashboard tests, and canonical dashboard build.
3. Run Cloudflare `--preflight` under the canonical Infisical wrapper.
4. If preflight passes, reconcile Access application/policy.
5. Deploy the Worker route and static assets.
6. Verify from an unauthenticated client that dashboard HTML/job JSON are not exposed and login is Cloudflare Access.
7. Authenticate as `hameedo@gmail.com` in a real browser and verify dashboard/UI.
8. Run Site Data integration and owner-browser write/read regression.
9. Compare rendered dashboard job IDs/counts with CareerTracker.
10. Test rollback by removing the Worker route from a bounded reversible test path or by verifying the previous CNAME/origin remains intact and documenting the exact disable command. Do not delete the old here.now site.
11. Independent reviewer moves tracker tasks from Review to Done.
12. Update the job-automation Vault status/report, run OKF validation, commit and push.

## Regression checks

- `./career-engine doctor`
- `./career-engine bundle status`
- current Career Engine validation suite used by the project
- dashboard build and count invariant
- browser desktop/mobile smoke
- Add Job UI opens; no submission/email occurs
- stage/comment/AI-request/preferences Site Data paths remain usable
- direct here.now fallback remains restricted

## Rollback

Because the existing DNS origin is preserved, rollback is:

1. disable/remove the Worker route for `career.farahdigital.com/*` (or redeploy the Worker without that route);
2. verify the existing proxied DNS record still targets the previous here.now origin;
3. verify `career.farahdigital.com` again returns the prior here.now restricted-access response;
4. keep or remove the new Access application according to whether it obstructs the restored here.now login path; if removed, verify the fallback remains restricted by here.now before declaring rollback complete.

No rollback step deletes CareerTracker, Site Data, generated application artifacts, or the here.now deployment.
