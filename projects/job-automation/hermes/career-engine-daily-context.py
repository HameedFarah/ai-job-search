#!/usr/bin/env python3
"""Thin runtime context for the centralized Career Engine daily scan.

Career facts, rules, scoring and generation policy are loaded from the repository
runtime bundle. This script intentionally contains no independent career prompt.

Runtime safety contract: Hermes runs this script from the dedicated clean
runtime worktree (never the mutable developer checkout). Before any Career
Engine import or bundle load the preflight fail-closes unless:

1. this working directory is a valid git worktree root that is clean and
   synchronized to origin/master (bounded fetch; strictly-behind may
   fast-forward only; dirty/ahead/diverged never reset/clean/stash/rebase);
2. the git-ignored runtime authority pointer ``runtime/runtime-authority.json``
   exists, is schema-valid and binds to a live tracker base that passes the
   canonical state-continuity census (jobs.csv present, jobs/*.json non-empty).
   A missing/wrong/empty binding must never cause a second empty tracker.

When the preflight fails, Hermes may still launch an agent even though this
script exits non-zero, so main() emits an explicit BLOCKED context ordering the
agent to stop: no scanning, no fallback improvisation, no career_engine import.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

RUNTIME_ROOT_POINTER = Path.home() / ".hermes/cron/career-engine-runtime-root.json"


def resolve_repo_root() -> Path:
    """Resolve the Career runtime worktree from scheduler-owned authority.

    Hermes executes the pre-run script from its own process cwd before the
    scheduled job's ``workdir`` is applied to agent tools. Therefore neither
    ``Path.cwd()`` nor ``TERMINAL_CWD`` is authoritative for deployed cron
    scripts. The repository reconciler writes a small machine-owned pointer in
    the default Hermes cron store; that pointer is authoritative. Environment
    and cwd fallbacks exist only for direct/manual tests and diagnostics.
    """
    if RUNTIME_ROOT_POINTER.is_file():
        try:
            payload = json.loads(RUNTIME_ROOT_POINTER.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        if isinstance(payload, dict) and payload.get("schema_version") == 1:
            workdir = str(payload.get("workdir", "")).strip()
            if workdir:
                return Path(workdir).expanduser().resolve()
    terminal_cwd = os.environ.get("TERMINAL_CWD", "").strip()
    return Path(terminal_cwd).expanduser().resolve() if terminal_cwd else Path.cwd().resolve()


REPO_ROOT = resolve_repo_root()
ORIGIN_REF = "origin/master"
SOURCE_TARGETS = Path("/home/hameedo/.hermes/cron/career-engine-source-targets.json")
RUNTIME_AUTHORITY_POINTER = Path("runtime/runtime-authority.json")
BLOCKED_INSTRUCTION = (
    "STOP - DO NOT SCAN. The source/runtime-authority preflight failed for the "
    "dedicated runtime worktree. Do not import career_engine, do not discover, "
    "score or ingest jobs, and do not improvise a scan from any fallback "
    "location such as /tmp or the developer checkout. Reply with exactly one "
    "BLOCKED report containing this error and take no further action. Never "
    "send email, contact a recruiter or submit an application."
)


class PreflightError(RuntimeError):
    """Raised when the runtime worktree or authority binding fails preflight."""


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run one bounded Git command for the runtime source preflight."""
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        timeout=60,
        check=check,
    )


def ensure_canonical_source(root: Path) -> dict[str, object]:
    """Fail closed unless the operational checkout is clean and on origin/master.

    Source executes from the dedicated clean runtime worktree while mutable
    CareerTracker/artifact/runtime state remains bound to the canonical live
    tracker base through ``runtime/runtime-authority.json``. The source worktree
    may fast-forward when clean and strictly behind, but it must never reset,
    clean, stash, rebase, overwrite local work, or initialize a second tracker.
    """
    if not (root / ".git").exists():
        raise PreflightError(f"Career Engine runtime is not a Git checkout: {root}")

    before_status = _git(root, "status", "--porcelain=v1").stdout.strip()
    if before_status:
        raise PreflightError("Career Engine runtime has tracked/untracked source changes; refusing daily scan")

    try:
        _git(root, "fetch", "origin", "master")
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        raise PreflightError(f"unable to fetch {ORIGIN_REF} for the daily scan: {exc}") from exc
    head_before = _git(root, "rev-parse", "HEAD").stdout.strip()
    origin_master = _git(root, "rev-parse", "origin/master").stdout.strip()
    fast_forwarded = False

    if head_before != origin_master:
        ancestor = _git(root, "merge-base", "--is-ancestor", "HEAD", "origin/master", check=False)
        if ancestor.returncode != 0:
            raise PreflightError(
                "Career Engine runtime source is ahead/diverged from origin/master; refusing daily scan"
            )
        _git(root, "merge", "--ff-only", "origin/master")
        fast_forwarded = True

    head_after = _git(root, "rev-parse", "HEAD").stdout.strip()
    origin_after = _git(root, "rev-parse", "origin/master").stdout.strip()
    after_status = _git(root, "status", "--porcelain=v1").stdout.strip()
    if after_status or head_after != origin_after:
        raise PreflightError("Career Engine runtime source did not converge cleanly to origin/master")

    return {
        "status": "canonical",
        "head_before": head_before,
        "head": head_after,
        "origin_master": origin_after,
        "fast_forwarded": fast_forwarded,
        "clean": True,
    }


