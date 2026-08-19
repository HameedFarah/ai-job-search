from pathlib import Path
from unittest.mock import patch

from tools import career_dashboard_add_job as add_job


DESCRIPTION = (
    "Lead a specialist discipline outside the current architecture/design target lane, "
    "including team coordination, reporting, stakeholder interfaces and delivery oversight."
)


def test_url_only_non_target_job_is_recorded_without_generation_attempt(tmp_path: Path) -> None:
    prepared = {
        "job_id": "abcdef1234567890",
        "fit_score": {"total": 28},
        "stage": "rejected",
        "skip_reason": "out_of_lane_discipline",
        "blockers": [],
        "outputs": {
            "normalized_job": "/tmp/normalized.json",
            "fit_score": "/tmp/fit.json",
        },
    }
    generated: list[dict] = []
    refreshed: list[tuple[Path, Path]] = []

    with patch.object(add_job, "_fetch_structured_job", return_value={
        "job_description": DESCRIPTION,
        "company": "Example Company",
        "role": "Senior Commercial Manager",
        "location": "Riyadh, Saudi Arabia",
        "external_job_id": "REQ-OUT-1",
    }), patch.object(add_job, "_run_prepare", return_value=prepared):
        job_id, message = add_job.run_add_job(
            repo=tmp_path,
            dispatcher=tmp_path / "dispatcher.py",
            website_root=tmp_path / "dashboard",
            data={"job_url": "https://example.com/jobs/out-1"},
            generate_package=lambda **kwargs: generated.append(kwargs) or "generated_and_rendered",
            refresh_dashboard=lambda repo, site: refreshed.append((repo, site)),
        )

    assert job_id == "abcdef1234567890"
    assert generated == []
    assert refreshed
    assert "non-target role" in message
    assert "no generation packet was created" in message
    assert "Nothing was sent or submitted" in message


def test_generation_ready_missing_packet_is_bounded_not_assistant_crash(tmp_path: Path) -> None:
    prepared = {
        "job_id": "abcdef1234567890",
        "fit_score": {"total": 81},
        "stage": "generation_ready",
        "skip_reason": "",
        "blockers": [],
        "outputs": {
            "generation_packet": str(tmp_path / "artifacts" / "abcdef1234567890" / "generation_packet.json"),
        },
    }
    generated: list[dict] = []
    refreshed: list[tuple[Path, Path]] = []

    with patch.object(add_job, "_run_prepare", return_value=prepared):
        job_id, message = add_job.run_add_job(
            repo=tmp_path,
            dispatcher=tmp_path / "dispatcher.py",
            website_root=tmp_path / "dashboard",
            data={
                "job_description": DESCRIPTION,
                "company": "Example Development",
                "role": "Senior Design Manager",
            },
            generate_package=lambda **kwargs: generated.append(kwargs) or "generated_and_rendered",
            refresh_dashboard=lambda repo, site: refreshed.append((repo, site)),
        )

    assert job_id == "abcdef1234567890"
    assert generated == []
    assert refreshed
    assert "generation packet was unavailable" in message
    assert "Rebuild documents" in message
    assert "Nothing was sent or submitted" in message


def test_backward_compatible_prepare_payload_without_stage_still_generates(tmp_path: Path) -> None:
    prepared = {
        "job_id": "abcdef1234567890",
        "fit_score": {"total": 84},
        "blockers": [],
    }
    generated: list[dict] = []

    with patch.object(add_job, "_run_prepare", return_value=prepared):
        job_id, message = add_job.run_add_job(
            repo=tmp_path,
            dispatcher=tmp_path / "dispatcher.py",
            website_root=tmp_path / "dashboard",
            data={
                "job_description": DESCRIPTION,
                "company": "Example Development",
                "role": "Senior Design Manager",
            },
            generate_package=lambda **kwargs: generated.append(kwargs) or "generated_and_rendered",
            refresh_dashboard=lambda repo, site: None,
        )

    assert job_id == "abcdef1234567890"
    assert generated and generated[0]["force_regenerate"] is True
    assert "completed the internal package" in message
