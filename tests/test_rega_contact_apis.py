"""Tests for career_engine.rega_enrichment.contact_apis.

All network calls are mocked — no live paid/provider calls.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from career_engine.rega_enrichment.contact_apis import (
    ContactAPIs,
    _scrub_recursive,
    _url_scrub_api_key,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FAKE_HUNTER_KEY = "hunter-api-key-SECRET123"
FAKE_PROSPEO_KEY = "prospeo-key-SECRET456"
FAKE_SNOV_USER = "snov-user-SECRET789"
FAKE_SNOV_SECRET = "snov-secret-SECRET012"


class _FakeResponse:
    def __init__(self, payload: Any, status: int = 200) -> None:
        self.status_code = status
        self._payload = payload

    def json(self) -> Any:
        return self._payload


class _FakeSession:
    """Records requests and returns pre-configured responses."""

    def __init__(self, responses: list[tuple[int, Any]] | None = None) -> None:
        self._responses = list(responses or [])
        self._calls: list[dict[str, Any]] = []
        self._idx = 0

    def request(self, method: str, url: str, **kwargs: Any) -> _FakeResponse:
        self._calls.append({"method": method, "url": url, **kwargs})
        if self._idx < len(self._responses):
            status, payload = self._responses[self._idx]
            self._idx += 1
            return _FakeResponse(payload, status)
        return _FakeResponse(None, 500)

    @property
    def last_call(self) -> dict[str, Any]:
        return self._calls[-1] if self._calls else {}

    def get_calls(self, method: str | None = None, url_contains: str | None = None) -> list[dict[str, Any]]:
        out = self._calls
        if method:
            out = [c for c in out if c.get("method") == method]
        if url_contains:
            out = [c for c in out if url_contains in c.get("url", "")]
        return out


def _make_api(
    tmp_path: Path,
    session: _FakeSession,
    budgets: dict[str, float] | None = None,
    *,
    env: dict[str, str] | None = None,
) -> ContactAPIs:
    if budgets is None:
        budgets = {"hunter": 10.0, "prospeo": 3.0, "snov": 10.0}
    env = env or {}
    # Set env keys before construction so __init__ picks them up
    os.environ["HUNTER_IO_API_KEY"] = env.get("HUNTER_IO_API_KEY", FAKE_HUNTER_KEY)
    os.environ["PROSPEO_API_KEY"] = env.get("PROSPEO_API_KEY", FAKE_PROSPEO_KEY)
    os.environ["SNOV_IO_API_USER_ID"] = env.get("SNOV_IO_API_USER_ID", FAKE_SNOV_USER)
    os.environ["SNOV_IO_API_SECRET"] = env.get("SNOV_IO_API_SECRET", FAKE_SNOV_SECRET)
    return ContactAPIs(
        cache_dir=tmp_path,
        budgets=budgets,
        session=session,  # type: ignore[arg-type]
    )


def _clean_env() -> None:
    for k in ("HUNTER_IO_API_KEY", "PROSPEO_API_KEY", "SNOV_IO_API_USER_ID", "SNOV_IO_API_SECRET"):
        os.environ.pop(k, None)


@pytest.fixture(autouse=True)
def _clear_env():
    _clean_env()
    yield
    _clean_env()


# ===================================================================
# Scrubbing / security tests
# ===================================================================

class TestScrubbing:
    def test_scrub_recursive_removes_api_key_keys(self):
        obj = {"api_key": "SECRET", "data": {"key": "also-secret", "value": "ok"}, "list": [{"secret": "x", "safe": "y"}]}
        result = _scrub_recursive(obj)
        assert "api_key" not in result
        assert "key" not in result["data"]
        assert result["data"]["value"] == "ok"
        assert result["list"][0]["safe"] == "y"
        assert "secret" not in result["list"][0]

    def test_url_scrub_removes_api_key_param(self):
        url = "https://api.hunter.io/v2/domain-search?domain=ex.com&api_key=SECRET123&limit=10"
        cleaned = _url_scrub_api_key(url)
        assert "SECRET123" not in cleaned
        assert "domain=ex.com" in cleaned
        assert "limit=10" in cleaned

    def test_no_secret_in_cache_files(self, tmp_path):
        session = _FakeSession([(200, {
            "data": {"requests": {"credits": {"remaining": 50}}},
        })])
        api = _make_api(tmp_path, session)
        api.account_balances()
        # Scan all JSON files in cache_dir for secret strings
        for p in tmp_path.glob("*.json"):
            content = p.read_text(encoding="utf-8", errors="ignore")
            assert FAKE_HUNTER_KEY not in content
            assert FAKE_PROSPEO_KEY not in content
            assert FAKE_SNOV_USER not in content
            assert FAKE_SNOV_SECRET not in content

    def test_no_secret_in_error_messages(self, tmp_path):
        session = _FakeSession([(0, None)])
        api = _make_api(tmp_path, session)
        result = api.hunter_contacts("example.com")
        assert result == []
        serialized = json.dumps(api.stats)
        assert FAKE_HUNTER_KEY not in serialized

    def test_scrub_preserves_safe_keys(self):
        obj = {"email": "hr@example.com", "name": "Jane", "status": "success", "api_key": "NOPE"}
        result = _scrub_recursive(obj)
        assert result["email"] == "hr@example.com"
        assert result["name"] == "Jane"
        assert result["status"] == "success"
        assert "api_key" not in result


# ===================================================================
# account_balances
# ===================================================================

class TestAccountBalances:
    def test_hunter_balance_success(self, tmp_path):
        session = _FakeSession([(200, {
            "data": {"requests": {"credits": {"remaining": 42}}},
        })])
        api = _make_api(tmp_path, session)
        balances = api.account_balances()
        assert balances["hunter"]["status"] == "success"
        assert balances["hunter"]["remaining"] == 42

    def test_prospeo_balance_success(self, tmp_path):
        session = _FakeSession([
            # Hunter
            (200, {"data": {"requests": {"credits": {"remaining": 50}}}}),
            # Prospeo
            (200, {
                "error": False,
                "response": {"remaining_credits": 100, "current_plan": "STARTER"},
            }),
            # Snov token + balance
            (200, {"access_token": "tok"}),
            (200, {"data": {"balance": 0}}),
        ])
        api = _make_api(tmp_path, session)
        balances = api.account_balances()
        assert balances["prospeo"]["status"] == "success"
        assert balances["prospeo"]["remaining"] == 100

    def test_snov_balance_success(self, tmp_path):
        session = _FakeSession([
            # Hunter
            (200, {"data": {"requests": {"credits": {"remaining": 50}}}}),
            # Prospeo
            (200, {"error": False, "response": {"remaining_credits": 0}}),
            # Snov token + balance
            (200, {"access_token": "tok_abc123"}),
            (200, {"data": {"balance": 500}}),
        ])
        api = _make_api(tmp_path, session)
        balances = api.account_balances()
        assert balances["snov"]["status"] == "success"
        assert balances["snov"]["remaining"] == 500

    def test_missing_credentials_reported(self, tmp_path):
        _clean_env()
        os.environ["HUNTER_IO_API_KEY"] = ""
        os.environ["PROSPEO_API_KEY"] = ""
        os.environ["SNOV_IO_API_USER_ID"] = ""
        os.environ["SNOV_IO_API_SECRET"] = ""
        session = _FakeSession()
        api = ContactAPIs(cache_dir=tmp_path, budgets={}, session=session)  # type: ignore[arg-type]
        balances = api.account_balances()
        assert balances["hunter"]["status"] == "missing_credential"
        assert balances["prospeo"]["status"] == "missing_credential"
        assert balances["snov"]["status"] == "missing_credential"

    def test_auth_failure_counted(self, tmp_path):
        session = _FakeSession([(401, {"error": "unauthorized"})])
        api = _make_api(tmp_path, session)
        balances = api.account_balances()
        assert balances["hunter"]["status"] == "auth_failed"
        assert api.stats["errors"].get("hunter", 0) >= 1


# ===================================================================
# hunter_contacts
# ===================================================================

class TestHunterContacts:
    def test_basic_domain_search(self, tmp_path):
        session = _FakeSession([(200, {
            "data": {
                "emails": [{
                    "value": "hr@example.com",
                    "first_name": "Jane",
                    "last_name": "Doe",
                    "position": "HR Manager",
                    "sources": [{"uri": "https://example.com/team"}],
                    "verification": {"status": "valid"},
                }],
            },
        })])
        api = _make_api(tmp_path, session)
        contacts = api.hunter_contacts("example.com")
        assert len(contacts) == 1
        assert contacts[0]["email"] == "hr@example.com"
        assert contacts[0]["name"] == "Jane Doe"
        assert contacts[0]["title"] == "HR Manager"
        assert contacts[0]["provider"] == "hunter"
        assert contacts[0]["domain"] == "example.com"
        assert contacts[0]["source_urls"] == ["https://example.com/team"]
        assert contacts[0]["provider_verification"] == {"status": "valid"}

    def test_cache_hit_skips_network(self, tmp_path):
        session = _FakeSession([(200, {
            "data": {"emails": [{"value": "a@b.com", "first_name": "A"}]},
        })])
        api = _make_api(tmp_path, session)
        api.hunter_contacts("b.com")
        assert len(session._calls) == 1
        # Second call should hit cache
        result2 = api.hunter_contacts("b.com")
        assert len(session._calls) == 1  # no new request
        assert len(result2) == 1
        assert result2[0]["email"] == "a@b.com"

    def test_empty_result_not_cached_as_completed(self, tmp_path):
        session = _FakeSession([(200, {"data": {"emails": []}})])
        api = _make_api(tmp_path, session)
        result = api.hunter_contacts("empty.com")
        assert result == []
        # Even empty results should be cached as completed
        assert len(session._calls) == 1

    def test_auth_halt_does_not_consume_credits(self, tmp_path):
        session = _FakeSession([(401, {"error": "unauthorized"})])
        api = _make_api(tmp_path, session)
        result = api.hunter_contacts("blocked.com")
        assert result == []
        # Budget should be released
        tracker = api._budget_tracker
        assert tracker.reserved("hunter") == 0.0


# ===================================================================
# hunter verify
# ===================================================================

class TestHunterVerify:
    def test_valid_deliverable_is_safe(self, tmp_path):
        session = _FakeSession([(200, {
            "data": {"status": "valid", "result": "deliverable"},
        })])
        api = _make_api(tmp_path, session)
        v = api.verify("good@example.com")
        assert v["status"] == "RECEIVING"
        assert v["safe_to_send"] is True
        assert v["email"] == "good@example.com"
        assert "checked_at" in v

    def test_catchall_is_blocked(self, tmp_path):
        session = _FakeSession([(200, {
            "data": {"status": "accept_all", "result": "accept_all"},
        })])
        api = _make_api(tmp_path, session)
        v = api.verify("catchall@example.com")
        assert v["status"] == "UNKNOWN"
        assert v["safe_to_send"] is False

    def test_unknown_status_is_blocked(self, tmp_path):
        session = _FakeSession([(200, {
            "data": {"status": "unknown", "result": "unknown"},
        })])
        api = _make_api(tmp_path, session)
        v = api.verify("unknown@example.com")
        assert v["status"] == "UNKNOWN"
        assert v["safe_to_send"] is False

    def test_disposable_is_blocked(self, tmp_path):
        session = _FakeSession([(200, {
            "data": {"status": "valid", "disposable": True, "result": "valid but disposable"},
        })])
        api = _make_api(tmp_path, session)
        v = api.verify("temp@throwaway.com")
        # status maps to RECEIVING but safe_to_send is False because disposable
        assert v["status"] == "RECEIVING"
        assert v["safe_to_send"] is False

    def test_invalid_is_not_receiving(self, tmp_path):
        session = _FakeSession([(200, {
            "data": {"status": "invalid", "result": "mailbox_not_found"},
        })])
        api = _make_api(tmp_path, session)
        v = api.verify("bad@example.com")
        assert v["status"] == "NOT_RECEIVING"
        assert v["safe_to_send"] is False

    def test_verify_is_cached(self, tmp_path):
        session = _FakeSession([(200, {
            "data": {"status": "valid"},
        })])
        api = _make_api(tmp_path, session)
        v1 = api.verify("cached@example.com")
        v2 = api.verify("cached@example.com")
        assert len(session._calls) == 1
        assert v1["safe_to_send"] == v2["safe_to_send"]

    def test_budget_exhausted_returns_error(self, tmp_path):
        session = _FakeSession()
        api = _make_api(tmp_path, session, budgets={"hunter": 0.0})
        v = api.verify("x@example.com")
        assert v["status"] == "UNKNOWN"
        assert v.get("error") == "budget_exhausted"

    def test_auth_failure_releases_budget(self, tmp_path):
        session = _FakeSession([(401, {"error": "unauthorized"})])
        api = _make_api(tmp_path, session)
        v = api.verify("x@example.com")
        assert v["status"] == "UNKNOWN"
        assert api._budget_tracker.reserved("hunter") == 0.0


# ===================================================================
# credit exhaustion across resume
# ===================================================================

class TestCreditExhaustionAcrossResume:
    def test_hunter_search_budget_exhausted_persists(self, tmp_path):
        """Budget reservation persists in _budget.json so resume doesn't double-charge."""
        session1 = _FakeSession([(200, {
            "data": {"emails": [{"value": "a@b.com"}]},
        })])
        api1 = _make_api(tmp_path, session1, budgets={"hunter": 1.0})
        result = api1.hunter_contacts("b.com")
        assert len(result) == 1

        # Budget file persists
        budget_file = tmp_path / "_budget.json"
        assert budget_file.is_file()
        saved = json.loads(budget_file.read_text())
        assert saved.get("hunter", 0) >= 1.0

        # New session with same cache_dir — budget already consumed
        session2 = _FakeSession()
        api2 = _make_api(tmp_path, session2, budgets={"hunter": 1.0})
        result2 = api2.hunter_contacts("b.com")  # cache hit
        assert len(result2) == 1  # from cache
        # But a NEW domain should be budget-gated
        result3 = api2.hunter_contacts("c.com")
        assert result3 == []
        assert len(session2._calls) == 0  # no network calls at all

    def test_prospeo_enrich_budget_exhaustion(self, tmp_path):
        """Prospeo enrich stops after budget cap even mid-loop."""
        session = _FakeSession([
            # Search
            (200, {
                "error": False,
                "results": [
                    {
                        "person": {"person_id": "p1", "current_job_title": "HR Manager", "full_name": "A"},
                        "company": {"website": "example.com"},
                    },
                    {
                        "person": {"person_id": "p2", "current_job_title": "Recruiter", "full_name": "B"},
                        "company": {"website": "example.com"},
                    },
                ],
            }),
            # Enrich 1 (uses 1 credit)
            (200, {
                "person": {
                    "full_name": "A",
                    "current_job_title": "HR Manager",
                    "email": {"email": "a@example.com", "status": "VERIFIED"},
                    "linkedin_url": "https://linkedin.com/in/a",
                },
                "company": {"website": "example.com"},
            }),
            # Enrich 2 should not happen (budget exhausted)
        ])
        api = _make_api(tmp_path, session, budgets={"prospeo": 2.0})  # search=1, enrich=1, no room for 2nd enrich
        result = api.prospeo_contacts("example.com")
        assert len(result) == 1
        assert result[0]["email"] == "a@example.com"


