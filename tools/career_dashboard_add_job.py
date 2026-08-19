#!/usr/bin/env python3
"""Owner-supplied Career dashboard job intake.

This module is intentionally narrow: it accepts a pasted JD and/or a public job
URL, extracts structured JobPosting metadata when available, passes the vacancy
through the canonical Career Engine prepare/dedupe/scoring path, and generates
only that job's package when it is eligible. It never sends or submits anything.
"""

from __future__ import annotations

import hashlib
import html as html_lib
import json
import re
import subprocess
import sys
import tempfile
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse


class AddJobError(RuntimeError):
    pass


ADD_JOB_REQUEST_KIND = "career_engine_add_job"
ADD_JOB_REQUEST_SCHEMA_VERSION = 1
ADD_JOB_INPUT_FIELDS = ("job_url", "job_description", "company", "role", "location")


def _normalize_request_data(data: dict[str, Any]) -> dict[str, Any]:
    """Decode schema-compatible Add Job input from the generic prompt field.

    Legacy top-level fields remain supported for already-created/test records,
    but current browser writes keep job-specific metadata inside ``prompt`` so
    the live ``ai_requests`` collection does not need schema expansion.
    """
    normalized = dict(data)
    raw_prompt = data.get("prompt")
    if not isinstance(raw_prompt, str) or not raw_prompt.strip():
        return normalized
    try:
        payload = json.loads(raw_prompt)
    except json.JSONDecodeError:
        return normalized
    if not isinstance(payload, dict) or payload.get("kind") != ADD_JOB_REQUEST_KIND:
        return normalized
    if payload.get("schema_version") != ADD_JOB_REQUEST_SCHEMA_VERSION:
        raise AddJobError("Unsupported Add Job request payload version.")
    for field in ADD_JOB_INPUT_FIELDS:
        if not str(normalized.get(field, "") or "").strip():
            normalized[field] = payload.get(field, "")
    return normalized


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg"}:
            self._skip += 1
        elif tag.lower() in {"p", "div", "li", "br", "section", "article", "h1", "h2", "h3", "h4"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg"} and self._skip:
            self._skip -= 1
        elif tag.lower() in {"p", "div", "li", "section", "article"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)

    def text(self) -> str:
        value = html_lib.unescape("".join(self.parts))
        value = re.sub(r"[ \t]+", " ", value)
        value = re.sub(r"\n\s*\n+", "\n", value)
        return value.strip()


def _strip_html(value: str) -> str:
    parser = _TextExtractor()
    parser.feed(value or "")
    return parser.text()


def _valid_public_url(value: str) -> str:
    url = str(value or "").strip()
    if not url:
        return ""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise AddJobError("Job link must be a valid http/https URL.")
    if parsed.hostname in {"localhost", "127.0.0.1", "::1"}:
        raise AddJobError("Local/private job URLs are not supported.")
    return url


def _jobposting_nodes(value: Any) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    if isinstance(value, dict):
        types = value.get("@type")
        type_values = types if isinstance(types, list) else [types]
        if any(str(item).lower() == "jobposting" for item in type_values):
            nodes.append(value)
        for child in value.values():
            nodes.extend(_jobposting_nodes(child))
    elif isinstance(value, list):
        for child in value:
            nodes.extend(_jobposting_nodes(child))
    return nodes


def _json_ld_job(html: str) -> dict[str, Any]:
    scripts = re.findall(
        r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
        html,
        flags=re.I | re.S,
    )
    for raw in scripts:
        try:
            payload = json.loads(html_lib.unescape(raw.strip()))
        except json.JSONDecodeError:
            continue
        nodes = _jobposting_nodes(payload)
        if nodes:
            return nodes[0]
    return {}


def _company_name(job: dict[str, Any]) -> str:
    org = job.get("hiringOrganization")
    if isinstance(org, dict):
        return str(org.get("name", "")).strip()
    return str(org or "").strip()


def _location_text(job: dict[str, Any]) -> str:
    raw = job.get("jobLocation")
    locations = raw if isinstance(raw, list) else [raw]
    parts: list[str] = []
    for item in locations:
        if not isinstance(item, dict):
            continue
        address = item.get("address")
        if isinstance(address, dict):
            values = [
                address.get("addressLocality"), address.get("addressRegion"),
                address.get("addressCountry"),
            ]
            text = ", ".join(str(value).strip() for value in values if str(value or "").strip())
            if text:
                parts.append(text)
        elif address:
            parts.append(str(address).strip())
    return " | ".join(dict.fromkeys(parts))


def _identifier(job: dict[str, Any], url: str) -> str:
    identifier = job.get("identifier")
    if isinstance(identifier, dict):
        value = identifier.get("value") or identifier.get("name")
        if value:
            return str(value).strip()[:160]
    elif identifier:
        return str(identifier).strip()[:160]
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:24] if url else ""


def extract_structured_job(html: str, url: str) -> dict[str, str]:
    job = _json_ld_job(html)
    if not job:
        return {}
    return {
        "role": str(job.get("title", "")).strip(),
        "company": _company_name(job),
        "location": _location_text(job),
        "job_description": _strip_html(str(job.get("description", ""))),
        "posting_date": str(job.get("datePosted", "")).strip(),
        "external_job_id": _identifier(job, url),
    }


