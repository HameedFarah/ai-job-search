---
name: naukrigulf-search
version: 1.0.0
description: >
  Search NaukriGulf.com for jobs in the GCC region (UAE, Saudi Arabia, Qatar,
  Oman, Bahrain, Kuwait). Trigger phrases: naukrigulf jobs, GCC jobs, jobs in
  Dubai, jobs in Riyadh, jobs in Doha.
context: fork
enabled: false  # DISABLED — fixture mode only, connection-blocked, not production-ready
allowed-tools: Bash(bun run .agents/skills/naukrigulf-search/cli/src/cli.ts *)
---

# NaukriGulf Search Skill (GCC)

> **STATUS: DISABLED — NOT PRODUCTION-READY.**
> Live scraping of NaukriGulf.com returns no content (connection timeout or
> blocked). This skill ships only for reference: the CLI follows the portal-skill
> contract (`search`/`detail`, `--format json`) but returns fixture data and has
> **no automated tests** that pass against the live portal. It is disabled
> (`enabled: false`) so `/scrape` skips it. Do not present results from this
> skill as live NaukriGulf listings.

## Commands (contract-compatible, fixture mode)

### Search
```bash
bun run .agents/skills/naukrigulf-search/cli/src/cli.ts search --query "architect" --location "Saudi Arabia" --limit 5
```

### Detail
```bash
bun run .agents/skills/naukrigulf-search/cli/src/cli.ts detail <job_id>
```

## Output Format
JSON matching the normalized job schema (see `ingestion/schema.md` / the shared
tracker in `tools/tracker.py`).

## Notes
- NaukriGulf returns no content (connection timeout or blocked)
- Fixture mode only until a working API path exists (an Apify NaukriGulf actor or
  a JSearch API integration are candidate alternatives)
- Re-enable only after live-verified tests pass; keep `enabled: false` otherwise
