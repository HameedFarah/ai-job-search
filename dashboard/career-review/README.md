# Career Application Review

Private permanent review surface for Career Engine applications.

- Primary live URL: `https://career.farahdigital.com/`
- Permanent fallback URL: `https://gilded-timber-xfj7.here.now/`
- Access: restricted to `hameedo@gmail.com`
- Site Data: stage, comments (append-only), AI requests and design preference persist across deployments
- Source: `/home/hameedo/projects/ai-job-search/dashboard/career-review`
- Application artifacts: `/home/hameedo/projects/ai-job-search/projects/job-automation/artifacts`

## Features

- **Mobile-first Kanban board**: the board owns the viewport. The header is intentionally minimal, last-scan state sits as a small top-right status, five compact status chips jump to the main active board positions, and search/filter/sort/display controls stay collapsed behind small icon buttons until tapped. The board also includes a terminal `Closed / inactive` column for non-actionable roles.
- **Resume-first job detail overlay**: clicking a card opens a full-height detail surface with Close and the primary next action permanently accessible at the top, compact metadata, an independently scrollable inline selected-resume viewer, a cover-letter/email text box, and collapsed secondary sections for fit, documents, comments, AI requests, stage and resume-variant selection.
- **Persistent Site Data** (here.now Site Data, `site/.herenow/data.json`): `workflow`, `comments` (append-only, with type + resolved state), `history` (append-only events), `ai_requests` (pending queue), `preferences` (dashboard theme). Browser writes use public access + `publicMutation: open` as allowed by official Site Data rules; secrets never live in browser code.
- **Application evidence**: opening an official portal records `portal_opened` with URL, time, interface source and selected-document evidence. It is explicitly labelled as portal-open evidence only. `application_submitted` or `email_sent_owner_confirmed` is recorded only after a separate explicit owner confirmation; an optional confirmation/reference note is retained. Gmail confirmation and recruiter-reply matches can later append separate evidence events rather than rewriting owner history. ChatGPT/agents read/write records through the owner API (`/api/v1/publishes/{slug}/data/...`).
- **Tags**: recency (Fresh ≤2d, Recent ≤7d, Aging ≤30d, Old >30d, Unknown) and score (High ≥85, Good 70–84, Marginal 55–69, Low <55) with text labels — never color-only.
- **Five dashboard designs** (Executive Navy default, Compact Slate, Warm Paper, High Contrast, Minimal Grid) persisted in Site Data.
- **Gmail**: opens a prefilled compose window in `hameedo@gmail.com` **only in a new tab**; if the popup is blocked a copyable link is shown and the dashboard tab is never replaced. No mailto handlers, no auto-send.
- The page never sends email or submits an application.

## Update

```bash
node scripts/build_site.js
node scripts/publish_here_now.js
node scripts/connect_custom_domain.js
node scripts/test_site_data.js
```

`build_site.js` reads the validated package manifest and the full canonical tracker under `projects/job-automation/data/jobs/`. It preserves the prepared package keys, imports every tracker role, copies any generated role-specific documents, combines manual reviewed-role decisions, and deduplicates by canonical job ID and normalized source/application URL. `publish_here_now.js` reuses `.deploy.json`, updates the same permanent slug, and reapplies restricted access without exposing credentials. `test_site_data.js` runs an owner-API integration test (create/patch/read/delete) against every Site Data collection and leaves no records. `smoke_submission_tracking.js` mocks Site Data locally and proves portal-open and submission-confirmation events remain separate.

Posting dates are shown as "unknown" when the scan data does not contain them (the engine never invents dates); `found_at` uses the verified `live_verified_at` value when present. ATS resume variants are displayed when generated; otherwise the detail view states that none exist.
