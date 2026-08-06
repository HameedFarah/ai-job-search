"""Source adapters for discovery-only job ingestion.

Design contract (mirrors the Career Engine no-send policy):

- Adapters only discover, normalize and emit scanner-compatible job records.
- They never send email, contact a recruiter or submit an application.
- Posting dates are never fabricated: every date carries a precision
  (``exact`` | ``day`` | ``month`` | ``unknown``) and its source field.
- Every emitted report carries ``send_or_submit: false`` and every scanner
  job defaults to ``live_status: unverified`` until authoritative official
  employer or ATS verification succeeds.

Package layout:

- :mod:`registry` - source registry, capability matrix and value-free runtime status.
- :mod:`dates` - posting-date parsing and precision model.
- :mod:`provenance` - strict provenance records.
- :mod:`dedupe` - deterministic dedupe keys and an in-memory store.
- :mod:`network` - bounded HTTP fetch (stdlib only, explicit timeouts).
- :mod:`base` - adapter contract, DiscoveryJob/DiscoveryReport and unavailable-source handling.
- :mod:`adapters` - official ATS/employer adapters plus discovery-only Brave, Jooble and Careerjet.
- :mod:`alerts` - authenticated job-alert normalization without direct board scraping.
- :mod:`routing` - fail-closed normal/residential/denied route policy.
- :mod:`cli` - ``registry`` / ``probe`` / ``verify`` / ``route-check`` / ``ingest`` commands.
"""

from .base import (
    DiscoveryJob,
    DiscoveryReport,
    SourceAdapter,
    SourceError,
    SourceUnavailable,
)
from .dates import PostingDate
from .provenance import Provenance

__version__ = "1.0.0"

__all__ = [
    "DiscoveryJob",
    "DiscoveryReport",
    "PostingDate",
    "Provenance",
    "SourceAdapter",
    "SourceError",
    "SourceUnavailable",
    "__version__",
]
