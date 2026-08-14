"""Managed external ATS providers reused from Fighter90/career-ops-ui.

This is intentionally a thin dependency manifest, not a copy of the upstream
implementations. Career Engine keeps its provenance/no-send/scoring contract;
the portal-specific HTTP/parsing logic stays maintained in career-ops-ui.

The lock SHA is reviewed before activation. The weekly managed-source watch
reports newer upstream SHAs but never changes the runtime pin automatically.
"""

from __future__ import annotations

UPSTREAM_REPO = "Fighter90/career-ops-ui"
UPSTREAM_BRANCH = "main"
UPSTREAM_REF = "308722f2cc8be3b5dd591d5566ee97b56b90cf44"
DEFAULT_CHECKOUT = "/home/hameedo/projects/career-ops-ui"

# Generic ATS providers prioritized for GCC/Jordan employers, followed by
# useful global ATS coverage. These IDs correspond to upstream adapter modules
# under server/lib/portals/adapters/<id>.mjs.
PROVIDERS: dict[str, dict[str, object]] = {
    "workday": {"priority": 1, "region": "gcc+jordan+global", "posting_date": "exact/partial"},
    "successfactors": {"priority": 1, "region": "gcc+jordan+global", "posting_date": "unknown"},
    "oraclecloud": {"priority": 1, "region": "gcc+jordan+global", "posting_date": "exact"},
    "icims": {"priority": 1, "region": "gcc+jordan+global", "posting_date": "unknown"},
    "avature": {"priority": 1, "region": "gcc+jordan+global", "posting_date": "provider"},
    "eightfold": {"priority": 1, "region": "gcc+jordan+global", "posting_date": "provider"},
    "jobvite": {"priority": 2, "region": "global", "posting_date": "provider"},
    "jibeapply": {"priority": 2, "region": "global", "posting_date": "provider"},
    "bamboohr": {"priority": 2, "region": "global", "posting_date": "provider"},
    "breezy": {"priority": 2, "region": "global", "posting_date": "provider"},
    "comeet": {"priority": 2, "region": "global", "posting_date": "provider"},
    "teamtailor": {"priority": 2, "region": "global", "posting_date": "provider"},
    # Existing native Career Engine adapters remain available as fallbacks and
    # for regression comparison. Prefer managed upstream implementations when
    # the managed checkout is healthy.
    "greenhouse": {"priority": 2, "region": "global", "posting_date": "exact"},
    "lever": {"priority": 2, "region": "global", "posting_date": "exact"},
    "ashby": {"priority": 2, "region": "global", "posting_date": "exact"},
    "smartrecruiters": {"priority": 2, "region": "global", "posting_date": "exact"},
}


def managed_source_id(provider: str) -> str:
    return f"managed_{provider}"


def provider_from_source_id(source_id: str) -> str:
    prefix = "managed_"
    if not source_id.startswith(prefix):
        raise KeyError(source_id)
    provider = source_id[len(prefix):]
    if provider not in PROVIDERS:
        raise KeyError(source_id)
    return provider
