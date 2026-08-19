from __future__ import annotations

from pathlib import Path

from career_engine import post_scan


ROOT = Path(__file__).parents[1]


def test_post_scan_reconciliation_runs_in_authority_order(monkeypatch, tmp_path: Path) -> None:
    calls: list[str] = []

    def gmail(root):
        calls.append("gmail")
        return {"reconciled": [{"job_id": "applied"}]}

    def irrelevant(root):
        calls.append("irrelevant")
        return {"changed": [{"job_id": "negative"}]}

    def calibration(root, report):
        calls.append("calibration")
        assert "gmail_submission_reconciliation" in report
        assert "owner_irrelevant_reconciliation" in report
        return {"adjusted_count": 1}

    monkeypatch.setattr(post_scan, "reconcile_submission_mail", gmail)
    monkeypatch.setattr(post_scan, "reconcile_irrelevant_feedback", irrelevant)
    monkeypatch.setattr(post_scan, "apply_owner_feedback_calibration", calibration)

    report = {"results": []}
    returned = post_scan.reconcile_after_scan(tmp_path, report)

    assert returned is report
    assert calls == ["gmail", "irrelevant", "calibration"]
    assert report["owner_feedback_calibration"]["adjusted_count"] == 1


def test_optional_connected_reconciliation_failure_is_reported_not_raised(monkeypatch, tmp_path: Path) -> None:
    def broken(root):
        raise RuntimeError("connected service unavailable")

    monkeypatch.setattr(post_scan, "reconcile_submission_mail", broken)
    monkeypatch.setattr(post_scan, "reconcile_irrelevant_feedback", lambda root: {"changed": []})
    monkeypatch.setattr(post_scan, "apply_owner_feedback_calibration", lambda root, report: {"adjusted_count": 0})

    report = post_scan.reconcile_after_scan(tmp_path, {"results": []})

    failure = report["gmail_submission_reconciliation"]
    assert "connected service unavailable" in failure["error"]
    assert failure["send_or_submit"] is False
    assert report["owner_irrelevant_reconciliation"] == {"changed": []}


def test_core_scanner_owns_post_scan_reconciliation_for_every_entry_point() -> None:
    scanner = (ROOT / "career_engine/scanner.py").read_text(encoding="utf-8")
    assert "from .post_scan import reconcile_after_scan" in scanner
    assert "reconcile_after_scan(root, report)" in scanner

    # All supported wrappers and the native CLI already converge on run_scan;
    # they must not duplicate the post-scan connected-data pass themselves.
    wrappers = (
        ROOT / "projects/job-automation/daily_scanner.py",
        ROOT / "projects/job-automation/hermes_scanner.py",
        ROOT / "projects/job-automation/chatgpt_scanner.py",
    )
    for path in wrappers:
        source = path.read_text(encoding="utf-8")
        assert "run_scan(" in source, path
        assert "reconcile_after_scan" not in source, path

    cli = (ROOT / "career_engine/cli.py").read_text(encoding="utf-8")
    assert "report = run_scan(" in cli
    assert "result = scan(" in cli
