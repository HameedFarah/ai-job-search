# Search Queries for Job Scraper (Saudi Arabia / GCC focus)

<!-- SETUP: Customize these queries based on your skills, target roles, and location -->

## Installed portal CLIs (primary for `/scrape`)

`/scrape` discovers every portal skill under `.agents/skills/*/SKILL.md` and runs its CLI first. Shipped country-agnostic CLIs include `linkedin-search` and `freehire-search`; the Danish demo portals are installed but disabled (`enabled: false`) for this fork's GCC/KSA market. You do **not** need a matching `site:` line below for those CLIs to run.

The `site:` query templates in this file are the **WebSearch fallback** — for portals without a CLI, company career pages, or when a CLI fails.

## Search Sites

Primary:
- **linkedin.com/jobs** - LinkedIn job listings (filter: Saudi Arabia / Riyadh / GCC); also covered by `linkedin-search` CLI
- **freehire.me** - country-agnostic aggregator of ~50 ATS platforms; covered by `freehire-search` CLI
- Company career pages (Google `site:` filters) for target employers: real-estate developers, engineering/architectural consultancies, major contractors, PMCs, development authorities

## Query Categories

Queries are grouped by priority. All queries target the Saudi/GCC job market (Riyadh primary; Dubai/Doha as secondary GCC hubs).

### Priority 1: [YOUR_PRIMARY_ROLE_TYPE]

These match your strongest and most desired career direction.

```
site:linkedin.com/jobs "[YOUR_PRIMARY_JOB_TITLE]" "Riyadh" "Saudi Arabia"
site:linkedin.com/jobs "[YOUR_KEY_SKILL]" "Riyadh" "Saudi Arabia"
site:linkedin.com/jobs "[YOUR_PRIMARY_JOB_TITLE]" "Dubai" OR "Doha"
```

### Priority 2: [YOUR_DOMAIN_EXPERTISE]

These match your domain expertise (architecture, design management, project delivery, construction oversight, district management, client/BD).

```
site:linkedin.com/jobs [YOUR_DOMAIN_KEYWORD_1] "Riyadh" "Saudi Arabia"
site:linkedin.com/jobs [YOUR_DOMAIN_KEYWORD_2] "Saudi Arabia"
site:linkedin.com/jobs "design manager" "Riyadh" OR "NEOM" OR "Red Sea"
```

### Priority 3: [YOUR_ADJACENT_ROLE_TYPE]

Adjacent roles you could pivot into.

```
site:linkedin.com/jobs "[YOUR_ADJACENT_TITLE_1]" "Riyadh" "Saudi Arabia"
site:linkedin.com/jobs "[YOUR_ADJACENT_TITLE_2]" "Riyadh" "Saudi Arabia"
site:linkedin.com/jobs "[YOUR_ADJACENT_TITLE_1]" "Dubai" OR "Abu Dhabi"
```

### Priority 4: Broader Technical / Consulting

Wider net for general technical roles.

```
site:linkedin.com/jobs "[YOUR_KEY_SKILL]" "Riyadh" "Saudi Arabia"
site:linkedin.com/jobs "technical consultant" "Riyadh" "Saudi Arabia"
site:linkedin.com/jobs "project director" "Riyadh" OR "NEOM" OR "Qiddiya"
```

## Location Filter

When evaluating results, verify the job location is within the target GCC market. Define acceptable areas:
- Riyadh and surrounding areas
- Remote (within KSA time zone or fully remote)
- Other KSA cities (if relocation is feasible)
- Dubai / Abu Dhabi / Doha (secondary, if relocation is feasible)

## Date Filter

Only include jobs posted within the last 14 days, or with an application deadline that has not yet passed. If a posting date cannot be determined, include it but flag as "date unknown".

## Adapting Queries

If the user specifies a focus area, select queries from the matching category and also generate 2-3 custom queries for that focus. For example:
- "/scrape [focus_area]" -> relevant category queries + custom focus-specific queries
