"""Fail-closed Outscraper email deliverability validation.

This module deliberately wraps the existing :class:`OutscraperClient` rather
than creating a second provider stack. The API key remains in the client's
request header and is never returned in evidence.
"""
from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import urlencode

from .provider_clients import OutscraperClient, ProviderBudget, _record

TERMINAL_STATUSES = {"RECEIVING", "INVALID", "BLACKLISTED", "UNKNOWN"}
# Outscraper's current public API FAQ documents array batching up to 25 queries
# per request (query=a&query=b&...). Keep this fail-closed even if a caller asks
# for a larger batch.
MAX_BATCH_SIZE = 25


def _normalise_emails(emails: Iterable[str]) -> list[str]:
    """Return unique, non-empty email strings while preserving input order."""
    seen: set[str] = set()
    out: list[str] = []
    for raw in emails:
        email = str(raw or "").strip().lower()
        if not email or email in seen:
            continue
        seen.add(email)
        out.append(email)
    return out


def _validation_record(
    email: str,
    status: str,
    *,
    source: str,
    status_details: str = "",
    cost_status: str = "free_tier_or_existing_metered_credit",
) -> dict:
    safe_status = status.upper() if status.upper() in TERMINAL_STATUSES else "UNKNOWN"
    return _record(
        "outscraper",
        source,
        "Outscraper email deliverability validation",
        status=safe_status,
        cost_status=cost_status,
        email=email,
        verification=safe_status,
        status_details=status_details,
        safe_to_send=safe_status == "RECEIVING",
    )


def validate_emails(
    client: OutscraperClient,
    emails: Iterable[str],
    budget: ProviderBudget,
    *,
    batch_size: int = MAX_BATCH_SIZE,
) -> list[dict]:
    """Validate emails with Outscraper's ``/email-validator`` endpoint.

    Only ``RECEIVING`` is represented as ``safe_to_send=True``. ``UNKNOWN``
    is terminal provider evidence but remains fail-closed for outreach.
    Missing rows, unexpected statuses, network errors, authentication errors,
    quota errors, and budget exhaustion never become send-eligible.
    """
    source = client.root + "/email-validator"
    normalised = _normalise_emails(emails)
    if not normalised:
        return []
    if not client.key:
        return [
            _record(
                "outscraper",
                source,
                "email validation gated",
                status="missing_credential",
                cost_status="not_charged",
                email=email,
                safe_to_send=False,
            )
            for email in normalised
        ]

    size = max(1, min(int(batch_size), MAX_BATCH_SIZE))
    output: list[dict] = []
    for offset in range(0, len(normalised), size):
        batch = normalised[offset : offset + size]
        if not budget.permit(billable=True, credits=len(batch)):
            output.extend(
                _record(
                    "outscraper",
                    source,
                    "email validation gated",
                    status="budget_exhausted",
                    cost_status="not_charged",
                    email=email,
                    safe_to_send=False,
                )
                for email in batch
            )
            continue

        params = [("query", email) for email in batch]
        params.append(("async", "false"))
        request_url = source + "?" + urlencode(params)
        http_status, body = client._request(
            "GET", request_url, {"X-API-KEY": client.key}
        )
        if http_status != 200 or not isinstance(body, dict):
            failure_status = (
                "auth_failed"
                if http_status in (401, 403)
                else "quota_required"
                if http_status in (402, 429)
                else "network_failed"
                if http_status == 0
                else "failed"
            )
            output.extend(
                _record(
                    "outscraper",
                    source,
                    "email validation failed closed",
                    status=failure_status,
                    cost_status="not_charged",
                    email=email,
                    safe_to_send=False,
                )
                for email in batch
            )
            continue

        rows = body.get("data") or []
        if not isinstance(rows, list):
            rows = []
        by_query: dict[str, dict] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            query = str(row.get("query") or "").strip().lower()
            if query:
                by_query[query] = row

        for email in batch:
            row = by_query.get(email)
            if row is None:
                output.append(
                    _validation_record(
                        email,
                        "UNKNOWN",
                        source=source,
                        status_details="missing_result",
                    )
                )
                continue
            output.append(
                _validation_record(
                    email,
                    str(row.get("status") or "UNKNOWN"),
                    source=source,
                    status_details=str(row.get("status_details") or ""),
                )
            )
    return output
