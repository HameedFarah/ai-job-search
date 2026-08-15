"""Pipeline regression: clearly non-target jobs terminate before generation."""

from __future__ import annotations

from types import SimpleNamespace

from career_engine import pipeline


class _FakeTracker:
    def __init__(self) -> None:
        self.updates = []

    def ingest(self, payload, **kwargs):
        return {"job_id": "skip-role-001"}

    def update_job(self, job_id, fields, **kwargs):
        self.updates.append((job_id, fields, kwargs))


def test_prepare_terminally_rejects_production_individual_contributor(tmp_path, monkeypatch) -> None:
    tracker_base = tmp_path / "tracker"
    tracker_base.mkdir()
    paths = SimpleNamespace(repo_root=tmp_path / "repo", tracker_base=tracker_base)
    config = {"scoring": {"thresholds": {"high_priority": 70}}}
    tracker = _FakeTracker()
    normalized = {
        "source": "successfactors",
        "reference": "857334523",
        "source_url": "https://careers.example/job/857334523/",
        "company": "Example Company",
        "role": "Urban Designer",
        "location": "Riyadh",
        "posting_date": "2026-08-15",
        "posting_date_precision": "day",
        "posting_date_source": "source",
        "full_job_description": "Produce urban design packages and coordinate design information.",
        "jd_hash": "abc",
        "live_status": "live",
        "live_verified_at": "2026-08-15T10:00:00+00:00",
        "live_verification_source": "official",
        "requirements": [],
    }
    score = {
        "total": 82,
        "recommendation": "high_priority",
        "calibration": {
            "out_of_lane": False,
            "production": True,
            "has_management": False,
        },
    }

    monkeypatch.setattr(pipeline, "load_config", lambda root=None: (config, paths))
    monkeypatch.setattr(pipeline, "load_bundle", lambda root=None: {"taxonomy": {}, "bundle_hash": "bundle-1"})
    monkeypatch.setattr(pipeline, "normalize_job", lambda payload, taxonomy: normalized)
    monkeypatch.setattr(pipeline, "_load_tracker", lambda paths: tracker)
    monkeypatch.setattr(pipeline, "match_evidence", lambda normalized, bundle: {})
    monkeypatch.setattr(pipeline, "score_fit", lambda normalized, matches, bundle: score)
    monkeypatch.setattr(
        pipeline,
        "decide_route",
        lambda normalized, bundle: {"route": "portal", "application_url": normalized["source_url"], "blocker": ""},
    )
    monkeypatch.setattr(pipeline, "validate_live_status", lambda normalized: [])

    def should_not_generate(**kwargs):
        raise AssertionError("non-target role must not create a generation packet")

    monkeypatch.setattr(pipeline, "create_generation_packet", should_not_generate)

    state = pipeline.prepare({"anything": True}, root=tmp_path)

    assert state["stage"] == "rejected"
    assert state["skip_reason"] == "non_target_production_individual_contributor"
    assert state["blockers"] == []
    assert "generation_packet" not in state["outputs"]
    assert tracker.updates[-1][1]["processing_status"] == "rejected"
    assert tracker.updates[-1][1]["next_action"] == "Skipped automatically as a non-target role"
    assert tracker.updates[-1][2]["requires_owner_review"] is False
