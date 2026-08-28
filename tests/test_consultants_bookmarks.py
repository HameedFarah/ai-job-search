import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "projects/job-automation/config/gcc-employers.v1.json"
BOOKMARKS = ROOT / "projects/job-automation/config/consultants-bookmarks.v1.json"


def test_all_consultants_bookmarks_are_accounted_for_and_deduplicated():
    payload = json.loads(BOOKMARKS.read_text(encoding="utf-8"))
    rows = payload["bookmarks"]
    # The stale, unidentified SuccessFactors `abdullahal` bookmark was
    # deliberately deleted after employer-specific endpoint verification.
    assert len(rows) == 43
    assert len({row["id"] for row in rows}) == 43
    assert "sap-successfactors-abdullahal" not in {row["id"] for row in rows}
    assert all(row.get("url") for row in rows)
    assert all(row["scan"] is False for row in rows if row["class"] in {
        "tool_manual_tracker", "recruiter_poster_signal", "recruiter_discovery_source",
        "job_board_discovery_source", "unresolved_ats_tenant", "manual_index",
    })
    ids = {row["id"] for row in rows}
    for row in rows:
        if "duplicate_of" in row:
            assert row["duplicate_of"] in ids
    active = [row for row in rows if row["status"] == "active"]
    employers = {row["id"]: row for row in json.loads(REGISTRY.read_text(encoding="utf-8"))["employers"]}
    for row in active:
        assert row["class"] in {"direct_employer", "direct_employer_consultant", "official_ats"}
        assert row.get("employer_id") in employers


def test_discovery_and_manual_sources_cannot_be_authoritative():
    payload = json.loads(BOOKMARKS.read_text(encoding="utf-8"))
    for row in payload["bookmarks"]:
        if "discovery" in row["class"] or row["class"] in {"tool_manual_tracker", "recruiter_poster_signal", "manual_index"}:
            assert row["scan"] is False
            assert row["status"] == "manual"


def test_ats_aliases_are_preserved_for_known_employers():
    rows = {row["id"]: row for row in json.loads(BOOKMARKS.read_text(encoding="utf-8"))["bookmarks"]}
    assert "https://jobs.egis-group.com/omrania-part-of-egis-group" in rows["omrania"]["aliases"]
    assert "https://worley.taleo.net/" in rows["worley-taleo"]["aliases"]
