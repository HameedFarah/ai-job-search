"""Deterministic runtime safety tests for the portal-first REGA scanner."""
from datetime import datetime
import json

from runtime import rega_priority_scan as scanner
from runtime import run_outscraper_monitored as monitored
from runtime import run_rega_priority_scan as entrypoint
from runtime.rega_priority_scan import (
    _blocked_candidate,
    build_dedupe_state,
    normalized_company,
    select_best_eligible_candidate,
)


def test_dedupe_state_covers_all_live_authorities():
    master = [
        {"Email": "master@example.com", "Email_Normalized": "MASTER@example.com"},
    ]
    send_queue = [
        {
            "Email": "queued@example.com",
            "Company_or_Office": "Queued Co",
            "Send_State": "READY_VERIFIED_OUTSCRAPER",
        },
    ]
    auto_queue = [
        {"Email": "auto@example.com", "Company_or_Office": "Auto Co", "Status": "PENDING"},
    ]
    sent = [
        {
            "Recipient_Email": "sent@example.com",
            "Company_or_Office": "Sent Co",
            "Delivery_State": "SENT",
            "Bounce_State": "",
        },
        {
            "Recipient_Email": "dead@example.com",
            "Company_or_Office": "Bounced Co",
            "Delivery_State": "BOUNCED",
            "Bounce_State": "PERMANENT",
        },
    ]

    state = build_dedupe_state(master, send_queue, auto_queue, sent)

    assert state["known_emails"] == {
        "master@example.com",
        "queued@example.com",
        "auto@example.com",
        "sent@example.com",
        "dead@example.com",
    }
    assert state["permanent_bounces"] == {"dead@example.com"}
    assert state["contacted_companies"] == {"sent co"}
    assert state["queued_companies"] == {"queued co", "auto co"}


def test_hold_or_failed_queue_does_not_block_company_replacement():
    state = build_dedupe_state(
        [],
        [
            {
                "Email": "held@example.com",
                "Company_or_Office": "Replacement Co",
                "Send_State": "HOLD_OUTSCRAPER_UNKNOWN",
            },
            {
                "Email": "dead@example.com",
                "Company_or_Office": "Bounced Co",
                "Send_State": "FAILED_PERMANENT",
            },
        ],
        [{"Email": "hold-auto@example.com", "Company_or_Office": "Auto Hold Co", "Status": "HOLD"}],
        [],
    )
    assert state["queued_companies"] == set()
    assert {"held@example.com", "dead@example.com", "hold-auto@example.com"}.issubset(state["known_emails"])


def test_permanent_bounce_is_stronger_than_generic_duplicate():
    reason = _blocked_candidate(
        "dead@example.com",
        {"dead@example.com"},
        {"dead@example.com"},
    )
    assert reason == "permanent_bounce_exact_mailbox_blocked"


def test_known_email_is_blocked_without_inventing_bounce_state():
    reason = _blocked_candidate(
        "known@example.com",
        {"known@example.com"},
        set(),
    )
    assert reason == "email_already_known_or_queued"


def test_new_email_is_not_blocked():
    assert _blocked_candidate("new@example.com", set(), set()) == ""


def test_bounced_company_is_not_marked_successfully_contacted():
    state = build_dedupe_state(
        [],
        [],
        [],
        [{
            "Recipient_Email": "dead@example.com",
            "Company_or_Office": "Retry Company",
            "Delivery_State": "BOUNCED",
            "Bounce_State": "PERMANENT",
        }],
    )
    assert normalized_company("Retry Company") not in state["contacted_companies"]


def test_best_eligible_candidate_skips_bounced_and_known_mailboxes():
    candidates = [
        {"email": "careers@example.com", "rank": 0, "kind": "recruitment"},
        {"email": "hr@example.com", "rank": 0, "kind": "recruitment"},
        {"email": "info@example.com", "rank": 2, "kind": "general"},
    ]
    selected, rejected = select_best_eligible_candidate(
        candidates,
        {"careers@example.com", "hr@example.com"},
        {"careers@example.com"},
    )
    assert selected is not None
    assert selected["email"] == "info@example.com"
    assert rejected == {"permanent_bounce": 1, "known_email": 1}


def test_no_eligible_candidate_is_explicit():
    selected, rejected = select_best_eligible_candidate(
        [{"email": "dead@example.com", "rank": 0, "kind": "recruitment"}],
        {"dead@example.com"},
        {"dead@example.com"},
    )
    assert selected is None
    assert rejected["permanent_bounce"] == 1


