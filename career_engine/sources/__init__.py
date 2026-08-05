"""Source adapters for discovery-only job ingestion.

Design contract (mirrors the Career Engine no-send policy):

- Adapters only discover, normalize and emit scanner-compatible job records.
- They never send email, contact a recruiter or submit an application.
- Posting dates are never fabricated: every date carries a precision
  (``exact`` | ``day`` | ``month`` | ``unknown``) and its source field.
- Every emitted report carries ``send_or_submit: false`` and every scanner
  job defaults to ``live_status: unverified`` so the central engine can score
  it but cannot generate application content until the live-vacancy gate is
  satisfied with an authoritative verification source.

Package layout:

- :mod:`registry` - source registry + capability matrix.
- :mod:`dates` - posting-date parsing and precision model.
- :mod:`provenance` - strict provenance records.
- :mod:`dedupe` - deterministic dedupe keys and an in-memory store.
- :mod:`network` - bounded HTTP fetch (stdlib only, explicit timeouts).
- :mod:`base` - adapter contract, DiscoveryJob/DiscoveryReport.
- :mod:`adapters` - Greenhouse, Lever, Ashby, SmartRecruiters, Workable,
  JobPosting JSON-LD/sitemaps, search discovery, inbox contract.
- :mod:`cli` - ``registry`` / ``probe`` / ``verify`` / ``ingest`` commands.
"""

from .dates import PostingDate
from .provenance import Provenance
from .base import DiscoveryJob, DiscoveryReport, SourceAdapter, SourceError

__version__ = "1.0.0"

__all__ = [
    "DiscoveryJob",
    "DiscoveryReport",
    "PostingDate",
    "Provenance",
    "SourceAdapter",
    "SourceError",
    "__version__",
]
