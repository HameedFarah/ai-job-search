from __future__ import annotations

import json
from pathlib import Path

from career_engine.pipeline import prepare
from career_engine.review import record_review_diff, validate_review_diff
from tests.test_career_engine_v1 import engine_root, job_payload  # noqa: F401


def review_payload(job_id: str) -> dict:
    return {
        "schema_version": 1,
        "review_id": "2026-08-06-hermes-review-001",
        "reviewed_at": "2026-08-06T10:15:00+03:00",
        "hermes_run_id": "hermes-2026-08-06-0900",
        "hermes_report": "projects/job-automation/runtime/hermes-scan.json",
        "reviewer": "chatgpt",
        "verdict": "corrected",
        "job_diffs": [
            {
                "job_id": job_id,
                "verdict": "corrected",
                "differences": [
                    {
                        "area": "selected_cv",
                        "before": {"variant": "ats-linear"},
                        "after": {"variant": "modern-executive-sidebar"},
                        "reason": "Email routes default to the sidebar CV unless the owner overrides the selection.",
                        "evidence_refs": ["projects/job-automation/config/career-engine.v1.json"],
                        "reusable_rule": "Use sidebar for email and ATS for portals unless a saved owner override exists."
                    }
                ]
            }
        ],
        "improvement_rules": [
            "Read the latest review diff before every Hermes daily scan and apply accepted reusable rules."
        ],
        "notes": [],
        "send_or_submit": False
    }


def test_review_diff_validation_and_recording(job_payload: dict[str, str], engine_root: Path) -> None:
    state = prepare(job_payload, root=engine_root, actor="hermes")
    payload = review_payload(state["job_id"])
    assert validate_review_diff(payload) == []

    result = record_review_diff(payload, root=engine_root)

    assert result["valid"] is True
    saved = Path(result["saved_to"])
    latest = Path(result["latest"])
    assert saved.is_file()
    assert latest.is_file()
    assert json.loads(saved.read_text(encoding="utf-8"))["review_id"] == payload["review_id"]
    events = (engine_root / "projects/job-automation/logs/events.jsonl").read_text(encoding="utf-8")
    assert '"action": "reviewed"' in events
    assert payload["review_id"] in events


def test_review_diff_rejects_send_permission() -> None:
    payload = review_payload("12345678")
    payload["send_or_submit"] = True
    errors = validate_review_diff(payload)
    assert "send_or_submit must be false" in errors
