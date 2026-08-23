import json
import subprocess
import sys
from pathlib import Path

from career_engine.rega_enrichment.provider_clients import (
    AnymailFinderClient,
    DataForSEOClient,
    OutscraperClient,
    ProviderBudget,
    TombaClient,
    ZeroBounceClient,
    classify_local_part,
)
from career_engine.rega_enrichment.provider_waterfall import (
    promote_official_contact,
    run_configured_domain_waterfall,
    run_waterfall,
    validate_official_intended_contact,
)

ROOT = Path(__file__).parents[1]


class FakeResponse:
    def __init__(self, payload, status=200):
        self.status = status
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def opener_with(payload, captured=None, status=200):
    def _open(request, timeout=15):
        if captured is not None:
            captured.append(request)
        return FakeResponse(payload, status=status)
    return _open


def test_budget_defaults_to_zero_billable_calls():
    budget = ProviderBudget()
    assert not budget.permit(billable=True)
    assert budget.calls == 0


def test_dataforseo_search_parses_organic_and_cost_without_exposing_credential():
    payload = {
        "tasks": [{
            "cost": 0.0006,
            "result": [{"items": [
                {"type": "organic", "url": "https://example.com/careers", "title": "Careers", "description": "Join us"},
                {"type": "people_also_ask", "url": "https://ignore.example"},
            ]}],
        }]
    }
    captured = []
    client = DataForSEOClient("login:password", opener=opener_with(payload, captured))
    budget = ProviderBudget(allow_existing_credit=True, max_calls=1)
    result = client.search("Example careers Saudi Arabia", budget)
    assert result[0]["metadata"]["url"] == "https://example.com/careers"
    assert result[0]["metadata"]["task_cost"] == 0.0006
    assert result[0]["cost_status"] == "charged"
    serialized = json.dumps(result)
    assert "login" not in serialized and "password" not in serialized
    assert captured[0].get_header("Authorization").startswith("Basic ")


def test_tomba_missing_secret_is_isolated_and_no_call_occurs():
    called = []
    client = TombaClient("key-only", "", opener=opener_with({}, called))
    probe = client.rate_limits()
    assert probe["status"] == "missing_required_secret"
    result = client.domain_search("example.com", ProviderBudget(allow_existing_credit=True, max_calls=1, max_domains=1))
    assert result[0]["status"] == "missing_required_secret"
    assert called == []


def test_tomba_domain_search_preserves_candidate_sources():
    payload = {"data": {"emails": [{
        "email": "jane@example.com",
        "first_name": "Jane",
        "last_name": "Doe",
        "position": "Talent Acquisition Manager",
        "department": "HR",
        "seniority": "manager",
        "verification": {"status": "valid"},
        "sources": [{"uri": "https://example.com/team"}],
    }]}}
    client = TombaClient("key", "secret", opener=opener_with(payload))
    result = client.domain_search("example.com", ProviderBudget(allow_existing_credit=True, max_calls=1, max_domains=1))
    item = result[0]
    assert item["status"] == "candidate"
    assert item["metadata"]["email"] == "jane@example.com"
    assert item["metadata"]["position"] == "Talent Acquisition Manager"
    assert item["metadata"]["source_urls"] == ["https://example.com/team"]
    assert item["metadata"]["mailbox_class"] == "person"


def test_outscraper_domain_contacts_preserve_public_source_refs():
    payload = {"status": "Success", "data": [{
        "domain": "example.com",
        "emails": [{"value": "hr@example.com", "sources": [{"ref": "https://example.com/careers"}]}],
    }]}
    captured = []
    client = OutscraperClient("secret-key", opener=opener_with(payload, captured))
    result = client.domain_contacts("example.com", ProviderBudget(allow_existing_credit=True, max_calls=1, max_domains=1))
    item = result[0]
    assert item["metadata"]["email"] == "hr@example.com"
    assert item["metadata"]["mailbox_class"] == "role_candidate"
    assert item["metadata"]["source_urls"] == ["https://example.com/careers"]
    assert item["status"] == "candidate"
    assert "secret-key" not in json.dumps(result)
    assert captured[0].get_header("X-api-key") == "secret-key"


def test_anymail_uses_only_valid_emails_and_actual_credit_field():
    payload = {
        "credits_charged": 1,
        "email_status": "valid",
        "emails": ["risky@example.com", "valid@example.com"],
        "valid_emails": ["valid@example.com"],
    }
    client = AnymailFinderClient("key", opener=opener_with(payload))
    result = client.company("example.com", ProviderBudget(allow_existing_credit=True, max_calls=1, max_credits=1, max_domains=1))
    assert len(result) == 1
    assert result[0]["metadata"]["email"] == "valid@example.com"
    assert result[0]["metadata"]["credits_charged"] == 1
    assert result[0]["metadata"]["email_status"] == "valid"


def test_anymail_not_found_is_free_and_not_a_contact():
    payload = {"credits_charged": 0, "email_status": "not_found", "emails": [], "valid_emails": []}
    client = AnymailFinderClient("key", opener=opener_with(payload))
    result = client.company("example.com", ProviderBudget(allow_existing_credit=True, max_calls=1, max_credits=1, max_domains=1))
    assert result[0]["status"] == "not_found"
    assert result[0]["cost_status"] == "free"
    assert result[0]["metadata"]["credits_charged"] == 0


