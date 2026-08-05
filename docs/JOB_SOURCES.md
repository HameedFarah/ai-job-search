# Career Engine Job Sources

**Status:** Implemented - discovery-only source-adapter framework.

This document is the factual companion to the in-repo source registry
(`career_engine/sources/registry.py`). The registry is the single authority
for which sources may be used, how they are accessed, what posting-date
precision they support, and why blocked sources are blocked. Adapters, tests,
probes and this document all derive from it.

Scope: **discovery-only ingestion**. Nothing in this framework sends email,
contacts a recruiter, submits an application, or writes mailbox data. Every
probe report carries `send_or_submit: false`, and every emitted job defaults
to `live_status: unverified` so the central Career Engine can score it but
cannot generate application content until the live-vacancy gate is satisfied
by an authoritative verification source.

---

## 1. Design principles

| Principle | Enforcement |
|---|---|
| Discovery only, no-send | `DiscoveryReport.send_or_submit` is always `false`; probes perform read-only HTTP GETs with explicit timeouts. |
| Strict provenance | Every job carries `provenance` (source id/name/kind, official flag, fetched-at, extracted-from, raw id, verification note). |
| Posting-date precision | Dates are never fabricated. Every value carries `exact \| day \| month \| unknown` plus the upstream field that produced it. |
| Dedupe | Stable keys from external id (+ URL), falling back to a normalized (company, role, location) triple; the central tracker deduplicates again by `(source, external_job_id, source_url, jd_hash)`. |
| Official-first | Non-official discovery candidates (search engines, inbox alerts) are never ingested as authoritative until verified against the employer's own page. |
| Bounded probes | Per-request timeout (12s), body size caps, `--limit` job caps. `--offline` runs deterministic fixture probes with no network. |
| No fragile scraping as core | Authenticated LinkedIn scraping, Bayt/NaukriGulf HTML scraping and Google/Bing keyed APIs are blocked or disabled sources (see §3). |

## 2. Adapters

Implemented under `career_engine/sources/adapters/` and probed from
`python3 -m career_engine.sources.cli`.

| Adapter id | Source | Endpoint | Posting date | Auth | Live-verified 2026-08-05 |
|---|---|---|---|---|---|
| `greenhouse` | Greenhouse Job Board API | `boards-api.greenhouse.io/v1/boards/{token}/jobs` | `first_published` (exact) | none | **GCC: `careem` (25), `tamara` (40)** |
| `lever` | Lever Postings API | `api.lever.co/v0/postings/{company}?mode=json` | `createdAt` ms (exact) | none | `leverdemo` (demo board) |
| `ashby` | Ashby Posting API | `api.ashbyhq.com/posting-api/job-board/{company}` | `publishedAt` (exact) | none | `ramp`, `linear`, `notion`, `plaid`, `opensea` |
| `smartrecruiters` | SmartRecruiters Public Postings | `api.smartrecruiters.com/v1/companies/{id}/postings` | `releasedDate` (exact) | none | `SmartRecruiters` (8 postings) |
| `workable` | Workable career pages/feeds | `apply.workable.com/api/v3\|v1/.../jobs` | `published_on` (day) | none | unverified (404 on tested identifiers) |
| `jsonld` | Employer career pages | JobPosting JSON-LD + job sitemaps | `datePosted` (exact) | none | verified offline + gate logic |
| `search_discovery` | Google/Bing/DuckDuckGo | DuckDuckGo Instant Answer API (public) | none | none/key | candidates only, must verify |
| `inbox_gmail` / `linkedin_alerts` | Mailbox alert inboxes | contract only | none | oauth/session | blocked (no authorized connector) |
| `gcc_freehire` | freehire.me aggregator API | `freehire.me` | approximate | none | shipped upstream, enabled |

## 3. Capability matrix and blocked sources

Run `python3 -m career_engine.sources.cli registry` for the full matrix. The
short version:

- **Active (no credentials):** greenhouse, lever, ashby, smartrecruiters,
  jsonld, gcc_freehire.
- **Partial:** workable (live identifier unverified), search_discovery
  (DuckDuckGo best-effort; Google/Bing blocked without API keys).
