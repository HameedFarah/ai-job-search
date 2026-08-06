# Career Engine Job-Source Expansion

**Date:** 2026-08-06  
**Scope:** discovery and normalization only; no send, recruiter contact, or application submission.

## Architecture

```text
official employer/ATS sources
+ Brave Search API
+ Jooble API
+ Careerjet API (manual user-triggered only)
+ authenticated job-alert emails
-> discovery-only records
-> official employer or ATS verification
-> central Career Engine scoring and owner review
```

Brave, Jooble, Careerjet, Freehire and alert-derived records remain non-official. They use a blank application route and `live_status=unverified` until promotion through an official employer or ATS source. Missing provider credentials produce an `unavailable` source result and do not fail the wider scan.

## Runtime variables

| Provider | Variable | Runtime state without it |
|---|---|---|
| Brave Search | `BRAVE_SEARCH_API_KEY` | unavailable |
| Jooble | `JOOBLE_API_KEY` | unavailable |
| Careerjet | `CAREERJET_API_KEY` | unavailable |

`CAREERJET_AFFID` is accepted only as a legacy secret-name alias. New deployments must use `CAREERJET_API_KEY`. No value belongs in Git or a repository `.env` file.

Careerjet also requires a directly user-triggered request plus the actual public user IP and user-agent. Scheduled Careerjet scans are denied. The adapter does not persist the IP or user-agent in job records and redacts them from provider error messages.

## Job-alert normalization

Supported authenticated email-alert sources: LinkedIn, Bayt, NaukriGulf, GulfTalent, Indeed, Foundit and Gotogulf. Mailbox access remains connector-owned. The normalizer accepts structured listings from an authenticated connector; it does not scrape boards or read cookies/session data.

## Residential fallback

The routing policy is fail closed:

- normal API and ATS traffic uses the VPS route;
- restricted boards are always denied residential fallback;
- a public employer domain can use residential routing only when it appears in a separate explicit allowlist and the loopback proxy is healthy;
- an allowlisted request is denied when the proxy is unavailable;
- the 40-employer discovery registry does **not** grant residential access by itself.

Restricted domains are defined in `career_engine/sources/routing.py`. No CAPTCHA, authentication wall, rate limit or access control may be bypassed.

## Commands

```bash
python3 -m career_engine.sources.cli registry
python3 -m career_engine.sources.cli probe --adapter brave_search --company "Design Manager Saudi Arabia"
python3 -m career_engine.sources.cli probe --adapter jooble --company "Project Director" --location "Riyadh"
python3 -m career_engine.sources.cli probe --adapter careerjet --company "Design Manager" --location "Saudi Arabia" --user-triggered --user-ip "$ACTUAL_USER_IP" --user-agent "$ACTUAL_USER_AGENT"
python3 -m career_engine.sources.cli route-check --url https://careers.example.com/job/1 --allowlist-file path/to/allowlist.json --proxy-available
```

Provider probes are bounded by timeout, response-size and result limits. Live probes are operational checks and must not be run until credentials are mapped through canonical Infisical paths.
