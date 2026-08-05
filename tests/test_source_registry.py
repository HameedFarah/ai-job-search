"""Registry and capability-matrix tests for the discovery source framework."""

from __future__ import annotations

import pytest

from career_engine.sources.registry import (
    STATUS_ACTIVE,
    STATUS_BLOCKED,
    STATUS_PARTIAL,
    capability_matrix,
    get_source,
    registry_payload,
    sources,
)


def test_registry_ids_are_unique_and_well_formed() -> None:
    entries = sources()
    ids = [item["id"] for item in entries]
    assert len(ids) == len(set(ids)), "source ids must be unique"
    for item in entries:
        assert item["id"]
        assert item["name"]
        assert item["kind"]
        assert item["priority"] >= 1
        assert item["posting_date"] in {"exact", "approximate", "none", "unknown"}
        assert isinstance(item["official"], bool)
        assert item["status"] in {STATUS_ACTIVE, STATUS_PARTIAL, STATUS_BLOCKED, "experimental"}


def test_blocked_sources_document_a_reason() -> None:
    blocked = [item for item in sources() if item["status"] == STATUS_BLOCKED]
    assert blocked, "at least one blocked source must be documented"
    for item in blocked:
        assert item["blocked_reason"], f"{item['id']} is blocked but has no reason"
    ids = {item["id"] for item in blocked}
    # The fragile/scraping-blocked sources named by connector research must be present.
    assert {"gcc_bayt", "gcc_naukrigulf", "gcc_gulftalent", "board_indeed", "linkedin_public"} <= ids


def test_capability_matrix_rows_match_registry() -> None:
    matrix = capability_matrix()
    entries = sources()
    assert len(matrix) == len(entries)
    for row in matrix:
        entry = get_source(row["source_id"])
        assert row["name"] == entry["name"]
        assert row["status"] == entry["status"]
        assert row["blocked_reason"] == entry["blocked_reason"]
        assert row["posting_date_support"] == entry["posting_date"]
        assert row["auth_required"] == entry["auth"]


def test_active_ats_apis_require_no_credentials() -> None:
    for source_id in ("greenhouse", "lever", "ashby", "smartrecruiters"):
        entry = get_source(source_id)
        assert entry["status"] == STATUS_ACTIVE
        assert entry["auth"] == "none"
        assert entry["official"] is True
        assert entry["posting_date"] == "exact"


def test_priority_one_sources_come_first_in_matrix() -> None:
    matrix = capability_matrix()
    assert matrix[0]["priority"] == 1
    priorities = [row["priority"] for row in matrix]
    assert priorities == sorted(priorities)


def test_registry_payload_declares_no_send() -> None:
    payload = registry_payload()
    assert payload["no_send_policy"] is True
    assert payload["schema_version"] == 1
    assert len(payload["sources"]) == len(payload["capability_matrix"])


def test_unknown_source_raises_key_error() -> None:
    with pytest.raises(KeyError):
        get_source("does-not-exist")
