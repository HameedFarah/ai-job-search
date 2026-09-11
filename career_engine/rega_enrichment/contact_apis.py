"""Contact API adapters for Hunter.io, Prospeo, and Snov.io.

Candidate-contact enrichment only — never authorizes sends or fabricates
addresses.  Credits are conservatively reserved before billable requests and
the reservation state is persisted so crash recovery never double-charges.
Cache persists sanitized results and never stores secrets, tokens, auth data
or request URLs containing api_key.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import requests

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FORBIDDEN_KEY_PREFIXES = frozenset({
    "api_key", "apikey", "api-key", "secret", "password", "token",
    "access_token", "client_secret", "authorization", "auth",
})

_FORBIDDEN_EXACT = frozenset({"key", "client_id"})

# Actual secret values supplied at runtime — scrubbed from string values
# before any cache write.  Populated once during ContactAPIs.__init__.
_supplied_secrets: set[str] = set()

_TOKEN_EXPIRY_SECONDS = 3500
_SAFE_TASK_HASH_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _register_secret(value: str) -> None:
    """Register a runtime secret so it is stripped from cached strings."""
    if value and len(value) >= 4:
        _supplied_secrets.add(value)


def _scrub_string_secrets(value: str) -> str:
    """Replace any known secret value embedded in a plain string."""
    for secret in _supplied_secrets:
        if secret in value:
            value = value.replace(secret, "***")
    return value


def _scrub_recursive(obj: Any) -> Any:
    """Recursively sanitize sensitive keys, values, and URL query-params."""
    if isinstance(obj, dict):
        cleaned: dict[str, Any] = {}
        for k, v in obj.items():
            kl = str(k).lower()
            if kl in _FORBIDDEN_EXACT or any(
                kl.startswith(p) for p in _FORBIDDEN_KEY_PREFIXES
            ):
                continue
            cleaned[k] = _scrub_recursive(v)
        return cleaned
    if isinstance(obj, list):
        return [_scrub_recursive(item) for item in obj]
    if isinstance(obj, str):
        s = _scrub_string_secrets(obj)
        if s.startswith(("http://", "https://")):
            return _url_scrub_api_key(s)
        return s
    return obj


def _url_scrub_api_key(url: str) -> str:
    """Remove query params whose names look like api-key carriers."""
    parsed = urlparse(url)
    params = parse_qs(parsed.query, keep_blank_values=True)
    cleaned = {
        k: v for k, v in params.items()
        if not any(k.lower().startswith(p) for p in _FORBIDDEN_KEY_PREFIXES)
        and k.lower() not in _FORBIDDEN_EXACT
    }
    return parsed._replace(query=urlencode(cleaned, doseq=True)).geturl()


def _cache_key(namespace: str, operation: str, *parts: str) -> str:
    raw = f"{namespace}:{operation}:" + ":".join(parts)
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _normalize_domain(domain: str) -> str:
    return domain.lower().removeprefix("https://").removeprefix("http://").removeprefix("www.").split("/")[0]


def _email_domain(email: str) -> str:
    """Extract the domain part of an email address, normalised."""
    parts = email.lower().split("@")
    return _normalize_domain(parts[1]) if len(parts) == 2 else ""


# ---------------------------------------------------------------------------
# Budget tracker (persisted)
# ---------------------------------------------------------------------------

class _BudgetTracker:
    """Per-provider credit reservation tracker persisted alongside cache."""

    def __init__(self, cache_dir: Path) -> None:
        self._path = cache_dir / "_budget.json"
        self._data: dict[str, float] = {}
        self._load()

    def _load(self) -> None:
        if self._path.is_file():
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    self._data = {k: float(v) for k, v in raw.items()}
                else:
                    raise RuntimeError(
                        f"Corrupted budget file (not a dict): {self._path}"
                    )
            except (json.JSONDecodeError, ValueError, OSError) as exc:
                raise RuntimeError(
                    f"Corrupted budget file, cannot fail-open: {self._path}"
                ) from exc

    def _save(self) -> None:
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
        tmp.rename(self._path)

    def reserved(self, provider: str) -> float:
        return self._data.get(provider, 0.0)

    def reserve(self, provider: str, cost: float) -> None:
        self._data[provider] = self._data.get(provider, 0.0) + cost
        self._save()

    def release(self, provider: str, cost: float) -> None:
        cur = self._data.get(provider, 0.0)
        self._data[provider] = max(0.0, cur - cost)
        self._save()


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

class _Cache:
    def __init__(self, cache_dir: Path) -> None:
        self.dir = cache_dir
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self.dir / f"{key}.json"

    def get(self, key: str) -> dict[str, Any] | None:
        p = self._path(key)
        if not p.is_file():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise RuntimeError(
                f"Corrupted cache file, cannot fail-open: {p}"
            ) from exc

    def put(self, key: str, data: dict[str, Any]) -> None:
        p = self._path(key)
        safe = _scrub_recursive(data)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(safe, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.rename(p)

    def is_completed(self, key: str) -> bool:
        d = self.get(key)
        return isinstance(d, dict) and d.get("status") == "completed"

    def is_pending(self, key: str) -> bool:
        d = self.get(key)
        return isinstance(d, dict) and d.get("status") == "pending"


# ---------------------------------------------------------------------------
# ContactAPIs
# ---------------------------------------------------------------------------

class ContactAPIs:
    """Unified contact API adapter for Hunter.io, Prospeo, and Snov.io.

    Constructor parameters
    ----------------------
    cache_dir : Path
        Directory for sanitized cache and persisted budget reservations.
    budgets : dict[str, float]
        Per-provider upper-bound credit caps, e.g.
        ``{"hunter": 10.0, "prospeo": 3.0, "snov": 10.0}``.
    session : requests.Session | None
        Injectable session for testing.

    Environment keys (injected, never persisted):
        HUNTER_IO_API_KEY, PROSPEO_API_KEY,
        SNOV_IO_API_USER_ID, SNOV_IO_API_SECRET.
    """

    # Upper-bound credit costs
    HUNTER_SEARCH_COST = 1.0
    HUNTER_VERIFY_COST = 0.5
    HUNTER_SEARCH_LIMIT = 10
    PROSPEO_SEARCH_COST = 1.0
    PROSPEO_ENRICH_COST = 1.0
    PROSPEO_MAX_ENRICH = 2
    SNOV_DISCOVERY_COST = 10.0

    _REQUEST_TIMEOUT = 20
    _REQUEST_INTERVAL = 1.1
    _SNOV_MAX_POLLS = 4
    _SNOV_POLL_DELAY = 2.0

    _HR_TITLE_KEYWORDS = ("human resource", "human resources", "recruit", "talent acquisition", "hr ")

    def __init__(
        self,
        cache_dir: Path,
        budgets: dict[str, float],
        session: requests.Session | None = None,
    ) -> None:
        self.cache = _Cache(cache_dir)
        self._budgets_cfg: dict[str, float] = dict(budgets)
        self.session = session or requests.Session()
        self._budget_tracker = _BudgetTracker(cache_dir)

        # Env-injected keys — never stored in cache or errors
        self._hunter_key = os.environ.get("HUNTER_IO_API_KEY", "")
        self._prospeo_key = os.environ.get("PROSPEO_API_KEY", "")
        self._snov_user_id = os.environ.get("SNOV_IO_API_USER_ID", "")
        self._snov_secret = os.environ.get("SNOV_IO_API_SECRET", "")

        # Register secrets for scrubbing from strings
        _register_secret(self._hunter_key)
        _register_secret(self._prospeo_key)
        _register_secret(self._snov_user_id)
        _register_secret(self._snov_secret)

        # Snov token lives in memory only (with acquisition timestamp)
        self._snov_token: str | None = None
        self._snov_token_at: float = 0.0

        # Provider halt state (runtime-only, not persisted)
        self._halted: set[str] = set()

        # Runtime counters (not persisted)
        self._call_counts: dict[str, int] = {}
        self._credit_counts: dict[str, float] = {}
        self._error_counts: dict[str, int] = {}
        self._last_request_at = 0.0

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def stats(self) -> dict[str, Any]:
        """Call counts, credit reservations, error counts, and halt state."""
        reserved: dict[str, float] = {}
        for p in ("hunter", "prospeo", "snov"):
            r = self._budget_tracker.reserved(p)
            if r > 0:
                reserved[p] = r
        return {
            "calls": dict(self._call_counts),
            "credits": dict(self._credit_counts),
            "errors": dict(self._error_counts),
            "reserved": reserved,
            "halted": sorted(self._halted),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _is_halted(self, provider: str) -> bool:
        return provider in self._halted

    def _halt(self, provider: str) -> None:
        self._halted.add(provider)

    def _budget_ok(self, provider: str, cost: float) -> bool:
        cap = self._budgets_cfg.get(provider, 0.0)
        used = self._budget_tracker.reserved(provider)
        return used + cost <= cap

    def _reserve(self, provider: str, cost: float) -> bool:
        if not self._budget_ok(provider, cost):
            return False
        self._budget_tracker.reserve(provider, cost)
        return True

    def _release(self, provider: str, cost: float) -> None:
        self._budget_tracker.release(provider, cost)

    def _count(self, provider: str, *, credit: float = 0.0, error: bool = False) -> None:
        self._call_counts[provider] = self._call_counts.get(provider, 0) + 1
        if credit > 0:
            self._credit_counts[provider] = self._credit_counts.get(provider, 0) + credit
        if error:
            self._error_counts[provider] = self._error_counts.get(provider, 0) + 1

    def _safe_request(
        self, method: str, url: str, **kwargs: Any,
    ) -> tuple[int, dict[str, Any] | None]:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self._REQUEST_INTERVAL:
            time.sleep(self._REQUEST_INTERVAL - elapsed)
        self._last_request_at = time.monotonic()
        kwargs.setdefault("timeout", self._REQUEST_TIMEOUT)
        try:
            resp = self.session.request(method, url, **kwargs)
            try:
                body: dict[str, Any] | None = resp.json()
            except Exception:
                body = None
            return resp.status_code, body
        except Exception:
            return 0, None

    # ------------------------------------------------------------------
    # account_balances
    # ------------------------------------------------------------------

    def account_balances(self) -> dict[str, Any]:
        """Remaining credits for all configured providers (free calls only)."""
        result: dict[str, Any] = {}

        # Hunter
        if self._is_halted("hunter"):
            result["hunter"] = {"provider": "hunter", "status": "halted"}
        elif self._hunter_key:
            code, body = self._safe_request(
                "GET", "https://api.hunter.io/v2/account",
                params={"api_key": self._hunter_key},
            )
            self._count("hunter")
            if code == 200 and isinstance(body, dict):
                remaining = (
                    (body.get("data") or {})
                    .get("requests", {})
                    .get("credits", {})
                    .get("remaining")
                )
                result["hunter"] = {"provider": "hunter", "status": "success", "remaining": remaining}
            elif code in (401, 403):
                result["hunter"] = {"provider": "hunter", "status": "auth_failed"}
                self._count("hunter", error=True)
                self._halt("hunter")
            else:
                result["hunter"] = {"provider": "hunter", "status": "failed"}
                self._count("hunter", error=True)
        else:
            result["hunter"] = {"provider": "hunter", "status": "missing_credential"}

        # Prospeo
        if self._is_halted("prospeo"):
            result["prospeo"] = {"provider": "prospeo", "status": "halted"}
        elif self._prospeo_key:
            code, body = self._safe_request(
                "GET", "https://api.prospeo.io/account-information",
                headers={"X-KEY": self._prospeo_key},
            )
            self._count("prospeo")
            if code == 200 and isinstance(body, dict) and not body.get("error"):
                resp_data = body.get("response") or {}
                remaining = resp_data.get("remaining_credits") if isinstance(resp_data, dict) else None
                result["prospeo"] = {"provider": "prospeo", "status": "success", "remaining": remaining}
            elif code in (401, 403):
                result["prospeo"] = {"provider": "prospeo", "status": "auth_failed"}
                self._count("prospeo", error=True)
                self._halt("prospeo")
            else:
                result["prospeo"] = {"provider": "prospeo", "status": "failed"}
                self._count("prospeo", error=True)
        else:
            result["prospeo"] = {"provider": "prospeo", "status": "missing_credential"}

        # Snov
        if self._is_halted("snov"):
            result["snov"] = {"provider": "snov", "status": "halted"}
        elif self._snov_user_id and self._snov_secret:
            token = self._snov_get_token()
            if token:
                code, body = self._safe_request(
                    "GET", "https://api.snov.io/v1/get-balance",
                    headers={"Authorization": f"Bearer {token}"},
                )
                self._count("snov")
                if code == 200 and isinstance(body, dict):
                    data_block = body.get("data")
                    if isinstance(data_block, dict):
                        balance = data_block.get("balance")
                    else:
                        balance = body.get("balance")
                    result["snov"] = {"provider": "snov", "status": "success", "remaining": balance}
                elif code in (401, 403):
                    result["snov"] = {"provider": "snov", "status": "auth_failed"}
                    self._count("snov", error=True)
                    self._halt("snov")
                else:
                    result["snov"] = {"provider": "snov", "status": "failed"}
                    self._count("snov", error=True)
            else:
                result["snov"] = {"provider": "snov", "status": "auth_failed"}
                self._count("snov", error=True)
        else:
            result["snov"] = {"provider": "snov", "status": "missing_credential"}

        return result

    # ------------------------------------------------------------------
    # Snov.io OAuth (memory only, refresh after 3500 s)
    # ------------------------------------------------------------------

    def _snov_get_token(self) -> str | None:
        if self._snov_token and (time.time() - self._snov_token_at) < _TOKEN_EXPIRY_SECONDS:
            return self._snov_token
        code, body = self._safe_request(
            "POST", "https://api.snov.io/v1/oauth/access_token",
            data={
                "grant_type": "client_credentials",
                "client_id": self._snov_user_id,
                "client_secret": self._snov_secret,
            },
        )
        if code == 200 and isinstance(body, dict):
            token = body.get("access_token")
            if not token:
                data_block = body.get("data")
                if isinstance(data_block, dict):
                    token = data_block.get("access_token")
            if token and isinstance(token, str):
                self._snov_token = token
                self._snov_token_at = time.time()
                return token
        return None

    # ------------------------------------------------------------------
    # hunter_contacts
    # ------------------------------------------------------------------

    def hunter_contacts(self, domain: str) -> list[dict[str, Any]]:
        """Hunter.io domain-search — cost 1 credit, limit 10 results."""
        if self._is_halted("hunter"):
            return []
        if not self._hunter_key:
            return []

        ck = _cache_key("hunter", "search", domain)
        if self.cache.is_completed(ck):
            return (self.cache.get(ck) or {}).get("contacts", [])
        if self.cache.is_pending(ck):
            return []

        reserve = getattr(self, "hunter_verification_reserve", 0.0)
        if not self._budget_ok("hunter", self.HUNTER_SEARCH_COST + reserve):
            return []
        if not self._reserve("hunter", self.HUNTER_SEARCH_COST):
            return []

        self.cache.put(ck, {"status": "pending", "contacts": []})

        code, body = self._safe_request(
            "GET", "https://api.hunter.io/v2/domain-search",
            params={
                "domain": domain,
                "limit": self.HUNTER_SEARCH_LIMIT,
                "api_key": self._hunter_key,
            },
        )

        if code == 200 and isinstance(body, dict):
            self._count("hunter", credit=self.HUNTER_SEARCH_COST)
            emails = ((body.get("data") or {}).get("emails")) or []
            contacts = self._normalise_hunter_emails(emails, domain)
            self.cache.put(ck, {"status": "completed", "contacts": contacts})
            return contacts

        # Error handling: only unambiguous non-charge errors release
        if code in (401, 403):
            self._release("hunter", self.HUNTER_SEARCH_COST)
            self._count("hunter", error=True)
            self._halt("hunter")
        elif code in (402, 429):
            # Ambiguous — keep reservation, halt provider
            self._count("hunter", credit=self.HUNTER_SEARCH_COST)
            self._halt("hunter")
        else:
            # timeout/5xx/malformed — ambiguous, keep reservation
            self._count("hunter", credit=self.HUNTER_SEARCH_COST)
        return []

    def _normalise_hunter_emails(
        self, emails: list[dict[str, Any]], domain: str,
    ) -> list[dict[str, Any]]:
        contacts: list[dict[str, Any]] = []
        target = _normalize_domain(domain)
        for entry in emails:
            if not isinstance(entry, dict) or not entry.get("value"):
                continue
            email_str = str(entry["value"])
            # Omit off-domain email
            if _email_domain(email_str) != target:
                continue
            source_urls: list[str] = []
            for src in entry.get("sources") or []:
                if isinstance(src, dict) and src.get("uri"):
                    source_urls.append(_url_scrub_api_key(str(src["uri"])))
            first = str(entry.get("first_name") or "")
            last = str(entry.get("last_name") or "")
            name = f"{first} {last}".strip()
            contacts.append({
                "email": email_str,
                "provider": "hunter",
                "name": name,
                "title": str(entry.get("position") or ""),
                "source_urls": source_urls,
                "provider_verification": _scrub_recursive(entry.get("verification") or {}),
                "domain": domain,
            })
        return contacts

    # ------------------------------------------------------------------
    # verify (Hunter only)
    # ------------------------------------------------------------------

    def verify(self, email: str) -> dict[str, Any]:
        """Hunter email verification — cost 0.5 credits.

        Pending cache entries use status ``pending``; callers see
        ``UNKNOWN`` for pending / missing / budget / network results.
        """
        if self._is_halted("hunter"):
            return {
                "email": email, "provider": "hunter",
                "status": "UNKNOWN", "safe_to_send": False,
                "error": "provider_halted",
            }
        if not self._hunter_key:
            return {
                "email": email, "provider": "hunter",
                "status": "UNKNOWN", "safe_to_send": False,
                "error": "missing_credential",
            }

        ck = _cache_key("hunter", "verify", email)
        cached = self.cache.get(ck)
        if cached and cached.get("status") == "pending":
            return {
                "email": email, "provider": "hunter",
                "status": "UNKNOWN", "safe_to_send": False,
            }
        if cached and cached.get("status") != "pending":
            return cached

        if not self._reserve("hunter", self.HUNTER_VERIFY_COST):
            return {
                "email": email, "provider": "hunter",
                "status": "UNKNOWN", "safe_to_send": False,
                "error": "budget_exhausted",
            }

        # Write genuine pending marker (status == "pending")
        self.cache.put(ck, {
            "email": email, "provider": "hunter",
            "status": "pending", "safe_to_send": False,
        })

        code, body = self._safe_request(
            "GET", "https://api.hunter.io/v2/email-verifier",
            params={"email": email, "api_key": self._hunter_key},
        )

        if code == 200 and isinstance(body, dict):
            self._count("hunter", credit=self.HUNTER_VERIFY_COST)
            data = body.get("data") or {}
            raw = str(data.get("status") or "").lower()
            mapped = _map_hunter_status(raw)
            disposable = bool(data.get("disposable"))
            accept_all = raw == "accept_all" or bool(data.get("accept_all"))
            result_val = data.get("result")
            # safe_to_send: valid + result in (None, 'deliverable') +
            # not accept_all + not disposable + email matches input
            email_matches = (
                not data.get("email")
                or str(data.get("email")).lower() == email.lower()
            )
            safe = (
                raw == "valid"
                and result_val in (None, "deliverable")
                and not disposable
                and not accept_all
                and mapped == "RECEIVING"
                and email_matches
            )
            verification: dict[str, Any] = {
                "email": email,
                "provider": "hunter",
                "status": mapped,
                "verification": raw,
                "status_details": str(result_val or raw),
                "safe_to_send": safe,
                "checked_at": _utc_now(),
                "source_url": "https://api.hunter.io/v2/email-verifier",
            }
            self.cache.put(ck, verification)
            return verification

        # Error handling
        if code in (401, 403):
            self._release("hunter", self.HUNTER_VERIFY_COST)
            self._count("hunter", error=True)
            self._halt("hunter")
        elif code in (402, 429):
            self._count("hunter", credit=self.HUNTER_VERIFY_COST)
            self._halt("hunter")
        else:
            # timeout/5xx/malformed — ambiguous, keep reservation
            self._count("hunter", credit=self.HUNTER_VERIFY_COST)
        return {
            "email": email, "provider": "hunter",
            "status": "UNKNOWN", "safe_to_send": False,
        }

    # ------------------------------------------------------------------
    # prospeo_contacts
    # ------------------------------------------------------------------

    def prospeo_contacts(self, domain: str) -> list[dict[str, Any]]:
        """Prospeo search + enrich — search 1 credit, max 2 enriches at 1 each."""
        if self._is_halted("prospeo"):
            return []
        if not self._prospeo_key:
            return []

        ck = _cache_key("prospeo", "contacts", domain)
        if self.cache.is_completed(ck):
            return (self.cache.get(ck) or {}).get("contacts", [])
        if self.cache.is_pending(ck):
            return []

        if not self._reserve("prospeo", self.PROSPEO_SEARCH_COST):
            return []

        self.cache.put(ck, {"status": "pending", "contacts": []})

        target = _normalize_domain(domain)
        search_payload: dict[str, Any] = {
            "page": 1,
            "filters": {
                "company": {"websites": {"include": [domain]}},
                "person_job_title": {
                    "include": list(self._HR_TITLE_KEYWORDS),
                    "match_mode": "CONTAINS",
                },
            },
        }

        code, body = self._safe_request(
            "POST", "https://api.prospeo.io/search-person",
            headers={"X-KEY": self._prospeo_key, "Content-Type": "application/json"},
            json=search_payload,
        )

        if code in (401, 403):
            self._release("prospeo", self.PROSPEO_SEARCH_COST)
            self._count("prospeo", error=True)
            self._halt("prospeo")
            return []
        if code in (402, 429):
            self._count("prospeo", credit=self.PROSPEO_SEARCH_COST)
            self._halt("prospeo")
            return []
        # Check for Prospeo-specific error codes
        if isinstance(body, dict) and body.get("error"):
            error_code = str(body.get("error_code") or "").upper()
            if error_code in ("INVALID_API_KEY", "INSUFFICIENT_CREDITS"):
                self._halt("prospeo")
                self._count("prospeo", credit=self.PROSPEO_SEARCH_COST, error=True)
                return []
            self._release("prospeo", self.PROSPEO_SEARCH_COST)
            self._count("prospeo", error=True)
            return []
        if code != 200 or not isinstance(body, dict):
            # timeout/5xx/malformed — ambiguous, keep reservation
            self._count("prospeo", credit=self.PROSPEO_SEARCH_COST)
            return []

        self._count("prospeo", credit=self.PROSPEO_SEARCH_COST)

        results_raw = body.get("results") or []
        if not isinstance(results_raw, list):
            results_raw = []

        # Filter: company website domain must match, title must be HR-related
        candidates: list[dict[str, Any]] = []
        for item in results_raw:
            if not isinstance(item, dict):
                continue
            person = item.get("person")
            company = item.get("company")
            if not isinstance(person, dict) or not isinstance(company, dict):
                continue
            comp_website = str(company.get("website") or "").lower()
            if not comp_website:
                continue
            actual = _normalize_domain(comp_website)
            if actual != target:
                continue
            title = str(person.get("current_job_title") or "").lower()
            if not any(kw in title for kw in self._HR_TITLE_KEYWORDS):
                continue
            candidates.append(person)

        # Enrich at most 2
        contacts: list[dict[str, Any]] = []
        enrichments_done = 0
        for person in candidates:
            if enrichments_done >= self.PROSPEO_MAX_ENRICH:
                break
            person_id = person.get("person_id")
            if not person_id:
                continue
            if not self._reserve("prospeo", self.PROSPEO_ENRICH_COST):
                break

            ecode, ebody = self._safe_request(
                "POST", "https://api.prospeo.io/enrich-person",
                headers={"X-KEY": self._prospeo_key, "Content-Type": "application/json"},
                json={
                    "only_verified_email": True,
                    "enrich_mobile": False,
                    "data": {"person_id": str(person_id)},
                },
            )
            enrichments_done += 1

            # Error handling for enrich
            if ecode in (401, 403):
                self._release("prospeo", self.PROSPEO_ENRICH_COST)
                self._count("prospeo", error=True)
                self._halt("prospeo")
                break
            if ecode in (402, 429):
                self._count("prospeo", credit=self.PROSPEO_ENRICH_COST)
                self._halt("prospeo")
                break
            if isinstance(ebody, dict) and ebody.get("error"):
                ec = str(ebody.get("error_code") or "").upper()
                if ec in ("INVALID_API_KEY", "INSUFFICIENT_CREDITS"):
                    self._count("prospeo", credit=self.PROSPEO_ENRICH_COST)
                    self._halt("prospeo")
                    break
            if ecode != 200 or not isinstance(ebody, dict) or ebody.get("error"):
                # ambiguous — keep reservation
                self._count("prospeo", credit=self.PROSPEO_ENRICH_COST)
                continue

            self._count("prospeo", credit=self.PROSPEO_ENRICH_COST)

            ep = ebody.get("person") or {}
            ec = ebody.get("company") or {}
            if not isinstance(ep, dict):
                continue

            # Prospeo official schema: person.email.email (confirmed via docs)
            email_obj = ep.get("email") or {}
            if not isinstance(email_obj, dict) or not email_obj.get("email"):
                continue

            email_str = str(email_obj["email"])

            # Normalize email domain; omit off-domain email
            if _email_domain(email_str) != target:
                continue

            # Verify company domain post-enrich
            enriched_website = str((ec or {}).get("website") or "")
            if _normalize_domain(enriched_website) != target:
                continue

            # Post-enrich title check: verify enriched title is HR-relevant
            enriched_title = str(ep.get("current_job_title") or "").lower()
            if enriched_title and not any(kw in enriched_title for kw in self._HR_TITLE_KEYWORDS):
                continue

            linkedin = ep.get("linkedin_url")
            source_urls = [str(linkedin)] if linkedin and isinstance(linkedin, str) else []

            contacts.append({
                "email": email_str,
                "provider": "prospeo",
                "name": str(ep.get("full_name") or ""),
                "title": str(ep.get("current_job_title") or ""),
                "source_urls": source_urls,
                "provider_verification": {
                    "email_status": str((email_obj.get("status") or "").upper()),
                    "method": str(email_obj.get("verification_method") or ""),
                },
                "domain": domain,
            })

        self.cache.put(ck, {"status": "completed", "contacts": contacts})
        return contacts

    # ------------------------------------------------------------------
    # snov_contacts
    # ------------------------------------------------------------------

    def snov_contacts(self, domain: str) -> list[dict[str, Any]]:
        """Snov.io generic-contacts — conservative 10-credit budget.

        Starts an async task (HTTP 202), polls up to 4 times with 2 s delay.
        Stores the task_hash so crash recovery never repeats the start call.
        """
        if self._is_halted("snov"):
            return []
        if not self._snov_user_id or not self._snov_secret:
            return []

        ck = _cache_key("snov", "contacts", domain)
        if self.cache.is_completed(ck):
            return (self.cache.get(ck) or {}).get("contacts", [])
        if self.cache.is_pending(ck):
            # Check for existing task_hash to resume polling
            tk = _cache_key("snov", "task", domain)
            task_data = self.cache.get(tk)
            task_hash = task_data.get("task_hash") if isinstance(task_data, dict) else None
            if not task_hash or not _SAFE_TASK_HASH_RE.match(str(task_hash)):
                return []  # No valid task to resume — ambiguous
            return self._snov_resume_polling(domain, ck, str(task_hash))

        token = self._snov_get_token()
        if not token:
            return []

        if not self._reserve("snov", self.SNOV_DISCOVERY_COST):
            return []

        self.cache.put(ck, {"status": "pending", "contacts": []})

        tk = _cache_key("snov", "task", domain)
        task_data = self.cache.get(tk)
        task_hash: str | None = task_data.get("task_hash") if isinstance(task_data, dict) else None

        if not task_hash:
            scode, sbody = self._safe_request(
                "POST", "https://api.snov.io/v2/domain-search/generic-contacts/start",
                headers={"Authorization": f"Bearer {token}"},
                data={"domain": domain},
            )
            if scode == 202 and isinstance(sbody, dict):
                meta = sbody.get("meta") or {}
                task_hash = meta.get("task_hash")
                if not task_hash:
                    result_link = (sbody.get("links") or {}).get("result", "")
                    if result_link:
                        parts = result_link.rstrip("/").split("/")
                        task_hash = parts[-1] if parts else None
                if task_hash and _SAFE_TASK_HASH_RE.match(str(task_hash)):
                    self.cache.put(tk, {"task_hash": str(task_hash)})
                    self._count("snov", credit=self.SNOV_DISCOVERY_COST)
                elif task_hash:
                    task_hash = None
            elif scode in (401, 403):
                self._release("snov", self.SNOV_DISCOVERY_COST)
                self._count("snov", error=True)
                self._halt("snov")
                return []
            elif scode in (402, 429):
                self._count("snov", credit=self.SNOV_DISCOVERY_COST)
                self._halt("snov")
                return []
            else:
                # timeout/5xx/malformed — ambiguous, keep reservation
                self._count("snov", credit=self.SNOV_DISCOVERY_COST)
                return []

        if not task_hash:
            # Accepted but malformed task response may already have charged.
            return []

        return self._snov_resume_polling(domain, ck, task_hash)

    def _snov_resume_polling(
        self, domain: str, ck: str, task_hash: str,
    ) -> list[dict[str, Any]]:
        """Poll Snov for task results. No new reservation or start call."""
        token = self._snov_get_token()
        if not token:
            return []

        result_url = f"https://api.snov.io/v2/domain-search/generic-contacts/result/{task_hash}"

        for _ in range(self._SNOV_MAX_POLLS):
            time.sleep(self._SNOV_POLL_DELAY)
            pcode, pbody = self._safe_request(
                "GET", result_url,
                headers={"Authorization": f"Bearer {token}"},
            )
            self._count("snov")

            if pcode == 200 and isinstance(pbody, dict):
                data_items = pbody.get("data") or []
                status = str(pbody.get("status") or "").lower()
                if not isinstance(data_items, list):
                    data_items = []
                if status == "completed" or (
                    isinstance(data_items, list) and len(data_items) > 0 and status not in ("pending", "")
                ):
                    contacts = self._normalise_snov(data_items, domain)
                    self.cache.put(ck, {"status": "completed", "contacts": contacts})
                    return contacts
                # still pending — continue polling
                continue
            if pcode in (401, 403):
                break
            if pcode == 429:
                self._halt("snov")
                break
            if pcode == 202:
                continue
            break

        # Did not complete — stay pending, do not auto-retry on next call
        return []

    def _normalise_snov(
        self, data_items: list[dict[str, Any]], domain: str,
    ) -> list[dict[str, Any]]:
        contacts: list[dict[str, Any]] = []
        target = _normalize_domain(domain)
        for item in data_items:
            if not isinstance(item, dict):
                continue
            email = str(item.get("email") or "").strip()
            if not email or "@" not in email:
                continue
            # Omit off-domain email
            if _email_domain(email) != target:
                continue
            parts: list[str] = []
            if item.get("first_name"):
                parts.append(str(item["first_name"]))
            if item.get("last_name"):
                parts.append(str(item["last_name"]))
            contacts.append({
                "email": email,
                "provider": "snov",
                "name": " ".join(parts),
                "title": str(item.get("position") or item.get("job_title") or ""),
                "source_urls": [],
                "provider_verification": {},
                "domain": domain,
            })
        return contacts


# ---------------------------------------------------------------------------
# Hunter status mapping
# ---------------------------------------------------------------------------

def _map_hunter_status(raw: str) -> str:
    """Map Hunter verifier status to RECEIVING / UNKNOWN / NOT_RECEIVING."""
    if raw == "valid":
        return "RECEIVING"
    if raw in ("invalid", "disabled", "deleted"):
        return "NOT_RECEIVING"
    return "UNKNOWN"
