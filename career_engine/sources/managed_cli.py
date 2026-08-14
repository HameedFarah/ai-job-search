"""CLI for ATS providers maintained in Fighter90/career-ops-ui.

This keeps the external portal implementations separately maintained while
emitting the same Career Engine DiscoveryReport shape and no-send guarantees.

Examples:

    python3 -m career_engine.sources.managed_cli providers
    python3 -m career_engine.sources.managed_cli probe --provider workday \
      --company 'Parsons|https://parsons.wd5.myworkdayjobs.com/en-US/Search'
    python3 -m career_engine.sources.managed_cli probe --provider oraclecloud \
      --company '{"name":"Employer","careers_url":"https://tenant.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/jobs"}'
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from .adapters.career_ops_managed import ManagedCareerOpsAdapter
from .base import DiscoveryReport, SourceError, SourceResult, SourceUnavailable
from .dedupe import DedupeStore
from .managed_providers import PROVIDERS, UPSTREAM_REF, UPSTREAM_REPO
from .provenance import utc_now_iso


def provider_payload() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "upstream_repo": UPSTREAM_REPO,
        "reviewed_ref": UPSTREAM_REF,
        "automatic_upstream_activation": False,
        "providers": [
            {"provider": name, **meta} for name, meta in sorted(
                PROVIDERS.items(), key=lambda item: (int(item[1]["priority"]), item[0])
            )
        ],
        "send_or_submit": False,
    }


def run_probe(
    *,
    provider: str,
    company: str,
    location: str | None = None,
    limit: int = 20,
    offline: bool = False,
) -> dict[str, Any]:
    if provider not in PROVIDERS:
        raise SourceError(f"Unknown managed provider {provider!r}")
    adapter_id = f"managed_{provider}"
    report = DiscoveryReport(adapter=adapter_id, company_identifier=company)
    adapter = ManagedCareerOpsAdapter(provider, fixtures_dir=None)
    try:
        discovered = adapter.search(
            company=company,
            location=location,
            limit=max(1, min(int(limit), 100)),
            offline=offline,
        )
    except SourceUnavailable as exc:
        report.add_source(SourceResult(adapter_id, "unavailable", utc_now_iso(), error=str(exc)))
        report.notes.append(str(exc))
        payload = report.to_data()
        payload["managed_upstream"] = {"repo": UPSTREAM_REPO, "ref": UPSTREAM_REF}
        return payload
    except SourceError as exc:
        report.add_source(SourceResult(adapter_id, "error", utc_now_iso(), error=str(exc)))
        report.notes.append(str(exc))
        payload = report.to_data()
        payload["managed_upstream"] = {"repo": UPSTREAM_REPO, "ref": UPSTREAM_REF}
        return payload

    dedupe = DedupeStore()
    for job in discovered:
        if not dedupe.add(job.dedupe_key()):
            report.duplicates_dropped += 1
            continue
        report.jobs.append(job.to_scanner_job())
        report.raw_jobs.append(job.to_data())
    report.add_source(
        SourceResult(
            adapter_id,
            "ok" if report.jobs else "empty",
            utc_now_iso(),
            jobs_fetched=len(report.jobs),
        )
    )
    report.notes.append(
        f"Portal HTTP/parsing implementation provided by {UPSTREAM_REPO}@{UPSTREAM_REF}; "
        "Career Engine supplied normalization/provenance/no-send controls."
    )
    payload = report.to_data()
    payload["managed_upstream"] = {"repo": UPSTREAM_REPO, "ref": UPSTREAM_REF}
    payload["offline_fixture"] = bool(offline)
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("providers")
    probe = sub.add_parser("probe")
    probe.add_argument("--provider", required=True, choices=sorted(PROVIDERS))
    probe.add_argument("--company", required=True)
    probe.add_argument("--location")
    probe.add_argument("--limit", type=int, default=20)
    probe.add_argument("--offline", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "providers":
        print(json.dumps(provider_payload(), indent=2, ensure_ascii=False))
        return 0
    payload = run_probe(
        provider=args.provider,
        company=args.company,
        location=args.location,
        limit=args.limit,
        offline=args.offline,
    )
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    statuses = {row.get("status") for row in payload.get("sources", [])}
    return 1 if "error" in statuses else 0


if __name__ == "__main__":
    raise SystemExit(main())
