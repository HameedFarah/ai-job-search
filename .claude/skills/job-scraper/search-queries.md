# Search Queries for Job Scraper (Saudi Arabia Focus)

<!-- Danish portals disabled — see job-scraper SKILL.md for portal exclusion list -->
<!-- Search is scoped to LinkedIn (global) for Saudi Arabia / Riyadh market -->

## Search Sites

Primary:
- **linkedin.com/jobs** - LinkedIn job listings (global, filter to Saudi Arabia)

## Query Categories

Queries are grouped by priority. All queries target the Saudi job market.

### Priority 1: [YOUR_PRIMARY_ROLE_TYPE]

These match your strongest and most desired career direction.

```
site:linkedin.com/jobs "[YOUR_PRIMARY_JOB_TITLE]" "Riyadh" "Saudi Arabia"
site:linkedin.com/jobs "[YOUR_KEY_SKILL]" "Riyadh" "Saudi Arabia"
```

### Priority 2: [YOUR_DOMAIN_EXPERTISE]

These match your domain expertise.

```
site:linkedin.com/jobs [YOUR_DOMAIN_KEYWORD_1] "Riyadh" "Saudi Arabia"
site:linkedin.com/jobs [YOUR_DOMAIN_KEYWORD_2] "Saudi Arabia"
```

### Priority 3: [YOUR_ADJACENT_ROLE_TYPE]

Adjacent roles you could pivot into.

```
site:linkedin.com/jobs "[YOUR_ADJACENT_TITLE_1]" "Riyadh" "Saudi Arabia"
site:linkedin.com/jobs "[YOUR_ADJACENT_TITLE_2]" "Riyadh" "Saudi Arabia"
```

### Priority 4: Broader Technical / Consulting

Wider net for general technical roles.

```
site:linkedin.com/jobs "[YOUR_KEY_SKILL]" "Riyadh" "Saudi Arabia"
site:linkedin.com/jobs "technical consultant" "Riyadh" "Saudi Arabia"
```

## Location Filter

When evaluating results, verify the job location is within reasonable commute distance from Riyadh. Define acceptable areas:
- Riyadh and surrounding areas
- Remote (within KSA time zone or fully remote)
- Other KSA cities (if relocation is feasible)

## Date Filter

Only include jobs posted within the last 14 days, or with an application deadline that has not yet passed. If a posting date cannot be determined, include it but flag as "date unknown".

## Adapting Queries

If the user specifies a focus area, select queries from the matching category and also generate 2-3 custom queries for that focus. For example:
- "/scrape [focus_area]" -> relevant category queries + custom focus-specific queries