def read_runtime_authority(root: Path) -> dict[str, object]:
    """Validate the machine-generated runtime authority pointer and its target.

    The pointer is generated by tools/reconcile_career_scheduler.py into the
    ignored ``runtime/`` directory of the dedicated runtime worktree. It binds
    every Career Engine entry point launched here to the canonical live tracker
    base via career_engine.config.load_config — no exported env var required.
    """
    pointer = root / RUNTIME_AUTHORITY_POINTER
    if not pointer.is_file():
        raise PreflightError(
            f"runtime authority pointer missing: {pointer}; run the scheduler reconciler before scanning"
        )
    try:
        payload = json.loads(pointer.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PreflightError(f"invalid runtime authority pointer {pointer}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise PreflightError(f"unsupported runtime authority pointer schema in {pointer}")
    base_text = str(payload.get("tracker_base", "")).strip()
    if not base_text:
        raise PreflightError(f"runtime authority pointer has an empty tracker_base: {pointer}")
    base = Path(base_text).expanduser().resolve()
    if not base.is_dir():
        raise PreflightError(f"runtime authority pointer target does not exist: {base}")
    jobs_csv = base / "data/jobs.csv"
    jobs_dir = base / "data/jobs"
    job_records = len(list(jobs_dir.glob("*.json"))) if jobs_dir.is_dir() else 0
    if not jobs_csv.is_file() or job_records < 1:
        raise PreflightError(
            f"live tracker authority at {base} fails continuity census "
            f"(jobs.csv={jobs_csv.is_file()}, job records={job_records}); refusing to "
            "initialize or copy a second tracker"
        )
    return {
        "status": "canonical",
        "pointer": str(pointer),
        "tracker_base": str(base),
        "jobs_csv": True,
        "job_records": job_records,
    }


def blocked_context(error: str) -> dict[str, object]:
    return {
        "task": "Career Engine daily scan",
        "status": "blocked",
        "instruction": BLOCKED_INSTRUCTION,
        "do_not_scan": True,
        "error": error,
        "repository": str(REPO_ROOT),
        "send_or_submit": False,
    }


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
    # Preflight first: source sync plus runtime authority binding. Career Engine
    # code is imported only after both gates pass, so a failed preflight can
    # never fall back to improvising a scan from an unverified location.
    try:
        source_sync = ensure_canonical_source(REPO_ROOT)
        runtime_authority = read_runtime_authority(REPO_ROOT)
    except (PreflightError, OSError, ValueError) as exc:
        print(json.dumps(blocked_context(f"{type(exc).__name__}: {str(exc)[:400]}"), indent=2))
        return 2

    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from career_engine.bundle import load_bundle

    bundle = load_bundle(REPO_ROOT)
    generation_threshold = int(bundle["config"]["daily_scanner"]["minimum_score_for_generation"])
    source_targets = json.loads(SOURCE_TARGETS.read_text(encoding="utf-8")) if SOURCE_TARGETS.exists() else {}
    gmail_reconciliation = reconcile_applied_mail()
    context = {
        "task": "Run the repository-owned Career Engine daily scanner using the central runtime bundle and the configured discovery-source registry.",
        "repository": str(REPO_ROOT),
        "source_sync": source_sync,
        "runtime_authority": runtime_authority,
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
            "Run the repository Hermes scanner against that file. Do not score or draft independently in the cron prompt. After ingestion, run `./career-engine run --all` so every canonical eligible record is prepared through the central no-send pipeline rather than stopping at the routine daily packet cap; report any per-job errors or deferred records explicitly.",
            "After run/reconcile, explicitly drain every eligible non-submitted pending manual-generation request: read/export each request, ground the structured output only in current canonical Vault career evidence, the full job description and runtime bundle, include claim citations, import with `./career-engine generate import`, then validate, render both required variants as applicable, package the selected route, and create a verified UNSENT Gmail draft only for a verified email route. Re-run and sync until no eligible pending generation remains, or record a genuine evidence/validation blocker for each remaining request. Keep the configured per-scan cap; do not replace the manual adapter with a hidden provider implementation; never fabricate evidence, send/contact/submit, or mark applied.",
            f"For every effective score of {generation_threshold} or higher that remains eligible and non-submitted, verify the central packet exists and render both Modern Executive Sidebar and ATS Linear DOCX/PDF variants. Persist one selected submission variant per job: sidebar by default for email routes and ATS by default for portal routes, honoring any dashboard preview override. Attach exactly one selected CV PDF to an email draft.",
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
