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


## Yield correction, 2026-09-11

The owner rejected the low first-pass yield. Paused the service and diagnosed
false-negative identity matching (legal suffixes, Arabic/English transliteration,
and single-word brands), script/style text displacing contact details, skipped
existing career paths, and unreadable JavaScript shells.

Discovery version 2 strips legal suffixes, requires employer branding plus Saudi
and sector evidence, strips script/style text, reuses existing career paths,
prioritizes contact/employment links, and uses bounded free public Jina Reader
retrieval for JavaScript shells (100 cumulative requests; stops on capacity errors).
The prior news-site, agency-portfolio and similarly named Egyptian company false
matches remain rejected. No guessed addresses or send eligibility changes.

Retry all previously unresolved/held version-1 records while preserving prior
history, existing confirmed evidence on failed retries, the shared cache and credit
reservations. Report website-published addresses, mailbox-verified addresses and
portals independently so email status does not hide portal results.

Live read-only acceptance recovered website-published info inboxes at Tilal,
Wakan and Alzamiliah, plus careers pages at Tilal, Wakan and Ladun. These
addresses are not called mailbox-verified until the verification API succeeds.
Hunter cap increases from 35 to 45 existing credits, still bounded by live account
balance with a 10% reserve. The last 10 budget credits are reserved for mailbox
verification; cached Hunter discovery remains available. Prospeo/Snov caps remain
60/200. No purchase or automatic top-up is enabled.


A later live diagnostic found Google CAPTCHA and Exa transport-level HTTP 429
errors were parsed as empty successful searches. Corrected Exa error-envelope
handling, invalidated empty-cache reuse, and added a tested SearXNG Yandex fallback
with two-second minimum search spacing and per-run engine disablement on errors.
Wakan Arabic lookup returned the official domain; capacity failures now pause
research instead of declaring companies unresolvable. This is an additional cause
of the first-pass low yield; old unresolved rows are retried.


## Owner-authorized enrichment and dated sender release, 2026-09-12

Owner explicitly authorized enriching the remainder, adding recovered addresses
to the sender queue, and sending tomorrow at 08:00 with the correct resume,
profile/portfolio and cover email. VPS date was September 12: release date is
**2026-09-13 08:00 Asia/Riyadh**, explicitly stated in the conversation.

Discovery version 3 adds short distinctive-name searches, public Reader retries
for failed candidate pages, and up to 500 cumulative Reader requests. Existing
Snov credits now fund official v2 mailbox verification; Hunter is reserved for
verification fallback, not new domain searches. Explicit catch-all/negative
results remain held. Caps: Hunter 60, Prospeo 60, Snov 400 cumulative reservations;
each remains bounded by live account balance with a 10% reserve. No purchases.

`scripts/rega_sender_admission.py` stages every unique recovered address in the
existing Auto Send Queue, preserving existing rows and adding only HOLD records.
Only exact-email RECEIVING results with relevance evidence qualify for dated
release, after canonical Gmail/company/domain/bounce dedupe. Full readback follows
writes. The authorization and campaign hashes are runtime evidence, not a new
operational queue. New candidates are staged periodically while enrichment runs.

`scripts/rega_recovered_sender.py` reuses the production central sender and scopes
its queue reads to this authorized recovery source. It preserves the canonical
ledger, singleton lock, Gmail/MIME/readback checks, 96-second cadence, 300 daily
cap and 08:00-19:00 window. It does not release unrelated pending Balady rows.
Exactly two checked PDFs are used: one Arabic CV and the 2026 portfolio, plus the
existing Arabic cover email. Campaign content or attachment hash changes stop the
release for review. No immediate send occurs during setup or acceptance.

Task: t_73094078. Delegation route authority commit
91f6e40033cdb8f7e6350a1b77a872ea080063e9. Route 1 timed out; route 2 InferX completed.
Parent rejected its undocumented verifier endpoints and corrected against official
https://snov.io/api. Live v2 acceptance correctly held info@tilalre.com as catch-all.


Owner correction: start is Saturday **2026-09-12 08:00 Asia/Riyadh**, not September 13. Runtime authorization, queue schedule evidence and active release timer were corrected and verified. This supersedes the earlier tomorrow interpretation.


## Discovery v4, 2026-09-12

At 02:02 Riyadh, verified mailboxes reached 25 (3 HR, 22 general), with
99 unique candidates and 27 portal URLs across 23 companies. Remaining identity
failures were 462. Preserve 100 Snov reservation credits for verification, blocking
new generic domain-contact calls when they would consume that allowance.

Use the official Snov v2 company-domain-by-name endpoint as a bounded additional
identity clue for priority A/B rows after ordinary discovery fails. Exact requested
name/result matching, persistent task cache, bounded polling and existing-credit
budget remain required. Provider domains never establish identity by themselves;
the same independent employer-site branding checks still apply. Support registry-
supplied acronyms that exactly match both the domain label and homepage branding,
with sector and Saudi evidence. Snov reservation ceiling 500 remains bounded by
live available balance; no purchases. Live probe evidence is
`snov-company-domain-probe.json` in the shared runtime evidence directory.

The three-name live pilot returned zero domain clues. Bulk company-name API lookup
therefore remains disabled unless REGA_ENABLE_COMPANY_DOMAIN_LOOKUP=1 is explicitly
set; do not spend remaining credits on this unproven path. The acronym identity fix
and protected verification allowance remain enabled. Last pre-restart checkpoint:
28 verified mailboxes (3 HR, 25 general), 99 candidates, 73 first-party emails,
27 portal URLs for 23 companies.

## Discovery v5
Preserve exact search URLs (including HTTP, www and language paths) before
homepage fallback. Use six candidate hosts per query instead of a shared ten-host
ceiling that starved later name variants. Recognize KSA as explicit Saudi evidence.
Live cached/public-page checks now confirm NHC and Ajdan; Awtad remains blocked
by its firewall. Reopen unresolved prior-version records without weakening employer
identity, exact-address verification or sender deduplication requirements.
