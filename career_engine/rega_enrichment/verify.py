"""Candidate verification — fetch and independently verify identity before accepting domain.

Critical rule: search results are discovery only. Never promote a search result
directly. Every candidate must be fetched and identity-verified.
"""

from __future__ import annotations

import re
import os
import httpx
from urllib.parse import urlparse, urlsplit
from datetime import datetime, timezone

from .config import GENERIC_TOKENS, BLOCKED_HOSTS
from .models import CandidateResult, CompanyRecord, distinctive_tokens, hostname_tokens

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?:\+966|966|0)?\s*5\d[\s\-]?\d{3}[\s\-]?\d{4}|\+966[\s\-]?1\d[\s\-]?\d{3}[\s\-]?\d{4}|9200\d{5}")

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

def _read_hermes_env(key: str) -> str:
    from pathlib import Path
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
    import os
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

def is_blocked(host: str) -> bool:
    h = host.lower()
    return any(b in h for b in BLOCKED_HOSTS)

def fetch_via_firecrawl_extract(url: str) -> tuple[str, str, str]:
    """Fetch via Firecrawl extract, returns (title, markdown, html)."""
    key = api_key()
    if not key:
        raise RuntimeError("FIRECRAWL_API_KEY missing for extract")
    endpoint = "https://api.firecrawl.dev/v1/scrape"
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    payload = {"url": url, "formats": ["markdown", "html"], "onlyMainContent": True, "waitFor": 1200}
    with httpx.Client(timeout=45) as client:
        resp = client.post(endpoint, json=payload, headers=headers)
        # Handle rate limits
        if resp.status_code == 429:
            # Surface for caller to backoff
            resp.raise_for_status()
        resp.raise_for_status()
        data = resp.json()
    d = data.get("data") or data
    title = str(d.get("title") or d.get("metadata", {}).get("title") or "")
    markdown = str(d.get("markdown") or d.get("content") or "")
    html = str(d.get("html") or "")
    return title, markdown, html

def fetch_direct(url: str) -> tuple[str, str]:
    """Fallback direct fetch with httpx."""
    headers = {"User-Agent": "Mozilla/5.0 (compatible; REGA-enrichment/1.0; +https://farahdigital.com)"}
    with httpx.Client(timeout=20, follow_redirects=True, headers=headers) as client:
        resp = client.get(url)
        resp.raise_for_status()
        text = resp.text
        # Naive title extraction
        m = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
        title = re.sub(r"<[^>]+>", " ", m.group(1)).strip() if m else ""
        # Strip tags for content
        content = re.sub(r"<script.*?</script>", " ", text, flags=re.I | re.S)
        content = re.sub(r"<style.*?</style>", " ", content, flags=re.I | re.S)
        content = re.sub(r"<[^>]+>", " ", content)
        content = re.sub(r"\s+", " ", content).strip()[:20000]
        return title, content

