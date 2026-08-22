#!/usr/bin/env python3
"""Thin runtime context for the centralized Career Engine daily scan.

Career facts, rules, scoring and generation policy are loaded from the repository
runtime bundle. This script intentionally contains no independent career prompt.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path("/home/hameedo/projects/ai-job-search")
SOURCE_TARGETS = Path("/home/hameedo/.hermes/cron/career-engine-source-targets.json")
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from career_engine.bundle import load_bundle


def reconcile_applied_mail() -> dict[str, object]:
    """Run the repository-owned read-only Gmail reconciler before agent reasoning.

    A Gmail outage must never block vacancy discovery. The helper owns matching and
    tracker evidence; this pre-run only returns bounded counts/status for the cron
    context so Hermes does not implement a second matcher.
    """
    command = [
        sys.executable,
        str(REPO_ROOT / "tools/reconcile_gmail_applications.py"),
        "--repo", str(REPO_ROOT),
        "--days", "45",
        "--limit", "200",
        "--backend", "auto",
    ]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=150, check=False)
    except Exception as exc:
        return {"status": "failed", "error": f"{type(exc).__name__}: {str(exc)[:300]}"}
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "helper exited non-zero").strip()
        return {"status": "failed", "error": detail[:500]}
    try:
        report = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {"status": "failed", "error": "helper returned invalid JSON"}
    return {
        "status": "ok" if report.get("mail_backend") not in {"failed"} else "blocked",
        "mail_backend": report.get("mail_backend", "unknown"),
        "applied_jobs": report.get("applied_jobs", 0),
        "matches": (len(report.get("matches", []) or []) if isinstance(report.get("matches"), list) else int(report.get("matches") or 0)),
        "ambiguous": (len(report.get("ambiguous", []) or []) if isinstance(report.get("ambiguous"), list) else int(report.get("ambiguous") or 0)),
        "unmatched_candidates": (len(report.get("unmatched_candidates", []) or []) if isinstance(report.get("unmatched_candidates"), list) else int(report.get("unmatched_candidates") or 0)),
        "already_recorded": (len(report.get("already_recorded", []) or []) if isinstance(report.get("already_recorded"), list) else int(report.get("already_recorded") or 0)),
        "backend_failures": report.get("backend_failures", []),
        "runtime_report": str(REPO_ROOT / "projects/job-automation/runtime/gmail-reconciliation.json"),
    }


def main() -> int:
    bundle = load_bundle(REPO_ROOT)
    generation_threshold = int(bundle["config"]["daily_scanner"]["minimum_score_for_generation"])
    source_targets = json.loads(SOURCE_TARGETS.read_text(encoding="utf-8")) if SOURCE_TARGETS.exists() else {}
    gmail_reconciliation = reconcile_applied_mail()
    context = {
        "task": "Run the repository-owned Career Engine daily scanner using the central runtime bundle and the configured discovery-source registry.",
        "repository": str(REPO_ROOT),
        "bundle_hash": bundle["bundle_hash"],
        "career_engine_skill": str(REPO_ROOT / "skills/career-engine/SKILL.md"),
        "scanner_entry_point": str(REPO_ROOT / "projects/job-automation/hermes_scanner.py"),
        "source_cli": "python3 -m career_engine.sources.cli",
        "source_targets_file": str(SOURCE_TARGETS),
        "source_targets": source_targets,
        "applied_job_gmail_reconciliation": gmail_reconciliation,
        "workflow": [
            "Before discovery, read projects/job-automation/runtime/review-diffs/latest.json when it exists. Apply accepted reusable rules from the latest ChatGPT review and report which rules were adopted; never treat a review diff as permission to send or submit.",
            "Inspect hameedo@gmail.com through the configured Himalaya account for discovery mail only: specific vacancies, exact application instructions, recruiter outreach and forwarded job messages since the saved career-email cursor. Do not change flags, labels or folders; creating or updating an unsent application draft is the only permitted mailbox write.",
            "Applied-job Gmail reconciliation has already run once in the pre-run and is supplied in `applied_job_gmail_reconciliation`. Use that result and its runtime report; do not run the helper again and do not duplicate its matching logic in Hermes. Gmail confirmation/reply reads are strictly read-only: never change flags, labels, folders, read state, drafts or messages through this reconciliation path.",
            "Use the attached LinkedIn-public, Freehire, research and email skills for broad GCC discovery without authenticated LinkedIn scraping.",
            "Run the repository-owned consultant source scan on every daily run with `python3 -m career_engine.sources.cli consultants-scan`. Ingest only its successful official-job results into the same bounded jobs input, then pass them through the central Career Engine scanner with its consultant-source support enabled (`daily_scanner --consultants`); do not duplicate consultant-source logic in Hermes.",
            "Report consultant-source counts separately (active/configured, scanned, successful jobs, skipped manual/unresolved, duplicates, blocked and failed). Treat DNS, network, timeout, authentication, anti-bot and parser failures as failures/blocked-source evidence—not as zero vacancies—and preserve the exact reason for each affected source.",
            "Inspect every configured discovery board, employer career page and public ATS target. Run supported ATS and JSON-LD probes through python3 -m career_engine.sources.cli probe, respecting each target limit and writing runtime-only reports.",
            "Record inaccessible, authenticated, anti-bot or unsupported sources as blocked with the exact reason; do not silently omit them or bypass access controls.",
            "Merge and deduplicate all discovered jobs by source ID, normalized URL and JD hash; preserve full JD, provenance and posting-date precision.",
            "Keep verification status and source quality as confidence metadata. Verification is useful but is not required for scoring or application preparation. Explicitly closed roles remain blocked.",
            "Save every complete job record and full job description to one bounded JSON input file with a jobs array.",
            "Run the repository Hermes scanner against that file. Do not score or draft independently in the cron prompt.",
            f"For every effective score of {generation_threshold} or higher, generate through the central packet and render both Modern Executive Sidebar and ATS Linear DOCX/PDF variants. Persist one selected submission variant per job: sidebar by default for email routes and ATS by default for portal routes, honoring any dashboard preview override. Attach exactly one selected CV PDF to an email draft.",
            "For an email route with a real recipient, create or update one unsent draft in hameedo@gmail.com. Fill To and Subject from the vacancy instructions when specified; otherwise use the verified recipient and subject 'Abdelhamid Farah - <Post Name>'. For portal routes, prepare the dashboard package without creating an email unless a genuine email route exists.",
            "Use at most one free-prose generation pass per selected role unless deterministic validation fails or evidence is materially ambiguous.",
            "Carry the repository Gmail-reconciliation helper results into the daily report and tracker summary separately from new-job ingestion; do not perform a second independent confirmation/reply matcher.",
            "Synchronize the full canonical tracker, selected template, generated variants, draft state and owner-review status into the Career Engine dashboard.",
            "Return source-by-source counts, blocked sources, database changes, structured scan results, both resume variants, selected submission variant, draft changes, dashboard synchronization and owner-review actions.",
            "Never send email, contact a recruiter or submit an application."
        ],
        "inference_routing_authority": "github:HameedFarah/obsidian@master:projects/agent-ops/model-routing.md",
        "send_or_submit": False
    }
    print(json.dumps(context, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