# ===================================================================
# no repeat after ambiguous billable timeout
# ===================================================================

class TestNoRepeatAfterAmbiguousTimeout:
    def test_hunter_pending_cache_not_auto_repeated(self, tmp_path):
        """A pending cache entry is not auto-retried."""
        ck_dir = tmp_path / "cache"
        ck_dir.mkdir()
        # Manually write a pending cache entry
        import hashlib
        key = hashlib.sha256(b"hunter:search:stale.com").hexdigest()[:32]
        (ck_dir / f"{key}.json").write_text(json.dumps({
            "status": "pending", "contacts": [],
        }))

        session = _FakeSession()
        api = _make_api(ck_dir, session, budgets={"hunter": 10.0})
        result = api.hunter_contacts("stale.com")
        assert result == []  # pending → empty, no retry
        assert len(session._calls) == 0

    def test_snov_pending_task_not_restarted(self, tmp_path):
        """A pending Snov cache entry does not restart the task."""
        import hashlib
        key = hashlib.sha256(b"snov:contacts:stale.com").hexdigest()[:32]
        tmp_path.mkdir(exist_ok=True)
        (tmp_path / f"{key}.json").write_text(json.dumps({
            "status": "pending", "contacts": [],
        }))

        session = _FakeSession()
        api = _make_api(tmp_path, session, budgets={"snov": 10.0})
        os.environ["SNOV_IO_API_USER_ID"] = FAKE_SNOV_USER
        os.environ["SNOV_IO_API_SECRET"] = FAKE_SNOV_SECRET
        result = api.snov_contacts("stale.com")
        assert result == []
        assert len(session._calls) == 0

    def test_prospeo_pending_cache_not_auto_repeated(self, tmp_path):
        import hashlib
        key = hashlib.sha256(b"prospeo:contacts:stale.com").hexdigest()[:32]
        (tmp_path / f"{key}.json").write_text(json.dumps({
            "status": "pending", "contacts": [],
        }))
        session = _FakeSession()
        api = _make_api(tmp_path, session, budgets={"prospeo": 10.0})
        result = api.prospeo_contacts("stale.com")
        assert result == []
        assert len(session._calls) == 0


