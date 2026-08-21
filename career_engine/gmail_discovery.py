"""Gmail job-alert / vacancy discovery as a central scan source path.

This module is the *general discovery* counterpart to gmail_reconcile.py
(submission evidence).  It runs bounded Gmail search, classifies each
message, extracts deterministic vacancy candidates where supported, and
returns sanitized counters plus the candidate jobs for ingestion.

Privacy: the sanitized report never contains message IDs, subjects,
bodies, URLs or sender addresses.  Those stay in the candidate jobs'
provenance (internal) and in the tracker's append-only history.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import date, timedelta, timezone
from pathlib import Path
from typing import Any

from .gmail import search_messages

# Reuse the submission phrase list for classification parity.
try:  # avoid hard import cycle
    from .gmail_reconcile import SUBMISSION_PHRASES
except ImportError:  # pragma: no cover - fallback
    SUBMISSION_PHRASES = tuple()

_GMAIL_DISCOVERY_STATE = Path("projects/job-automation/runtime/gmail-discovery-state.json")

PLATFORM_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("linkedin", ("linkedin.com", "linkedin")),
    ("bayt", ("bayt.com",)),
    ("naukrigulf", ("naukrigulf.com", "naukri")),
    ("glassdoor", ("glassdoor.com", "glassdoor")),
    ("workday", ("myworkdayjobs.com", "workday")),
    ("workable", ("workable.com", "workablemail.com")),
    ("smartrecruiters", ("smartrecruiters.com",)),
    ("greenhouse", ("greenhouse.io",)),
    ("successfactors", ("successfactors", "success-factor")),
    ("icims", ("icims.com",)),
    ("lever", ("lever.co",)),
]

JOB_ALERT_SUBJECT = (
    "job alert",
    "jobs you may be interested",
    "recommended jobs",
    "new jobs for you",
    "daily job alert",
    "jobs matching",
    "job recommendations",
    "you may be a fit",
    "your job alert for",
    "job alert for",
    "bayt.com - jobs",
    "naukrigulf jobs",
)

VACANCY_SUBJECT = ("vacancy", "vacancies", "new vacancy", "career opportunity")
RECRUITER_SUBJECT = ("recruiter", "talent acquisition", "hiring for")
INTERVIEW_TERMS = ("interview", "assessment", "technical test", "hirevue")
STATUS_TERMS = ("application update", "status update", "shortlisted", "not selected", "rejected")


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _lower(value: Any) -> str:
    return _text(value).lower()


def _detect_platform(sender: str, body: str, urls: list[str]) -> str:
    blob = f"{sender} {' '.join(urls)} {body[:800]}".lower()
    for platform, needles in PLATFORM_RULES:
        if any(needle in blob for needle in needles):
            return platform
    if "careers." in blob or "jobs." in blob:
        return "direct_employer"
    return "other"


def classify_message_category(message: dict[str, Any]) -> str:
    subject = _lower(message.get("subject"))
    body = _lower(message.get("body"))
    sender = _lower(message.get("from"))
    combined = f"{subject}\n{body}"
    # submission confirmation takes precedence
    if any(phrase in combined for phrase in SUBMISSION_PHRASES):
        # handled as submission in reconcile; discovery counts it separately below
        return "submission_confirmation"
    # LinkedIn job alert sender is deterministic even without subject phrase
    if "jobalerts-noreply" in sender or "jobs-noreply" in sender or "job-alerts" in sender:
        return "job_alert"
    if any(term in combined for term in INTERVIEW_TERMS):
        return "interview_assessment"
    if any(term in combined for term in STATUS_TERMS):
        return "application_status"
    if any(term in sender for term in ("recruiter", "talent acquisition", "hiring manager")):
        return "recruiter"
    if any(term in subject for term in JOB_ALERT_SUBJECT):
        return "job_alert"
    if any(term in subject for term in VACANCY_SUBJECT):
        return "vacancy_notification"
    if "apply" in subject and "instruction" in combined:
        return "application_instruction"
    if any(term in subject for term in ("application received", "thank you for applying")):
        return "submission_confirmation"
    return "other"


# Deterministic URL -> vacancy extractors.
_VACANCY_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("linkedin", re.compile(r"linkedin\.com/(?:comm/)?jobs/view/(?:[^/?#]*-)?(\d+)", re.I)),
    ("bayt", re.compile(r"bayt\.com/[^?#]*/(\d{5,})", re.I)),
    ("naukrigulf", re.compile(r"naukrigulf\.com/[^?#]*?(\d{6,})", re.I)),
    ("glassdoor", re.compile(r"glassdoor\.com/job-listing/[^?#]*?(\d{5,})", re.I)),
    ("workday", re.compile(r"myworkdayjobs\.com/[^?#]*/job/[^?#]*?([A-Z0-9_-]{6,})", re.I)),
    ("workable", re.compile(r"apply\.workable\.com/j/([A-Za-z0-9]+)", re.I)),
    ("greenhouse", re.compile(r"boards\.greenhouse\.io/([^/]+)/jobs/(\d+)", re.I)),
    ("lever", re.compile(r"jobs\.lever\.co/([^/]+)/([A-Za-z0-9-]+)", re.I)),
    ("smartrecruiters", re.compile(r"jobs\.smartrecruiters\.com/[^/]+/(\d+)", re.I)),
    ("successfactors", re.compile(r"careers\.[^/]+/[^?#]*job[^\d]*(\d{5,})", re.I)),
    ("icims", re.compile(r"icims\.com/jobs/(\d+)/", re.I)),
]

_SLUG_TITLE_RE = re.compile(r"linkedin\.com/(?:comm/)?jobs/view/([^\d?#]+?)-(\d+)", re.I)


def _slug_to_title(slug: str) -> str:
    cleaned = re.sub(r"[-_]+", " ", slug).strip()
    # Drop trailing "at <company>" fragment
    cleaned = re.sub(r"\s+at\s+.*$", "", cleaned, flags=re.I).strip()
    return " ".join(word.capitalize() for word in cleaned.split())[:120]


def _extract_vacancy_from_url(url: str, message: dict[str, Any]) -> dict[str, Any] | None:
    # Canonicalize LinkedIn to a clean URL for tracker provenance
    linked = re.search(r"linkedin\.com/(?:comm/)?jobs/view/(?:[^/?#]*-)?(\d+)", url, re.I)
    if linked:
        job_id = linked.group(1)
        slug_match = _SLUG_TITLE_RE.search(url)
        title = ""
        if slug_match:
            title = _slug_to_title(slug_match.group(1))
            if len(title) < 4:
                title = ""
        canonical = f"https://www.linkedin.com/jobs/view/{job_id}/"
        return {"external_job_id": job_id, "role": title, "source_url": canonical, "platform": "linkedin"}
    for platform, pattern in _VACANCY_PATTERNS:
        match = pattern.search(url)
        if not match:
            continue
        if platform == "greenhouse":
            company, job_id = match.group(1), match.group(2)
            canonical = f"https://boards.greenhouse.io/{company}/jobs/{job_id}"
            return {"external_job_id": job_id, "role": "", "company_hint": company, "source_url": canonical, "platform": platform}
        if platform == "lever":
            company, job_id = match.group(1), match.group(2)
            canonical = f"https://jobs.lever.co/{company}/{job_id}"
            return {"external_job_id": job_id, "role": "", "company_hint": company, "source_url": canonical, "platform": platform}
        job_id = match.group(1)
        # Generic platforms: keep canonical as extracted URL without query/fragment
        clean = url.split("#", 1)[0].split("?", 1)[0]
        return {"external_job_id": job_id, "role": "", "source_url": clean, "platform": platform}
    # Generic fallback: any URL with /job(s)/ and an id-like token
    generic = re.search(r"/jobs?/[^?#]*?(\d{5,})", url, re.I)
    if generic:
        clean = url.split("#", 1)[0].split("?", 1)[0]
        return {"external_job_id": generic.group(1), "role": "", "source_url": clean, "platform": "direct_employer"}
    return None


def _url_key(url: str) -> str:
    raw = _text(url).split("#", 1)[0].split("?", 1)[0].rstrip("/").lower()
    linked = re.search(r"linkedin\.com/(?:comm/)?jobs/view/(?:[^/?#]*-)?(\d+)", raw, re.I)
    if linked:
        return f"linkedin-job:{linked.group(1)}"
    return raw


def _state_path(root: Path, rel: Path = _GMAIL_DISCOVERY_STATE) -> Path:
    from .config import load_config

    try:
        _, paths = load_config(root)
        return paths.tracker_base / "runtime" / "gmail-discovery-state.json"
    except Exception:
        return root / rel


def _load_state(root: Path) -> dict[str, Any]:
    path = _state_path(root)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(root: Path, state: dict[str, Any]) -> None:
    path = _state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _candidate_job_id(url: str, role: str, company: str) -> str:
    material = f"gmail-discovery|{_url_key(url)}|{_text(role).lower()}|{_text(company).lower()}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def discover_job_mail(
    root: Path,
    *,
    start: date | None = None,
    end_inclusive: date | None = None,
    max_results: int = 150,
) -> dict[str, Any]:
    """Bounded Gmail discovery for job alerts / vacancy notifications.

    Returns a *sanitized* stats dict plus an internal ``candidates`` list
    (not part of the sanitized projection) for ingestion.  Never sends or
    submits.
    """
    today = date.today()
    end_inclusive = end_inclusive or today
    state = _load_state(root)
    if start is None:
        try:
            start = date.fromisoformat(str(state.get("last_sync") or "")) - timedelta(days=1)
        except ValueError:
            start = end_inclusive - timedelta(days=3)
    after = (start - timedelta(days=1)).strftime("%Y/%m/%d")
    before = (end_inclusive + timedelta(days=1)).strftime("%Y/%m/%d")
    query = f"after:{after} before:{before} -in:spam -in:trash"

    authenticated = True
    messages: list[dict[str, Any]] = []
    error: str | None = None
    try:
        # Use gws via search_messages; fallback is surfaced as error (himalaya
        # fallback is accepted per spec but gws is verified live).
        messages = search_messages(query, max_results=max_results)
    except Exception as exc:  # noqa: BLE001
        authenticated = False
        error = f"{type(exc).__name__}: {str(exc)[:400]}"
        result: dict[str, Any] = {
            "schema_version": 1,
            "authenticated": False,
            "query": query,
            "start_date": start.isoformat(),
            "end_date": end_inclusive.isoformat(),
            "messages_scanned": 0,
            "career_relevant_messages": 0,
            "job_alert_messages": 0,
            "recruiter_messages": 0,
            "vacancy_messages": 0,
            "application_instruction_messages": 0,
            "submission_confirmation_messages": 0,
            "application_status_messages": 0,
            "interview_or_assessment_messages": 0,
            "candidate_jobs_extracted": 0,
            "jobs_new_after_deduplication": 0,
            "jobs_matched_existing": 0,
            "ambiguous_messages_manual_review": 0,
            "platform_counts": {},
            "errors": [error] if error else [],
            "send_or_submit": False,
            "candidates": [],
        }
        return result

    career_relevant = 0
    counters: Counter[str] = Counter()
    platform_counts: Counter[str] = Counter()
    candidates_raw: list[dict[str, Any]] = []
    ambiguous = 0

    for message in messages:
        sender = _text(message.get("from"))
        body = _text(message.get("body"))
        urls: list[str] = [str(v) for v in message.get("urls", []) if v]
        platform = _detect_platform(sender, body, urls)
        platform_counts[platform] += 1
        category = classify_message_category(message)
        counters[category] += 1
        # career_relevant: any non-other category or platform != other
        if category != "other" or platform != "other":
            career_relevant += 1
        # Only job_alert / vacancy_notification produce candidates
        if category not in {"job_alert", "vacancy_notification"}:
            continue
        # Ambiguous: digest with no extractable URL
        extracted_any = False
        for url in urls:
            extracted = _extract_vacancy_from_url(url, message)
            if not extracted:
                continue
            extracted_any = True
            external = _text(extracted.get("external_job_id"))
            role = _text(extracted.get("role"))
            company = _text(extracted.get("company_hint") or "")
            source_url = _text(extracted.get("source_url"))
            if not external and not source_url:
                continue
            # Require at least an external id or a meaningful URL; role may be empty (sparse record)
            candidates_raw.append({
                "company": company,
                "role": role,
                "location": "",
                "source": "gmail_alert",
                "source_url": source_url,
                "application_url": source_url,
                "external_job_id": external,
                "full_job_description": "",
                "live_status": "unverified",
                "provenance": {
                    "source": "gmail_alert",
                    "platform": platform,
                    "gmail_message_id": _text(message.get("id")),
                },
                "source_path": "gmail_job_alerts",
                "found_date": today.isoformat(),
            })
        if not extracted_any:
            # digest recognized as alert but no deterministic vacancies -> manual review counter
            ambiguous += 1

    # Internal dedupe by url_key
    seen: set[str] = set()
    candidates: list[dict[str, Any]] = []
    for item in candidates_raw:
        key = _url_key(item.get("source_url", "")) or item.get("external_job_id", "")
        if key in seen:
            continue
        seen.add(key)
        candidates.append(item)

    _save_state(root, {"last_sync": end_inclusive.isoformat()})

    result = {
        "schema_version": 1,
        "authenticated": True,
        "query": query,
        "start_date": start.isoformat(),
        "end_date": end_inclusive.isoformat(),
        "messages_scanned": len(messages),
        "career_relevant_messages": career_relevant,
        "job_alert_messages": counters.get("job_alert", 0),
        "recruiter_messages": counters.get("recruiter", 0),
        "vacancy_messages": counters.get("vacancy_notification", 0),
        "application_instruction_messages": counters.get("application_instruction", 0),
        "submission_confirmation_messages": counters.get("submission_confirmation", 0),
        "application_status_messages": counters.get("application_status", 0),
        "interview_or_assessment_messages": counters.get("interview_assessment", 0),
        "candidate_jobs_extracted": len(candidates_raw),
        "jobs_new_after_deduplication": len(candidates),
        "jobs_matched_existing": 0,  # filled by caller after tracker dedupe
        "ambiguous_messages_manual_review": ambiguous,
        "platform_counts": dict(platform_counts),
        "errors": [error] if error else [],
        "send_or_submit": False,
        "candidates": candidates,
    }
    return result
