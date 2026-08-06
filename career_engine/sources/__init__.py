"""Discovery-only job source adapters for the Career Engine.

Adapters never send email, contact recruiters or submit applications.
Non-official discovery records remain unverified until an official employer or
ATS source verifies them.
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
