"""Mailbox alert inbox adapter contract (Gmail / LinkedIn job alerts).

Inbound-only. Recruiter emails and job-alert digests are a discovery surface:
an ``InboxMessage`` is normalized into a discovery record and must then be
verified against the official employer posting (via the jsonld gate) before
it can be ingested as authoritative.

This adapter performs no network I/O. Live mailbox reading requires an
authorized connector: the repository-native Gmail/gws path currently reports
``invalid_grant`` (documented in CAREER_ENGINE_V1_IMPLEMENTATION.md), and
LinkedIn alerts require a connected browser session. Until one of those is
available the adapter is blocked, and that state is reported rather than
silently skipped. Mailbox-derived data is runtime data and is never committed.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from ..base import DiscoveryJob, SourceAdapter, SourceError, html_to_text
from ..dates import unknown
from ..provenance import provenance as make_provenance


@dataclass(slots=True)
class InboxMessage:
    source_id: str  # inbox_gmail | linkedin_alerts
    message_id: str
    sender: str
    subject: str
    received_at: str
    body_text: str
    attachments: list[str] | None = None

    def to_data(self) -> dict[str, Any]:
        return asdict(self)


class InboxSourceAdapter(SourceAdapter):
    """Blocked-by-default adapter; provides the ingestion contract only."""

    source_id = "inbox_gmail"
    source_name = "Gmail Recruiter / Job-Alert Inbox"
    source_kind = "inbox"
    official = False

    def search(
        self,
        *,
        company: str,
        location: str | None = None,
        limit: int = 10,
        fetch_full: bool = False,
        offline: bool = False,
    ) -> list[DiscoveryJob]:
        raise SourceError(
            f"{self.source_id} is blocked: no authorized mailbox connection. "
            "Repository-native Gmail/gws reports invalid_grant; a connected "
            "ChatGPT Gmail connector or a LinkedIn-alert session is required."
        )

    def from_message(
        self,
        message: InboxMessage,
        *,
        company: str,
        role: str,
        description: str | None = None,
    ) -> DiscoveryJob:
        """Normalize one inbox message into a discovery job (no network I/O)."""
        if not message.body_text and not description:
            raise SourceError("Inbox message has no body text and no description was supplied")
        body = (description or message.body_text).strip()
        return DiscoveryJob(
            adapter_id=message.source_id,
            company=company.strip(),
            role=role.strip(),
            location="",
            external_job_id=message.message_id,
            detail_url="",
            application_url="",
            posted=unknown(message.source_id),
            found_date=message.received_at[:10],
            description_html=body,
            description_text=html_to_text(body),
            provenance=make_provenance(
                source_id=message.source_id,
                source_name=self.source_name if message.source_id == self.source_id else "LinkedIn Job Alerts (Inbox Surface)",
                source_kind=self.source_kind,
                official=False,
                extracted_from="mailbox alert (inbound; requires official verification)",
                raw_id=message.message_id,
                verification="unverified inbox alert - verify against the official employer posting",
            ),
            extra={"sender": message.sender, "subject": message.subject},
        )
