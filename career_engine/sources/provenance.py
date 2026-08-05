"""Strict provenance for discovery-only job ingestion.

Every discovered job carries a Provenance record that answers four questions:

- which adapter/source produced it (``source_id`` / ``source_name``);
- whether that source is an official employer publication (``official``);
- when it was fetched and whether the fetch succeeded;
- which upstream field/endpoint the record was extracted from.

Records from non-official or unverified sources are never ingested as
authoritative; they are either discarded or held as verification candidates.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True, slots=True)
class Provenance:
    source_id: str
    source_name: str
    source_kind: str
    official: bool
    fetched_at: str
    fetch_success: bool
    extracted_from: str = ""
    detail_url: str = ""
    raw_id: str = ""
    verification: str = ""  # e.g. "official employer careers page", "unverified discovery candidate"
    notes: list[str] = field(default_factory=list)

    def to_data(self) -> dict[str, Any]:
        return asdict(self)


def provenance(
    *,
    source_id: str,
    source_name: str,
    source_kind: str,
    official: bool,
    extracted_from: str,
    detail_url: str = "",
    raw_id: str = "",
    verification: str = "",
    fetched_at: str | None = None,
    fetch_success: bool = True,
    notes: list[str] | None = None,
) -> Provenance:
    return Provenance(
        source_id=source_id,
        source_name=source_name,
        source_kind=source_kind,
        official=official,
        fetched_at=fetched_at or utc_now_iso(),
        fetch_success=fetch_success,
        extracted_from=extracted_from,
        detail_url=detail_url,
        raw_id=raw_id,
        verification=verification or ("official employer publication" if official else "unverified discovery candidate"),
        notes=list(notes or []),
    )
