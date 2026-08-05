"""Source framework CLI: registry, probe, verify and ingest.

Usage:

    python3 -m career_engine.sources.cli registry [--json]
    python3 -m career_engine.sources.cli probe --adapter greenhouse --company careem \
            [--limit 10] [--location ""] [--output PATH] [--offline] [--full]
    python3 -m career_engine.sources.cli verify --url <careers-or-ats-url> [--offline]
    python3 -m career_engine.sources.cli ingest --file probe.json \
            [--scanner-id hermes_scanner] [--output PATH]

Invariants:

- ``registry`` always prints ``no_send_policy: true``.
- ``probe`` never writes anywhere except the optional ``--output`` file and
  every emitted report carries ``send_or_submit: false``.
- ``ingest`` feeds the probe output through the central scanner, which scores
  jobs and blocks generation until the live-vacancy gate is satisfied.
- Every probe is bounded: per-request timeout (12s), size caps, and a
  ``--limit`` job cap. Use ``--offline`` for deterministic fixture probes.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from .base import DiscoveryJob, DiscoveryReport, SourceAdapter, SourceError, html_to_text
from .dedupe import DedupeStore
from .registry import get_source, registry_payload

FIXTURES_DIR = str(Path(__file__).resolve().parent / "fixtures")

# Adapter id -> (class, allowed kinds). Search discovery and inbox are handled
# separately because they do not emit jobs without verification/auth.
_KNOWN_KINDS = ("ats_api", "ats_web", "employer_page")


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


def build_adapter(adapter_id: str, *, offline: bool = False) -> SourceAdapter:
    from .adapters.ashby import AshbyAdapter
    from .adapters.greenhouse import GreenhouseAdapter
    from .adapters.jsonld import JsonLdAdapter
    from .adapters.lever import LeverAdapter
    from .adapters.smartrecruiters import SmartRecruitersAdapter
    from .adapters.workable import WorkableAdapter

    registry = {
        "greenhouse": GreenhouseAdapter,
        "lever": LeverAdapter,
        "ashby": AshbyAdapter,
        "smartrecruiters": SmartRecruitersAdapter,
        "workable": WorkableAdapter,
        "jsonld": JsonLdAdapter,
    }
    cls = registry.get(adapter_id)
    if cls is None:
        raise SourceError(
            f"Adapter {adapter_id!r} is not probe-runnable here. Registry status: "
            + _describe_blocked(adapter_id)
        )
    return cls(fixtures_dir=FIXTURES_DIR if offline else None)


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
) -> dict[str, Any]:
    """Run one bounded probe and return a DiscoveryReport payload."""
    entry = get_source(adapter_id)
    report = DiscoveryReport(adapter=adapter_id, company_identifier=company)
    if entry.get("status") == "blocked":
        reason = entry.get("blocked_reason") or "blocked"
        report.blocked.append({"adapter": adapter_id, "reason": reason})
        report.notes.append(f"Source {adapter_id} is blocked; no probe attempted.")
        return _report_payload(report, offline=offline)

    adapter = build_adapter(adapter_id, offline=offline)
    dedupe = DedupeStore()
    try:
        discovered = adapter.search(
            company=company,
            location=location,
            limit=limit,
            fetch_full=fetch_full,
            offline=offline,
        )
    except SourceError as exc:
        report.add_source(
            _source_result(adapter_id, status="error", error=str(exc))
        )
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
    report.add_source(
        _source_result(adapter_id, status=status, jobs_fetched=len(jobs))
    )
    report.notes.append(f"Adapter {adapter_id} probed company identifier {company!r}: {len(jobs)} jobs.")
    return _report_payload(report, offline=offline)


def _source_result(adapter_id: str, *, status: str, jobs_fetched: int = 0, error: str = "") -> Any:
    from .provenance import utc_now_iso
    from .base import SourceResult

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


def run_ingest(file_path: str, *, scanner_id: str, output: str = "") -> dict[str, Any]:
    """Feed a probe output file through the central Career Engine scanner.

    Discovery-only by construction: the central scanner never sends or submits
    and blocks generation for unverified vacancies. Offline fixture reports are
    refused when the resolved root is the real repository.
    """
    from ..config import repo_root
    from ..scanner import run_scan, write_report

    source_file = Path(file_path)
    payload = json.loads(source_file.read_text(encoding="utf-8"))
    root = repo_root()
    production_root = Path(__file__).resolve().parents[2]
    fixture_report = payload.get("offline_fixture") is True or payload.get("ingest_allowed_in_production") is False
    allow_fixture = os.environ.get("CAREER_ENGINE_ALLOW_FIXTURE_INGEST", "").strip() == "1"
    if fixture_report and root.resolve() == production_root.resolve() and not allow_fixture:
        raise ValueError("Offline fixture probe reports cannot be ingested into the production Career Engine tracker")
    report = run_scan(source_file, root=root, scanner_id=scanner_id)
    if output:
        write_report(report, Path(output))
    return report


def _cmd_registry() -> dict[str, Any]:
    return registry_payload()


def _cmd_probe(args: argparse.Namespace) -> dict[str, Any]:
    return run_probe(
        adapter_id=args.adapter,
        company=args.company,
        location=args.location or None,
        limit=args.limit,
        offline=args.offline,
        fetch_full=args.full,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="career-engine-sources",
        description="Discovery-only job source adapters for the Career Engine (no-send).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    registry = sub.add_parser("registry", help="Print the source registry and capability matrix")
    registry.add_argument("--json", action="store_true", help="Emit JSON")

    probe = sub.add_parser("probe", help="Run a bounded discovery probe against one source")
    probe.add_argument("--adapter", required=True, help="Adapter/source id (see registry)")
    probe.add_argument("--company", required=True, help="Board token / company id / careers URL")
    probe.add_argument("--location", default="", help="Optional location filter")
    probe.add_argument("--limit", type=int, default=10, help="Maximum jobs to emit (bounded)")
    probe.add_argument("--full", action="store_true", help="Fetch full detail where required (SmartRecruiters)")
    probe.add_argument("--offline", action="store_true", help="Use fixtures instead of the network")
    probe.add_argument("--output", default="", help="Write the DiscoveryReport JSON to this path")

    verify = sub.add_parser("verify", help="Verify a candidate URL against the employer's own page")
    verify.add_argument("--url", required=True)
    verify.add_argument("--offline", action="store_true")

    ingest = sub.add_parser("ingest", help="Feed a probe output through the central scanner (scoring only)")
    ingest.add_argument("--file", required=True)
    ingest.add_argument("--scanner-id", choices=("hermes_scanner", "chatgpt_scanner"), default="hermes_scanner")
    ingest.add_argument("--output", default="")
    return parser


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "registry":
            emit(_cmd_registry())
            return 0
        if args.command == "probe":
            result = _cmd_probe(args)
            emit(result)
            if args.output:
                out = Path(args.output)
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return 0
        if args.command == "verify":
            emit(run_verify(args.url, offline=args.offline))
            return 0
        if args.command == "ingest":
            emit(run_ingest(args.file, scanner_id=args.scanner_id, output=args.output))
            return 0
        raise AssertionError(args.command)
    except KeyError as exc:
        emit({"error": "unknown_source", "message": str(exc)})
        return 2
    except (SourceError, ValueError) as exc:
        emit({"error": type(exc).__name__, "message": str(exc)})
        return 2
    except Exception as exc:  # pragma: no cover - defensive
        emit({"error": type(exc).__name__, "message": str(exc)})
        return 70


if __name__ == "__main__":
    sys.exit(main())