# ===================================================================
# Snov: 202 pending then complete
# ===================================================================

class TestSnovPolling:
    def test_202_then_complete(self, tmp_path):
        """Snov returns 202 on start, then 200 with data on poll."""
        session = _FakeSession([
            # OAuth token
            (200, {"access_token": "tok_snov"}),
            # Start task
            (202, {"meta": {"task_hash": "abc123"}, "links": {"result": "https://api.snov.io/v2/domain-search/generic-contacts/result/abc123"}}),
            # Poll 1: pending
            (200, {"data": [], "status": "pending"}),
            # Poll 2: completed with results
            (200, {"data": [
                {"email": "hr@corp.com", "first_name": "HR", "last_name": "Person", "position": "Recruiter"},
            ], "status": "completed"}),
        ])
        api = _make_api(tmp_path, session, budgets={"snov": 15.0})
        result = api.snov_contacts("corp.com")
        assert len(result) == 1
        assert result[0]["email"] == "hr@corp.com"
        assert result[0]["provider"] == "snov"
        assert result[0]["name"] == "HR Person"

    def test_task_hash_cached_for_resume(self, tmp_path):
        """Task hash is persisted so crash-resume doesn't repeat start."""
        session = _FakeSession([
            (200, {"access_token": "tok"}),
            (202, {"meta": {"task_hash": "hash1"}, "links": {}}),
            (200, {"data": [{"email": "x@y.com"}], "status": "completed"}),
        ])
        api = _make_api(tmp_path, session, budgets={"snov": 15.0})
        api.snov_contacts("y.com")
        # Verify task hash is cached
        import hashlib
        tk = hashlib.sha256(b"snov:task:y.com").hexdigest()[:32]
        cached = json.loads((tmp_path / f"{tk}.json").read_text())
        assert cached["task_hash"] == "hash1"

    def test_snov_does_not_invent_emails(self, tmp_path):
        """Only actual returned email strings are included."""
        session = _FakeSession([
            (200, {"access_token": "tok"}),
            (202, {"meta": {"task_hash": "h1"}, "links": {}}),
            (200, {"data": [
                {"email": "real@corp.com", "first_name": "R"},
                {"name_only": "no-email-person"},  # no email field
                {"email": "", "first_name": "Empty"},
                {"email": "not-an-email", "first_name": "Bad"},
            ], "status": "completed"}),
        ])
        api = _make_api(tmp_path, session, budgets={"snov": 15.0})
        result = api.snov_contacts("corp.com")
        emails = [c["email"] for c in result]
        assert emails == ["real@corp.com"]

    def test_snov_poll_exhaustion_returns_empty(self, tmp_path):
        """If polls exhaust without completion, returns empty (pending)."""
        session = _FakeSession([
            (200, {"access_token": "tok"}),
            (202, {"meta": {"task_hash": "h2"}, "links": {}}),
            (200, {"data": [], "status": "pending"}),
            (200, {"data": [], "status": "pending"}),
            (200, {"data": [], "status": "pending"}),
            (200, {"data": [], "status": "pending"}),
        ])
        api = _make_api(tmp_path, session, budgets={"snov": 15.0})
        result = api.snov_contacts("stuck.com")
        assert result == []

    def test_snov_auth_failure_halts(self, tmp_path):
        session = _FakeSession([
            (200, {"access_token": "tok"}),
            (401, {"error": "unauthorized"}),
        ])
        api = _make_api(tmp_path, session, budgets={"snov": 15.0})
        result = api.snov_contacts("blocked.com")
        assert result == []


