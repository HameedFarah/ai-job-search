# REGA API enrichment execution plan

Owner-authorized 2026-09-11: implement and run on VPS; preserve REGA before Balady and HR/recruitment before general inbox, retaining all official portals. This plan supersedes the review-only execution outline. Durable task: Hermes general / t_96c554af.

## Findings driving implementation

Live cohort has 750 unique nonblank Master IDs. The 16 checkpoint exclusions are 12 existing receiving-email statuses and four portal statuses. The Qwant backend actually reports CAPTCHA; its old wrapper hides that as zero results. A direct Exa MCP known-positive query recovered the official Armal site. All three newly supplied APIs authenticate and accept relevant calls.

## Implementation and verification

- Use an isolated codex/rega-api-enrichment-20260911 branch based on resolver source c5bf017. Preserve original resolver/sender worktrees.
- Expose `career-engine rega-enrich` through the existing engine CLI. Keep the existing Master Tracker as authority; local JSON/CSV files are resumable execution evidence and exports only.
- Fetch identities using the existing healthy SearXNG Google engine, with Exa MCP as fallback with a persistent 1,200-search ceiling, request deadlines and a five-consecutive-error circuit breaker. Confirm candidate identities on fetched first-party pages; never accept a domain merely because a provider suggested it.
- Collect both email and portal routes. Use first-party pages and Hunter, Prospeo HR search/reveal, and Snov generic-contact fallback; verify selected evidenced mailboxes with Hunter. Keep named employee candidates held unless appropriate hiring evidence is independently available.
- Secrets enter only through the maintained Infisical runtime injector and the value-free four-key manifest. Never write credentials to Git or artifacts.
- Persist conservative credit reservations before requests. Cache successes, preserve ambiguous charge reservations, resume asynchronous results without starting a second operation, and stop a provider on quota/auth failures. No purchase endpoints.
- Append findings to existing Master Tracker Notes, fill a newly confirmed website if blank, and read back the writes. Preserve existing email/send/lifecycle fields: research authorization does not release a new sending cohort. Existing queue suppression and company coverage guide research selection.
- Use one VPS research service and one global lock. Existing senders/timers are untouched. Heartbeat every 15 seconds, progress per company, company timeout 150 seconds, service timeout six hours.

## Pilot then full run

First run: 12 companies, six known-domain and six unresolved identities. Credit ceilings: Hunter 15, Prospeo 20, Snov 50. This replaces the proposed 30-company pilot with a smaller initial live acceptance to reduce spend and catch integration defects earlier. It is a feasibility sample, not a reliable whole-cohort yield estimate.

After safe live results and tracker readback, continue the same checkpoint across all remaining eligible REGA rows. Total persisted credit-reservation caps for the run: Hunter 35, Prospeo 60, Snov 200; startup also caps spend to 90% of current remaining provider capacity. These are upper bounds, not forecasts or permission to buy more credit. A provider may stop while free identity/page research continues.

## Operation

Run from this checkout:

```bash
rtk scripts/rega-enrichment-vps.sh pilot
rtk scripts/rega-enrichment-vps.sh start
rtk scripts/rega-enrichment-vps.sh status
rtk scripts/rega-enrichment-vps.sh stop
```

State/export root: `/home/hameedo/projects/ai-job-search/runtime/acceptance/rega-api-enrichment-20260911`.

Read `summary.json`, `heartbeat.json`, `index.csv`, and `run.log`. `scope_processed` means the selected research pass ended, not that every company has an email or that outreach was sent. Distinguish identity-unconfirmed, backend errors, mailbox holds, portal-only, and validated HR/general outcomes. Stop/resume uses the same checkpoint and budgets.

## Monitoring and spending decision

Recommend checking after 30 minutes, then hourly while the service is active; investigate a heartbeat older than two minutes, failed service, provider halt or a prolonged absence of newly processed companies. No extra monitoring service is necessary. Report useful incremental company routes per actual credit, and compare account balances before recommending any top-up. Google through the already installed SearXNG is the working free search path. Qwant is CAPTCHA-blocked; Exa shared free access hit its limit during the pilot. Brave remains optional only if the healthy free Google path also fails. Use provider account results rather than an assumed free-plan entitlement.

## Acceptance

Focused unit/regression tests, source diff review, no secrets in stored artifacts, pilot output verified against tracker, process alive with advancing checkpoint, restart-safe budgets, exact source commit and concise owner monitoring instructions. Live evidence and final source SHA are recorded beside the run; do not commit live tracker/export data.

## Pilot corrections accepted before scaling

The initial bounded run exposed shared Exa free-cap exhaustion, a named-HR false match from another person’s social activity, and third-party article/agency domains incorrectly matched to employer names. The run was stopped; affected records were corrected with append-only notes and exact readback, the one newly filled incorrect website was restored to its original blank value, and all pilot research records were archived before rerunning. No contact entered the send queue. Regression tests now require homepage ownership evidence and the hiring profile’s own name/title; 113 targeted tests pass.