- **Blocked, reason documented:**
  - `gcc_bayt` - Cloudflare 403 on automated requests (fixture mode only;
    evidence in `.agents/skills/bayt-search`). Re-enable only after a
    live-verified, non-credentialed path exists.
  - `gcc_naukrigulf` - connection timeout/blocked (fixture mode only;
    evidence in `.agents/skills/naukrigulf-search`).
  - `gcc_gulftalent` - no maintained connector found upstream; anti-bot,
    account and ToS risk.
  - `board_indeed` - strong anti-bot and policy risk; web-search fallback only.
  - `linkedin_public` - fragile authenticated scraping must not be a core
    dependency; inbox-alert surface only, official verification required.
  - `inbox_gmail` - repository-native Gmail/gws reports `invalid_grant`;
    requires a connected ChatGPT Gmail connector. Contract implemented; live
    reading blocked without an authorized mailbox.

These decisions follow the canonical Vault connector research
(`projects/job-automation/reports/2026-08-01-connector-research.md`) and the
repository skill evidence under `.agents/skills/`.

## 4. Legality and reliability research (summary)

- **Greenhouse Job Board API** - documented public API, no authentication for
  read; structured and stable; posting dates and full HTML content included.
  Official employer publication.
- **Lever Postings API** - public endpoint used by Lever-hosted boards;
  `mode=json` returns full records including `createdAt` (ms epoch).
- **Ashby Posting API** - public `posting-api/job-board/{company}`; plain and
  HTML descriptions plus `publishedAt`; used by many modern companies.
- **SmartRecruiters** - public company postings API; note it returns empty
  `content` (not 404) for unknown identifiers, so probes report `empty`.
- **Workable** - public feeds exist but the tested endpoints returned 404 for
  every account identifier on 2026-08-05; treated as fallback/direct-source
  class until a live identifier is confirmed.
- **JobPosting JSON-LD / sitemaps** - schema.org standard; the most portable
  official source for employers without public ATS APIs.
- **Search engines** - DuckDuckGo Instant Answer API is a public JSON API
  (no HTML scraping). Google/Bing require API keys. Candidates must be
  verified against the official employer page before ingest.
- **GCC boards** - Bayt/NaukriGulf/GulfTalent/Indeed are blocked for the
  reasons above; freehire remains the enabled aggregator secondary source.

## 5. Usage

```bash
# Registry + capability matrix
python3 -m career_engine.sources.cli registry

# Bounded live probe (GCC employer verified on Greenhouse)
python3 -m career_engine.sources.cli probe --adapter greenhouse --company careem --limit 10

# Bounded probe with output file (runtime output - never commit)
python3 -m career_engine.sources.cli probe --adapter greenhouse --company tamara \
    --limit 5 --output /tmp/opencode/jobs-probe.json

# SmartRecruiters requires --full to fetch descriptions (bounded by --limit)
python3 -m career_engine.sources.cli probe --adapter smartrecruiters --company SmartRecruiters \
    --limit 5 --full --output /tmp/opencode/sr-probe.json

# Employer careers page via JobPosting JSON-LD
python3 -m career_engine.sources.cli probe --adapter jsonld --company https://example.com/careers

# Verify a discovery candidate against the employer's own page
python3 -m career_engine.sources.cli verify --url https://example.com/careers/jobs

# Feed a probe output through the central scanner (scoring only; no-send)
python3 -m career_engine.sources.cli ingest --file /tmp/opencode/jobs-probe.json \
    --scanner-id hermes_scanner --output /tmp/opencode/scan-report.json

# Deterministic offline fixture probes (no network)
python3 -m career_engine.sources.cli probe --adapter greenhouse --company careem --offline
```

The `ingest` command runs the central scanner (`career_engine.scanner.run_scan`),
which uses the same live-vacancy gate, scoring and no-send policy as the
ChatGPT and Hermes scanners. Discovery-only records land as `unverified` and
are therefore scored but blocked from generation until an owner or later scan
verifies the vacancy against an authoritative source.

## 6. Posting-date contract with the tracker

The tracker stores a `posting_date` string. The framework encodes precision
and source inline, e.g.:

```
2026-07-06 (exact, from Greenhouse first_published)
2026-07-08 (day, from Workable published_on)
unknown
```

`found_date` is always recorded as the first-seen date. Dates are never
fabricated; when no date exists the precision is `unknown` and the value is
`None`.

## 7. Runtime outputs stay uncommitted

Probe outputs, scan reports and tracker data are runtime outputs. They must
never be committed. The framework writes only where `--output` says to; the
repository keeps source, registry, fixtures, tests and docs under version
control.
