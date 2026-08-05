"""Fail-closed guards for synthetic and fixture vacancy data.

Production tracker writes must never accept RFC 2606 example domains, localhost,
offline fixture records, or source-adapter fixture provenance. These checks run
before any tracker layout, CSV, JSON, event, or artifact write.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

_RESERVED_HOSTS = {"localhost", "localhost.localdomain"}
_RESERVED_SUFFIXES = (".example", ".invalid", ".test", ".localhost")


def synthetic_url_reason(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    except ValueError:
        return "malformed_url"
    host = (parsed.hostname or "").strip(".").lower()
    if not host:
        return ""
    labels = host.split(".")
    if host in _RESERVED_HOSTS or any(host.endswith(suffix) for suffix in _RESERVED_SUFFIXES):
        return f"reserved_host:{host}"
    if "example" in labels:
        return f"rfc2606_example_host:{host}"
    return ""


def fixture_payload_reason(payload: dict[str, Any]) -> str:
    for key in ("source_url", "application_url", "detail_url"):
        reason = synthetic_url_reason(payload.get(key))
        if reason:
            return f"{key}:{reason}"
    provenance = payload.get("provenance") or payload.get("discovery_provenance") or {}
    if isinstance(provenance, dict):
        if provenance.get("offline_fixture") is True or provenance.get("fixture") is True:
            return "fixture_provenance"
        extracted = str(provenance.get("extracted_from", "")).lower()
        if "fixture" in extracted or extracted == "offline":
            return "fixture_provenance"
        for key in ("detail_url", "source_url", "application_url"):
            reason = synthetic_url_reason(provenance.get(key))
            if reason:
                return f"provenance.{key}:{reason}"
    company = str(payload.get("company", "")).strip().lower()
    if company in {"oasis development co", "example company", "test company"}:
        return f"fixture_company:{company}"
    return ""


def reject_fixture_payload(payload: dict[str, Any]) -> None:
    reason = fixture_payload_reason(payload)
    if reason:
        raise ValueError(f"Synthetic/offline fixture job rejected before tracker write: {reason}")
