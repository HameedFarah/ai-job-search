"""Search-engine discovery adapter (Google/Bing/DuckDuckGo).

Discovery-only by design: this adapter emits verification *candidates*, never
authoritative job records. Candidates are promoted to official provenance only
through :meth:`jsonld.JsonLdAdapter.verify_official`, which requires the
employer's own page (JobPosting JSON-LD) or a known official ATS host.

Engines:

- ``duckduckgo`` - public Instant Answer API, no key, best-effort (used by the
  default probe; the API is a JSON API, not HTML scraping).
- ``google`` / ``bing`` - require API keys; blocked in this repository until a
  key is configured. Reported as blocked sources, never silently skipped.

No employer credentials are ever needed. Results never include email or
contact discovery; this is vacancy discovery only.
"""

from __future__ import annotations

import json
import urllib.parse
from dataclasses import asdict, dataclass
from typing import Any

from .. import network
from ..base import SourceAdapter, SourceError
from ..provenance import Provenance

DDG_API = "https://api.duckduckgo.com/"

BLOCKED_ENGINES = {
    "google": "Google Custom Search requires an API key (SERPAPI/GOOGLE_API_KEY not configured)",
    "bing": "Bing Search API requires an API key (BING_API_KEY not configured)",
}


@dataclass(slots=True)
class DiscoveryCandidate:
    url: str
    title: str
    engine: str
    source_hint: str = ""

    def to_data(self) -> dict[str, Any]:
        return asdict(self)


class SearchDiscoveryAdapter(SourceAdapter):
    source_id = "search_discovery"
    source_name = "Search-Engine Discovery -> Official Verification"
    source_kind = "discovery"
    official = False

    def search(
        self,
        *,
        company: str,
        location: str | None = None,
        limit: int = 10,
        fetch_full: bool = False,
        offline: bool = False,
    ) -> list:
        """Discover candidate URLs. Raises for blocked engines.

        Returns a list of :class:`DiscoveryCandidate`; these are NOT jobs and
        must pass ``jsonld.verify_official`` before any ingest.
        """
        query = f"{company} careers jobs"
        if location:
            query += f" {location}"
        if offline:
            with open(self._fixture_path("discovery-duckduckgo.json"), encoding="utf-8") as handle:
                payload = json.load(handle)
            candidates = self._parse_ddg(payload, query)
        else:
            payload = network.fetch_json(DDG_API + "?" + urllib.parse.urlencode({"q": query, "format": "json", "no_html": 1, "no_redirect": 1}))
            candidates = self._parse_ddg(payload, query)
        return candidates[:limit]

    def verify(
        self,
        url: str,
        *,
        offline: bool = False,
        verifier=None,
    ) -> Provenance:
        """Verify a candidate URL against the employer's own page."""
        from .jsonld import JsonLdAdapter

        verifier = verifier or JsonLdAdapter()
        return verifier.verify_official(url, offline=offline)

    def blocked_engines(self) -> dict[str, str]:
        return dict(BLOCKED_ENGINES)

    def _parse_ddg(self, payload: Any, query: str) -> list[DiscoveryCandidate]:
        if not isinstance(payload, dict):
            return []
        candidates: list[DiscoveryCandidate] = []
        for topic in payload.get("RelatedTopics", []) or []:
            if isinstance(topic, dict) and topic.get("Topics"):
                for nested in topic["Topics"]:
                    self._add_candidate(candidates, nested)
            else:
                self._add_candidate(candidates, topic)
        return candidates

    def _add_candidate(self, candidates: list[DiscoveryCandidate], topic: Any) -> None:
        if not isinstance(topic, dict):
            return
        url = str(topic.get("FirstURL") or "").strip()
        if not url.startswith("http"):
            return
        candidates.append(
            DiscoveryCandidate(
                url=url,
                title=str(topic.get("Text") or topic.get("Result") or "").strip()[:200],
                engine="duckduckgo",
            )
        )