def test_reserve_blocked_paid_lookup_is_persisted_without_provider_retry(tmp_path, monkeypatch):
    root = tmp_path / "scan"
    root.mkdir()
    checkpoint = {
        "master_id": "CE-00999",
        "company": "Reserve Co",
        "state": "complete",
        "result": "reserve_reached_before_validation",
        "domain": "example.com",
    }
    (root / "checkpoint.jsonl").write_text(json.dumps(checkpoint) + "\n", encoding="utf-8")

    row = {"Master_ID": "CE-00999", "Company_or_Office": "Reserve Co", "__row_number": "2"}
    calls = []
    monkeypatch.setattr(entrypoint, "rclone_access_token", lambda _remote: "token")
    monkeypatch.setattr(entrypoint, "read_master", lambda _token: (["Master_ID"], [row]))
    monkeypatch.setattr(
        entrypoint,
        "persist_no_contact",
        lambda token, headers, master_row, domain, detail: calls.append((token, master_row["Master_ID"], domain, detail)),
    )

    assert entrypoint._persist_reserve_blocked_terminal(root) == 1
    assert len(calls) == 1
    assert calls[0][1:3] == ("CE-00999", "example.com")
    assert "reserve blocked mailbox validation" in calls[0][3]
    persisted = entrypoint.load_jsonl(root / "checkpoint.jsonl")["CE-00999"]
    assert persisted["state"] == "no_route"
    assert persisted["result"] == "reserve_reached_before_validation_persisted"
    assert persisted["master_tracker_persisted"] is True


def test_company_name_normalization_is_stable_for_dedupe():
    assert normalized_company("  Example   Development  ") == "example development"


def test_campaign_freshness_date_uses_riyadh_timezone(monkeypatch):
    real_datetime = datetime

    class FixedDateTime:
        @classmethod
        def now(cls, tz):
            assert getattr(tz, "key", "") == "Asia/Riyadh"
            return real_datetime(2026, 9, 7, 0, 5, tzinfo=tz)

    monkeypatch.setattr(scanner, "datetime", FixedDateTime)
    assert scanner.today() == "2026-09-07"


def test_zero_search_candidates_remain_retryable(monkeypatch):
    row = {
        "Master_ID": "CE-ZERO",
        "Source_Record_ID": "999",
        "Company_or_Office": "Zero Results Development",
        "Arabic_Name": "شركة نتائج صفر",
        "Region": "Riyadh",
        "Address_or_Website": "",
        "Source_Verification": "Not researched",
        "Source_Status": "Not researched",
    }
    monkeypatch.setattr(scanner, "searxng_qwant_search", lambda query, limit=5: [])

    domain, detail = scanner.free_discover_domain(row)

    assert domain == ""
    assert detail == {
        "basis": "free_discovery_unavailable",
        "candidate_count": 0,
        "retryable": True,
    }


def test_simple_company_name_fallback_can_recover_domain(monkeypatch):
    row = {
        "Master_ID": "CE-JED",
        "Source_Record_ID": "513",
        "Company_or_Office": "Central Jeddah Development",
        "Arabic_Name": "شركة وسط جدة للتطوير",
        "Region": "Jeddah",
        "Address_or_Website": "",
        "Source_Verification": "Not researched",
        "Source_Status": "Not researched",
    }
    calls = []

    def fake_search(query, limit=5):
        calls.append(query)
        if query == "Central Jeddah Development":
            return [{
                "url": "https://www.jeddahcentral.com/",
                "title": "Jeddah Central Development Company - Home",
                "description": "Official Jeddah Central Development Company website.",
            }]
        return []

    def fake_verify(candidate, company):
        candidate.verification_status = "confirmed"
        candidate.verification_score = 24
        candidate.verification_method = "hostname_token_match,title_token_match"
        return candidate

    monkeypatch.setattr(scanner, "searxng_qwant_search", fake_search)
    monkeypatch.setattr(scanner, "verify_candidate", fake_verify)

    domain, detail = scanner.free_discover_domain(row)

    assert calls[-1] == "Central Jeddah Development"
    assert domain == "jeddahcentral.com"
    assert detail["basis"] == "searxng_qwant_plus_direct_identity_verification"


def test_discovery_unavailable_restores_nonterminal_sheet_state(monkeypatch):
    row = {"Master_ID": "CE-ZERO", "__row_number": "2", "Notes": ""}
    captured = {}

    def fake_update(token, headers, master_row, updates):
        captured.update(updates)

    monkeypatch.setattr(scanner, "update_master_fields", fake_update)
    scanner.persist_discovery_unavailable("token", [], row)

    assert captured["Send_Eligibility"] == "NO_EMAIL_DISCOVERED_NOT_RESEARCHED"
    assert "temporarily unavailable" in captured["Source_Status"].lower()
    assert "retry" in captured["Next_Action"].lower()
    assert "not classified as no-domain" in captured["Notes"]


