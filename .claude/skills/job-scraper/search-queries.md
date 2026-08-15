# Search Queries for Job Scraper (Saudi Arabia / GCC focus)

<!-- SETUP: Customize these queries based on your skills, target roles, and location -->

## Installed portal CLIs (primary for `/scrape`)

`/scrape` discovers every portal skill under `.agents/skills/*/SKILL.md` and runs its CLI first. Shipped country-agnostic CLIs include `linkedin-search` and `freehire-search`; the Danish demo portals are installed but disabled (`enabled: false`) for this fork's GCC/KSA market. You do **not** need a matching `site:` line below for those CLIs to run.

The `site:` query templates in this file are the **WebSearch fallback** — for portals without a CLI, company career pages, or when a CLI fails.

## Search Sites

Primary:
- **linkedin.com/jobs** - LinkedIn job listings (filter: Saudi Arabia / Jordan / GCC); also covered by `linkedin-search` CLI
- **freehire.me** - country-agnostic aggregator of ~50 ATS platforms; covered by `freehire-search` CLI
- Company career pages for target employers: real-estate developers, engineering/architectural consultancies, major contractors, PMCs, development authorities, and institutional asset owners with meaningful in-house engineering / property / capital-project functions

Institutional asset-owner discovery must explicitly include:
- banks and financial institutions;
- airlines, airports and transport operators;
- hospitals and healthcare groups;
- universities and major education institutions;
- hospitality and hotel groups;
- major retail groups and shopping-centre owners/operators;
- industrial/corporate asset owners with substantial building portfolios;
- government and semi-government institutions.

Do not assume these employers will use specialist titles such as `Capital Projects Manager` or `Corporate Real Estate Manager`. They often advertise ordinary titles such as `Project Manager`, `Senior Project Manager`, `Engineering Project Manager`, `Facilities Project Manager`, `Construction Project Manager`, `Design Manager`, `Development Manager`, `Property Manager`, `Architect`, or `Architectural Engineer`.

## Query Categories

Queries are grouped by priority. Saudi Arabia is the primary market. Jordan is also an explicit target market and may include roles below the owner's current management seniority when the role is otherwise credible and relevant. The entire GCC is in scope: Saudi Arabia, United Arab Emirates, Qatar, Kuwait, Bahrain and Oman.

### Priority 1: Design / Architecture Leadership

These match the strongest and most desired career direction.

```
site:linkedin.com/jobs ("Design Manager" OR "Senior Design Manager" OR "Design Director" OR "Director of Design" OR "Head of Design") "Saudi Arabia"
site:linkedin.com/jobs ("Architecture Manager" OR "Architectural Design Manager" OR "Head of Architecture" OR "Technical Design Manager") "Saudi Arabia"
site:linkedin.com/jobs ("Architectural Project Manager" OR "Architecture Project Manager" OR "Design Project Manager") "Saudi Arabia"
```

### Priority 2: Project / Program / Technical Leadership

These match project delivery, design management, construction oversight and technical leadership.

```
site:linkedin.com/jobs ("Senior Project Manager" OR "Project Director") (design OR construction OR architecture OR development OR PMC) "Saudi Arabia"
site:linkedin.com/jobs ("Program Manager" OR "Programme Manager" OR "Program Director" OR "Programme Director") (construction OR design OR architecture OR development OR infrastructure) "Saudi Arabia"
site:linkedin.com/jobs ("Technical Manager" OR "Technical Director" OR "Design Engineering Manager" OR "Engineering Design Manager") (construction OR architecture OR infrastructure OR building OR "real estate") "Saudi Arabia"
```

### Priority 3: Developers / Construction / Delivery

```
site:linkedin.com/jobs ("Development Manager" OR "Development Director" OR "Design Development Manager") ("real estate" OR property OR development OR construction OR design) "Saudi Arabia"
site:linkedin.com/jobs ("Senior Construction Manager" OR "Construction Director") (building OR construction OR development OR architecture OR design) "Saudi Arabia"
site:linkedin.com/jobs ("Engineering Project Manager" OR "Projects Engineering Manager" OR "Engineering Manager") (building OR facilities OR construction OR property OR "real estate") "Saudi Arabia"
```

### Priority 4: Institutional / Asset-Owner In-House Roles

This category is mandatory. Search large organizations that own, develop, refurbish or operate significant physical assets even when their core business is not construction or real estate.

Use **ordinary role titles plus employer/sector context** rather than rare specialist titles.

```
site:linkedin.com/jobs "Project Manager" (bank OR banking OR airline OR airport OR hospital OR healthcare OR university OR hospitality OR hotel OR retail) "Saudi Arabia" (construction OR design OR facilities OR engineering OR property OR "real estate")
site:linkedin.com/jobs "Senior Project Manager" (bank OR banking OR airline OR airport OR hospital OR healthcare OR university OR hospitality OR hotel OR retail) "Saudi Arabia" (construction OR design OR facilities OR engineering OR property OR "real estate")
site:linkedin.com/jobs ("Facilities Project Manager" OR "Facility Project Manager" OR "Engineering Project Manager" OR "Construction Project Manager") "Saudi Arabia"
site:linkedin.com/jobs ("Design Manager" OR Architect OR "Architectural Engineer") (bank OR airline OR airport OR hospital OR healthcare OR university OR hotel OR retail) "Saudi Arabia"
site:linkedin.com/jobs ("Project Manager" OR "Engineering Project Manager" OR "Facilities Project Manager") (government OR ministry OR authority OR commission) "Saudi Arabia" (construction OR facilities OR engineering OR design)
```

