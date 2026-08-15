"""Thin adapter over maintained ATS integrations in Fighter90/career-ops-ui.

Career Engine owns normalization, provenance, safety and scoring. Portal-specific
HTTP/parsing logic remains in the separately maintained upstream repository.
The checkout is pinned to a reviewed SHA; a newer upstream commit is reported by
GitHub Actions and adopted only after review/tests.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..base import DiscoveryJob, SourceAdapter, SourceError, SourceUnavailable
from ..dates import parse_date
from ..managed_providers import DEFAULT_CHECKOUT, PROVIDERS, UPSTREAM_REF, UPSTREAM_REPO
from ..provenance import provenance as make_provenance

_MAX_STDOUT = 8 * 1024 * 1024
_RUNNER_TIMEOUT = 20


class ManagedCareerOpsAdapter(SourceAdapter):
    """Use one career-ops-ui provider while preserving Career Engine contracts."""

    source_kind = "ats_web"
    official = True

    def __init__(self, provider: str, *, fixtures_dir: str | None = None) -> None:
        super().__init__(fixtures_dir=fixtures_dir)
        if provider not in PROVIDERS:
            raise SourceError(f"Unsupported managed provider: {provider!r}")
        self.provider = provider
        self.source_id = f"managed_{provider}"
        self.source_name = f"{provider} via {UPSTREAM_REPO}"

    def search(
        self,
        *,
        company: str,
        location: str | None = None,
        limit: int = 10,
        fetch_full: bool = False,
        offline: bool = False,
    ) -> list[DiscoveryJob]:
        if offline:
            payload = self._offline_payload()
        else:
            payload = self._run_managed(company)

        company_entry = self._parse_company_spec(company)
        fallback_name = str(company_entry.get("name") or "").strip()
        results: list[DiscoveryJob] = []
        for item in payload.get("jobs", []):
            if not isinstance(item, dict):
                continue
            job = self._map_job(item, fallback_name)
            if not job.role or not job.detail_url:
                continue
            if location and location.lower() not in job.location.lower():
                continue
            results.append(job)
            if len(results) >= max(1, min(int(limit), 100)):
                break
        return results

    def _run_managed(self, company: str) -> dict[str, Any]:
        node = shutil.which("node")
        if not node:
            raise SourceUnavailable("Node.js is required for managed career-ops-ui sources")
        checkout = Path(os.environ.get("CAREER_OPS_UI_DIR", DEFAULT_CHECKOUT)).expanduser()
        if not checkout.is_dir():
            raise SourceUnavailable(
                f"Managed source checkout not found at {checkout}. "
                f"Install the pinned {UPSTREAM_REPO}@{UPSTREAM_REF}."
            )
        self._assert_checkout_ref(checkout)
        runner = Path(__file__).resolve().parents[3] / "tools" / "career_ops_source_runner.mjs"
        if not runner.is_file():
            raise SourceUnavailable(f"Managed source runner missing: {runner}")
        company_entry = self._parse_company_spec(company)
        try:
            proc = subprocess.run(
                [node, str(runner), self.provider, json.dumps(company_entry), str(checkout)],
                capture_output=True,
                text=True,
                timeout=_RUNNER_TIMEOUT,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise SourceError(f"Managed {self.provider} probe timed out") from exc
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "managed source failed").strip()
            raise SourceError(f"Managed {self.provider} probe failed: {err[:1500]}")
        if len(proc.stdout.encode("utf-8", errors="ignore")) > _MAX_STDOUT:
            raise SourceError(f"Managed {self.provider} result exceeded {_MAX_STDOUT} bytes")
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise SourceError(f"Managed {self.provider} returned invalid JSON") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
            raise SourceError(f"Managed {self.provider} returned an invalid payload")
        return payload

    @staticmethod
    def _parse_company_spec(value: str) -> dict[str, Any]:
        text = str(value or "").strip()
        if not text:
            raise SourceError("Managed ATS source requires a company/careers URL spec")
        if text.startswith("{"):
            try:
                data = json.loads(text)
            except json.JSONDecodeError as exc:
                raise SourceError("Managed ATS company JSON is invalid") from exc
            if not isinstance(data, dict):
                raise SourceError("Managed ATS company JSON must be an object")
            return data
        if text.startswith("@"):
            path = Path(text[1:]).expanduser()
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise SourceError(f"Could not load managed company spec {path}: {exc}") from exc
            if not isinstance(data, dict):
                raise SourceError("Managed ATS company file must contain a JSON object")
            return data
        if "|" in text:
            name, url = text.split("|", 1)
            return {"name": name.strip(), "careers_url": url.strip()}
        if text.startswith(("https://", "http://")):
            return {"name": "", "careers_url": text}
        raise SourceError(
            "Managed ATS company spec must be a careers URL, 'Company|URL', JSON object, or @file.json"
        )

    @staticmethod
    def _assert_checkout_ref(checkout: Path) -> None:
        try:
            proc = subprocess.run(
                ["git", "-C", str(checkout), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SourceUnavailable(f"Cannot verify managed source checkout at {checkout}") from exc
        head = proc.stdout.strip() if proc.returncode == 0 else ""
        if head != UPSTREAM_REF:
            raise SourceUnavailable(
                f"Managed source checkout is {head or 'unreadable'}, expected reviewed ref {UPSTREAM_REF}. "
                "Review upstream changes and bump the lock before activating them."
            )

    def _map_job(self, item: dict[str, Any], fallback_name: str) -> DiscoveryJob:
        detail_url = str(item.get("url") or "").strip()
        external_id = str(item.get("id") or detail_url).strip()
        role = str(item.get("title") or "").strip()
        company = str(item.get("company") or fallback_name).strip()
        location = str(item.get("location") or "").strip()
        date_value = item.get("date")
        snippet = str(item.get("snippet") or "").strip()
        return DiscoveryJob(
            adapter_id=self.source_id,
            company=company,
            role=role,
            location=location,
            external_job_id=external_id,
            detail_url=detail_url,
            application_url=detail_url,
            posted=parse_date(date_value, f"{UPSTREAM_REPO}:{self.provider}.date"),
            found_date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            description_text=snippet,
            provenance=make_provenance(
                source_id=self.source_id,
                source_name=self.source_name,
                source_kind=self.source_kind,
                official=True,
                extracted_from=f"managed upstream {UPSTREAM_REPO}@{UPSTREAM_REF}:{self.provider}",
                detail_url=detail_url,
                raw_id=external_id,
            ),
            extra={
                "managed_upstream_repo": UPSTREAM_REPO,
                "managed_upstream_ref": UPSTREAM_REF,
                "managed_provider": self.provider,
                "workplace_type": item.get("workplaceType") or "",
                "is_remote": bool(item.get("isRemote")),
            },
        )

    def _offline_payload(self) -> dict[str, Any]:
        path = Path(self._fixture_path("managed-source-jobs.json"))
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SourceError(f"Managed source fixture unavailable: {exc}") from exc
        jobs = data.get(self.provider, []) if isinstance(data, dict) else []
        return {"provider": self.provider, "endpoint": "offline-fixture", "jobs": jobs}
