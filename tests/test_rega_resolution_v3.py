"""Deterministic tests for REGA resolution v3."""
from __future__ import annotations

from career_engine.rega_enrichment.models import CompanyRecord
from career_engine.rega_enrichment.resolution import (
    MuqawilProfile,
    career_value_score,
    is_terminal_resolution,
    parse_muqawil_profile,
    priority_band,
)
from runtime import rega_resolution_v3 as resolver


def test_priority_score_prefers_current_riyadh_operating_project():
    row = {
        "Master_ID": "CE-A",
        "Company_or_Office": "Example Development",
        "Arabic_Name": "شركة المثال للتطوير العقاري",
        "Region": "Riyadh",
        "Address_or_Website": "https://example.sa/",
        "Source_Verification": "Verified - official first-party company domain",
        "Source_Status": "Active developer; official domain verified",
        "Notes": "Current project is under construction in Riyadh.",
    }
    score = career_value_score(row)
    assert score >= 55
    assert priority_band(score) == "A"


def test_priority_score_is_only_triage_and_keeps_sparse_company_nonnegative():
    row = {
        "Master_ID": "CE-C",
        "Company_or_Office": "Sparse One Person Company",
        "Arabic_Name": "شركة شخص واحد",
        "Region": "Other",
        "Source_Verification": "Not researched",
        "Source_Status": "No verified official domain",
        "Notes": "",
    }
    score = career_value_score(row)
    assert 0 <= score <= 100
    assert priority_band(score) in {"B", "C"}


def test_verified_muqawil_medium_company_increases_research_priority():
    row = {
        "Master_ID": "CE-M",
        "Company_or_Office": "Example Contractor",
        "Arabic_Name": "شركة المثال للمقاولات",
        "Region": "Eastern Prov.",
        "Source_Verification": "Not researched",
        "Source_Status": "No verified official domain",
        "Notes": "",
    }
    profile = MuqawilProfile(
        name="Example Contractor",
        url="https://muqawil.org/en/contractors/100/200",
        company_size="Medium",
        membership_number="123456",
        status="Account Verified",
    )
    assert career_value_score(row, muqawil=profile) > career_value_score(row)


def test_muqawil_parser_requires_identity_match_and_extracts_noncontact_metadata():
    company = CompanyRecord(
        company_id="CE-MUQ",
        license_no="1234",
        english_name="Example Contracting Company",
        arabic_name="شركة المثال للمقاولات",
        location="Riyadh",
    )
    html = """
    <html><body>
      <h1>Example Contracting Company</h1>
      <div>Membership Number | 7654321</div>
      <div>Status | Account Verified</div>
      <div>Company Size Based on Number of Employees | Medium Company Size</div>
      <div>City - Region | Riyadh | Riyadh</div>
      <div>Email | published@example.com</div>
    </body></html>
    """
    profile = parse_muqawil_profile(
        company,
        "https://muqawil.org/en/contractors/100/200",
        html,
    )
    assert profile is not None
    assert profile.company_size == "Medium"
    assert profile.membership_number == "7654321"
    assert profile.status == "Account Verified"
    assert not hasattr(profile, "email")


def test_muqawil_parser_rejects_wrong_company():
    company = CompanyRecord(
        company_id="CE-MUQ",
        license_no="1234",
        english_name="Example Contracting Company",
        arabic_name="شركة المثال للمقاولات",
        location="Riyadh",
    )
    html = "<html><body><h1>Completely Different Construction Company</h1><div>Company Size | Large</div></body></html>"
    assert parse_muqawil_profile(
        company,
        "https://muqawil.org/en/contractors/100/200",
        html,
    ) is None


def test_resolution_terminal_states_are_idempotent():
    statuses = [
        "Verified receiving email route",
        "Verified careers/ATS application route",
        "Resolved - verified company; no usable employment route",
        "Resolved - official mailbox not receiving (INVALID)",
        "Resolved - official mailbox found; validation unavailable",
        "Resolved - company already successfully contacted",
        "Resolved - company already covered by active/sent outreach",
        "Identity unconfirmed after automated REGA resolution",
    ]
    assert all(is_terminal_resolution({"Source_Status": status}) for status in statuses)
    assert not is_terminal_resolution({"Source_Status": "No verified official domain"})


def test_paid_unavailable_does_not_imply_provider_call():
    assert resolver._paid_available(None, 1.0, 0.003) is False


def test_paid_available_respects_reserve(monkeypatch):
    class Client:
        pass

    monkeypatch.setattr(resolver.legacy, "balance", lambda _client: 1.002)
    assert resolver._paid_available(Client(), 1.0, 0.003) is False
    monkeypatch.setattr(resolver.legacy, "balance", lambda _client: 1.01)
    assert resolver._paid_available(Client(), 1.0, 0.003) is True


def test_selected_rows_skips_terminal_but_keeps_current_unresolved():
    master = [
        {
            "Master_ID": "CE-1",
            "Record_Type": "REGA_COMPANY_NO_EMAIL",
            "Company_or_Office": "Current Co",
            "Region": "Riyadh",
            "Source_Status": "No verified official domain",
            "Source_Verification": "Not researched",
        },
        {
            "Master_ID": "CE-2",
            "Record_Type": "REGA_COMPANY_NO_EMAIL",
            "Company_or_Office": "Done Co",
            "Region": "Riyadh",
            "Source_Status": "Resolved - verified company; no usable employment route",
        },
        {
            "Master_ID": "CE-3",
            "Record_Type": "REGA_COMPANY_NO_EMAIL",
            "Company_or_Office": "Already Contacted Co",
            "Region": "Riyadh",
            "Source_Status": "Resolved - company already successfully contacted",
        },
    ]
    selected = resolver._selected(master, reopen_unconfirmed=False)
    assert [row["Master_ID"] for row in selected] == ["CE-1"]


def test_identity_unconfirmed_persistence_is_fail_closed(monkeypatch):
    row = {"Master_ID": "CE-X", "Notes": ""}
    captured = {}
    monkeypatch.setattr(resolver.legacy, "today", lambda: "2026-09-09")
    monkeypatch.setattr(
        resolver.legacy,
        "update_master_fields",
        lambda token, headers, master_row, updates: captured.update(updates),
    )
    resolver.persist_identity_unconfirmed(
        "token", [], row, "A", 60, "no verified domain", None
    )
    assert captured["Source_Status"] == "Identity unconfirmed after automated REGA resolution"
    assert captured["Send_Eligibility"] == "NO_VERIFIED_EMAIL_ROUTE"
    assert "Priority-A" in captured["Next_Action"]
    assert "IDENTITY_UNCONFIRMED" in captured["Notes"]


def test_company_covered_persistence_prevents_duplicate_route(monkeypatch):
    row = {"Master_ID": "CE-X", "Notes": ""}
    captured = {}
    monkeypatch.setattr(resolver.legacy, "today", lambda: "2026-09-09")
    monkeypatch.setattr(
        resolver.legacy,
        "update_master_fields",
        lambda token, headers, master_row, updates: captured.update(updates),
    )
    resolver.persist_company_covered(
        "token", [], row, "successfully_contacted", "A", 70
    )
    assert captured["Source_Status"] == "Resolved - company already successfully contacted"
    assert captured["Send_Eligibility"] == "NO_ADDITIONAL_COMPANY_ROUTE_REQUIRED"
    assert is_terminal_resolution(captured)
