#!/usr/bin/env python3
"""Compatibility entry point for the centralized Career Engine scanners.

Use `--scanner-id hermes_scanner` for Hermes and `--scanner-id chatgpt_scanner`
for the ChatGPT daily scan. All facts, scoring, generation and validation remain in
the central `career_engine` package.

Hermes production scans also publish one sanitized, derived review projection to
the dedicated ``career-review-runtime`` Git branch. CareerTracker remains the
single operational authority; this projection exists only so scheduled ChatGPT
reviews can verify the completed VPS scan without direct host access. The export
never contains JDs, URLs, email addresses/messages, comments, CV contents,
application documents, credentials or secret values.
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from career_engine.bundle import load_bundle
from career_engine.config import load_config
from career_engine.pipeline import _load_tracker
from career_engine.scanner import SCANNER_ACTORS, add_path_scan_statistics, run_scan, write_report
from career_engine.targeting import reconcile_existing_non_target_jobs

_GMAIL_DISCOVERY_LABEL = "gmail_job_alerts"

REVIEW_RUNTIME_BRANCH = "career-review-runtime"
REVIEW_RUNTIME_REMOTE = "origin"
REVIEW_RUNTIME_ROOT = Path("projects/job-automation/review-runtime")
REVIEW_HISTORY_DAYS = 14
RIYADH = ZoneInfo("Asia/Riyadh")


def _number(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_number(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def _git(*args: str, cwd: Path = REPO_ROOT, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _git_value(*args: str, cwd: Path = REPO_ROOT) -> str:
    return _git(*args, cwd=cwd).stdout.strip()


def _review_date(scanned_at: str) -> str:
    try:
        parsed = datetime.fromisoformat(scanned_at.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(RIYADH).date().isoformat()
    except (TypeError, ValueError):
        return datetime.now(RIYADH).date().isoformat()


def _safe_numeric_summary(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    for key, item in value.items():
        if isinstance(item, bool):
            result[str(key)] = item
        elif isinstance(item, int):
            result[str(key)] = item
        elif isinstance(item, float):
            result[str(key)] = round(item, 2)
    result["error_present"] = bool(value.get("error"))
    return result


def _score_distribution(results: list[dict[str, Any]], threshold: int) -> dict[str, int]:
    bands = {
        "unscored": 0,
        "below_50": 0,
        "selective_50_64": 0,
        "credible_65_69": 0,
        "eligible_70_plus": 0,
    }
    for item in results:
        score = _int_number(item.get("fit_score"))
        if score is None:
            bands["unscored"] += 1
        elif score >= threshold:
            bands["eligible_70_plus"] += 1
        elif score >= 65:
            bands["credible_65_69"] += 1
        elif score >= 50:
            bands["selective_50_64"] += 1
        else:
            bands["below_50"] += 1
    return bands


def _gmail_platform_counts(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {str(k): int(v) for k, v in value.items() if isinstance(v, int)}


def _gmail_review_block(report: dict[str, Any]) -> dict[str, Any]:
    discovery = report.get("gmail_discovery") if isinstance(report.get("gmail_discovery"), dict) else {}
    submission = report.get("gmail_submission_reconciliation") if isinstance(report.get("gmail_submission_reconciliation"), dict) else {}
    reconciled = submission.get("reconciled") if isinstance(submission.get("reconciled"), list) else []
    unmatched = submission.get("unmatched") if isinstance(submission.get("unmatched"), list) else []
    discovery_errors = discovery.get("errors") if isinstance(discovery.get("errors"), list) else []
    submission_error = submission.get("error")
    errors: list[str] = []
    errors.extend(str(v) for v in discovery_errors if v)
    if submission_error:
        errors.append(str(submission_error))
    return {
        "authenticated": bool(discovery.get("authenticated", submission.get("messages_scanned") is not None)),
        "messages_scanned": int(discovery.get("messages_scanned") or submission.get("messages_scanned") or 0),
        "career_relevant_messages": int(discovery.get("career_relevant_messages") or 0),
        "job_alert_messages": int(discovery.get("job_alert_messages") or 0),
        "recruiter_messages": int(discovery.get("recruiter_messages") or 0),
        "vacancy_messages": int(discovery.get("vacancy_messages") or 0),
        "application_instruction_messages": int(discovery.get("application_instruction_messages") or 0),
        "submission_confirmation_messages": int(discovery.get("submission_confirmation_messages") or submission.get("submission_messages_classified") or 0),
        "application_status_messages": int(discovery.get("application_status_messages") or 0),
        "interview_or_assessment_messages": int(discovery.get("interview_or_assessment_messages") or 0),
        "candidate_jobs_extracted": int(discovery.get("candidate_jobs_extracted") or 0),
        "jobs_new_after_deduplication": int(discovery.get("jobs_new_after_deduplication") or 0),
        "jobs_matched_existing": int(discovery.get("jobs_matched_existing") or 0),
        "application_confirmations_matched": len(reconciled),
        "application_confirmations_unmatched": len(unmatched),
        "application_states_changed": int(submission.get("application_states_changed") or sum(1 for r in reconciled if isinstance(r, dict) and r.get("changed"))),
        "ambiguous_messages_manual_review": int(discovery.get("ambiguous_messages_manual_review") or 0) + int(submission.get("ambiguous_manual_review") or 0),
        "errors": errors[:10],
        "platform_counts": _gmail_platform_counts(discovery.get("platform_counts")),
        "send_or_submit": False,
    }


def _url_key_for_merge(url: str) -> str:
    import re as _re

    raw = _re.sub(r"\s+", " ", str(url or "")).strip().split("#", 1)[0].split("?", 1)[0].rstrip("/").lower()
    m = _re.search(r"linkedin\.com/(?:comm/)?jobs/view/(?:[^/?#]*-)?(\d+)", raw, _re.I)
    if m:
        return f"linkedin-job:{m.group(1)}"
    return raw


def _job_projection(
    result: dict[str, Any],
    *,
    rows_by_id: dict[str, dict[str, str]],
    tracker: Any,
) -> dict[str, Any]:
    job_id = str(result.get("job_id") or "")
    row = rows_by_id.get(job_id, {})
    record: dict[str, Any] = {}
    try:
        record = tracker.get_job(job_id)
    except (KeyError, ValueError):
        pass
    processing = record.get("processing_state") or {}
    route = result.get("route") if isinstance(result.get("route"), dict) else {}
    if not route:
        route = processing.get("route") if isinstance(processing.get("route"), dict) else {}
    submission_package = record.get("submission_package") or {}
    return {
        "job_id": job_id,
        "company": str(row.get("company") or ""),
        "role": str(row.get("role") or ""),
        "fit_score": _int_number(result.get("fit_score")),
        "processing_status": str(row.get("processing_status") or result.get("processing_status") or ""),
        "application_status": str(row.get("application_status") or ""),
        "source_path": str(result.get("source_path") or ""),
        "route": str(route.get("route") or ""),
        "selected_resume_variant": str(processing.get("selected_resume_variant") or ""),
        "blocker_count": len(result.get("blockers") or []),
        "is_new": bool(result.get("is_new")),
        "generation_packet_present": bool(result.get("generation_packet")),
        "submission_package_present": bool(submission_package),
    }


def _build_review_bundle(report: dict[str, Any]) -> dict[str, Any]:
    config, paths = load_config(REPO_ROOT)
    bundle = load_bundle(REPO_ROOT)
    tracker = _load_tracker(paths)
    rows = tracker.list_rows()
    rows_by_id = {str(row.get("job_id") or ""): row for row in rows}
    results = [item for item in report.get("results", []) if isinstance(item, dict)]
    threshold = int(config["scoring"]["thresholds"]["high_priority"])
    numeric_scores = [score for item in results if (score := _number(item.get("fit_score"))) is not None]
    projections = [_job_projection(item, rows_by_id=rows_by_id, tracker=tracker) for item in results]
    new_ranked = sorted(
        [item for item in projections if item["is_new"] and item["fit_score"] is not None],
        key=lambda item: (-int(item["fit_score"]), item["company"].lower(), item["role"].lower()),
    )
    eligible_ranked = sorted(
        [
            item
            for item in projections
            if item["fit_score"] is not None
            and int(item["fit_score"]) >= threshold
            and item["blocker_count"] == 0
        ],
        key=lambda item: (-int(item["fit_score"]), item["company"].lower(), item["role"].lower()),
    )
    status_counts = dict(Counter(str(row.get("processing_status") or "") for row in rows))
    application_counts = dict(Counter(str(row.get("application_status") or "") for row in rows))
    statistics_block = report.get("statistics") if isinstance(report.get("statistics"), dict) else {}
    source_coverage: list[dict[str, Any]] = []
    by_path = statistics_block.get("by_path") if isinstance(statistics_block.get("by_path"), dict) else {}
    for name, item in sorted(by_path.items(), key=lambda pair: pair[0].lower()):
        if not isinstance(item, dict):
            continue
        source_coverage.append({
            "path": str(name),
            "attempted": bool(item.get("attempted", True)),
            "status": str(item.get("status") or ""),
            "jobs_discovered": int(item.get("jobs_discovered") or 0),
            "jobs_ingested": int(item.get("jobs_ingested") or 0),
            "new_jobs": int(item.get("new_jobs") or 0),
            "existing_jobs": int(item.get("existing_jobs") or 0),
            "generation_candidates": int(item.get("generation_candidates") or 0),
            "blocked_or_below_threshold": int(item.get("blocked_or_below_threshold") or 0),
            "error_present": bool(item.get("error")),
        })
    source_head = ""
    origin_master = ""
    try:
        source_head = _git_value("rev-parse", "HEAD")
        _git("fetch", REVIEW_RUNTIME_REMOTE, check=False)
        origin_master = _git_value("rev-parse", f"{REVIEW_RUNTIME_REMOTE}/master")
    except (subprocess.SubprocessError, OSError):
        pass
    scanned_at = str(report.get("scanned_at") or datetime.now(timezone.utc).isoformat(timespec="seconds"))
    return {
        "schema_version": 1,
        "projection_type": "career_engine_daily_review",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "operation_date": _review_date(scanned_at),
        "scan": {
            "scanner_id": str(report.get("scanner_id") or ""),
            "scanned_at": scanned_at,
            "bundle_hash": str(report.get("bundle_hash") or bundle.get("bundle_hash") or ""),
            "jobs_discovered": int(statistics_block.get("jobs_discovered") or len(results)),
            "jobs_ingested": int(statistics_block.get("jobs_ingested") or len(results)),
            "new_jobs": int(statistics_block.get("new_jobs") or 0),
            "existing_jobs": int(statistics_block.get("existing_jobs") or 0),
            "generation_candidates": int(statistics_block.get("generation_candidates") or 0),
            "manual_review_needed": int(statistics_block.get("manual_review_needed") or 0),
            "weak_or_blocked": int(statistics_block.get("weak_or_blocked") or 0),
            "paths_total": int(statistics_block.get("paths_total") or len(source_coverage)),
            "paths_scanned": int(statistics_block.get("paths_scanned") or 0),
            "paths_failed": int(statistics_block.get("paths_failed") or 0),
        },
        "scoring": {
            "threshold": threshold,
            "processed_scored": len(numeric_scores),
            "average": round(statistics.fmean(numeric_scores), 2) if numeric_scores else None,
            "median": round(float(statistics.median(numeric_scores)), 2) if numeric_scores else None,
            "distribution": _score_distribution(results, threshold),
        },
        "top_new_roles": new_ranked[:12],
        "eligible_roles": eligible_ranked[:20],
        "source_coverage": source_coverage,
        "tracker": {
            "total_jobs": len(rows),
            "processing_status_counts": status_counts,
            "application_status_counts": application_counts,
        },
        "reconciliation": {
            "gmail_submission": _safe_numeric_summary(report.get("gmail_submission_reconciliation")),
            "owner_irrelevant": _safe_numeric_summary(report.get("owner_irrelevant_reconciliation")),
            "owner_feedback_calibration": _safe_numeric_summary(report.get("owner_feedback_calibration")),
            "target_lane": _safe_numeric_summary(report.get("target_lane_reconciliation")),
        },
        "gmail": _gmail_review_block(report),
        "source": {
            "head": source_head,
            "origin_master": origin_master,
            "head_matches_origin_master": bool(source_head and origin_master and source_head == origin_master),
        },
        "privacy": {
            "contains_secret_values": False,
            "contains_full_job_descriptions": False,
            "contains_urls": False,
            "contains_email_addresses_or_messages": False,
            "contains_comments_or_owner_notes": False,
            "contains_cv_or_cover_letter_content": False,
            "contains_application_documents": False,
        },
        "send_or_submit": False,
    }


def _trend_metric(bundle: dict[str, Any], key: str) -> int | float | None:
    scan = bundle.get("scan") if isinstance(bundle.get("scan"), dict) else {}
    scoring = bundle.get("scoring") if isinstance(bundle.get("scoring"), dict) else {}
    distribution = scoring.get("distribution") if isinstance(scoring.get("distribution"), dict) else {}
    mapping: dict[str, Any] = {
        "new_jobs": scan.get("new_jobs"),
        "processed_scored": scoring.get("processed_scored"),
        "eligible_70_plus": distribution.get("eligible_70_plus"),
        "generation_candidates": scan.get("generation_candidates"),
        "paths_failed": scan.get("paths_failed"),
    }
    value = mapping.get(key)
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _add_trend(bundle: dict[str, Any], worktree: Path) -> None:
    daily_dir = worktree / REVIEW_RUNTIME_ROOT / "daily"
    prior: list[dict[str, Any]] = []
    if daily_dir.is_dir():
        for path in sorted(daily_dir.glob("*.json"), reverse=True)[:6]:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict) and payload.get("operation_date") != bundle.get("operation_date"):
                prior.append(payload)
    history = [bundle, *prior[:6]]
    metric_names = ("new_jobs", "processed_scored", "eligible_70_plus", "generation_candidates", "paths_failed")
    today = {name: _trend_metric(bundle, name) for name in metric_names}
    yesterday = {name: _trend_metric(prior[0], name) if prior else None for name in metric_names}
    rolling: dict[str, Any] = {}
    for name in metric_names:
        values = [value for item in history if (value := _trend_metric(item, name)) is not None]
        rolling[name] = {
            "days_available": len(values),
            "total": round(sum(values), 2) if values else None,
            "average": round(sum(values) / len(values), 2) if values else None,
        }
    bundle["trend"] = {"today": today, "yesterday": yesterday, "rolling_7d": rolling}


def _publish_review_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    try:
        fetch = _git("fetch", REVIEW_RUNTIME_REMOTE, check=False)
        if fetch.returncode != 0:
            return {
                "status": "failed",
                "branch": REVIEW_RUNTIME_BRANCH,
                "error_type": "runtime_branch_fetch_failed",
            }
        with tempfile.TemporaryDirectory(prefix="career-review-publish-") as temp_root:
            worktree = Path(temp_root) / "worktree"
            _git("worktree", "add", "--detach", str(worktree), f"{REVIEW_RUNTIME_REMOTE}/{REVIEW_RUNTIME_BRANCH}")
            try:
                _add_trend(bundle, worktree)
                target_root = worktree / REVIEW_RUNTIME_ROOT
                daily_dir = target_root / "daily"
                daily_dir.mkdir(parents=True, exist_ok=True)
                operation_date = str(bundle["operation_date"])
                daily_path = daily_dir / f"{operation_date}.json"
                latest_path = target_root / "latest.json"
                text = json.dumps(bundle, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
                daily_path.write_text(text, encoding="utf-8")
                latest_path.write_text(text, encoding="utf-8")
                old_daily = sorted(daily_dir.glob("*.json"), reverse=True)[REVIEW_HISTORY_DAYS:]
                for path in old_daily:
                    path.unlink()
                _git("add", "-A", str(REVIEW_RUNTIME_ROOT), cwd=worktree)
                changed = _git("diff", "--cached", "--quiet", cwd=worktree, check=False).returncode != 0
                if not changed:
                    return {"status": "unchanged", "branch": REVIEW_RUNTIME_BRANCH, "operation_date": operation_date}
                _git("commit", "-m", f"ops(career): publish review evidence {operation_date}", cwd=worktree)
                commit = _git_value("rev-parse", "HEAD", cwd=worktree)
                pushed = _git(
                    "push",
                    REVIEW_RUNTIME_REMOTE,
                    f"HEAD:refs/heads/{REVIEW_RUNTIME_BRANCH}",
                    cwd=worktree,
                    check=False,
                )
                if pushed.returncode != 0:
                    return {
                        "status": "failed",
                        "branch": REVIEW_RUNTIME_BRANCH,
                        "operation_date": operation_date,
                        "commit": commit,
                        "error_type": "runtime_branch_push_failed",
                    }
                return {
                    "status": "published",
                    "branch": REVIEW_RUNTIME_BRANCH,
                    "path": str(REVIEW_RUNTIME_ROOT / "latest.json"),
                    "operation_date": operation_date,
                    "commit": commit,
                }
            finally:
                _git("worktree", "remove", "--force", str(worktree), check=False)
                _git("worktree", "prune", check=False)
    except (OSError, subprocess.SubprocessError, KeyError, ValueError, json.JSONDecodeError):
        return {
            "status": "failed",
            "branch": REVIEW_RUNTIME_BRANCH,
            "error_type": "review_bundle_publication_exception",
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Process discovered jobs through the central Career Engine")
    parser.add_argument("--input", required=True, help="JSON file produced by discovery connectors")
    parser.add_argument("--output", default="", help="Optional structured scan report path")
    parser.add_argument("--scanner-id", choices=tuple(SCANNER_ACTORS), default="hermes_scanner")
    parser.add_argument("--consultants", action="store_true", help="Include active consultant bookmarks via official JSON-LD probes")
    parser.add_argument(
        "--no-review-publish",
        action="store_true",
        help="Do not publish the sanitized Git review projection (Hermes scanner publishes by default)",
    )
    parser.add_argument(
        "--no-gmail",
        action="store_true",
        help="Disable Gmail discovery and submission reconciliation integration",
    )
    args = parser.parse_args(argv)
    source_path = Path(args.input)
    consultant_report = None
    if args.consultants:
        from career_engine.sources.consultants import scan_consultants

        consultant_report = scan_consultants(root=REPO_ROOT)
        base = json.loads(source_path.read_text(encoding="utf-8"))
        jobs = base if isinstance(base, list) else base.get("jobs", [])
        scan_paths = [] if isinstance(base, list) else base.get("paths", base.get("scan_paths", []))
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as handle:
            json.dump({"jobs": jobs + consultant_report["jobs"], "paths": scan_paths}, handle, ensure_ascii=False)
            source_path = Path(handle.name)

    gmail_discovery: dict[str, Any] | None = None
    if not args.no_gmail:
        try:
            from career_engine.gmail_discovery import discover_job_mail

            gmail_discovery = discover_job_mail(REPO_ROOT)
        except Exception as exc:  # noqa: BLE001
            gmail_discovery = {
                "schema_version": 1,
                "authenticated": False,
                "query": "",
                "start_date": "",
                "end_date": "",
                "messages_scanned": 0,
                "career_relevant_messages": 0,
                "job_alert_messages": 0,
                "recruiter_messages": 0,
                "vacancy_messages": 0,
                "application_instruction_messages": 0,
                "submission_confirmation_messages": 0,
                "application_status_messages": 0,
                "interview_or_assessment_messages": 0,
                "candidate_jobs_extracted": 0,
                "jobs_new_after_deduplication": 0,
                "jobs_matched_existing": 0,
                "ambiguous_messages_manual_review": 0,
                "platform_counts": {},
                "errors": [f"{type(exc).__name__}: {str(exc)[:400]}"],
                "send_or_submit": False,
                "candidates": [],
            }
        # Merge Gmail candidates into the scan input as a distinct source path
        try:
            candidates = list(gmail_discovery.get("candidates", []) if isinstance(gmail_discovery.get("candidates"), list) else [])
            if candidates:
                base_payload = json.loads(source_path.read_text(encoding="utf-8"))
                if isinstance(base_payload, list):
                    existing_jobs: list[dict[str, Any]] = base_payload
                    existing_paths: list[dict[str, Any]] = []
                    was_list = True
                else:
                    existing_jobs = list(base_payload.get("jobs", []) if isinstance(base_payload.get("jobs"), list) else [])
                    existing_paths = list(base_payload.get("paths", base_payload.get("scan_paths", [])) if isinstance(base_payload.get("paths", base_payload.get("scan_paths", [])), list) else [])
                    was_list = False
                existing_keys = {_url_key_for_merge(str(j.get("source_url") or j.get("application_url") or "")) for j in existing_jobs if j.get("source_url") or j.get("application_url")}
                existing_keys.update(str(j.get("external_job_id") or "") for j in existing_jobs if j.get("external_job_id"))
                new_candidates: list[dict[str, Any]] = []
                matched = 0
                for cand in candidates:
                    key = _url_key_for_merge(str(cand.get("source_url") or "")) or str(cand.get("external_job_id") or "")
                    if key and key in existing_keys:
                        matched += 1
                        continue
                    new_candidates.append(cand)
                    if key:
                        existing_keys.add(key)
                gmail_discovery["jobs_matched_existing"] = matched
                gmail_discovery["jobs_new_after_deduplication"] = len(new_candidates)
                if new_candidates:
                    merged_jobs = existing_jobs + new_candidates
                    # record gmail as a declared path
                    gmail_path_entry = {
                        "path": _GMAIL_DISCOVERY_LABEL,
                        "attempted": True,
                        "status": "observed" if new_candidates else "empty",
                        "jobs_discovered": len(candidates),
                        "error": "; ".join(str(e) for e in gmail_discovery.get("errors", []) if e)[:300],
                    }
                    if was_list:
                        merged_payload: Any = merged_jobs
                    else:
                        merged_payload = {"jobs": merged_jobs, "paths": existing_paths + [gmail_path_entry]}
                        # keep scan_paths alias if present
                        if "scan_paths" in base_payload:
                            merged_payload["scan_paths"] = existing_paths + [gmail_path_entry]
                    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as handle:
                        json.dump(merged_payload, handle, ensure_ascii=False)
                        source_path = Path(handle.name)
                else:
                    # still record the path even with zero new candidates (visible but empty)
                    if not isinstance(base_payload, list):
                        gmail_path_entry = {
                            "path": _GMAIL_DISCOVERY_LABEL,
                            "attempted": True,
                            "status": "empty" if not gmail_discovery.get("errors") else "error",
                            "jobs_discovered": 0,
                            "error": "; ".join(str(e) for e in gmail_discovery.get("errors", []) if e)[:300],
                        }
                        merged_payload = {"jobs": existing_jobs, "paths": existing_paths + [gmail_path_entry]}
                        if "scan_paths" in base_payload:
                            merged_payload["scan_paths"] = existing_paths + [gmail_path_entry]
                        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as handle:
                            json.dump(merged_payload, handle, ensure_ascii=False)
                            source_path = Path(handle.name)
            else:
                # No candidates but record empty path for visibility when authenticated
                try:
                    base_payload = json.loads(source_path.read_text(encoding="utf-8"))
                    if not isinstance(base_payload, list):
                        existing_paths = list(base_payload.get("paths", base_payload.get("scan_paths", [])) if isinstance(base_payload.get("paths", base_payload.get("scan_paths", [])), list) else [])
                        status = "error" if gmail_discovery.get("errors") else ("empty" if gmail_discovery.get("authenticated") else "unavailable")
                        gmail_path_entry = {
                            "path": _GMAIL_DISCOVERY_LABEL,
                            "attempted": True,
                            "status": status,
                            "jobs_discovered": 0,
                            "error": "; ".join(str(e) for e in gmail_discovery.get("errors", []) if e)[:300],
                        }
                        merged_payload = {"jobs": list(base_payload.get("jobs", [])), "paths": existing_paths + [gmail_path_entry]}
                        if "scan_paths" in base_payload:
                            merged_payload["scan_paths"] = existing_paths + [gmail_path_entry]
                        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as handle:
                            json.dump(merged_payload, handle, ensure_ascii=False)
                            source_path = Path(handle.name)
                except Exception:
                    pass
        except Exception:
            pass
    else:
        gmail_discovery = {
            "schema_version": 1,
            "authenticated": False,
            "query": "",
            "start_date": "",
            "end_date": "",
            "messages_scanned": 0,
            "career_relevant_messages": 0,
            "job_alert_messages": 0,
            "recruiter_messages": 0,
            "vacancy_messages": 0,
            "application_instruction_messages": 0,
            "submission_confirmation_messages": 0,
            "application_status_messages": 0,
            "interview_or_assessment_messages": 0,
            "candidate_jobs_extracted": 0,
            "jobs_new_after_deduplication": 0,
            "jobs_matched_existing": 0,
            "ambiguous_messages_manual_review": 0,
            "platform_counts": {},
            "errors": [],
            "send_or_submit": False,
            "candidates": [],
        }

    bundle = load_bundle(REPO_ROOT)
    _, paths = load_config(REPO_ROOT)
    target_lane_reconciliation = reconcile_existing_non_target_jobs(
        _load_tracker(paths),
        bundle.get("taxonomy", {}),
        actor=SCANNER_ACTORS[args.scanner_id],
    )

    # run_scan owns discovery plus higher-authority Gmail/owner reconciliation.
    report = run_scan(source_path, root=REPO_ROOT, scanner_id=args.scanner_id)
    report["target_lane_reconciliation"] = target_lane_reconciliation
    if gmail_discovery is not None:
        sanitized = {k: v for k, v in gmail_discovery.items() if k != "candidates"}
        report["gmail_discovery"] = sanitized

    if consultant_report is not None:
        report["consultant_sources"] = consultant_report["sources"]
        report["consultant_summary"] = consultant_report["summary"]
        add_path_scan_statistics(report, consultant_report["sources"])

    if args.scanner_id == "hermes_scanner" and not args.no_review_publish:
        review_bundle = _build_review_bundle(report)
        report["review_bundle_publication"] = _publish_review_bundle(review_bundle)
    else:
        report["review_bundle_publication"] = {
            "status": "skipped",
            "reason": "not_hermes_scanner" if args.scanner_id != "hermes_scanner" else "explicitly_disabled",
            "branch": REVIEW_RUNTIME_BRANCH,
        }

    print(write_report(report, Path(args.output) if args.output else None), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
