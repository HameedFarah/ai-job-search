"""Source framework CLI: registry, probe, verify, route-check and ingest.

Usage:

    python3 -m career_engine.sources.cli registry [--json]
    python3 -m career_engine.sources.cli probe --adapter greenhouse --company careem \
            [--limit 10] [--location ""] [--output PATH] [--offline] [--full]
    python3 -m career_engine.sources.cli probe --adapter careerjet --company "Design Manager" \
            --user-triggered --user-ip <actual-public-ip> --user-agent <actual-user-agent>
    python3 -m career_engine.sources.cli verify --url <careers-or-ats-url> [--offline]
    python3 -m career_engine.sources.cli route-check --url <url> \
            --allowlist-file <gcc-employers.json> [--proxy-available]
    python3 -m career_engine.sources.cli ingest --file probe.json \
            [--scanner-id hermes_scanner] [--output PATH]

Invariants:

- ``registry`` always prints ``no_send_policy: true`` and never exposes secret values.
- ``probe`` never writes anywhere except the optional ``--output`` file and
  every emitted report carries ``send_or_submit: false``.
- Discovery API, aggregator and alert records remain unverified until an
  official employer or ATS source promotes them.
- ``ingest`` feeds the probe output through the central scanner without sending,
  contacting recruiters or submitting applications.
- Every probe is bounded by request timeouts, response-size caps and a job limit.
- Missing provider credentials return ``unavailable`` without failing the wider scan.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from .base import (
    DiscoveryJob,
    DiscoveryReport,
    SourceAdapter,
    SourceError,
    SourceUnavailable,
)
from .dedupe import DedupeStore
from .registry import get_source, registry_payload

FIXTURES_DIR = str(Path(__file__).resolve().parent / "fixtures")


def _report_payload(report: DiscoveryReport, *, offline: bool) -> dict[str, Any]:
    payload = report.to_data()
    payload["offline_fixture"] = bool(offline)
    payload["ingest_allowed_in_production"] = not offline
    if offline:
        payload["notes"].append("OFFLINE FIXTURE OUTPUT: never ingest into the production tracker.")
        for job in payload.get("jobs", []):
            provenance = job.get("provenance")
            if not isinstance(provenance, dict):
                provenance = {}
                job["provenance"] = provenance
            provenance["offline_fixture"] = True
            provenance["extracted_from"] = "offline_fixture"
    return payload


def build_adapter(
    adapter_id: str,
    *,
    offline: bool = False,
    user_triggered: bool = False,
    user_ip: str = "",
    user_agent: str = "",
) -> SourceAdapter:
    kwargs: dict[str, Any] = {"fixtures_dir": FIXTURES_DIR if offline else None}
    if adapter_id == "greenhouse":
        from .adapters.greenhouse import GreenhouseAdapter
        cls: type[SourceAdapter] = GreenhouseAdapter
    elif adapter_id == "lever":
        from .adapters.lever import LeverAdapter
        cls = LeverAdapter
    elif adapter_id == "ashby":
        from .adapters.ashby import AshbyAdapter
        cls = AshbyAdapter
    elif adapter_id == "smartrecruiters":
        from .adapters.smartrecruiters import SmartRecruitersAdapter
        cls = SmartRecruitersAdapter
    elif adapter_id == "workday":
        from .adapters.workday import WorkdayAdapter
        cls = WorkdayAdapter
    elif adapter_id == "oracle_hcm":
        from .adapters.oracle_hcm import OracleHcmAdapter
        cls = OracleHcmAdapter
    elif adapter_id == "workable":
        from .adapters.workable import WorkableAdapter
        cls = WorkableAdapter
    elif adapter_id == "jsonld":
        from .adapters.jsonld import JsonLdAdapter
        cls = JsonLdAdapter
    elif adapter_id == "brave_search":
        from .adapters.aggregators import BraveSearchAdapter
        cls = BraveSearchAdapter
    elif adapter_id == "jooble":
        from .adapters.aggregators import JoobleAdapter
        cls = JoobleAdapter
    elif adapter_id == "careerjet":
        from .adapters.aggregators import CareerjetAdapter
        cls = CareerjetAdapter
        kwargs.update(
            user_triggered=user_triggered,
            user_ip=user_ip,
            user_agent=user_agent,
        )
    else:
        raise SourceError(
            f"Adapter {adapter_id!r} is not probe-runnable here. Registry status: "
            + _describe_blocked(adapter_id)
        )
    return cls(**kwargs)

def _describe_blocked(adapter_id: str) -> str:
    try:
        entry = get_source(adapter_id)
    except KeyError:
        return "unknown source id"
    reason = entry.get("blocked_reason") or "no adapter implementation"
    return f"status={entry.get('status')}; {reason}"


def run_probe(
    *,
    adapter_id: str,
    company: str,
    location: str | None = None,
    limit: int = 10,
    offline: bool = False,
    fetch_full: bool = False,
    user_triggered: bool = False,
    user_ip: str = "",
    user_agent: str = "",
) -> dict[str, Any]:
    """Run one bounded probe and return a DiscoveryReport payload."""
    entry = get_source(adapter_id)
    report = DiscoveryReport(adapter=adapter_id, company_identifier=company)
    if entry.get("status") == "blocked":
        reason = entry.get("blocked_reason") or "blocked"
        report.blocked.append({"adapter": adapter_id, "reason": reason})
        report.notes.append(f"Source {adapter_id} is blocked; no probe attempted.")
        return _report_payload(report, offline=offline)

    adapter = build_adapter(
        adapter_id,
        offline=offline,
        user_triggered=user_triggered,
        user_ip=user_ip,
        user_agent=user_agent,
    )
    dedupe = DedupeStore()
    try:
        discovered = adapter.search(
            company=company,
            location=location,
            limit=max(1, min(int(limit), 100)),
            fetch_full=fetch_full,
            offline=offline,
        )
    except SourceUnavailable as exc:
        report.add_source(
            _source_result(adapter_id, status="unavailable", error=str(exc))
        )
        report.notes.append(f"Source unavailable: {exc}")
        return _report_payload(report, offline=offline)
    except SourceError as exc:
        report.add_source(_source_result(adapter_id, status="error", error=str(exc)))
        report.notes.append(str(exc))
        return _report_payload(report, offline=offline)

    jobs: list[DiscoveryJob] = []
    for job in discovered:
        key = job.dedupe_key()
        if not dedupe.add(key):
            report.duplicates_dropped += 1
            continue
        jobs.append(job)

    for job in jobs:
        report.jobs.append(job.to_scanner_job())
        report.raw_jobs.append(job.to_data())
    status = "ok" if jobs else "empty"
    report.add_source(_source_result(adapter_id, status=status, jobs_fetched=len(jobs)))
    report.notes.append(
        f"Adapter {adapter_id} probed query/company identifier {company!r}: {len(jobs)} jobs."
    )
    if not entry.get("official", False):
        report.notes.append(
            "All emitted records remain unverified until official employer/ATS verification succeeds."
        )
    return _report_payload(report, offline=offline)


def _source_result(
    adapter_id: str,
    *,
    status: str,
    jobs_fetched: int = 0,
    error: str = "",
) -> Any:
    from .base import SourceResult
    from .provenance import utc_now_iso

    return SourceResult(
        adapter_id=adapter_id,
        status=status,
        fetched_at=utc_now_iso(),
        jobs_fetched=jobs_fetched,
        error=error,
    )


def run_verify(url: str, *, offline: bool = False) -> dict[str, Any]:
    """Run the official-verification gate on one candidate URL."""
    from .adapters.jsonld import JsonLdAdapter

    verifier = JsonLdAdapter(fixtures_dir=FIXTURES_DIR if offline else None)
    provenance = verifier.verify_official(url, offline=offline)
    return {
        "url": url,
        "verified_official": provenance.official,
        "provenance": provenance.to_data(),
    }


def run_route_check(
    url: str,
    *,
    allowlist_file: str,
    proxy_available: bool,
) -> dict[str, Any]:
    from .routing import decide_route

    payload = json.loads(Path(allowlist_file).read_text(encoding="utf-8"))
    domains = {
        str(item).lower().strip(".")
        for item in payload.get("policy", {}).get(
            "residential_allowlist_enabled_domains", []
        )
        if str(item).strip()
    }
    decision = decide_route(
        url,
        residential_allowlist=domains,
        proxy_available=proxy_available,
    )
    return decision.to_data()


def run_ingest(file_path: str, *, scanner_id: str, output: str = "") -> dict[str, Any]:
    """Feed a probe output file through the central Career Engine scanner.

    Discovery-only by construction: the central scanner never sends or submits.
    Offline fixture reports are refused when the resolved root is the production
    repository unless the explicit test-only override is present.
    """
    from ..config import repo_root
    from ..scanner import run_scan, write_report

    source_file = Path(file_path)
    payload = json.loads(source_file.read_text(encoding="utf-8"))
    root = repo_root()
    production_root = Path(__file__).resolve().parents[2]
    fixture_report = (
        payload.get("offline_fixture") is True
        or payload.get("ingest_allowed_in_production") is False
    )
    allow_fixture = os.environ.get("CAREER_ENGINE_ALLOW_FIXTURE_INGEST", "").strip() == "1"
    if fixture_report and root.resolve() == production_root.resolve() and not allow_fixture:
        raise ValueError(
            "Offline fixture probe reports cannot be ingested into the production Career Engine tracker"
        )
    report = run_scan(source_file, root=root, scanner_id=scanner_id)
    if output:
        write_report(report, Path(output))
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="career-engine-sources",
        description="Discovery-only job source adapters for the Career Engine (no-send).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    registry = sub.add_parser("registry", help="Print source and runtime status")
    registry.add_argument("--json", action="store_true", help="Emit JSON")

    probe = sub.add_parser("probe", help="Run one bounded discovery probe")
    probe.add_argument("--adapter", required=True)
    probe.add_argument("--company", required=True, help="Board id, careers URL or search keywords")
    probe.add_argument("--location", default="")
    probe.add_argument("--limit", type=int, default=10)
    probe.add_argument("--full", action="store_true")
    probe.add_argument("--offline", action="store_true")
    probe.add_argument("--output", default="")
    probe.add_argument(
        "--user-triggered",
        action="store_true",
        help="Required for Careerjet; confirms this query was directly requested by the user",
    )
    probe.add_argument("--user-ip", default="", help="Careerjet actual triggering user IP")
    probe.add_argument("--user-agent", default="", help="Careerjet actual triggering user-agent")

    verify = sub.add_parser("verify", help="Verify a candidate against an official source")
    verify.add_argument("--url", required=True)
    verify.add_argument("--offline", action="store_true")

    route = sub.add_parser("route-check", help="Evaluate the fail-closed residential route policy")
    route.add_argument("--url", required=True)
    route.add_argument("--allowlist-file", required=True)
    route.add_argument("--proxy-available", action="store_true")

    ingest = sub.add_parser("ingest", help="Feed probe output through the central scanner")
    ingest.add_argument("--file", required=True)
    ingest.add_argument(
        "--scanner-id",
        choices=("hermes_scanner", "chatgpt_scanner"),
        default="hermes_scanner",
    )
    ingest.add_argument("--output", default="")
    consultants = sub.add_parser("consultants-scan", help="Probe active consultant bookmarks via official JSON-LD")
    consultants.add_argument("--root", default="")
    consultants.add_argument("--limit", type=int, default=25)
    consultants.add_argument("--offline", action="store_true")
    consultants.add_argument("--output", default="")
    return parser


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "registry":
            emit(registry_payload())
            return 0
        if args.command == "probe":
            result = run_probe(
                adapter_id=args.adapter,
                company=args.company,
                location=args.location or None,
                limit=args.limit,
                offline=args.offline,
                fetch_full=args.full,
                user_triggered=args.user_triggered,
                user_ip=args.user_ip,
                user_agent=args.user_agent,
            )
            emit(result)
            if args.output:
                out = Path(args.output)
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(
                    json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            return 0
        if args.command == "verify":
            emit(run_verify(args.url, offline=args.offline))
            return 0
        if args.command == "route-check":
            emit(
                run_route_check(
                    args.url,
                    allowlist_file=args.allowlist_file,
                    proxy_available=args.proxy_available,
                )
            )
            return 0
        if args.command == "ingest":
            emit(run_ingest(args.file, scanner_id=args.scanner_id, output=args.output))
            return 0
        if args.command == "consultants-scan":
            from .consultants import scan_consultants
            from ..config import repo_root
            result = scan_consultants(root=Path(args.root) if args.root else repo_root(), limit=args.limit, offline=args.offline)
            if args.output:
                out = Path(args.output); out.parent.mkdir(parents=True, exist_ok=True); out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            emit(result)
            return 0
        raise AssertionError(args.command)
    except KeyError as exc:
        emit({"error": "unknown_source", "message": str(exc)})
        return 2
    except (SourceError, ValueError) as exc:
        emit({"error": type(exc).__name__, "message": str(exc)})
        return 2
    except Exception as exc:
        emit({"error": type(exc).__name__, "message": str(exc)})
        return 70


if __name__ == "__main__":
    sys.exit(main())
