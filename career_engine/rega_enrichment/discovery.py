"""Discovery via Hermes-maintained web backend (Firecrawl).

Never hand-scrape Bing/Qwant HTML. Use the configured Hermes web backend
(Firecrawl). Every request/result carries immutable company_id, license_no, query_id.
Concurrency must never rely on result order — results are keyed by IDs.
"""

from __future__ import annotations

import hashlib
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import re
import httpx

from .config import QUERY_TEMPLATES
from .models import CandidateResult, CompanyRecord, QuerySpec

FIRECRAWL_SEARCH_URL = "https://api.firecrawl.dev/v1/search"
# Fallback to Hermes plugin if direct API not available handled via direct httpx

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

def _read_hermes_env(key: str) -> str:
    # Direct read from Hermes .env to support system python without hermes_cli venv
    for p in [Path("/home/hameedo/.hermes/.env"), Path.home() / ".hermes" / ".env"]:
        if p.is_file():
            try:
                for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
                    line=line.strip()
                    if line.startswith(key+"="):
                        return line.split("=",1)[1].strip().strip('"').strip("'")
            except Exception:
                pass
    return ""

def api_key() -> str:
    # Prefer Hermes env via hermes_cli.config.get_env_value, fallback to process env and .env file
    try:
        import importlib.util
        if importlib.util.find_spec("hermes_cli.config"):
            from hermes_cli.config import get_env_value  # type: ignore
            v = get_env_value("FIRECRAWL_API_KEY")
            if v:
                return v.strip()
    except Exception:
        pass
    v = os.getenv("FIRECRAWL_API_KEY", "").strip()
    if v:
        return v
    return _read_hermes_env("FIRECRAWL_API_KEY")

def _brand(company: CompanyRecord) -> str:
    """Extract brand: first 1-2 distinctive tokens, fallback to first words."""
    import re
    from .config import GENERIC_TOKENS
    toks = [x for x in re.findall(r"[a-z0-9]+", company.english_name.lower()) if len(x) >= 3 and x not in GENERIC_TOKENS]
    if toks:
        # Take up to 2 most distinctive (longest)
        toks_sorted = sorted(toks, key=len, reverse=True)
        brand = " ".join(toks_sorted[:2])
        # Keep original order for brand? Use as is
        # Prefer original order first token(s)
        if len(toks) >= 1:
            brand = toks[0]
            if len(toks) >= 2 and len(toks[1]) >= 4:
                brand = f"{toks[0]} {toks[1]}"
        return brand
    # fallback: first two words
    parts = company.english_name.split()
    return " ".join(parts[:2]) if parts else company.english_name

def generate_queries(company: CompanyRecord) -> list[QuerySpec]:
    out: list[QuerySpec] = []
    brand = _brand(company)
    # Expand templates to include brand variants for better recall
    expanded_templates = list(QUERY_TEMPLATES)
    # Add brand-based queries if brand differs from full english
    if brand.lower() not in company.english_name.lower() or brand != company.english_name:
        expanded_templates.extend([
            '"{brand}" Saudi Arabia',
            '"{brand}" official website',
        ])
    for idx, tmpl in enumerate(expanded_templates):
        # Interpolate brand
        tmpl_filled = tmpl.replace("{brand}", brand)
        # Skip Arabic template if arabic name blank
        if "{arabic}" in tmpl_filled and not company.arabic_name.strip():
            continue
        # Skip location template if location blank
        if "{location}" in tmpl_filled and not company.location.strip():
            continue
        # Skip brand template if brand blank
        if "{brand}" in tmpl_filled and not brand.strip():
            continue
        qs = QuerySpec.make(company, tmpl_filled, f"q{idx+1}")
        out.append(qs)
    return out