def verify_candidate(candidate: CandidateResult, company: CompanyRecord) -> CandidateResult:
    """Verify candidate site identity. Mutates candidate with verification fields."""
    url = candidate.url.strip()
    host = (urlsplit(url).hostname or "").lower()
    if not host or is_blocked(host):
        candidate.verification_status = "rejected"
        candidate.verification_method = "rejected_unrelated"
        candidate.verification_score = -999
        candidate.verification_evidence = f"Blocked host: {host}"
        return candidate

    # Distinctive tokens
    eng_tokens = distinctive_tokens(company.english_name, GENERIC_TOKENS)
    arabic_name = company.arabic_name.strip()
    # Hostname normalized
    host_norm = hostname_tokens(host)

    # Fetch page
    title = ""
    content = ""
    source_type = "firecrawl_extract"
    try:
        t, md, _ = fetch_via_firecrawl_extract(url)
        title = t or candidate.title
        content = md or candidate.description
        if not content:
            # fallback direct
            t2, c2 = fetch_direct(url)
            title = title or t2
            content = content or c2
            source_type = "direct_fetch"
    except Exception as e:
        # Try direct fetch as fallback
        try:
            t2, c2 = fetch_direct(url)
            title = t2 or candidate.title
            content = c2 or candidate.description
            source_type = "direct_fetch"
        except Exception as e2:
            candidate.verification_status = "rejected"
            candidate.verification_method = "rejected_unrelated"
            candidate.verification_score = -10
            candidate.verification_evidence = f"Fetch failed: {type(e).__name__}: {e} / {type(e2).__name__}: {e2}"
            return candidate

    title_l = title.lower()
    content_l = content.lower()
    host_l = host.lower()

    score = 0
    evidence_parts: list[str] = []
    methods: list[str] = []

    # Host token matches — strongest signal
    host_hits = 0
    for tok in eng_tokens:
        if tok in host_norm:
            host_hits += 1
            score += 8
            evidence_parts.append(f"host token '{tok}' in {host}")
            methods.append("hostname_token_match")
    # Title token matches
    title_hits = 0
    for tok in eng_tokens:
        if tok in title_l:
            title_hits += 1
            score += 4
            evidence_parts.append(f"title token '{tok}'")
            methods.append("title_token_match")
    # Content exact English name
    english_l = company.english_name.lower()
    if english_l and len(english_l) >= 5 and english_l in content_l:
        score += 6
        evidence_parts.append(f"full English name in content")
        methods.append("content_identity_match")
    elif english_l and any(tok in content_l for tok in eng_tokens):
        # partial
        partial_hits = sum(tok in content_l for tok in eng_tokens)
        score += partial_hits * 1
        if partial_hits:
            evidence_parts.append(f"{partial_hits} distinctive tokens in content")
            methods.append("content_identity_match")

    # Arabic name match
    if arabic_name and arabic_name in content:
        score += 6
        evidence_parts.append(f"Arabic legal name '{arabic_name}' in content")
        methods.append("arabic_name_match")
    elif arabic_name and any(part in content for part in arabic_name.split() if len(part) >= 3):
        # weak arabic partial
        score += 1
        evidence_parts.append("Arabic partial match")

    # Official domain bonus .sa
    if ".sa" in host_l:
        score += 3
        evidence_parts.append(".sa domain")
    # Saudi context
    if "saudi" in content_l or "saudi" in title_l or "السعودية" in content:
        score += 1
        evidence_parts.append("Saudi context")

    # Check for company-like page: has contact/phone/email with same domain, or about/careers
    if "@" + host.split(".")[-2] in content_l if "." in host else False:
        score += 2
        evidence_parts.append("domain email in content")
        methods.append("official_contact_match")

    # Classification
    # Require at least one distinctive host token OR (title hit AND content identity)
    # For confirmed: host hit >=1 AND (title hit >=1 OR full English in content OR arabic in content)
    # For candidate: host hit >=1 OR (title hit >=1 AND content hit)
    # Otherwise unconfirmed/rejected
    has_host = host_hits >= 1
    has_title = title_hits >= 1
    has_full_english = english_l in content_l
    has_arabic = arabic_name and arabic_name in content

    # Determine verification status
    if has_host and (has_title or has_full_english or has_arabic):
        if score >= 12:
            status = "confirmed"
        elif score >= 8:
            status = "candidate"
        else:
            status = "unconfirmed"
    elif has_title and (has_full_english or has_arabic):
        if score >= 10:
            status = "candidate"
        else:
            status = "unconfirmed"
    elif score >= 8 and (has_full_english or has_arabic):
        status = "candidate"
    elif score >= 5:
        status = "unconfirmed"
    else:
        status = "rejected"

    # Extra rejection for completely unrelated content (no tokens at all)
    if host_hits == 0 and title_hits == 0 and not has_full_english and not has_arabic:
        status = "rejected"
        evidence_parts.append("No identity tokens matched")

    candidate.verification_status = status
    candidate.verification_method = ",".join(sorted(set(methods))) if methods else ("rejected_unrelated" if status=="rejected" else "manual_review")
    candidate.verification_score = float(score)
    candidate.verification_evidence = "; ".join(evidence_parts)[:2000] or "no evidence"
    # Store source type in engine field extension? keep engine, but we track via evidence later
    candidate.engine = f"{candidate.engine}|{source_type}"
    return candidate
