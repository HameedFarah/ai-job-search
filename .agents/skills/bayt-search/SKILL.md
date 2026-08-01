---
name: bayt-search
version: 1.0.0
description: >
  Search Bayt.com for jobs in the GCC / Jordan / MENA region (Dubai, Riyadh,
  Amman, Doha, Kuwait, Bahrain, Oman). Trigger phrases: bayt jobs, jobs in
  Jordan, GCC jobs, MENA jobs, Middle East jobs, Dubai jobs, Riyadh jobs.
context: fork
enabled: false  # DISABLED — fixture mode only, Cloudflare-blocked, not production-ready
allowed-tools: Bash(bun run .agents/skills/bayt-search/cli/src/cli.ts *)
---

# Bayt Search Skill (GCC/Jordan/MENA)

> **STATUS: DISABLED — NOT PRODUCTION-READY.**
> Live scraping of Bayt.com is blocked by Cloudflare bot protection (403 on all
> automated requests). This skill ships only for reference: the CLI follows the
> portal-skill contract (`search`/`detail`, `--format json`) but returns fixture
> data and has **no automated tests** that pass against the live portal. It is
> disabled (`enabled: false`) so `/scrape` skips it. Do not present results from
> this skill as live Bayt listings.

## Commands (contract-compatible, fixture mode)

### Search
```bash
bun run .agents/skills/bayt-search/cli/src/cli.ts search --query "architect" --location "Riyadh" --limit 5
```

### Detail
```bash
bun run .agents/skills/bayt-search/cli/src/cli.ts detail <job_id>
```

## Output Format
JSON matching the normalized job schema (see `ingestion/schema.md` / the shared
tracker in `tools/tracker.py`).

## Notes
- Bayt presents a Cloudflare challenge on all automated requests
- Fixture mode only until a working API path exists (SerpApi Google Jobs or an
  Apify actor are candidate alternatives)
- Re-enable only after live-verified tests pass; keep `enabled: false` otherwise
