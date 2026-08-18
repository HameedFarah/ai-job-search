# Career Cloudflare Access migration — decision log

## 2026-08-18 — Replace here.now viewer authentication

**Decision:** make Cloudflare Access the owner authentication gate for `career.farahdigital.com`.

**Reason:** the Career Engine and here.now Site Data API are healthy, while the owner-visible failure occurs on the here.now restricted-viewer login route before the dashboard loads.

**Impact:** dashboard serving/authentication moves to Cloudflare; CareerTracker and no-send/no-submit rules do not change.

## 2026-08-18 — Use Workers static assets on the existing proxied hostname

**Decision:** deploy the generated dashboard as Cloudflare Workers static assets on a Worker route for `career.farahdigital.com/*`, with `workers_dev` and preview URLs disabled.

**Reason:** this avoids a second public `pages.dev` production origin and avoids replacing the existing DNS record. The Worker route intercepts the existing proxied hostname, which makes rollback materially simpler.

**Impact:** the previous here.now origin and DNS target remain available behind the route until acceptance. Rollback can remove the Worker route rather than reconstruct the old origin.

## 2026-08-18 — Keep here.now Site Data initially

**Decision:** do not migrate the Site Data projection/evidence API as part of the access incident unless live browser acceptance proves cross-origin incompatibility.

**Reason:** owner-API CRUD for all five collections passed. Moving working state infrastructure during an authentication incident adds risk without current evidence of need.

**Impact:** browser state still uses here.now Site Data, while CareerTracker remains the only lifecycle authority. This dependency must be tested from the new Cloudflare-served origin.

## 2026-08-18 — Preserve the here.now deployment for rollback

**Decision:** do not delete `gilded-timber-xfj7` during this recovery.

**Reason:** it is the verified previous production deployment and provides a bounded rollback origin.

**Impact:** retirement of the here.now viewer/hosting path is a later cleanup decision after Cloudflare acceptance and stability, not part of the cutover itself.