# ===================================================================
# Prospeo: cross-domain person rejected before reveal
# ===================================================================

class TestProspeoCrossDomain:
    def test_cross_domain_person_rejected_before_enrich(self, tmp_path):
        """Persons from a different company domain are filtered out."""
        session = _FakeSession([
            # Search returns a person from a different domain
            (200, {
                "error": False,
                "results": [
                    {
                        "person": {
                            "person_id": "p_other",
                            "current_job_title": "HR Manager",
                            "full_name": "Other Person",
                        },
                        "company": {"website": "different-domain.com"},
                    },
                ],
            }),
            # Enrich should never be called
        ])
        api = _make_api(tmp_path, session, budgets={"prospeo": 10.0})
        result = api.prospeo_contacts("example.com")
        assert result == []
        # Only the search call should have been made, no enrich
        search_calls = session.get_calls(method="POST", url_contains="search-person")
        enrich_calls = session.get_calls(method="POST", url_contains="enrich-person")
        assert len(search_calls) == 1
        assert len(enrich_calls) == 0

    def test_non_hr_title_rejected_before_enrich(self, tmp_path):
        session = _FakeSession([
            (200, {
                "error": False,
                "results": [
                    {
                        "person": {
                            "person_id": "p_eng",
                            "current_job_title": "Software Engineer",
                            "full_name": "Engineer",
                        },
                        "company": {"website": "example.com"},
                    },
                ],
            }),
        ])
        api = _make_api(tmp_path, session, budgets={"prospeo": 10.0})
        result = api.prospeo_contacts("example.com")
        assert result == []
        enrich_calls = session.get_calls(method="POST", url_contains="enrich-person")
        assert len(enrich_calls) == 0

    def test_enrich_company_mismatch_rejected(self, tmp_path):
        """If enrich returns a different company domain, the person is skipped."""
        session = _FakeSession([
            (200, {
                "error": False,
                "results": [
                    {
                        "person": {"person_id": "p1", "current_job_title": "Recruiter", "full_name": "R"},
                        "company": {"website": "example.com"},
                    },
                ],
            }),
            # Enrich returns a different company
            (200, {
                "person": {
                    "full_name": "R",
                    "current_job_title": "Recruiter",
                    "email": {"email": "r@other.com", "status": "VERIFIED"},
                    "linkedin_url": "",
                },
                "company": {"website": "other-domain.com"},
            }),
        ])
        api = _make_api(tmp_path, session, budgets={"prospeo": 10.0})
        result = api.prospeo_contacts("example.com")
        assert result == []

    def test_prospeo_max_2_enriches(self, tmp_path):
        """At most 2 email enrichments per domain."""
        session = _FakeSession([
            (200, {
                "error": False,
                "results": [
                    {
                        "person": {"person_id": f"p{i}", "current_job_title": "Human Resources Manager", "full_name": f"Person{i}"},
                        "company": {"website": "bigco.com"},
                    }
                    for i in range(5)
                ],
            }),
            # Enrich 1
            (200, {"person": {"full_name": "Person0", "email": {"email": "p0@bigco.com", "status": "VERIFIED"}, "linkedin_url": ""}, "company": {"website": "bigco.com"}}),
            # Enrich 2
            (200, {"person": {"full_name": "Person1", "email": {"email": "p1@bigco.com", "status": "VERIFIED"}, "linkedin_url": ""}, "company": {"website": "bigco.com"}}),
            # Should not reach here
        ])
        api = _make_api(tmp_path, session, budgets={"prospeo": 10.0})
        result = api.prospeo_contacts("bigco.com")
        assert len(result) == 2
        enrich_calls = session.get_calls(method="POST", url_contains="enrich-person")
        assert len(enrich_calls) == 2

    def test_prospeo_search_error_halts(self, tmp_path):
        session = _FakeSession([(401, {"error": True})])
        api = _make_api(tmp_path, session, budgets={"prospeo": 10.0})
        result = api.prospeo_contacts("example.com")
        assert result == []
        assert api._budget_tracker.reserved("prospeo") == 0.0


