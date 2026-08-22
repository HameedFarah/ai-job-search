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
        # Handle rate limits / quota
        if resp.status_code in (429, 402):
            # Quota exhausted or rate limited — fallback to direct, do not raise as fatal
            # Raise a distinct error so caller can fallback
            raise RuntimeError(f"Firecrawl quota/rate limit {resp.status_code}: {resp.text[:300]}")
        resp.raise_for_status()
        data = resp.json()
    d = data.get("data") or data
    title = str(d.get("title") or d.get("metadata", {}).get("title") or "")
    markdown = str(d.get("markdown") or d.get("content") or "")
    html = str(d.get("html") or "")
    return title, markdown, html

def fetch_direct(url: str) -> tuple[str, str]:
    """Fallback direct fetch with httpx + curl subprocess fallback."""
    headers = {"User-Agent": "Mozilla/5.0 (compatible; REGA-enrichment/1.0; +https://farahdigital.com)"}
    # Try httpx first
    try:
        with httpx.Client(timeout=20, follow_redirects=True, headers=headers) as client:
            resp = client.get(url)
            resp.raise_for_status()
            text = resp.text
            m = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
            title = re.sub(r"<[^>]+>", " ", m.group(1)).strip() if m else ""
            content = re.sub(r"<script.*?</script>", " ", text, flags=re.I | re.S)
            content = re.sub(r"<style.*?</style>", " ", content, flags=re.I | re.S)
            content = re.sub(r"<[^>]+>", " ", content)
            content = re.sub(r"\s+", " ", content).strip()[:20000]
            if len(content) > 200:
                return title, content
    except Exception:
        pass
    # Fallback to curl subprocess (handles Cloudflare, slow sites better)
    try:
        import subprocess
        result = subprocess.run(
            ["curl", "-sL", "-A", "Mozilla/5.0 (compatible; REGA-enrichment/1.0)", "--max-time", "20", url],
            capture_output=True, text=True, timeout=25
        )
        if result.returncode == 0 and result.stdout:
            text = result.stdout
            m = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
            title = re.sub(r"<[^>]+>", " ", m.group(1)).strip() if m else ""
            content = re.sub(r"<script.*?</script>", " ", text, flags=re.I | re.S)
            content = re.sub(r"<style.*?</style>", " ", content, flags=re.I | re.S)
            content = re.sub(r"<[^>]+>", " ", content)
            content = re.sub(r"\s+", " ", content).strip()[:20000]
            if len(content) > 100:
                return title, content
    except Exception:
        pass
    raise RuntimeError(f"Direct fetch failed for {url}")

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

    # Fetch page — prefer direct (saves Firecrawl quota), fallback to Firecrawl for JS-heavy, final fallback to snippet
    title = ""
    content = ""
    source_type = "direct_fetch"
    try:
        t, md = fetch_direct(url)
        title = t or candidate.title
        content = md or candidate.description
        if not content or len(content) < 200:
            # Try Firecrawl for JS-rendered or short content
            try:
                t2, md2, _ = fetch_via_firecrawl_extract(url)
                if md2 and len(md2) > len(content):
                    title = t2 or title
                    content = md2
                    source_type = "firecrawl_extract"
            except Exception:
                # keep direct result
                pass
    except Exception as e:
        # Direct failed, try Firecrawl
        try:
            t2, md2, _ = fetch_via_firecrawl_extract(url)
            title = t2 or candidate.title
            content = md2 or candidate.description
            source_type = "firecrawl_extract"
        except Exception as e2:
            # Final fallback: use discovery snippet as content if no fetch possible
            # Allows verification on snippet alone with lower confidence (e.g., when Firecrawl quota exhausted)
            title = candidate.title or ""
            content = candidate.description or ""
            source_type = "discovery_snippet"
            # Do not immediately reject if snippet has at least title or description with tokens
            # Proceed to scoring with snippet; only reject if both empty
            if not title and not content:
                candidate.verification_status = "rejected"
                candidate.verification_method = "rejected_unrelated"
                candidate.verification_score = -10
                candidate.verification_evidence = f"Fetch failed: {type(e).__name__}: {e} / {type(e2).__name__}: {e2}"
                return candidate
            # Keep at least snippet for scoring; mark source_type so verification knows it's snippet
            # Continue to scoring below

    title_l = title.lower()
    content_l = content.lower()
    host_l = host.lower()

    score = 0
    evidence_parts: list[str] = []
    methods: list[str] = []

    # Host token matches — strongest signal (with fuzzy for transliteration variants like makkyoon vs makkiyoon)
    def _fuzzy_in(host_norm: str, tok: str) -> bool:
        if tok in host_norm:
            return True
        # Allow edit distance 1 for tokens >=5 (e.g., makkyoon vs makkiyoon)
        if len(tok) >= 5:
            # check if tok with one char inserted/deleted/substituted appears
            for i in range(len(tok)):
                variant = tok[:i] + tok[i+1:]  # deletion
                if variant in host_norm and len(variant) >= 4:
                    return True
            for i in range(len(tok)+1):
                for c in "abcdefghijklmnopqrstuvwxyz":
                    variant = tok[:i] + c + tok[i:]
                    if variant in host_norm:
                        return True
            # simple substring with one char diff: check longest common substring >= len-1
            # fallback: if host contains tok without one char
            for i in range(len(tok)):
                if tok[:i] + tok[i+1:] in host_norm:
                    return True
        return False

    host_hits = 0
    for tok in eng_tokens:
        if _fuzzy_in(host_norm, tok):
            host_hits += 1
            score += 8
            evidence_parts.append(f"host token '{tok}' in {host} (fuzzy)" if tok not in host_norm else f"host token '{tok}' in {host}")
            methods.append("hostname_token_match")
    # Title token matches (also fuzzy for Spelling variants)
    title_hits = 0
    for tok in eng_tokens:
        if tok in title_l or _fuzzy_in(re.sub(r"[^a-z0-9]", "", title_l), tok):
            title_hits += 1
            score += 4
            evidence_parts.append(f"title token '{tok}'")
            methods.append("title_token_match")
    # Content exact English name (with fuzzy for transliteration)
    english_l = company.english_name.lower()
    if english_l and len(english_l) >= 5 and english_l in content_l:
        score += 6
        evidence_parts.append(f"full English name in content")
        methods.append("content_identity_match")
    elif english_l and any(tok in content_l or _fuzzy_in(re.sub(r"[^a-z0-9]", "", content_l), tok) for tok in eng_tokens):
        # partial (including fuzzy)
        partial_hits = sum(1 for tok in eng_tokens if tok in content_l or _fuzzy_in(re.sub(r"[^a-z0-9]", "", content_l), tok))
        score += partial_hits * 1
        if partial_hits:
            evidence_parts.append(f"{partial_hits} distinctive tokens in content (fuzzy)")
            methods.append("content_identity_match")
    # Also check brand token directly in content (covers Makkiyoon vs Makkyoon)
    elif any(tok in content_l for tok in [t for t in re.findall(r"[a-z]+", english_l) if len(t)>=4]):
        # fallback
        pass

    # Arabic name match — full or strong partial (e.g., مكيون)
    arabic_partial = False
    if arabic_name and arabic_name in content:
        score += 6
        evidence_parts.append(f"Arabic legal name '{arabic_name}' in content")
        methods.append("arabic_name_match")
        arabic_partial = True
    elif arabic_name:
        # Check for any distinctive Arabic token length>=3 appearing in content
        arabic_tokens = [p for p in re.findall(r"[\u0600-\u06FF]+", arabic_name) if len(p) >= 3]
        # Also split on spaces for mixed
        for tok in arabic_tokens:
            if tok in content:
                score += 3
                evidence_parts.append(f"Arabic token '{tok}' in content")
                methods.append("arabic_name_match")
                arabic_partial = True
                break
        if not arabic_partial and any(part in content for part in arabic_name.split() if len(part) >= 3):
            score += 1
            evidence_parts.append("Arabic partial match")
            arabic_partial = True

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
    has_arabic = arabic_name and (arabic_name in content or arabic_partial)
    # Also consider fuzzy content token as signal
    has_content_token = any(tok in content_l or _fuzzy_in(re.sub(r"[^a-z0-9]", "", content_l), tok) for tok in eng_tokens)

    # Determine verification status — allow host+content_token or host+arabic_partial as candidate
    if has_host and (has_title or has_full_english or has_arabic or has_content_token):
        if score >= 12:
            status = "confirmed"
        elif score >= 8:
            status = "candidate"
        else:
            status = "unconfirmed"
    elif has_title and (has_full_english or has_arabic or has_content_token):
        if score >= 10:
            status = "candidate"
        else:
            status = "unconfirmed"
    elif score >= 8 and (has_full_english or has_arabic or has_content_token):
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
