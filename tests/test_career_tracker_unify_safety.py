from __future__ import annotations

import pytest

from tools import career_tracker_unify as base
from tools import career_tracker_unify_safe as safe


def record(job_id: str, *, company: str, role: str, url: str, external_id: str = "", source: str = "linkedin"):
    return {
        "job": {
            "job_id": job_id,
            "company": company,
            "role": role,
            "source_url": url,
            "external_job_id": external_id,
            "source": source,
            "processing_status": "ingested",
        },
        "processing_state": {},
    }


def test_generic_shared_careers_url_does_not_merge_different_roles():
    records = {
        "a": record("a", company="Example", role="Design Manager", url="https://example.com/careers"),
        "b": record("b", company="Example", role="Project Director", url="https://example.com/careers"),
    }
    assert safe.safe_exact_duplicate_groups(records) == []


def test_same_company_role_and_job_url_is_safe_exact_duplicate():
    records = {
        "a": record("a", company="Example", role="Design Manager", url="https://example.com/jobs/123"),
        "b": record("b", company="Example", role="Design Manager", url="https://example.com/jobs/123/"),
    }
    assert safe.safe_exact_duplicate_groups(records) == [["a", "b"]]


def test_linkedin_title_url_variants_match_only_when_role_matches():
    records = {
        "a": record("a", company="Example", role="Design Manager", url="https://www.linkedin.com/jobs/view/design-manager-4448815998/"),
        "b": record("b", company="Example", role="Design Manager", url="https://sa.linkedin.com/jobs/view/4448815998"),
        "c": record("c", company="Example", role="Project Manager", url="https://www.linkedin.com/jobs/view/4448815998"),
    }
    assert safe.safe_exact_duplicate_groups(records) == [["a", "b"]]


class FullCollectionHere:
    def __init__(self):
        self.slug = "test"
        self.api_key = "test"
        self.base = "https://example.invalid"


def test_site_data_reader_fails_closed_at_safety_limit(monkeypatch):
    monkeypatch.setattr(base.HereNow, "records", safe.complete_site_records, raising=True)
    monkeypatch.setattr(safe, "_original_records", lambda self, collection, limit=1000: [{}] * limit)
    here = FullCollectionHere()
    with pytest.raises(RuntimeError, match="full reconciliation cannot be proven"):
        safe.complete_site_records(here, "history", 1000)