# ===================================================================
# Stats property
# ===================================================================

class TestStats:
    def test_stats_track_calls_and_credits(self, tmp_path):
        session = _FakeSession([
            (200, {"data": {"emails": [{"value": "a@b.com"}]}}),
            (200, {"data": {"status": "valid"}}),
        ])
        api = _make_api(tmp_path, session)
        api.hunter_contacts("b.com")
        api.verify("a@b.com")
        s = api.stats
        assert s["calls"].get("hunter", 0) == 2
        assert s["credits"].get("hunter", 0) >= 1.5  # 1.0 + 0.5

    def test_stats_zero_when_no_calls(self, tmp_path):
        session = _FakeSession()
        api = _make_api(tmp_path, session)
        s = api.stats
        assert s["calls"] == {}
        assert s["credits"] == {}


# ===================================================================
# No invented emails
# ===================================================================

class TestNoInventedEmails:
    def test_hunter_empty_returns_empty(self, tmp_path):
        session = _FakeSession([(200, {"data": {"emails": []}})])
        api = _make_api(tmp_path, session)
        result = api.hunter_contacts("empty.com")
        assert result == []

    def test_hunter_no_data_field_returns_empty(self, tmp_path):
        session = _FakeSession([(200, {})])
        api = _make_api(tmp_path, session)
        result = api.hunter_contacts("nodata.com")
        assert result == []

    def test_prospeo_no_results_returns_empty(self, tmp_path):
        session = _FakeSession([(200, {"error": False, "results": []})])
        api = _make_api(tmp_path, session)
        result = api.prospeo_contacts("nobody.com")
        assert result == []

    def test_snov_empty_data_returns_empty(self, tmp_path):
        session = _FakeSession([
            (200, {"access_token": "tok"}),
            (202, {"meta": {"task_hash": "h"}, "links": {}}),
            (200, {"data": [], "status": "completed"}),
        ])
        api = _make_api(tmp_path, session, budgets={"snov": 15.0})
        result = api.snov_contacts("empty.com")
        assert result == []


