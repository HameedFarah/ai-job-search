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
    assert is_terminal_resolution({"Source_Status": "Verified receiving email route"})
    assert is_terminal_resolution({"Source_Status": "Verified careers/ATS application route"})
    assert is_terminal_resolution({"Source_Status": "Resolved - verified company; no usable employment route"})
    assert is_terminal_resolution({"Source_Status": "Identity unconfirmed after automated REGA resolution"})
    assert not is_terminal_resolution({"Source_Status": "No verified official domain"})


def test_paid_unavailable_does_not_imply_provider_call():
    assert resolver._paid_available(None, 1.0, 0.003) is False


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
    ]
    selected = resolver._selected_rows(master, reopen_unconfirmed=False)
    assert [row["Master_ID"] for row in selected] == ["CE-1"]
