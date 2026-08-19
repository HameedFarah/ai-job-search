from __future__ import annotations

from types import SimpleNamespace

from career_engine import owner_feedback


class FakeTracker:
    def __init__(self, rows):
        self.rows = rows

    def list_rows(self):
        return list(self.rows)


def config():
    return {
        "scoring": {
            "thresholds": {
                "high_priority": 70,
                "credible": 65,
                "selective": 50,
                "weak": 0,
            }
        }
    }


def patch_tracker(monkeypatch, tmp_path, rows):
    paths = SimpleNamespace(tracker_base=tmp_path)
    tracker = FakeTracker(rows)
    monkeypatch.setattr(owner_feedback, "load_config", lambda root: (config(), paths))
    monkeypatch.setattr(owner_feedback, "_load_tracker", lambda loaded_paths: tracker)


def test_two_irrelevant_same_title_activate_bounded_penalty(monkeypatch, tmp_path) -> None:
    patch_tracker(monkeypatch, tmp_path, [
        {"job_id": "a", "role": "Senior Commercial Manager", "outcome": "irrelevant", "application_status": "not_submitted", "processing_status": "inactive"},
        {"job_id": "b", "role": "Senior Commercial Manager", "outcome": "irrelevant", "application_status": "not_submitted", "processing_status": "inactive"},
    ])
    result = owner_feedback.build_owner_feedback_calibration(tmp_path)
    rule = result["patterns"]["senior commercial manager"]
    assert rule["active"] is True
    assert 0 < rule["penalty"] <= owner_feedback.MAX_OWNER_FEEDBACK_PENALTY
    assert rule["negative_samples"] == 2
    assert rule["positive_samples"] == 0


def test_one_irrelevant_sample_is_learning_evidence_but_not_active(monkeypatch, tmp_path) -> None:
    patch_tracker(monkeypatch, tmp_path, [
        {"job_id": "a", "role": "Senior Commercial Manager", "outcome": "irrelevant", "application_status": "not_submitted", "processing_status": "inactive"},
    ])
    result = owner_feedback.build_owner_feedback_calibration(tmp_path)
    rule = result["patterns"]["senior commercial manager"]
    assert rule["active"] is False
    assert rule["penalty"] == 0


def test_applied_positive_example_blocks_negative_title_penalty(monkeypatch, tmp_path) -> None:
    patch_tracker(monkeypatch, tmp_path, [
        {"job_id": "a", "role": "Technical Project Manager", "outcome": "irrelevant", "application_status": "not_submitted", "processing_status": "inactive"},
        {"job_id": "b", "role": "Technical Project Manager", "outcome": "irrelevant", "application_status": "not_submitted", "processing_status": "inactive"},
        {"job_id": "c", "role": "Technical Project Manager", "outcome": "", "application_status": "submitted", "processing_status": "applied"},
    ])
    result = owner_feedback.build_owner_feedback_calibration(tmp_path)
    rule = result["patterns"]["technical project manager"]
    assert rule["negative_samples"] == 2
    assert rule["positive_samples"] == 1
    assert rule["active"] is False
    assert rule["penalty"] == 0
