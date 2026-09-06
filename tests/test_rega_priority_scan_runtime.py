"""Deterministic runtime safety tests for the portal-first REGA scanner."""
from runtime.rega_priority_scan import _blocked_candidate, build_dedupe_state, normalized_company


def test_dedupe_state_covers_all_live_authorities():
    master = [
        {"Email": "master@example.com", "Email_Normalized": "MASTER@example.com"},
    ]
    send_queue = [
        {"Email": "queued@example.com", "Company_or_Office": "Queued Co"},
    ]
    auto_queue = [
        {"Email": "auto@example.com", "Company_or_Office": "Auto Co"},
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


def test_company_name_normalization_is_stable_for_dedupe():
    assert normalized_company("  Example   Development  ") == "example development"
