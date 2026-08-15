"""Existing-queue target-lane reconciliation regressions."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from career_engine.targeting import reconcile_existing_non_target_jobs


ROOT = Path(__file__).resolve().parents[1]
TAXONOMY = json.loads(
    (ROOT / "projects/job-automation/config/requirements-taxonomy.v1.json").read_text(encoding="utf-8")
)


class _FakeTracker:
    def __init__(self, base_dir: Path, rows: list[dict], records: dict[str, dict]) -> None:
        self.base_dir = base_dir
        self._rows = rows
        self._records = records
        self.updates: list[tuple[str, dict, dict]] = []

    def list_rows(self) -> list[dict]:
        return self._rows

    def get_job(self, job_id: str) -> dict:
        return self._records[job_id]

    def update_job(self, job_id: str, fields: dict, **kwargs) -> None:
        self.updates.append((job_id, fields, kwargs))
        record = self._records[job_id]
        for key, value in fields.items():
            if key == "processing_status":
                record["job"]["processing_status"] = value
                for row in self._rows:
                    if row["job_id"] == job_id:
                        row["processing_status"] = value
            else:
                record[key] = value


def _row(job_id: str, role: str, status: str, application_status: str = "not_submitted") -> dict:
    return {
        "job_id": job_id,
        "company": "Example",
        "role": role,
        "processing_status": status,
        "application_status": application_status,
    }


def _record(row: dict, owner_decision: str = "") -> dict:
    state = {"status": row["processing_status"]}
    if owner_decision:
        state["owner_decision"] = {"decision": owner_decision}
    return {
        "job": {
            "job_id": row["job_id"],
            "company": row["company"],
            "role": row["role"],
            "processing_status": row["processing_status"],
            "application_status": row["application_status"],
        },
        "processing_state": state,
    }


class TargetLaneReconciliationTests(unittest.TestCase):
    def test_reconciles_only_safe_early_non_target_records(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            rows = [
                _row("civil-review", "Civil Engineer", "manual_review_needed"),
                _row("urban-ready", "Urban Designer", "generation_ready"),
                _row("design-review", "Design Manager", "manual_review_needed"),
                _row("accepted-civil", "Civil Engineer", "generation_ready"),
                _row("applied-civil", "Civil Engineer", "manual_review_needed", "applied"),
                _row("generated-civil", "Civil Engineer", "generated_content_valid"),
            ]
            records = {row["job_id"]: _record(row) for row in rows}
            records["accepted-civil"] = _record(rows[3], owner_decision="accepted")
            tracker = _FakeTracker(base, rows, records)

            artifact_dir = base / "artifacts" / "urban-ready"
            artifact_dir.mkdir(parents=True)
            (artifact_dir / "generation_packet.json").write_text("{}\n", encoding="utf-8")
            (artifact_dir / "generation_packet.stage.json").write_text("{}\n", encoding="utf-8")
            (artifact_dir / "pipeline_state.json").write_text(
                json.dumps({"input_hash": "x", "data": {"stage": "generation_ready"}}) + "\n",
                encoding="utf-8",
            )

            result = reconcile_existing_non_target_jobs(tracker, TAXONOMY, actor="system")

            changed_ids = {item["job_id"] for item in result["changed_jobs"]}
            self.assertEqual(changed_ids, {"civil-review", "urban-ready"})
            self.assertEqual(result["changed_count"], 2)
            self.assertEqual(records["civil-review"]["job"]["processing_status"], "rejected")
            self.assertEqual(records["urban-ready"]["job"]["processing_status"], "rejected")
            self.assertEqual(records["design-review"]["job"]["processing_status"], "manual_review_needed")
            self.assertEqual(records["accepted-civil"]["job"]["processing_status"], "generation_ready")
            self.assertEqual(records["applied-civil"]["job"]["processing_status"], "manual_review_needed")
            self.assertEqual(records["generated-civil"]["job"]["processing_status"], "generated_content_valid")
            self.assertEqual(result["preserved_owner_decision_job_ids"], ["accepted-civil"])
            self.assertFalse((artifact_dir / "generation_packet.json").exists())
            self.assertFalse((artifact_dir / "generation_packet.stage.json").exists())
            pipeline = json.loads((artifact_dir / "pipeline_state.json").read_text(encoding="utf-8"))["data"]
            self.assertEqual(pipeline["stage"], "rejected")
            self.assertEqual(pipeline["skip_reason"], "non_target_production_individual_contributor")
            self.assertFalse(pipeline["external_action_allowed"])
            self.assertFalse(result["send_or_submit"])

    def test_scanner_wrappers_compile_with_reconciliation_hook(self) -> None:
        for relative in (
            "projects/job-automation/daily_scanner.py",
            "projects/job-automation/hermes_scanner.py",
            "projects/job-automation/chatgpt_scanner.py",
        ):
            source = (ROOT / relative).read_text(encoding="utf-8")
            compile(source, relative, "exec")
            self.assertIn("reconcile_existing_non_target_jobs", source)
            self.assertIn('report["target_lane_reconciliation"]', source)


if __name__ == "__main__":
    unittest.main()