def _fetch_structured_job(repo: Path, url: str) -> dict[str, str]:
    repo_text = str(repo)
    if repo_text not in sys.path:
        sys.path.insert(0, repo_text)
    try:
        from career_engine.sources.network import fetch_text
        from career_engine.sources.base import SourceError
    except ImportError as exc:
        raise AddJobError(f"Career Engine source fetcher unavailable: {exc}") from exc
    try:
        body = fetch_text(url, timeout=15, max_bytes=2 * 1024 * 1024)
    except SourceError as exc:
        raise AddJobError(f"Could not read the supplied job link: {exc}") from exc
    return extract_structured_job(body, url)


def _run_prepare(repo: Path, args: list[str]) -> dict[str, Any]:
    completed = subprocess.run(
        [str(repo / "career-engine"), "prepare", *args],
        cwd=repo,
        text=True,
        capture_output=True,
        timeout=300,
        check=False,
    )
    raw = (completed.stdout or "").strip()
    try:
        payload = json.loads(raw) if raw else {}
    except json.JSONDecodeError as exc:
        detail = (completed.stderr or raw)[-2000:]
        raise AddJobError(f"Career Engine prepare returned invalid output: {detail}") from exc
    # 20 = below threshold, 40 = unresolved route. Both are expected policy
    # outcomes and must be surfaced without turning the dashboard worker into a
    # system failure.
    if completed.returncode not in {0, 20, 40}:
        detail = (completed.stderr or raw)[-2500:]
        raise AddJobError(f"Career Engine prepare failed ({completed.returncode}): {detail}")
    return payload


def run_add_job(
    *,
    repo: Path,
    dispatcher: Path,
    website_root: Path,
    data: dict[str, Any],
    generate_package: Callable[..., str],
    refresh_dashboard: Callable[[Path, Path], None],
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[str, str]:
    """Add one owner-supplied job and return ``(job_id, status_message)``."""
    data = _normalize_request_data(data)
    url = _valid_public_url(str(data.get("job_url", "")))
    description = str(data.get("job_description", "") or "").strip()
    owner_pasted_description = bool(description)
    company = str(data.get("company", "") or "").strip()
    role = str(data.get("role", "") or "").strip()
    location = str(data.get("location", "") or "").strip()
    external_job_id = ""

    if not url and not description:
        raise AddJobError("Paste a job description or provide a job link.")

    if progress_callback:
        progress_callback({"kind": "add_job_progress", "phase": "reading", "message": "Reading job input…"})

    if url and (not description or not company or not role):
        structured = _fetch_structured_job(repo, url)
        description = description or structured.get("job_description", "")
        company = company or structured.get("company", "")
        role = role or structured.get("role", "")
        location = location or structured.get("location", "")
        external_job_id = structured.get("external_job_id", "")

    if len(description) < 80:
        raise AddJobError(
            "I could not extract a usable JobPosting description from that link. Paste the job description in the form and retry."
        )
    if not company or not role:
        raise AddJobError("Company and role are required when they cannot be extracted from the job link.")

    external_job_id = external_job_id or (
        hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
        if url else hashlib.sha256(f"{company}\n{role}\n{description}".encode("utf-8")).hexdigest()[:24]
    )

    if progress_callback:
        progress_callback({"kind": "add_job_progress", "phase": "scoring", "message": "Deduplicating and scoring…"})

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".txt", delete=False) as handle:
        handle.write(description)
        jd_path = Path(handle.name)
    try:
        source = "owner_dashboard" if url else "Supplied directly by owner"
        args = [
            "--jd-file", str(jd_path),
            "--company", company,
            "--role", role,
            "--source", source,
            "--external-job-id", external_job_id,
            "--actor", "owner",
        ]
        if owner_pasted_description:
            # Owner-pasted vacancies use the accelerated preparation path. Fit
            # scoring is still retained for analytics, but weak-fit/target-lane
            # gating must not delay package creation. Evidence validation and
            # no-send/no-submit controls remain fully enforced downstream.
            args.append("--force-weak")
        if url:
            args.extend(["--source-url", url, "--application-url", url])
        if location:
            args.extend(["--location", location])
        prepared = _run_prepare(repo, args)
    finally:
        jd_path.unlink(missing_ok=True)

    job_id = str(prepared.get("job_id", "")).strip()
    if not job_id:
        raise AddJobError("Career Engine did not return a job id for the supplied vacancy.")
    score = int((prepared.get("fit_score") or {}).get("total", 0))
    blockers = [str(item) for item in (prepared.get("blockers") or [])]

    if blockers:
        if progress_callback:
            progress_callback({
                "kind": "add_job_progress", "phase": "blocked", "job_id": job_id,
                "score": score, "message": "Job added but package generation is blocked by Career Engine policy."
            })
        refresh_dashboard(repo, website_root)
        return job_id, (
            f"Added {company} — {role} and scored it {score}/100. Package generation was not forced because Career Engine "
            f"blocked it: {'; '.join(blockers)}. The job is now on the dashboard for review."
        )

    if progress_callback:
        progress_callback({
            "kind": "add_job_progress", "phase": "generating", "job_id": job_id,
            "score": score, "message": "Generating CV and cover letter…"
        })
    action = generate_package(
        repo=repo,
        dispatcher=dispatcher,
        job_id=job_id,
        force_regenerate=True,
    )
    if progress_callback:
        progress_callback({
            "kind": "add_job_progress", "phase": "publishing", "job_id": job_id,
            "score": score, "message": "Publishing updated dashboard…"
        })
    refresh_dashboard(repo, website_root)
    return job_id, (
        f"Added {company} — {role}, scored it {score}/100, and completed the internal package ({action}). "
        "CV and cover-letter outputs are ready for review. Nothing was sent or submitted."
    )
