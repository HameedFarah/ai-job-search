from argparse import Namespace
from unittest.mock import Mock

import pytest

from career_engine.rega_enrichment.continuous import (
    identity_matches, parse_exa, process, select_rows, tracker_updates, contact_rank,
    hiring_evidence,
)


def test_search_error_is_not_no_results():
    with pytest.raises(RuntimeError):
        parse_exa({"isError": True, "content": []})
    assert parse_exa({"content": [{"type": "text", "text": "No results found"}]}) == []


def test_search_preserves_real_urls():
    results = parse_exa({"content": [{"type": "text", "text": "Title: Example\nURL: https://example.sa/contact\nHighlights:\nText"}]})
    assert results[0]["url"] == "https://example.sa/contact"


def test_generic_words_do_not_confirm_unrelated_company():
    row = {"Company_or_Office": "Jadwa Al Basatin Real Estate", "Arabic_Name": ""}
    assert not identity_matches(row, "Al Habtoor Real Estate Development in Saudi Arabia", "alhabtoor.sa")
    assert identity_matches(row, "Jadwa Al Basatin develops homes", "basatin.sa")


def test_single_brand_needs_domain_and_saudi_context():
    row = {"Company_or_Office": "Armal Real Estate", "Arabic_Name": ""}
    assert not identity_matches(row, "Armal news in Riyadh", "news.com")
    assert identity_matches(row, "Armal", "armal.com.sa")


def test_no_route_and_portal_records_are_reopened():
    rows = [dict(Master_ID=str(i), Company_or_Office=f"Firm {i}", Source_Status=status)
            for i, status in enumerate(["Resolved - verified company; no usable employment route",
                                       "Verified careers/ATS application route",
                                       "Verified receiving email route",
                                       "Resolved - company already successfully contacted"])]
    selected, excluded = select_rows(rows, {"contacted_companies": set(), "queued_companies": set()})
    assert {r["Master_ID"] for r in selected} == {"0", "1"}
    assert excluded == {"2": "existing_receiving_route", "3": "already_contacted"}


def fake_research():
    r = Mock()
    page = {"url": "https://example.sa/contact", "text": "hr@example.sa info@example.sa", "mailtos": []}
    r.resolve.return_value = ("example.sa", [page], [], "confirmed")
    r.routes.return_value = ([{"email": "info@example.sa", "provider": "first_party", "source_urls": [page["url"]], "title": ""}],
                             [{"url": "https://example.sa/careers", "source_url": page["url"], "kind": "careers_page"}])
    return r


def test_portal_and_general_email_do_not_stop_hr_discovery():
    apis = Mock()
    apis.hunter_contacts.return_value = [{"email": "hr@example.sa", "provider": "hunter", "title": "", "source_urls": []}]
    apis.verify.return_value = {"safe_to_send": True, "status": "RECEIVING"}
    result = process({"Master_ID": "CE-1", "Company_or_Office": "Example"}, fake_research(), apis,
                     {"known_emails": set(), "permanent_bounces": set()})
    assert result["selected"]["email"] == "hr@example.sa"
    assert result["portals"]
    assert len(result["contacts"]) == 2


def test_bounced_hr_cannot_displace_valid_general_email():
    apis = Mock()
    apis.hunter_contacts.return_value = [{"email": "hr@example.sa", "provider": "hunter", "title": "", "source_urls": []}]
    apis.verify.return_value = {"safe_to_send": True, "status": "RECEIVING"}
    result = process({"Master_ID": "CE-1", "Company_or_Office": "Example"}, fake_research(), apis,
                     {"known_emails": set(), "permanent_bounces": {"hr@example.sa"}})
    assert result["selected"]["email"] == "info@example.sa"
    apis.verify.assert_called_once_with("info@example.sa")


def test_customer_support_and_executives_are_not_hr_routes():
    assert contact_rank({"email": "support@example.sa", "title": "Support"}) == 9
    assert contact_rank({"email": "ceo@example.sa", "title": "CEO"}) == 9


def test_tracker_write_preserves_authorization_and_old_evidence():
    row = {"Notes": "Old portal evidence", "Email": "", "Source_Status": "Portal verified", "Send_Eligibility": "HOLD"}
    result = {"outcome": "validated_general", "domain": "example.sa", "at": "2026-09-11T12:00:00Z",
              "contacts": [{"email": "info@example.sa", "provider": "hunter", "source_urls": ["https://example.sa"],
                            "validation": {"status": "RECEIVING"}}], "portals": []}
    updates = tracker_updates(row, result)
    assert updates["Notes"].startswith("Old portal evidence | ")
    assert "info@example.sa" in updates["Notes"]
    assert not {"Email", "Send_Eligibility", "Source_Status", "Send_State"}.intersection(updates)


def test_social_activity_is_not_candidates_own_hiring_profile():
    research = Mock()
    research.search.return_value = {"results": [{"url": "https://www.linkedin.com/in/other-person",
        "title": "Other Person", "text": "HR at Example. Liked a post by Jane Doe."}]}
    candidate = {"email": "jane@example.sa", "name": "Jane Doe", "title": "HR Specialist", "domain": "example.sa"}
    assert hiring_evidence(candidate, {"Company_or_Office": "Example"}, research) == []