# ===================================================================
# Domain normalisation edge cases
# ===================================================================

class TestDomainNormalisation:
    def test_www_prefix_stripped_for_comparison(self, tmp_path):
        session = _FakeSession([
            (200, {
                "error": False,
                "results": [{
                    "person": {"person_id": "p1", "current_job_title": "HR Director", "full_name": "D"},
                    "company": {"website": "https://www.example.com"},
                }],
            }),
            (200, {
                "person": {
                    "full_name": "D",
                    "email": {"email": "d@example.com", "status": "VERIFIED"},
                    "linkedin_url": "",
                },
                "company": {"website": "https://www.example.com"},
            }),
        ])
        api = _make_api(tmp_path, session, budgets={"prospeo": 10.0})
        result = api.prospeo_contacts("example.com")
        assert len(result) == 1
        assert result[0]["email"] == "d@example.com"


@pytest.fixture(autouse=True)
def no_real_poll_delays(monkeypatch):
    monkeypatch.setattr(ContactAPIs, "_SNOV_POLL_DELAY", 0)
    monkeypatch.setattr(ContactAPIs, "_REQUEST_INTERVAL", 0)


def test_corrupt_budget_fails_closed(tmp_path):
    (tmp_path / "_budget.json").write_text("broken")
    with pytest.raises(RuntimeError):
        _make_api(tmp_path, _FakeSession())


