"""Deterministic dedupe keys and an in-memory dedupe store.

Dedupe operates at two levels in the pipeline:

1. This module: first-pass dedupe inside a probe run, keyed by stable
   external identifiers when the source provides them, otherwise by a
   normalized (company, role, location) triple.
2. The central tracker: ``Tracker.ingest`` already deduplicates by
   (source, external_job_id, source_url, jd_hash). The framework feeds it the
   same identifiers so the two layers agree.

Keys are SHA-256 digests of normalized text so they are stable across runs.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

_WS_RE = re.compile(r"\s+")


def normalize_key_text(value: str) -> str:
    return _WS_RE.sub(" ", (value or "").strip().lower())


_REQ_RE = re.compile(r"(?:job|requisition|req(?:uisition)?)[/_-]?([a-z]?\d{4,})|(?<![A-Z0-9])(R\d{4,})(?![A-Z0-9])", re.I)
_OFFICIAL_REQ_HOSTS = ("myworkdayjobs.com", "oraclecloud.com", "successfactors.com", "taleo.net")
_EMPLOYER_ALIASES = {
    "wsp in africa": "wsp",
    "wsp africa": "wsp",
    "parsons corporation": "parsons",
}


def employer_family(value: str) -> str:
    normalized = normalize_key_text(value)
    return _EMPLOYER_ALIASES.get(normalized, normalized)


def stable_vacancy_identity(*, company: str, role: str = "", external_job_id: str = "", source_url: str = "") -> str:
    """Cross-source identity for a stable official ATS requisition."""
    normalized_url = normalize_key_text(source_url)
    official_surface = any(host in normalized_url for host in _OFFICIAL_REQ_HOSTS)
    match = _REQ_RE.search(normalized_url) if official_surface else None
    stable_id = ((match.group(1) or match.group(2)) if match else "").lower()
    if not stable_id and official_surface:
        candidate = normalize_key_text(external_job_id)
        if candidate and not candidate.startswith("synthetic"):
            stable_id = candidate
    if stable_id and not stable_id.startswith("synthetic"):
        return f"{employer_family(company)}|{stable_id}"
    return ""


def dedupe_key(
    *,
    source_id: str,
    external_job_id: str = "",
    source_url: str = "",
    company: str = "",
    role: str = "",
    location: str = "",
) -> str:
    """A stable key: external id when present, else a normalized triple.

    When both ``external_job_id`` and ``source_url`` are present the URL is
    included so different boards under the same adapter never collide.
    """
    stable = stable_vacancy_identity(company=company, role=role, external_job_id=external_job_id, source_url=source_url)
    if stable:
        return hashlib.sha256(("vacancy|" + stable).encode("utf-8")).hexdigest()
    parts = [normalize_key_text(source_id)]
    external = normalize_key_text(external_job_id)
    url = normalize_key_text(source_url)
    if external:
        parts.append(f"external:{external}")
    if url:
        parts.append(f"url:{url}")
    if not external and not url:
        parts.append(f"company:{normalize_key_text(company)}")
        parts.append(f"role:{normalize_key_text(role)}")
        parts.append(f"location:{normalize_key_text(location)}")
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


@dataclass(slots=True)
class DedupeStore:
    """In-memory dedupe store for one probe run (not persisted)."""

    seen: set[str] = field(default_factory=set)

    def is_duplicate(self, key: str) -> bool:
        return key in self.seen

    def add(self, key: str) -> bool:
        """Return True if the key was newly added, False if it already existed."""
        if key in self.seen:
            return False
        self.seen.add(key)
        return True

    def __len__(self) -> int:
        return len(self.seen)
