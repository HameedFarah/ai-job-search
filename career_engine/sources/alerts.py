"""Normalize authenticated job-board email alerts into discovery-only jobs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .base import DiscoveryJob, SourceError, html_to_text
from .dates import unknown
from .provenance import provenance as make_provenance

SUPPORTED_ALERT_SOURCES: dict[str, str] = {
    "linkedin_alerts": "LinkedIn Job Alerts",
    "bayt_alerts": "Bayt Job Alerts",
    "naukrigulf_alerts": "NaukriGulf Job Alerts",
    "gulftalent_alerts": "GulfTalent Job Alerts",
    "indeed_alerts": "Indeed Job Alerts",
    "foundit_alerts": "Foundit Job Alerts",
    "gotogulf_alerts": "Gulf Jobs / Gotogulf Job Alerts",
}


@dataclass(slots=True)
class AlertListing:
    company: str
    role: str
    location: str
    url: str
    description: str = ""
    external_job_id: str = ""


def normalize_alert_listings(
    *,
    source_id: str,
    message_id: str,
    received_at: str,
    listings: list[AlertListing | dict[str, Any]],
) -> list[DiscoveryJob]:
    """Normalize already-extracted alert listings without reading the mailbox.

    Mailbox access remains connector-owned and authenticated. This function
    only normalizes structured items supplied by that connector.
    """
    if source_id not in SUPPORTED_ALERT_SOURCES:
        raise SourceError(f"Unsupported alert source: {source_id}")
    found_date = received_at[:10]
    if len(found_date) != 10:
        raise SourceError("received_at must begin with YYYY-MM-DD")
    jobs: list[DiscoveryJob] = []
    for index, raw in enumerate(listings):
        item = raw if isinstance(raw, AlertListing) else AlertListing(**raw)
        if not item.url.startswith(("http://", "https://")):
            continue
        external_id = item.external_job_id or f"{message_id}:{index}"
        jobs.append(
            DiscoveryJob(
                adapter_id=source_id,
                company=item.company.strip() or "Unknown employer",
                role=item.role.strip() or "Unspecified role",
                location=item.location.strip(),
                external_job_id=external_id,
                detail_url=item.url,
                application_url="",
                posted=unknown(source_id),
                found_date=found_date,
                description_html=item.description,
                description_text=html_to_text(item.description),
                provenance=make_provenance(
                    source_id=source_id,
                    source_name=SUPPORTED_ALERT_SOURCES[source_id],
                    source_kind="inbox",
                    official=False,
                    extracted_from="authenticated job-board email alert",
                    detail_url=item.url,
                    raw_id=external_id,
                    verification=(
                        "unverified email-alert candidate; verify against the "
                        "official employer or ATS posting"
                    ),
                ),
                extra={
                    "mailbox_message_id": message_id,
                    "requires_official_verification": True,
                },
            )
        )
    return jobs
