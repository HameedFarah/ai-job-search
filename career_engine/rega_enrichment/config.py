"""REGA enrichment — frozen configuration."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

PIPELINE_VERSION = "1.0.0"
SCHEMA_VERSION = 1

# Freeze identifiers for reproducibility
SCRAPER_VERSION = "firecrawl-v1+scrapegraph-qwen3-35b"
MODEL_VERSION = "openai/Qwen/Qwen3.6-35B-A3B-FP8"
BACKEND_VERSION = "searxng:127.0.0.1:8888"  # Firecrawl provider: firecrawl (credentials_configured=true, key not stored in Git)
HERMES_WEB_BACKEND = "firecrawl"  # historical backend label; runtime use remains rotation-gated
CANONICAL_REGA_INPUT = Path("/home/hameedo/tmp/rega-enrichment/rega-enrichment-queue-canonical.csv")
# Skill version — freeze at run start
SKILL_VERSION = "job-automation-data-enrichment:1.0.0 / company-enrichment-discovery:1.0.0"

# Identity authority — never modified during enrichment
IDENTITY_FIELDS = ["License No", "Arabic Name", "English Name", "English Location(s)"]

# Generic tokens that cannot establish identity
GENERIC_TOKENS = {
    "real", "estate", "urban", "development", "developer", "developers",
    "saudi", "arabia", "for", "and", "the", "co", "limited", "joint",
    "stock", "holding", "holdings", "properties", "property", "international",
    "general", "trade", "contracting", "trading", "industrial", "services",
    "service", "house", "houses", "united", "al", "bin", "bint", "ibn",
    "company", "group", "investment", "investments", "ltd", "llc", "inc",
    "one", "person", "closed", "establishment", "contracting", "commercial",
    "invest", "dev", "invest.",
}

BLOCKED_HOSTS = (
    "linkedin.com", "facebook.com", "instagram.com", "youtube.com",
    "wikipedia.org", "glassdoor.", "indeed.", "dnb.com", "zawya.com",
    "protenders.", "decypha.", "datocapital.", "researchgate.net",
    "scribd.com", "zhihu.com", "soundcloud.com", "microsoft.com",
    "soft98.", "britannica.com", "wordreference.com", "tripadvisor.",
    "bayut.", "tracxn.", "crunchbase.com", "zoominfo.com", "yellowpages",
)

# Fields to discover after official domain verification
ENRICH_FIELDS = [
    "official_website",
    "official_domain",
    "linkedin_company_page",
    "general_email",
    "main_phone",
    "careers_page",
    "ats_url",
    "ats_domain",
    "recruitment_email",
    "ttw_bd_email",
    "procurement_email",
    "supplier_registration_url",
]

# Confidence taxonomy
CONFIDENCE_VALUES = {"confirmed", "candidate", "unconfirmed", "not_found", "rejected"}

# Source types for evidence
SOURCE_TYPES = {"firecrawl_search", "firecrawl_extract", "direct_fetch", "official_page", "linkedin", "careers_page"}

# Verification methods
VERIFICATION_METHODS = {
    "hostname_token_match",
    "title_token_match",
    "content_identity_match",
    "arabic_name_match",
    "official_contact_match",
    "manual_review",
    "rejected_unrelated",
}

def canonical_input_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def git_sha(repo_root: Path | None = None) -> str:
    import subprocess
    root = repo_root or Path(__file__).resolve().parents[2]
    try:
        return subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        return "unknown"

def firecrawl_rotation_confirmed() -> bool:
    """Return True only after an operator explicitly confirms credential rotation."""
    return os.getenv("FIRECRAWL_ROTATED_CONFIRMED", "").strip().lower() in {"1", "true", "yes"}


def firecrawl_credentials_configured() -> bool:
    """Report usable Firecrawl credentials without treating a legacy key as authorized."""
    if not firecrawl_rotation_confirmed():
        return False
    if os.getenv("FIRECRAWL_API_KEY"):
        return True
    for p in [Path("/home/hameedo/.hermes/.env"), Path.home() / ".hermes" / ".env"]:
        if p.is_file():
            try:
                for line in p.read_text(errors="ignore").splitlines():
                    if line.strip().startswith("FIRECRAWL_API_KEY="):
                        val = line.split("=", 1)[1].strip().strip('"').strip("'")
                        if val and not val.startswith("#"):
                            return True
            except Exception:
                pass
    return False

def freeze_manifest(input_path: Path) -> dict:
    return {
        "pipeline_version": PIPELINE_VERSION,
        "schema_version": SCHEMA_VERSION,
        "scraper_version": SCRAPER_VERSION,
        "model_version": MODEL_VERSION,
        "backend_version": BACKEND_VERSION,
        "hermes_web_backend": HERMES_WEB_BACKEND,
        "search_backend": "searxng-qwant",
        "search_provider": "searxng",
        "fetch_provider": "firecrawl" if firecrawl_credentials_configured() else "direct",
        "firecrawl_rotation_confirmed": firecrawl_rotation_confirmed(),
        "credentials_configured": firecrawl_credentials_configured(),
        "skill_version": SKILL_VERSION,
        "git_sha": git_sha(),
        "input_sha256": canonical_input_sha(input_path) if input_path.exists() else None,
        "input_path": str(input_path),
    }

# Query templates — several per company using identity + location + activity
QUERY_TEMPLATES = [
    '"{english}" Saudi Arabia',
    '"{arabic}" السعودية',
    '"{english}" {location} Saudi',
    '"{english}" official website',
    '"{english}" careers jobs Saudi',
]

# ATS domains for detection
ATS_DOMAINS = [
    "workday", "taleo", "successfactors", "oraclecloud", "greenhouse",
    "lever", "ashby", "smartrecruiters", "avature", "icims", "brassring",
    "careers", "jobs", "apply",
]

# Email classification keywords
RECRUITMENT_KEYWORDS = ["career", "hr", "human resource", "recruit", "talent", "hiring", "apply", "vacanc", "job"]
PROCUREMENT_KEYWORDS = ["procure", "supplier", "vendor", "tender", "bid", "purchase"]
BD_KEYWORDS = ["business develop", "bd ", "partnership", "sales", "investor", "bd@"]
