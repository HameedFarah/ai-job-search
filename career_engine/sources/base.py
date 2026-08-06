"""Adapter contract, DiscoveryJob and DiscoveryReport.

No-send policy (mirrors the Career Engine contract):

- Adapters only discover and normalize. They never mutate external state.
- Every job emitted by a probe defaults to ``live_status: unverified`` so the
  central engine scores it but blocks generation until the live-vacancy gate
  is satisfied by an authoritative verification source.
- Every report carries ``send_or_submit: false``.
"""

from __future__ import annotations

import html
import re
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from .dates import PostingDate, to_tracker_text, unknown
from .dedupe import dedupe_key
from .provenance import Provenance, provenance as make_provenance

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_HTML_WS_RE = re.compile(r"[ \t\r\f\v]+")
_HTML_NL_RE = re.compile(r"\n{3,}")


class SourceError(Exception):
    """Base error for source adapters."""


class SourceUnavailable(SourceError):
    """A source cannot run, usually because required credentials are absent."""


def html_to_text(value: str | None) -> str:
    """Convert job description HTML to plain text (best effort, stdlib only)."""
    if not value:
        return ""
    text = _HTML_TAG_RE.sub("\n", value)
    text = html.unescape(text)
    text = text.replace("\u00a0", " ")
    lines = [_HTML_WS_RE.sub(" ", line).strip() for line in text.splitlines()]
    text = "\n".join(line for line in lines if line)
    return _HTML_NL_RE.sub("\n\n", text).strip()


@dataclass(slots=True)
class DiscoveryJob:
    """A normalized discovery-only job record."""

    adapter_id: str
    company: str
    role: str
    location: str
    external_job_id: str
    detail_url: str
    application_url: str
    posted: PostingDate
    found_date: str
    description_html: str = ""
    description_text: str = ""
    provenance: Provenance | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def has_description(self) -> bool:
        text = (self.description_text or html_to_text(self.description_html)).strip()
        return len(text) >= 80

    def to_scanner_job(self, *, live_status: str = "unverified") -> dict[str, Any]:
        """Convert to a central-engine-compatible scanner job record.

        ``live_status`` defaults to ``unverified``: discovery never claims a
        vacancy is live. Generation therefore stays blocked until an owner or
        later scan verifies the vacancy against an authoritative source.
        """
        posted = self.posted or unknown(self.adapter_id)
        provenance = self.provenance
        if provenance is None:
            provenance = make_provenance(
                source_id=self.adapter_id,
                source_name=self.adapter_id,
                source_kind="adapter",
                official=True,
                extracted_from="adapter",
                detail_url=self.detail_url,
                raw_id=self.external_job_id,
            )
        return {
            "company": self.company,
            "role": self.role,
            "location": self.location,
            "source": self.adapter_id,
            "source_url": self.detail_url,
            "external_job_id": self.external_job_id,
            "application_url": self.application_url,
            "full_job_description": (self.description_text or html_to_text(self.description_html)).strip(),
            "live_status": live_status,
            "live_verified_at": "",
            "live_verification_source": "",
            "posting_date": to_tracker_text(posted),
            "posting_date_precision": posted.precision,
            "posting_date_source": posted.source,
            "found_date": self.found_date,
            "adapter": self.adapter_id,
            "provenance": provenance.to_data(),
        }

    def dedupe_key(self) -> str:
        return dedupe_key(
            source_id=self.adapter_id,
            external_job_id=self.external_job_id,
            source_url=self.detail_url,
            company=self.company,
            role=self.role,
            location=self.location,
        )

    def to_data(self) -> dict[str, Any]:
        data = asdict(self)
        data["posted"] = self.posted.to_data() if self.posted else None
        data["provenance"] = self.provenance.to_data() if self.provenance else None
        return data


@dataclass(slots=True)
class SourceResult:
    """Outcome of one adapter probe (success or failure, never thrown away)."""

    adapter_id: str
    status: str  # ok | empty | error | blocked | unavailable | unverified
    fetched_at: str
    jobs_fetched: int = 0
    error: str = ""
    blocked_reason: str = ""


@dataclass(slots=True)
class DiscoveryReport:
    """Bounded discovery probe output, scanner-compatible by construction."""

    schema_version: int = 1
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    adapter: str = ""
    company_identifier: str = ""
    send_or_submit: bool = False
    sources: list[SourceResult] = field(default_factory=list)
    jobs: list[dict[str, Any]] = field(default_factory=list)
    raw_jobs: list[dict[str, Any]] = field(default_factory=list)
    duplicates_dropped: int = 0
    blocked: list[dict[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def add_source(self, result: SourceResult) -> None:
        self.sources.append(result)

    def to_data(self) -> dict[str, Any]:
        data = asdict(self)
        data["sources"] = [asdict(item) for item in self.sources]
        data["summary"] = {
            "jobs_emitted": len(self.jobs),
            "duplicates_dropped": self.duplicates_dropped,
            "sources_tried": len(self.sources),
            "send_or_submit": self.send_or_submit,
        }
        return data

    def as_json(self) -> str:
        import json

        return json.dumps(self.to_data(), ensure_ascii=False, indent=2) + "\n"


class SourceAdapter(ABC):
    """Base class for discovery-only source adapters."""

    source_id: str = ""
    source_name: str = ""
    source_kind: str = "ats_api"
    official: bool = True

    def __init__(self, *, fixtures_dir: str | None = None) -> None:
        self._fixtures_dir = fixtures_dir

    @abstractmethod
    def search(
        self,
        *,
        company: str,
        location: str | None = None,
        limit: int = 10,
        fetch_full: bool = False,
        offline: bool = False,
    ) -> list[DiscoveryJob]:
        """Discover at most ``limit`` jobs for ``company``."""

    def describe(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "source_name": self.source_name,
            "source_kind": self.source_kind,
            "official": self.official,
        }

    def _fixture_path(self, filename: str) -> str:
        base = self._fixtures_dir
        if base:
            import os

            path = os.path.join(base, filename)
            if os.path.isfile(path):
                return path
        import pathlib

        return str(pathlib.Path(__file__).resolve().parent / "fixtures" / filename)
