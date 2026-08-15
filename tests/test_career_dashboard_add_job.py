import json
from pathlib import Path

import pytest

from tools import career_dashboard_add_job as add_job


JOB_HTML = """
<html><head><script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "JobPosting",
  "title": "Senior Design Manager",
  "description": "<p>Lead multidisciplinary design delivery across complex projects.</p><p>Manage consultants, clients and design assurance through construction.</p>",
  "identifier": {"@type": "PropertyValue", "value": "REQ-123"},
  "hiringOrganization": {"@type": "Organization", "name": "Example Development"},
  "jobLocation": {"@type": "Place", "address": {"addressLocality": "Riyadh", "addressCountry": "Saudi Arabia"}}
}
</script></head><body></body></html>
"""


def test_extract_structured_jobposting():
    parsed = add_job.extract_structured_job(JOB_HTML, "https://example.com/jobs/123")
    assert parsed["role"] == "Senior Design Manager"
    assert parsed["company"] == "Example Development"
    assert parsed["external_job_id"] == "REQ-123"
    assert "Riyadh" in parsed["location"]
    assert "multidisciplinary design delivery" in parsed["job_description"]


def test_rejects_non_public_or_invalid_urls():
    with pytest.raises(add_job.AddJobError):
        add_job._valid_public_url("not-a-url")
    with pytest.raises(add_job.AddJobError):
        add_job._valid_public_url("http://localhost/job/1")


def test_pasted_job_is_prepared_and_generated(monkeypatch, tmp_path):
    prepared = {
        "job_id": "abcdef1234567890",
        "fit_score": {"total": 84},
        "blockers": [],
    }
    monkeypatch.setattr(add_job, "_run_prepare", lambda repo, args: prepared)
    generated = {}
    refreshed = []

    def fake_generate(**kwargs):
        generated.update(kwargs)
        return "generated_and_rendered"

    job_id, message = add_job.run_add_job(
        repo=tmp_path,
        dispatcher=tmp_path / "dispatcher.py",
        website_root=tmp_path / "dashboard",
        data={
            "job_description": (
                "Lead multidisciplinary design delivery across complex projects and manage consultant coordination, "
                "technical reviews, client interfaces, programme requirements and construction-stage design issues."
            ),
            "company": "Example Development",
            "role": "Senior Design Manager",
            "location": "Riyadh, Saudi Arabia",
        },
        generate_package=fake_generate,
        refresh_dashboard=lambda repo, root: refreshed.append((repo, root)),
    )
    assert job_id == "abcdef1234567890"
    assert generated["job_id"] == job_id
    assert generated["force_regenerate"] is True
    assert refreshed
    assert "84/100" in message
    assert "Nothing was sent or submitted" in message


def test_blocked_job_is_kept_without_forced_generation(monkeypatch, tmp_path):
    monkeypatch.setattr(add_job, "_run_prepare", lambda repo, args: {
        "job_id": "abcdef1234567890",
        "fit_score": {"total": 42},
        "blockers": ["below_generation_threshold:42"],
    })
    generated = []
    refreshed = []
    job_id, message = add_job.run_add_job(
        repo=tmp_path,
        dispatcher=tmp_path / "dispatcher.py",
        website_root=tmp_path / "dashboard",
        data={
            "job_description": "Fraud investigation specialist role requiring financial-crime investigations, AML controls and case management expertise.",
            "company": "Example Company",
            "role": "Fraud Investigator",
        },
        generate_package=lambda **kwargs: generated.append(kwargs),
        refresh_dashboard=lambda repo, root: refreshed.append((repo, root)),
    )
    assert job_id
    assert not generated
    assert refreshed
    assert "was not forced" in message
    assert "below_generation_threshold:42" in message


def test_url_metadata_fills_missing_fields(monkeypatch, tmp_path):
    monkeypatch.setattr(add_job, "_fetch_structured_job", lambda repo, url: {
        "job_description": "Lead design management, consultant coordination, technical assurance and construction-stage design delivery across a major programme.",
        "company": "Example Development",
        "role": "Design Manager",
        "location": "Riyadh, Saudi Arabia",
        "external_job_id": "REQ-999",
    })
    captured = {}

    def fake_prepare(repo, args):
        captured["args"] = args
        return {"job_id": "12345678abcdef00", "fit_score": {"total": 80}, "blockers": []}

    monkeypatch.setattr(add_job, "_run_prepare", fake_prepare)
    job_id, _ = add_job.run_add_job(
        repo=tmp_path,
        dispatcher=tmp_path / "dispatcher.py",
        website_root=tmp_path / "dashboard",
        data={"job_url": "https://example.com/jobs/999"},
        generate_package=lambda **kwargs: "generated_and_rendered",
        refresh_dashboard=lambda repo, root: None,
    )
    assert job_id == "12345678abcdef00"
    assert "REQ-999" in captured["args"]
    assert "https://example.com/jobs/999" in captured["args"]