def test_timeout_reservation_survives_restart(tmp_path):
    api = _make_api(tmp_path, _FakeSession([(500, None)]))
    assert api.hunter_contacts("example.com") == []
    assert api._budget_tracker.reserved("hunter") == 1
    fresh_session = _FakeSession()
    resumed = _make_api(tmp_path, fresh_session)
    assert resumed.hunter_contacts("example.com") == []
    assert not fresh_session.get_calls()
    assert resumed._budget_tracker.reserved("hunter") == 1


def test_valid_but_accept_all_flag_is_held(tmp_path):
    api = _make_api(tmp_path, _FakeSession([(200, {"data": {"status": "valid", "accept_all": True}})]))
    assert not api.verify("info@example.com")["safe_to_send"]


def test_mismatched_verifier_email_is_held(tmp_path):
    api = _make_api(tmp_path, _FakeSession([(200, {"data": {"status": "valid", "email": "other@example.com"}})]))
    assert not api.verify("info@example.com")["safe_to_send"]


def test_quota_failure_halts_other_domains(tmp_path):
    s = _FakeSession([(429, {})])
    api = _make_api(tmp_path, s)
    api.hunter_contacts("example.com")
    api.hunter_contacts("second.com")
    assert len(s.get_calls()) == 1
    assert "hunter" in api.stats["halted"]


def test_snov_resume_polls_without_new_start_or_charge(tmp_path):
    from career_engine.rega_enrichment.contact_apis import _cache_key
    api = _make_api(tmp_path, _FakeSession())
    api.cache.put(_cache_key("snov", "contacts", "example.com"), {"status": "pending", "contacts": []})
    api.cache.put(_cache_key("snov", "task", "example.com"), {"task_hash": "abcdef12345"})
    api._budget_tracker.reserve("snov", 10)
    session = _FakeSession([(200, {"access_token": "ephemeral"}), (200, {"status": "completed", "data": []})])
    resumed = _make_api(tmp_path, session)
    assert resumed.snov_contacts("example.com") == []
    assert not session.get_calls(url_contains="generic-contacts/start")
    assert len(session.get_calls(url_contains="generic-contacts/result")) == 1
    assert resumed._budget_tracker.reserved("snov") == 10