def test_zerobounce_uses_key_in_request_but_never_returns_it():
    captured = []
    client = ZeroBounceClient("zb-secret", opener=opener_with({"status": "valid"}, captured))
    result = client.validate(
        "target@example.com",
        ProviderBudget(allow_existing_credit=True, max_calls=1, max_credits=1),
        allow_existing_credit=True,
        intended_outreach=True,
    )
    assert result[0]["status"] == "valid"
    assert result[0]["metadata"]["safe_to_send"] is True
    assert "zb-secret" in captured[0].full_url
    assert "zb-secret" not in json.dumps(result)
    assert result[0]["source_url"].endswith("/validate")


def test_zerobounce_validation_is_gated_for_non_outreach():
    called = []
    client = ZeroBounceClient("zb-secret", opener=opener_with({}, called))
    result = client.validate("x@example.com", ProviderBudget(allow_existing_credit=True, max_calls=1, max_credits=1))
    assert result[0]["status"] == "not_permitted"
    assert called == []


def test_mailbox_classification_never_equates_generic_with_recruitment():
    assert classify_local_part("hr@example.com") == "role_candidate"
    assert classify_local_part("careers@example.com") == "role_candidate"
    assert classify_local_part("info@example.com") == "generic"
    assert classify_local_part("sales@example.com") == "generic"
    assert classify_local_part("jane.doe@example.com") == "person"


def test_waterfall_isolates_failures_and_keeps_contacts_non_official():
    rows = [{"provider": "x", "source_url": "https://provider.invalid", "evidence": "lookup", "metadata": {"email": "hr@example.com"}}]
    result = run_waterfall([("broken", lambda: (_ for _ in ()).throw(RuntimeError("boom"))), ("working", lambda: rows)])
    assert result.provider_statuses == [{"provider": "broken", "status": "network_failed", "error_type": "RuntimeError"}, {"provider": "working", "status": "success"}]
    assert result.contacts[0].provider == "working"
    assert not result.contacts[0].outreach_ready
    assert result.official_recruitment_contacts == []


def test_official_contact_requires_separate_evidence():
    contact = promote_official_contact("careers@example.com", "https://example.com/careers", "Official careers page lists this address")
    assert contact.outreach_ready
    assert contact.provider == "rega-official"


def test_configured_domain_waterfall_is_executable_fail_soft_and_non_promoting():
    class MissingTomba:
        def domain_search(self, domain, budget):
            return [{"status": "missing_required_secret", "source_url": "https://api.tomba.io/v1/domain-search", "evidence": "", "metadata": {}}]

    class BrokenOutscraper:
        def domain_contacts(self, domain, budget):
            raise RuntimeError("provider unavailable")

    class WorkingAnymail:
        def company(self, domain, budget):
            return [{
                "status": "candidate",
                "source_url": "https://api.anymailfinder.com/v5.1/find-email/company",
                "evidence": "provider candidate",
                "cost_status": "free",
                "metadata": {"email": "hr@example.com"},
            }]

    class MustNotRunZeroBounce:
        def validate(self, *args, **kwargs):
            raise AssertionError("provider-only candidates must not be validated for outreach")

    result = run_configured_domain_waterfall(
        "www.example.com",
        allow_existing_credit=True,
        env={},
        clients={
            "tomba": MissingTomba(),
            "outscraper": BrokenOutscraper(),
            "anymailfinder": WorkingAnymail(),
            "zerobounce": MustNotRunZeroBounce(),
        },
    )
    assert result.provider_statuses == [
        {"provider": "tomba", "status": "missing_required_secret"},
        {"provider": "outscraper", "status": "network_failed", "error_type": "RuntimeError"},
        {"provider": "anymailfinder", "status": "success"},
    ]
    assert [contact.value for contact in result.contacts] == ["hr@example.com"]
    assert result.official_recruitment_contacts == []
    assert not result.contacts[0].outreach_ready


def test_zerobounce_only_runs_after_separate_official_promotion():
    calls = []

    class FakeZeroBounce:
        def validate(self, email, budget, *, allow_existing_credit=False, intended_outreach=False):
            calls.append((email, allow_existing_credit, intended_outreach))
            return [{"provider": "zerobounce", "status": "valid", "metadata": {"safe_to_send": True}}]

    contact, validation = validate_official_intended_contact(
        "careers@example.com",
        "https://example.com/careers",
        "Official careers page lists this address",
        allow_existing_credit=True,
        env={},
        client=FakeZeroBounce(),
    )
    assert contact.outreach_ready
    assert calls == [("careers@example.com", True, True)]
    assert validation[0]["status"] == "valid"


def test_provider_cli_has_safe_non_billable_executable_path():
    proc = subprocess.run(
        [sys.executable, "-m", "career_engine.rega_enrichment.cli", "providers", "--domain", "example.com"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["domain"] == "example.com"
    assert payload["outreach_ready_count"] == 0
    assert payload["official_promotion_performed"] is False
    assert {row["provider"] for row in payload["provider_statuses"]} == {"tomba", "outscraper", "anymailfinder"}


def test_provider_runtime_manifests_use_canonical_value_free_schema():
    expected = {
        "dataforseo.json": ["DATAFORSEO_API_KEY"],
        "tomba.json": ["TOMBA_API_KEY", "TOMBA_API_SECRET"],
        "apify.json": ["APIFY_API_KEY"],
        "outscraper.json": ["OUTSCRAPER_API_KEY"],
        "anymailfinder.json": ["ANYMAILFINDER_API_KEY"],
        "zerobounce.json": ["ZEROBOUNCE_API_KEY"],
    }
    for name, keys in expected.items():
        payload = json.loads((ROOT / "runtime" / "providers" / name).read_text())
        assert payload["contains_secret_values"] is False
        assert payload["folder"] == "services"
        assert payload["keys"] == keys
        assert payload["service"].startswith("career-provider-")
        assert "required_env" not in payload and "value_free" not in payload
