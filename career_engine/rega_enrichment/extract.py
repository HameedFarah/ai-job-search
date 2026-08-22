"""Field extraction after official domain verification."""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse, urlsplit
from datetime import datetime, timezone
import httpx

from .config import RECRUITMENT_KEYWORDS, PROCUREMENT_KEYWORDS, BD_KEYWORDS, ATS_DOMAINS
from .models import CompanyRecord, EvidenceRecord

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(\+966[\s\-]?\d[\s\-]?\d{3}[\s\-]?\d{4}|\+966[\s\-]?1\d[\s\-]?\d{3}[\s\-]?\d{4}|9200\d{5}|5\d[\s\-]?\d{3}[\s\-]?\d{4})")

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

def fetch_markdown(url: str) -> tuple[str, str]:
    """Fetch url via Firecrawl extract, fallback to direct."""
    key = api_key()
    if key:
        try:
            endpoint = "https://api.firecrawl.dev/v1/scrape"
            headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
            payload = {"url": url, "formats": ["markdown", "html"], "onlyMainContent": False, "waitFor": 1500}
            with httpx.Client(timeout=45) as client:
                resp = client.post(endpoint, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
            d = data.get("data") or data
            md = str(d.get("markdown") or d.get("content") or "")
            title = str(d.get("title") or d.get("metadata", {}).get("title") or "")
            if md:
                return title, md
        except Exception:
            pass
    # direct
    headers = {"User-Agent": "Mozilla/5.0 (compatible; REGA-enrichment/1.0)"}
    with httpx.Client(timeout=20, follow_redirects=True, headers=headers) as client:
        resp = client.get(url)
        resp.raise_for_status()
        html = resp.text
        m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
        title = re.sub(r"<[^>]+>", " ", m.group(1)).strip() if m else ""
        text = re.sub(r"<script.*?</script>", " ", html, flags=re.I | re.S)
        text = re.sub(r"<style.*?</style>", " ", text, flags=re.I | re.S)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()[:30000]
        return title, text

def extract_links(markdown: str, base_url: str) -> list[str]:
    links = re.findall(r"\[.*?\]\((https?://[^\)]+)\)", markdown)
    # also bare urls
    bare = re.findall(r"https?://[^\s\)\]\"'<>]+", markdown)
    all_urls = list(dict.fromkeys(links + bare))
    # normalize relative
    normed = []
    for u in all_urls:
        if u.startswith("/"):
            u = urljoin(base_url, u)
        normed.append(u.split("#")[0].split("?")[0])
    return normed[:100]

def find_emails_with_context(text: str, window: int = 120) -> list[tuple[str, str]]:
    out = []
    for m in EMAIL_RE.finditer(text):
        email = m.group(0).lower()
        # filter obvious false positives
        if email.endswith(".png") or email.endswith(".jpg") or "example.com" in email:
            continue
        start = max(0, m.start() - window)
        end = min(len(text), m.end() + window)
        ctx = text[start:end].replace("\n", " ")[:400]
        out.append((email, ctx))
    # dedupe by email, keep first context
    seen = {}
    for e, c in out:
        if e not in seen:
            seen[e] = c
    return list(seen.items())

def classify_email(email: str, context: str, host: str) -> str:
    ctx_l = context.lower()
    # must be on same domain or explicitly recruitment?
    # Generic info@ remains general unless recruitment context explicitly says apply/send CV
    if any(k in ctx_l for k in RECRUITMENT_KEYWORDS):
        # check explicit instruction: context contains apply/send/career near email
        if any(w in ctx_l for w in ["apply", "send your cv", "send your resume", "career", "recruit", "talent", "hiring", "vacanc"]):
            return "recruitment"
        # else still candidate recruitment but lower confidence — we treat as candidate only if explicit
        # For safety, require explicit phrases
        return "unclassified_recruitment_candidate"
    if any(k in ctx_l for k in PROCUREMENT_KEYWORDS):
        if any(w in ctx_l for w in ["procure", "supplier", "vendor", "tender"]):
            return "procurement"
    if any(k in ctx_l for k in BD_KEYWORDS):
        return "bd"
    # domain match for general: if email domain == official host
    email_host = email.split("@")[-1].lower()
    host_norm = urlparse("https://" + host if "://" not in host else host).hostname or host
    if host_norm and email_host == host_norm.lower():
        return "general"
    # fallback general if strong host match, else general candidate
    return "general"

def extract_fields(company: CompanyRecord, official_url: str) -> tuple[dict[str, str], list[EvidenceRecord], dict[str, str]]:
    """
    Returns (field_values, evidence_list, debug_info).
    Only verified fields are returned; unverified remain blank.
    """
    verified_at = utc_now()
    host = (urlsplit(official_url).hostname or "").lower()
    if host.startswith("www."):
        host_display = host[4:]
    else:
        host_display = host
    base = f"https://{host}/" if host else official_url

    evidence: list[EvidenceRecord] = []
    values: dict[str, str] = {f: "" for f in ["official_website","official_domain","linkedin_company_page","general_email","main_phone","careers_page","ats_url","ats_domain","recruitment_email","ttw_bd_email","procurement_email","supplier_registration_url"]}
    debug: dict[str, str] = {}

    # official website/domain — already verified
    values["official_website"] = official_url.rstrip("/")
    values["official_domain"] = host_display
    evidence.append(EvidenceRecord(
        company_id=company.company_id,
        license_no=company.license_no,
        field="official_website",
        value=values["official_website"],
        source_url=official_url,
        source_type="official_page",
        evidence_text=f"Domain verified via fetch identity check; host {host_display} contains distinctive company token",
        verified_at=verified_at,
        confidence="confirmed",
        verification_method="hostname_token_match,content_identity_match",
    ))
    evidence.append(EvidenceRecord(
        company_id=company.company_id,
        license_no=company.license_no,
        field="official_domain",
        value=host_display,
        source_url=official_url,
        source_type="official_page",
        evidence_text=f"Official domain derived from verified website {official_url}",
        verified_at=verified_at,
        confidence="confirmed",
        verification_method="hostname_token_match",
    ))

    # Fetch homepage and common paths
    pages_to_fetch = {
        "homepage": official_url,
        "contact": urljoin(official_url, "/contact"),
        "contact_us": urljoin(official_url, "/contact-us"),
        "about": urljoin(official_url, "/about"),
        "careers": urljoin(official_url, "/careers"),
        "jobs": urljoin(official_url, "/jobs"),
        "join_us": urljoin(official_url, "/join-us"),
        "supplier": urljoin(official_url, "/supplier"),
        "vendors": urljoin(official_url, "/vendors"),
    }
    page_contents: dict[str, tuple[str, str]] = {}
    for key, url in pages_to_fetch.items():
        try:
            title, md = fetch_markdown(url)
            if md and len(md.strip()) > 200:
                page_contents[key] = (title, md)
        except Exception as e:
            debug[key] = f"fetch failed: {e}"
            continue

    # Combine homepage + contact for emails/phones
    combined_text = ""
    combined_url = official_url
    if "homepage" in page_contents:
        combined_text += "\n" + page_contents["homepage"][1]
    for k in ["contact", "contact_us"]:
        if k in page_contents:
            combined_text += "\n" + page_contents[k][1]
            combined_url = pages_to_fetch[k]  # last contact wins for evidence url

    # Extract emails
    emails_ctx = find_emails_with_context(combined_text, window=150)
    # Also from dedicated careers page later
    careers_text = ""
    careers_url_candidate = ""
    # Discover careers page via links
    homepage_links: list[str] = []
    if "homepage" in page_contents:
        homepage_links = extract_links(page_contents["homepage"][1], official_url)
    # Find careers link
    for link in homepage_links:
        low = link.lower()
        if any(seg in low for seg in ["/career", "/job", "/vacanc", "/join"]):
            # fetch this link to verify it is careers
            try:
                t, md = fetch_markdown(link)
                if md and len(md) > 200:
                    page_contents[f"discovered_{link}"] = (t, md)
                    careers_text = md
                    careers_url_candidate = link
                    break
            except Exception:
                continue
    # fallback to known careers paths that succeeded
    if not careers_url_candidate:
        for k in ["careers", "jobs", "join_us"]:
            if k in page_contents:
                careers_url_candidate = pages_to_fetch[k]
                careers_text = page_contents[k][1]
                break
    if careers_url_candidate and careers_text:
        values["careers_page"] = careers_url_candidate
        evidence.append(EvidenceRecord(
            company_id=company.company_id,
            license_no=company.license_no,
            field="careers_page",
            value=careers_url_candidate,
            source_url=careers_url_candidate,
            source_type="careers_page",
            evidence_text=page_contents.get(f"discovered_{careers_url_candidate}", page_contents.get("careers") or page_contents.get("jobs") or ("",""))[1][:600] if careers_url_candidate in [v for v in pages_to_fetch.values()] or f"discovered_{careers_url_candidate}" in page_contents else careers_text[:600],
            verified_at=verified_at,
            confidence="confirmed",
            verification_method="content_identity_match",
        ))
        # ATS detection from careers page
        for link in extract_links(careers_text, careers_url_candidate):
            low = link.lower()
            if any(ats in low for ats in ATS_DOMAINS):
                values["ats_url"] = link
                try:
                    h = (urlsplit(link).hostname or "").lower()
                except Exception:
                    h = link
                values["ats_domain"] = h
                evidence.append(EvidenceRecord(
                    company_id=company.company_id, license_no=company.license_no, field="ats_url", value=link, source_url=careers_url_candidate, source_type="careers_page",
                    evidence_text=f"ATS link found on careers page: {link}", verified_at=verified_at, confidence="confirmed", verification_method="content_identity_match"
                ))
                evidence.append(EvidenceRecord(
                    company_id=company.company_id, license_no=company.license_no, field="ats_domain", value=h, source_url=careers_url_candidate, source_type="careers_page",
                    evidence_text=f"ATS domain derived from ATS URL on careers page", verified_at=verified_at, confidence="confirmed", verification_method="content_identity_match"
                ))
                break
        # Also check for ATS text without link? e.g., "Apply via SuccessFactors"
        if not values["ats_url"] and any(ats in careers_text.lower() for ats in ["successfactors","workday","taleo","oracle"]):
            # keep blank but note
            debug["ats_text_hint"] = "ATS hint in careers page but no link extracted"
    # Emails classification — use combined_text + careers_text
    all_text_for_emails = combined_text + "\n" + careers_text
    for email, ctx in emails_ctx:
        cls = classify_email(email, ctx, host_display)
        # dedupe: don't overwrite stronger
        if cls == "recruitment":
            # require explicit recruitment instruction
            # check if context explicitly says to use this email for jobs
            explicit = any(phrase in ctx.lower() for phrase in ["send your cv","send cv","careers@","recruitment","apply","vacanc","join us","talent"])
            if explicit and not values["recruitment_email"]:
                values["recruitment_email"] = email
                evidence.append(EvidenceRecord(
                    company_id=company.company_id, license_no=company.license_no, field="recruitment_email", value=email, source_url=combined_url if email in combined_text else careers_url_candidate or official_url,
                    source_type="official_page" if email in combined_text else "careers_page",
                    evidence_text=ctx[:600], verified_at=verified_at, confidence="confirmed", verification_method="content_identity_match"
                ))
            elif not explicit:
                # Do not promote generic as recruitment — keep as general candidate?
                pass
        elif cls == "procurement":
            if not values["procurement_email"]:
                values["procurement_email"] = email
                evidence.append(EvidenceRecord(
                    company_id=company.company_id, license_no=company.license_no, field="procurement_email", value=email, source_url=combined_url, source_type="official_page",
                    evidence_text=ctx[:600], verified_at=verified_at, confidence="confirmed", verification_method="content_identity_match"
                ))
        elif cls == "bd":
            if not values["ttw_bd_email"]:
                values["ttw_bd_email"] = email
                evidence.append(EvidenceRecord(
                    company_id=company.company_id, license_no=company.license_no, field="ttw_bd_email", value=email, source_url=combined_url, source_type="official_page",
                    evidence_text=ctx[:600], verified_at=verified_at, confidence="candidate", verification_method="content_identity_match"
                ))
        elif cls == "general":
            if not values["general_email"]:
                # only set general if not already set and not a recruitment/procurement duplicate
                if email != values["recruitment_email"] and email != values["procurement_email"]:
                    values["general_email"] = email
                    evidence.append(EvidenceRecord(
                        company_id=company.company_id, license_no=company.license_no, field="general_email", value=email, source_url=combined_url, source_type="official_page",
                        evidence_text=ctx[:600], verified_at=verified_at, confidence="confirmed", verification_method="official_contact_match"
                    ))
        else: # unclassified
            # treat as general candidate if no general yet
            if not values["general_email"] and cls == "general":
                values["general_email"] = email

    # If no general email found but we have emails, promote first general-looking
    if not values["general_email"] and emails_ctx:
        # pick first email whose domain matches host
        for email, ctx in emails_ctx:
            if host_display in email.split("@")[-1]:
                values["general_email"] = email
                evidence.append(EvidenceRecord(
                    company_id=company.company_id, license_no=company.license_no, field="general_email", value=email, source_url=combined_url, source_type="official_page",
                    evidence_text=ctx[:600], verified_at=verified_at, confidence="candidate", verification_method="official_contact_match"
                ))
                break

    # Phone extraction from combined
    phones = PHONE_RE.findall(combined_text)
    # also from contact pages specifically
    if phones:
        # Normalize first phone
        raw = phones[0] if isinstance(phones[0], str) else phones[0][0] if phones else ""
        # Clean
        cleaned = re.sub(r"[\s\-]", "", raw)
        if cleaned:
            # Ensure +966 prefix
            if cleaned.startswith("5") and not cleaned.startswith("+"):
                cleaned = "+966" + cleaned.lstrip("0")
            elif cleaned.startswith("966") and not cleaned.startswith("+"):
                cleaned = "+" + cleaned
            elif cleaned.startswith("0"):
                cleaned = "+966" + cleaned[1:]
            values["main_phone"] = cleaned
            evidence.append(EvidenceRecord(
                company_id=company.company_id, license_no=company.license_no, field="main_phone", value=cleaned, source_url=combined_url, source_type="official_page",
                evidence_text=combined_text[max(0, combined_text.find(raw)-100):combined_text.find(raw)+200][:600], verified_at=verified_at, confidence="confirmed", verification_method="official_contact_match"
            ))

    # Supplier registration URL
    supplier_candidates = []
    for link in homepage_links:
        low = link.lower()
        if any(seg in low for seg in ["supplier","vendor","procure","tender","registration","register"]):
            supplier_candidates.append(link)
    for k in ["supplier","vendors"]:
        if k in page_contents:
            # check content for registration links
            for link in extract_links(page_contents[k][1], pages_to_fetch[k]):
                if any(seg in link.lower() for seg in ["supplier","vendor","register","tender"]):
                    supplier_candidates.append(link)
    if supplier_candidates:
        # pick first
        chosen = supplier_candidates[0]
        values["supplier_registration_url"] = chosen
        evidence.append(EvidenceRecord(
            company_id=company.company_id, license_no=company.license_no, field="supplier_registration_url", value=chosen, source_url=official_url, source_type="official_page",
            evidence_text=f"Supplier registration link found: {chosen}", verified_at=verified_at, confidence="candidate", verification_method="content_identity_match"
        ))

    # LinkedIn discovery: search via Firecrawl for linkedin
    # Do not scrape LinkedIn without verification; just discover candidate and verify via title?
    try:
        # We already have linkedin via discovery candidates? But we can search specifically
        from .discovery import firecrawl_search
        ln_queries = [f'"{company.english_name}" linkedin site:linkedin.com/company']
        for q in ln_queries:
            try:
                res = firecrawl_search(q, limit=3)
                for r in res:
                    if "linkedin.com/company" in r.get("url",""):
                        # verify title contains company distinctive token
                        title_l = r.get("title","").lower()
                        eng_tokens = [t for t in re.findall(r"[a-z0-9]+", company.english_name.lower()) if len(t)>=4]
                        if any(tok in title_l for tok in eng_tokens) or company.english_name.lower() in title_l:
                            values["linkedin_company_page"] = r["url"]
                            evidence.append(EvidenceRecord(
                                company_id=company.company_id, license_no=company.license_no, field="linkedin_company_page", value=r["url"], source_url=r["url"], source_type="linkedin",
                                evidence_text=r.get("title","")[:600] + " | " + r.get("description","")[:600], verified_at=verified_at, confidence="candidate", verification_method="title_token_match"
                            ))
                            break
            except Exception:
                continue
            if values["linkedin_company_page"]:
                break
    except Exception:
        pass

    return values, evidence, debug