def test_source_url_never_stores_key(tmp_path):
    api = _make_api(tmp_path, _FakeSession([(200, {"data": {"emails": [{"value": "info@example.com", "sources": [{"uri": "https://example.com?api_key="+FAKE_HUNTER_KEY}]}]}})]))
    api.hunter_contacts("example.com")
    assert all(FAKE_HUNTER_KEY not in p.read_text() for p in tmp_path.glob("*.json"))


# ===================================================================
# Snov verify
# ===================================================================

class TestSnovVerify:
    def setup_api(self, tmp_path, monkeypatch):
        from unittest.mock import Mock
        monkeypatch.setenv("SNOV_IO_API_USER_ID", "test")
        monkeypatch.setenv("SNOV_IO_API_SECRET", "test-secret")
        api = ContactAPIs(tmp_path, {"snov": 3})
        api._snov_get_token = Mock(return_value="test-token")
        monkeypatch.setattr("career_engine.rega_enrichment.contact_apis.time.sleep", lambda _: None)
        return api

    def completed(self, **updates):
        result = {"smtp_status": "valid", "is_valid_format": True, "is_disposable": False, "is_gibberish": False}
        result.update(updates)
        return {"status": "completed", "data": [{"email": "hr@example.sa", "result": result}]}

    def test_official_v2_schema_and_cached_result(self, tmp_path, monkeypatch):
        from unittest.mock import Mock
        api = self.setup_api(tmp_path, monkeypatch)
        api._safe_request = Mock(side_effect=[(200, {"data": {"task_hash": "abc123"}}), (200, self.completed())])
        result = api.verify_snov("hr@example.sa")
        assert result["safe_to_send"] and result["status"] == "RECEIVING"
        assert api._safe_request.call_args_list[0].args[1].endswith("/v2/email-verification/start")
        assert api._safe_request.call_args_list[0].kwargs["data"] == {"emails[]": ["hr@example.sa"]}
        assert api.verify_snov("hr@example.sa") == result
        assert api._safe_request.call_count == 2

    @pytest.mark.parametrize("updates", [{"smtp_status": "unknown", "unknown_status_reason": "catchall"},
        {"smtp_status": "not_valid"}, {"is_disposable": True}, {"is_gibberish": True},
        {"is_valid_format": False}, {"unknown_status_reason": "hidden_by_owner"}, {"is_disposable": None}])
    def test_unsafe_results_held(self, tmp_path, monkeypatch, updates):
        from unittest.mock import Mock
        api = self.setup_api(tmp_path, monkeypatch)
        api._safe_request = Mock(side_effect=[(200, {"data": {"task_hash": "abc123"}}), (200, self.completed(**updates))])
        assert api.verify_snov("hr@example.sa")["safe_to_send"] is False

    def test_pending_resumes_without_second_start_or_reservation(self, tmp_path, monkeypatch):
        from unittest.mock import Mock
        api = self.setup_api(tmp_path, monkeypatch)
        api._safe_request = Mock(side_effect=[(202, {"data": {"task_hash": "abc123"}})] + [(200, {"status": "in_progress"})] * 4)
        assert not api.verify_snov("hr@example.sa")["safe_to_send"]
        used = api._budget_tracker.reserved("snov")
        api._safe_request = Mock(return_value=(200, self.completed()))
        assert api.verify_snov("hr@example.sa")["safe_to_send"]
        assert api._safe_request.call_args.args[0] == "GET"
        assert api._budget_tracker.reserved("snov") == used

    def test_wrong_email_and_quota_fail_closed(self, tmp_path, monkeypatch):
        from unittest.mock import Mock
        api = self.setup_api(tmp_path, monkeypatch)
        data = self.completed(); data["data"][0]["email"] = "other@example.sa"
        api._safe_request = Mock(side_effect=[(200, {"data": {"task_hash": "abc123"}}), (200, data)])
        assert not api.verify_snov("hr@example.sa")["safe_to_send"]
        api._safe_request = Mock(return_value=(429, {}))
        assert not api.verify_snov("info@example.sa")["safe_to_send"]
        assert api._is_halted("snov")