def test_free_discovery_is_bounded_to_high_signal_candidates(monkeypatch):
    row = {
        "Master_ID": "CE-BOUND",
        "Source_Record_ID": "515",
        "Company_or_Office": "Bounded Example Development",
        "Arabic_Name": "شركة المثال المحدودة",
        "Region": "Riyadh",
        "Address_or_Website": "",
        "Source_Verification": "Not researched",
        "Source_Status": "Not researched",
    }
    calls = []

    def fake_search(query, limit=3):
        calls.append((query, limit))
        marker = len(calls)
        return [
            {"url": f"https://candidate{marker}{idx}.example/", "title": "Candidate", "description": ""}
            for idx in range(1, 4)
        ]

    def fake_verify(candidate, company):
        candidate.verification_status = "rejected"
        candidate.verification_score = 0
        candidate.verification_method = "insufficient_identity"
        return candidate

    monkeypatch.setattr(scanner, "searxng_qwant_search", fake_search)
    monkeypatch.setattr(scanner, "verify_candidate", fake_verify)
    domain, detail = scanner.free_discover_domain(row)

    assert domain == ""
    assert detail["basis"] == "free_discovery_no_confirmed_domain"
    assert detail["candidate_count"] == 6
    assert len(calls) == 2
    assert all(limit == 3 for _, limit in calls)
    assert "official website" in calls[0][0]


def test_maps_fallback_requires_independent_identity_verification(monkeypatch):
    row = {
        "Master_ID": "CE-JED",
        "Source_Record_ID": "513",
        "Company_or_Office": "Central Jeddah Development",
        "Arabic_Name": "شركة وسط جدة للتطوير",
        "Region": "Jeddah",
        "Address_or_Website": "",
        "Source_Verification": "Not researched",
        "Source_Status": "Not researched",
    }

    class FakeClient:
        def maps_businesses(self, query, budget, *, limit=3):
            assert query == "Central Jeddah Development, Jeddah, Saudi Arabia"
            assert limit == 3
            return [{
                "status": "candidate",
                "metadata": {
                    "name": "Jeddah Central Development Company",
                    "site": "https://www.jeddahcentral.com/",
                    "full_address": "Jeddah, Saudi Arabia",
                    "category": "Real estate developer",
                },
            }]

    def fake_verify(candidate, company):
        assert candidate.engine == "outscraper-google-maps"
        assert candidate.url == "https://www.jeddahcentral.com/"
        candidate.verification_status = "confirmed"
        candidate.verification_score = 24
        candidate.verification_method = "hostname_concat_brand,title_token_match"
        return candidate

    monkeypatch.setattr(scanner, "verify_candidate", fake_verify)
    domain, detail = scanner.maps_discover_domain(FakeClient(), row)

    assert domain == "jeddahcentral.com"
    assert detail["basis"] == "outscraper_maps_plus_direct_identity_verification"
    assert detail["verification_score"] == 24
    assert detail["websites_evaluated"] == 1


def test_maps_fallback_rejects_provider_business_without_verified_identity(monkeypatch):
    row = {
        "Master_ID": "CE-JED",
        "Source_Record_ID": "513",
        "Company_or_Office": "Central Jeddah Development",
        "Arabic_Name": "شركة وسط جدة للتطوير",
        "Region": "Jeddah",
    }

    class FakeClient:
        def maps_businesses(self, query, budget, *, limit=3):
            return [{
                "status": "candidate",
                "metadata": {
                    "name": "Unrelated Central Trading",
                    "site": "https://unrelated.example/",
                    "full_address": "Jeddah, Saudi Arabia",
                },
            }]

    def fake_verify(candidate, company):
        candidate.verification_status = "rejected"
        candidate.verification_score = 2
        candidate.verification_method = "insufficient_identity"
        return candidate

    monkeypatch.setattr(scanner, "verify_candidate", fake_verify)
    domain, detail = scanner.maps_discover_domain(FakeClient(), row)

    assert domain == ""
    assert detail["basis"] == "outscraper_maps_no_confirmed_domain"
    assert detail["retryable"] is False
    assert detail["websites_evaluated"] == 1


def test_maps_provider_failure_remains_retryable():
    row = {
        "Master_ID": "CE-FAIL",
        "Source_Record_ID": "514",
        "Company_or_Office": "Provider Failure Development",
        "Arabic_Name": "",
        "Region": "Riyadh",
    }

    class FakeClient:
        def maps_businesses(self, query, budget, *, limit=3):
            return [{"status": "failed", "metadata": {}}]

    domain, detail = scanner.maps_discover_domain(FakeClient(), row)

    assert domain == ""
    assert detail["basis"] == "outscraper_maps_provider_failure"
    assert detail["retryable"] is True
    assert detail["provider_status"] == "failed"


def test_searxng_html_fallback_extracts_bounded_result_fields():
    html = """
    <div id="urls">
      <article class="result result-default category-general">
        <a href="https://www.jeddahcentral.com/" class="url_header">site</a>
        <h3><a href="https://www.jeddahcentral.com/">Jeddah Central Development Company - Home</a></h3>
        <p class="content">Official Jeddah Central Development Company website.</p>
      </article>
    </div>
    """
    results = monitored._normalize_html_results(html, limit=1)
    assert results == [{
        "url": "https://www.jeddahcentral.com/",
        "title": "Jeddah Central Development Company - Home",
        "description": "Official Jeddah Central Development Company website.",
        "engine": "searxng-html",
    }]