def firecrawl_search(query: str, limit: int = 5) -> list[dict[str, Any]]:
    key = api_key()
    if not key:
        raise RuntimeError("FIRECRAWL_API_KEY is not configured — Hermes web backend unavailable")
    payload = {
        "query": query,
        "limit": limit,
        "scrapeOptions": {"formats": ["markdown"]},
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    # Retry with exponential backoff for transient 502/timeout
    last_exc = None
    for attempt in range(3):
        try:
            with httpx.Client(timeout=25) as client:
                resp = client.post(FIRECRAWL_SEARCH_URL, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                break
        except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.HTTPStatusError) as e:
            last_exc = e
            # 429, 502, 503, timeout are retryable
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status in (429, 502, 503, 504) or isinstance(e, (httpx.ReadTimeout, httpx.ConnectTimeout)):
                time.sleep(1.5 * (2 ** attempt))
                continue
            raise
        except Exception as e:
            last_exc = e
            time.sleep(1.0 * (2 ** attempt))
            continue
    else:
        # exhausted retries
        raise last_exc if last_exc else RuntimeError("Firecrawl search failed")
    # data already set from successful attempt
    # Normalize response shapes: Firecrawl returns {success, data: {web: [...]}} or {data: [...]}
    web = []
    if isinstance(data, dict):
        d = data.get("data") or {}
        if isinstance(d, dict):
            web = d.get("web") or d.get("results") or []
        elif isinstance(d, list):
            web = d
        # Also check top-level
        if not web and "web" in data:
            web = data["web"]
    # Fallback to raw list if needed
    if not web and isinstance(data, list):
        web = data
    normalized = []
    for item in web[:limit]:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or item.get("link") or "").strip()
        if not url:
            continue
        normalized.append({
            "url": url,
            "title": str(item.get("title") or "").strip(),
            "description": str(item.get("description") or item.get("content") or item.get("markdown") or "").strip()[:2000],
            "engine": "firecrawl",
        })
    return normalized

def searxng_search(query: str, limit: int = 5) -> list[dict[str, Any]]:
    """Optional fallback via local SearXNG (loopback). Used only if Firecrawl fails."""
    base = os.getenv("SEARXNG_URL", "http://127.0.0.1:8888").rstrip("/")
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.get(f"{base}/search", params={"q": query, "format": "json", "engines": "qwant,bing"}, headers={"Accept": "application/json"})
            resp.raise_for_status()
            data = resp.json()
        results = data.get("results", [])[:limit]
        out = []
        for r in results:
            out.append({"url": r.get("url",""), "title": r.get("title",""), "description": r.get("content","")[:2000], "engine": ",".join(r.get("engines",[])) or "searxng"})
        return out
    except Exception:
        return []

def discover_company(company: CompanyRecord, limit_per_query: int = 5, delay_s: float = 0.8) -> list[CandidateResult]:
    queries = generate_queries(company)
    candidates: list[CandidateResult] = []
    seen_urls: set[str] = set()
    for qs in queries:
        raw_results: list[dict[str, Any]] = []
        try:
            raw_results = firecrawl_search(qs.query_text, limit=limit_per_query)
        except Exception as e:
            # Firecrawl rate limit / error — try SearXNG fallback for this query, then continue
            # Do not fail entire company on one query failure
            try:
                raw_results = searxng_search(qs.query_text, limit=limit_per_query)
            except Exception:
                raw_results = []
        ts = utc_now()
        for pos, item in enumerate(raw_results, 1):
            url = item.get("url","").strip()
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            # Skip obviously non-HTTP
            if not url.startswith(("http://","https://")):
                continue
            candidates.append(CandidateResult(
                company_id=company.company_id,
                license_no=company.license_no,
                query_id=qs.query_id,
                url=url,
                title=item.get("title","")[:500],
                description=item.get("description","")[:2000],
                engine=item.get("engine","unknown"),
                position=pos,
                retrieved_at=ts,
            ))
        # Throttle even though Firecrawl is paid — respect provider limits
        time.sleep(delay_s)
    return candidates
