#!/usr/bin/env python3
"""Compatibility entrypoint for the current REGA-priority Outscraper workflow.

The prior implementation was frozen to the obsolete 1,236-row Send Queue and
757-company / 728-unresolved REGA snapshot. Keeping that implementation reachable
from an old timer/service could waste provider credit or fail closed as the live
Career Engine grows.

This entrypoint delegates live execution to ``run_rega_priority_scan`` and also
strengthens its zero-paid-cost discovery step: Qwant is tried first, then the
local SearXNG instance is allowed to use its configured/default engines, followed
by bounded DuckDuckGo/Google/Bing fallbacks. Search results are still independently
identity-verified before a domain can reach Outscraper.

Paid execution remains REGA-only, with one domain-contact lookup and one best
mailbox validation at most per company, a $1 default balance reserve, no Balady
or engineering-office Outscraper path, and no Gmail send path.

``mailbox_route_kind`` remains here for legacy read-only/preparation imports.
"""
from __future__ import annotations

import argparse
from html.parser import HTMLParser
import os
import sys
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
DIRECT_LOCALS = {"hr", "career", "careers", "job", "jobs", "recruit", "recruitment", "talent", "hiring"}
GENERAL_LOCALS = {"info", "contact", "contactus", "hello", "admin", "office", "enquiry", "enquiries", "inquiry", "inquiries"}
EXCLUDED_LOCALS = {
    "support", "privacy", "legal", "abuse", "finance", "financial", "investor", "investors",
    "ir", "billing", "accounts", "accounting", "sales", "security", "webmaster",
}


def mailbox_route_kind(email: str) -> str:
    local = email.split("@", 1)[0].lower() if "@" in email else ""
    if local in EXCLUDED_LOCALS:
        return "excluded"
    if local in DIRECT_LOCALS:
        return "recruitment"
    if local in GENERAL_LOCALS:
        return "general"
    return "other"


def _normalize_results(data: dict, engine: str, limit: int) -> list[dict]:
    out: list[dict] = []
    for row in (data.get("results") or [])[:limit]:
        if not isinstance(row, dict):
            continue
        url = str(row.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            continue
        out.append({
            "url": url,
            "title": str(row.get("title") or "").strip(),
            "description": str(row.get("content") or "").strip()[:2000],
            "engine": engine,
        })
    return out


class _SearxHtmlResultsParser(HTMLParser):
    """Extract the same bounded result fields from SearXNG's local HTML surface."""

    def __init__(self, limit: int) -> None:
        super().__init__(convert_charrefs=True)
        self.limit = limit
        self.results: list[dict] = []
        self._article_depth = 0
        self._current: dict[str, str] | None = None
        self._capture_title = False
        self._capture_description = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_map = {key: value or "" for key, value in attrs}
        classes = set(attrs_map.get("class", "").split())
        if tag == "article" and "result" in classes and len(self.results) < self.limit:
            self._article_depth = 1
            self._current = {"url": "", "title": "", "description": "", "engine": "searxng-html"}
            return
        if not self._current:
            return
        self._article_depth += 1
        if tag == "a" and not self._current["url"]:
            href = attrs_map.get("href", "").strip()
            if href.startswith(("http://", "https://")):
                self._current["url"] = href
        if tag == "h3":
            self._capture_title = True
        elif tag == "p" and "content" in classes:
            self._capture_description = True

    def handle_endtag(self, tag: str) -> None:
        if not self._current:
            return
        if tag == "h3":
            self._capture_title = False
        elif tag == "p":
            self._capture_description = False
        if tag == "article" and self._article_depth == 1:
            if self._current["url"]:
                self._current["title"] = " ".join(self._current["title"].split())
                self._current["description"] = " ".join(self._current["description"].split())[:2000]
                self.results.append(self._current)
            self._current = None
            self._article_depth = 0
            return
        self._article_depth = max(0, self._article_depth - 1)

    def handle_data(self, data: str) -> None:
        if not self._current:
            return
        if self._capture_title:
            self._current["title"] += data + " "
        elif self._capture_description:
            self._current["description"] += data + " "


def _normalize_html_results(text: str, limit: int) -> list[dict]:
    parser = _SearxHtmlResultsParser(limit)
    parser.feed(text)
    return parser.results[:limit]


def free_searxng_search(query: str, limit: int = 5) -> list[dict]:
    """Free/local REGA discovery with bounded multi-engine fallback.

    No Outscraper, DataForSEO, Firecrawl, or other paid provider is called here.
    The scanner's independent identity verification remains mandatory after this
    discovery step.
    """
    from career_engine.rega_enrichment.discovery import searxng_qwant_search

    qwant = searxng_qwant_search(query, limit=limit)
    if qwant:
        return qwant

    base = os.getenv("SEARXNG_URL", "http://127.0.0.1:8888").rstrip("/")
    attempts: list[tuple[str, dict[str, str]]] = [
        ("searxng-default", {"q": query, "format": "json"}),
        ("searxng-duckduckgo", {"q": query, "format": "json", "engines": "duckduckgo"}),
        ("searxng-google", {"q": query, "format": "json", "engines": "google"}),
        ("searxng-bing", {"q": query, "format": "json", "engines": "bing"}),
    ]
    for label, params in attempts:
        try:
            with httpx.Client(timeout=12) as client:
                response = client.get(
                    f"{base}/search",
                    params=params,
                    headers={"Accept": "application/json"},
                )
                response.raise_for_status()
                found = _normalize_results(response.json(), label, limit)
                if found:
                    return found
        except Exception:
            continue

    # Some upstream engines can fail or challenge only on SearXNG's JSON route
    # while the ordinary local results page still contains usable results from
    # other configured free engines. Reuse that first-party local surface rather
    # than incorrectly treating an adapter failure as "no official domain".
    try:
        with httpx.Client(timeout=12) as client:
            response = client.get(
                f"{base}/search",
                params={"q": query},
                headers={"Accept": "text/html"},
            )
            response.raise_for_status()
            found = _normalize_html_results(response.text, limit)
            if found:
                return found
    except Exception:
        pass
    return []


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Required for live provider/Sheet execution")
    # Legacy arguments are accepted so an older service/timer remains compatible.
    parser.add_argument("--monitor-dir", default="")
    parser.add_argument("--rega-input", default="")
    parser.add_argument("--rega-workers", type=int, default=4)
    parser.add_argument("--rega-delay", type=float, default=0.2)
    # Current useful controls.
    parser.add_argument("--reserve-usd", type=float, default=1.00)
    parser.add_argument("--max-companies", type=int, default=0)
    args, _unknown = parser.parse_known_args()

    if not args.apply:
        raise SystemExit("refusing live REGA priority scan without --apply")

    # Import after parsing; the target module explicitly disables paid Firecrawl
    # and DataForSEO discovery fallbacks for this REGA run.
    from runtime import run_rega_priority_scan as scanner

    # Replace only the scanner module's discovery function. Its downstream
    # verify_candidate step still independently establishes company identity.
    scanner.searxng_qwant_search = free_searxng_search

    forwarded = [
        str(REPO_ROOT / "runtime" / "run_rega_priority_scan.py"),
        "--apply",
        "--reserve-usd",
        str(max(0.0, float(args.reserve_usd))),
    ]
    if int(args.max_companies) > 0:
        forwarded += ["--max-companies", str(int(args.max_companies))]
    sys.argv = forwarded
    return scanner.main()


if __name__ == "__main__":
    raise SystemExit(main())
