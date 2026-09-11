from argparse import Namespace
from unittest.mock import Mock

import pytest

from career_engine.rega_enrichment.continuous import (
    identity_matches, parse_exa, process, select_rows, tracker_updates, contact_rank,
    hiring_evidence,
    official_home_matches,
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
    apis.verify_snov.return_value = {"status": "UNKNOWN"}
    apis.hunter_contacts.return_value = [{"email": "hr@example.sa", "provider": "hunter", "title": "", "source_urls": []}]
    apis.verify.return_value = {"safe_to_send": True, "status": "RECEIVING"}
    result = process({"Master_ID": "CE-1", "Company_or_Office": "Example"}, fake_research(), apis,
                     {"known_emails": set(), "permanent_bounces": set()})
    assert result["selected"]["email"] == "hr@example.sa"
    assert result["portals"]
    assert len(result["contacts"]) == 2


def test_bounced_hr_cannot_displace_valid_general_email():
    apis = Mock()
    apis.verify_snov.return_value = {"status": "UNKNOWN"}
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


def test_financial_article_and_agency_portfolio_are_not_official_sites():
    row = {"Company_or_Office": "Laden Investment Listed Joint", "Arabic_Name": "شركة لدن للاستثمار"}
    page = {"url": "https://bitarabi.com/", "html": "<title>سعر سهم شركة لدن للاستثمار</title>", "text": "شركة لدن للاستثمار سعر السهم Laden Investment Listed"}
    assert not official_home_matches(row, page, "bitarabi.com")
    row = {"Company_or_Office": "Dream Real Estate Investment", "Arabic_Name": "شركة دريم للاستثمار العقاري"}
    page = {"url": "https://meawal.com/", "html": "<title>Meawal Web Design</title>", "text": "Dream Real Estate Investment is our web design client"}
    assert not official_home_matches(row, page, "meawal.com")


@pytest.mark.parametrize("english,arabic,host,title", [
    ("Tilal Real Estate", "شركة تلال العقارية", "tilalre.com", "Tilal Real Estate | تلال العقارية"),
    ("Waken Real Estate Dev & Invest", "شركة وكن للتطوير والاستثمار العقاري", "wakan.sa", "الصفحة الرئيسية - وكن"),
    ("Al Zamiliya Investment Co", "الشركة الزاملية للاستثمار", "alzamiliah.com", "الزاملية للتطوير والاستثمار العقاري"),
])
def test_own_branding_survives_legal_suffix_and_transliteration(english, arabic, host, title):
    page = {"url": "https://" + host + "/", "html": "<title>" + title + "</title>",
            "text": title + " الرياض للتطوير العقاري"}
    assert official_home_matches({"Company_or_Office": english, "Arabic_Name": arabic}, page, host)


def test_similar_egyptian_brand_is_not_saudi_company():
    page = {"url": "https://dreamvieweg.com/", "html": "<title>Dream View Development</title>",
            "text": "Dream View Development دريم للتطوير العقاري Egypt Cairo"}
    assert not official_home_matches({"Company_or_Office": "Dream Real Estate Dev & Investment",
                                      "Arabic_Name": "شركة دريم للتطوير والاستثمار العقاري"}, page, "dreamvieweg.com")


def test_script_and_style_cannot_displace_real_contact_text():
    from career_engine.rega_enrichment.continuous import parse_page
    page = parse_page("<script>" + "noise " * 20000 + "</script><style>.x{}</style><p>hr@example.sa</p>", "https://example.sa/")
    assert page["text"].strip() == "hr@example.sa"


def test_existing_career_path_is_crawled_after_identity_confirmation():
    from career_engine.rega_enrichment.continuous import Research
    r = Research.__new__(Research)
    homepage = {"url": "https://tilalre.com/", "html": "<title>Tilal Real Estate</title>", "text": "Tilal Real Estate Riyadh"}
    career = {"url": "https://tilalre.com/career/", "text": "Apply"}
    r.fetch = Mock(side_effect=[homepage, career])
    host, pages, evidence, state = r.resolve({"Company_or_Office": "Tilal Real Estate", "Address_or_Website": career["url"]})
    assert state == "confirmed" and career in pages


def test_exa_transport_429_is_not_empty_search_success():
    with pytest.raises(RuntimeError):
        parse_exa({"error": "HTTP 429", "issue": {"statusCode": 429}})


def test_google_captcha_uses_yandex_and_replaces_old_empty_cache(tmp_path):
    import hashlib, json
    from career_engine.rega_enrichment.continuous import Research
    query = "company website"
    cache = tmp_path / "search" / (hashlib.sha256(query.encode()).hexdigest() + ".json")
    cache.parent.mkdir()
    cache.write_text(json.dumps({"status": "ok", "provider": "exa", "results": []}))
    r = Research(tmp_path)
    blocked = Mock(); blocked.json.return_value = {"unresponsive_engines": [["google", "CAPTCHA"]], "results": []}
    working = Mock(); working.json.return_value = {"results": [{"url": "https://company.sa/", "title": "Company"}], "unresponsive_engines": []}
    r.session = Mock(); r.session.get.side_effect = [blocked, working]
    result = r.search(query)
    assert result["provider"] == "searxng-yandex"
    assert result["results"][0]["url"] == "https://company.sa/"
    assert r.stats["disabled_engines"] == ["google"]


def test_registry_acronym_can_match_own_public_brand():
    row = {"Company_or_Office": "National Housing Company (NHC)", "Arabic_Name": "الشركة الوطنية للاسكان"}
    page = {"url": "https://nhc.sa/", "html": "<title>NHC</title>", "text": "NHC Real Estate Saudi Arabia"}
    assert official_home_matches(row, page, "nhc.sa")


def test_resolve_preserves_search_url_and_checks_later_queries(tmp_path):
    from career_engine.rega_enrichment.continuous import Research, parse_page
    r = Research(tmp_path)
    r.search = Mock(side_effect=[{"status": "ok", "results": [
        {"url": f"https://irrelevant{i}.sa/", "title": "Other"} for i in range(8)]},
        {"status": "ok", "results": [{"url": "http://armal.sa/en", "title": "Armal"}]}])
    good = parse_page("<title>Armal</title><p>Saudi real estate development</p>", "http://armal.sa/en")
    r.fetch = Mock(side_effect=lambda url: good if url == "http://armal.sa/en" else None)
    r.render_public = Mock(return_value=None)
    host, pages, evidence, status = r.resolve({"Arabic_Name": "ارمال", "Company_or_Office": "Armal Real Estate"})
    assert status == "confirmed" and host == "armal.sa"
    r.fetch.assert_any_call("http://armal.sa/en")


def test_ksa_branding_is_saudi_evidence():
    from career_engine.rega_enrichment.continuous import parse_page
    page = parse_page("<title>Ajdan | Premier Real Estate Developer KSA</title>", "https://ajdan.com/")
    assert official_home_matches({"Company_or_Office": "Ajdan Real Estate Development"}, page, "ajdan.com")
