---
name: paid-jobs-api-search
version: 1.0.0
description: >
  Abstraction layer for paid job APIs (SerpApi, JSearch, Apify). Trigger phrases:
  paid jobs api, serpapi jobs, jsearch jobs, apify jobs, api jobs.
context: fork
enabled: false  # DISABLED — fixture mode only, no API credentials configured
allowed-tools: Bash(bun run .agents/skills/paid-jobs-api-search/cli/src/cli.ts *)
---

# Paid Jobs API Discovery Layer

> **STATUS: DISABLED — NOT PRODUCTION-READY.**
> No API credentials are configured, so this layer runs in fixture mode only and
> has **no automated tests** that pass against a live provider. It is disabled
> (`enabled: false`) so `/scrape` skips it. Re-enable only after a provider is
> configured and live-verified tests pass.

A provider-agnostic interface for paid job API providers. When a provider is
configured, `/scrape` would call this skill's CLI the same way as any other
portal skill; results map to the shared tracker schema.

## Providers (when configured)

| Provider | Coverage | Notes |
|----------|----------|-------|
| SerpApi Google Jobs | Global | Requires SERPAPI_API_KEY |
| JSearch / RapidAPI | Global | Requires JSEARCH_API_KEY |
| Apify actors (Bayt, NaukriGulf) | GCC/MENA | Requires APIFY_TOKEN |

## Output Format
JSON matching the normalized job schema (see `ingestion/schema.md` / the shared
tracker in `tools/tracker.py`).
