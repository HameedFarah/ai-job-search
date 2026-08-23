from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from career_engine.rega_enrichment.provider_batch import run_provider_batch
from career_engine.rega_enrichment.provider_waterfall import WaterfallResult
from career_engine.rega_enrichment.providers import recruitment_contact


FIELDS = ["company_id", "License No", "English Name", "official_domain", "assignment"]


def _sidecar(path: Path) -> Path:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows([
            {"company_id": "1", "License No": "A", "English Name": "Alpha", "official_domain": "www.alpha.sa", "assignment": "confirmed"},
            {"company_id": "2", "License No": "B", "English Name": "Alpha Branch", "official_domain": "https://alpha.sa/", "assignment": "confirmed"},
            {"company_id": "3", "License No": "C", "English Name": "Beta", "official_domain": "beta.sa", "assignment": "candidate"},
            {"company_id": "4", "License No": "D", "English Name": "Unknown", "official_domain": "", "assignment": "not_found"},
        ])
    return path


def test_provider_batch_dedupes_confirmed_domains_and_never_promotes(tmp_path: Path) -> None:
    calls = []

    def fake(domain: str, *, allow_existing_credit: bool = False):
        calls.append((domain, allow_existing_credit))
        return WaterfallResult(
            provider_statuses=[{"provider": "outscraper", "status": "success"}],
            contacts=[recruitment_contact("outscraper", "contact@alpha.sa", "https://provider.example/alpha", "provider candidate")],
        )

    sidecar = _sidecar(tmp_path / "sidecar.csv")
    output = tmp_path / "candidates.jsonl"
    summary = run_provider_batch(sidecar, output, allow_existing_credit=True, waterfall=fake)

    assert calls == [("alpha.sa", True)]
    assert summary["eligible_unique_domains"] == 1
    assert summary["processed_unique_domains"] == 1
    assert summary["candidate_contacts"] == 1
    assert summary["outreach_ready_contacts"] == 0
    assert summary["zerobounce_called"] is False
    assert summary["purchase_or_topup_performed"] is False
    assert summary["official_fields_mutated"] is False
    record = json.loads(output.read_text(encoding="utf-8").strip())
    assert record["company_ids"] == ["1", "2"]
    assert record["candidate_contacts"][0]["official_recruitment"] is False
    assert record["outreach_ready_count"] == 0


def test_provider_batch_candidate_domains_require_explicit_opt_in(tmp_path: Path) -> None:
    calls = []

    def fake(domain: str, *, allow_existing_credit: bool = False):
        calls.append(domain)
        return WaterfallResult(provider_statuses=[], contacts=[])

    sidecar = _sidecar(tmp_path / "sidecar.csv")
    run_provider_batch(sidecar, tmp_path / "confirmed.jsonl", waterfall=fake)
    assert calls == ["alpha.sa"]

    calls.clear()
    run_provider_batch(sidecar, tmp_path / "with-candidates.jsonl", include_candidates=True, waterfall=fake)
    assert calls == ["alpha.sa", "beta.sa"]


def test_provider_batch_rejects_provider_side_official_promotion(tmp_path: Path) -> None:
    def unsafe(domain: str, *, allow_existing_credit: bool = False):
        return WaterfallResult(
            provider_statuses=[{"provider": "unsafe", "status": "success"}],
            contacts=[recruitment_contact("rega-official", "careers@alpha.sa", "https://alpha.sa/careers", "official", official=True)],
        )

    sidecar = _sidecar(tmp_path / "sidecar.csv")
    with pytest.raises(RuntimeError, match="official recruitment promotion"):
        run_provider_batch(sidecar, tmp_path / "unsafe.jsonl", waterfall=unsafe)


def test_provider_batch_max_domains_is_bounded(tmp_path: Path) -> None:
    calls = []

    def fake(domain: str, *, allow_existing_credit: bool = False):
        calls.append(domain)
        return WaterfallResult(provider_statuses=[], contacts=[])

    sidecar = _sidecar(tmp_path / "sidecar.csv")
    summary = run_provider_batch(
        sidecar,
        tmp_path / "bounded.jsonl",
        include_candidates=True,
        max_domains=1,
        waterfall=fake,
    )
    assert calls == ["alpha.sa"]
    assert summary["processed_unique_domains"] == 1
    assert summary["eligible_unique_domains"] == 2