Also search the official career sites of high-value institutional employers directly. Employer-sector membership alone is not enough to qualify a role.

**Qualification rule:** retain a discovered institutional role only when the JD materially involves physical assets, architecture, building design, construction, development, fit-out, facilities capital projects, engineering project delivery, property development, or related owner-side project governance. Exclude software, IT, digital transformation, data, product, cybersecurity, purely financial, and purely operational facilities roles unless the JD contains a credible built-environment/project scope.

### Priority 5: Jordan Broader Market

Jordan is a deliberate target even when the title is below current Saudi/GCC seniority. Include credible architecture, design and project-delivery roles rather than filtering only for director/manager titles.

```
site:linkedin.com/jobs (Architect OR "Senior Architect" OR "Project Architect" OR "Lead Architect" OR "Architectural Engineer") Jordan -software -cloud -solutions
site:linkedin.com/jobs ("Design Manager" OR "Architectural Design Manager" OR "Design Coordinator") Jordan
site:linkedin.com/jobs ("Project Manager" OR "Architectural Project Manager" OR "Design Project Manager") (architecture OR construction OR design OR building OR "real estate") Jordan
site:linkedin.com/jobs ("Facilities Manager" OR "Facility Manager" OR "Facilities Project Manager" OR "Real Estate Project Manager") Jordan
```

When using LinkedIn or another source with a structured location filter, use the **country location entity for Jordan**, not a free-text location that could resolve to Jordan, Pennsylvania.

### Priority 6: GCC-Wide Secondary Coverage

Run the senior built-environment search families across **all GCC countries**, not only UAE and Qatar. Saudi Arabia remains primary, but relevant roles in UAE, Qatar, Kuwait, Bahrain and Oman must be retained for scoring.

```
site:linkedin.com/jobs ("Design Manager" OR "Senior Design Manager" OR "Design Director" OR "Project Director" OR "Senior Project Manager" OR "Program Director" OR "Technical Director" OR "Development Manager" OR "Construction Director" OR "Engineering Project Manager") (architecture OR construction OR design OR "real estate" OR development OR infrastructure OR facilities) "United Arab Emirates"
site:linkedin.com/jobs ("Design Manager" OR "Senior Design Manager" OR "Design Director" OR "Project Director" OR "Senior Project Manager" OR "Program Director" OR "Technical Director" OR "Development Manager" OR "Construction Director" OR "Engineering Project Manager") (architecture OR construction OR design OR "real estate" OR development OR infrastructure OR facilities) Qatar
site:linkedin.com/jobs ("Design Manager" OR "Senior Design Manager" OR "Project Director" OR "Senior Project Manager" OR "Technical Director" OR "Development Manager" OR "Construction Director" OR "Engineering Project Manager") (architecture OR construction OR design OR "real estate" OR development OR infrastructure OR facilities) Kuwait
site:linkedin.com/jobs ("Design Manager" OR "Senior Design Manager" OR "Project Director" OR "Senior Project Manager" OR "Technical Director" OR "Development Manager" OR "Construction Director" OR "Engineering Project Manager") (architecture OR construction OR design OR "real estate" OR development OR infrastructure OR facilities) Bahrain
site:linkedin.com/jobs ("Design Manager" OR "Senior Design Manager" OR "Project Director" OR "Senior Project Manager" OR "Technical Director" OR "Development Manager" OR "Construction Director" OR "Engineering Project Manager") (architecture OR construction OR design OR "real estate" OR development OR infrastructure OR facilities) Oman
```

Institutional / asset-owner discovery also applies GCC-wide. Do not limit bank, aviation, healthcare, education, hospitality, retail, government/semi-government, industrial/corporate property or facilities-capital-project searches to Saudi Arabia when the source supports other GCC countries.

## Location Filter

When evaluating results, verify the job location is within the target market. Define acceptable areas:
- Saudi Arabia country-wide, with Riyadh and major development hubs included;
- Jordan country-wide, including Amman, with broader acceptable title/seniority coverage;
- United Arab Emirates country-wide, especially Dubai / Abu Dhabi;
- Qatar country-wide, especially Doha;
- Kuwait country-wide;
- Bahrain country-wide;
- Oman country-wide;
- Remote roles only when legally/geographically feasible.

## Date Filter

Only include jobs posted within the last 14 days, or with an application deadline that has not yet passed. If a posting date cannot be determined, include it but flag as "date unknown".

## Screening Rules

- Do not over-filter discovery by title. Search broadly enough to capture employer-specific naming conventions, then score the full JD in Career Engine.
- Do not promote an institutional employer role merely because the employer is prestigious or asset-rich. The actual duties must match the owner's evidence-supported built-environment/project capability.
- For Saudi Arabia / GCC, prioritize senior leadership and management roles but retain unusually strong adjacent roles for scoring.
- For Jordan, permit lower-title roles where location/value makes them credible options.
- Continue to reject software/IT/data/product/cybersecurity roles even when generic words such as `architect`, `engineering manager`, `project manager`, or `delivery manager` appear in the title.

## Adapting Queries

If the user specifies a focus area, select queries from the matching category and also generate 2-3 custom queries for that focus. For example:
- `/scrape [focus_area]` -> relevant category queries + custom focus-specific queries
