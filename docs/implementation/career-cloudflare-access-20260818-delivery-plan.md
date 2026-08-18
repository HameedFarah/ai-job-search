# Career Cloudflare Access migration — delivery plan

**Plan ID:** `career-cloudflare-access-20260818`

## Outcome

Restore reliable private access to `https://career.farahdigital.com/` without weakening privacy, changing CareerTracker authority, or creating a second application-state store.

## Delivery sequence

1. **Baseline and rollback capture** — preserve the current here.now fallback, DNS/origin state and unrelated local repository work.
2. **Source implementation** — add a Worker static-assets configuration, Cloudflare Access reconciler/deployer, security headers and deterministic validation.
3. **Preflight** — prove Cloudflare token permissions, existing DNS is proxied, exact-email Access policy can be reconciled, and the generated site passes canonical count gates.
4. **Protected cutover** — create/update Access first, then deploy the Worker static-assets route. Keep `workers.dev` and preview URLs disabled.
5. **Acceptance** — prove unauthenticated denial, authenticated owner browser access, current dashboard counts, UI workflow and here.now Site Data read/write behavior.
6. **Closeout** — independently review evidence, update job-automation canon, validate/push the Vault, retain the here.now deployment only as bounded rollback until the new path has remained accepted.

## Execution ownership

- OpenCode: source changes, tests, static validation and implementation commit.
- Hermes/SRE: Cloudflare preflight, deployment, browser/runtime verification, rollback verification and Vault closeout.
- ChatGPT: acceptance criteria, evidence review and final status.

## Non-goals

- moving CareerTracker or changing its lifecycle authority;
- changing no-send/no-submit policy;
- rewriting the dashboard UI;
- migrating here.now Site Data unless the post-cutover browser test proves it is incompatible;
- deleting the existing here.now fallback during this incident recovery.

## Stop conditions

Stop before production mutation and report BLOCKED if:

- the Cloudflare token cannot prove the required Workers and Access write permissions;
- the target DNS record is not Cloudflare-proxied and a safe rollback cannot be captured;
- Access cannot be established before the Worker route becomes active;
- source/dashboard counts diverge from CareerTracker;
- the Worker would expose a `workers.dev` or preview URL;
- the authenticated dashboard cannot persist its existing Site Data workflow without weakening access controls.
