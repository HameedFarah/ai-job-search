import json
from pathlib import Path

from career_engine.rega_enrichment.continuous import DISCOVERY_VERSION, _batch_maps_prefetch, process


class NoCallOutscraper:
    def maps_businesses_batch(self, *args, **kwargs):
        raise AssertionError("fresh Outscraper call is forbidden")


class NoHostResearch:
    def resolve(self, row):
        return "", [], [], "identity_unconfirmed"


class NoResolveResearch:
    def resolve(self, row):
        raise AssertionError("generic research must be skipped in cache-only mode")


def test_batch_prefetch_cache_only_never_calls_provider(tmp_path: Path):
    cache = {"discovery_version": DISCOVERY_VERSION, "maps_batch_version": 3,
             "records": {"CE-1": {"records": [{"status": "candidate"}]}}}
    (tmp_path / "outscraper-maps-v9.json").write_text(json.dumps(cache))
    got = _batch_maps_prefetch(tmp_path, [], {}, NoCallOutscraper(), allow_fresh=False)
    assert got == {"CE-1": [{"status": "candidate"}]}


def test_cache_miss_no_credit_finishes_without_provider_call():
    row = {"Master_ID": "CE-2", "Company_or_Office": "Example", "Arabic_Name": "", "Region": "Riyadh"}
    result = process(row, NoHostResearch(), object(), object(), outscraper=object(), maps_cache={}, outscraper_allow_fresh=False)
    assert result["outcome"] == "identity_unconfirmed"
    assert result["outscraper_maps"]["basis"] == "outscraper_cache_miss_no_credit"


def test_cached_provider_failure_is_terminal_without_credit():
    row = {"Master_ID": "CE-3", "Company_or_Office": "Example", "Arabic_Name": "", "Region": "Riyadh"}
    cache = {"CE-3": [{"status": "quota_required"}]}
    result = process(row, NoResolveResearch(), object(), object(), outscraper=object(), maps_cache=cache, outscraper_allow_fresh=False)
    assert result["outcome"] == "identity_unconfirmed"
    assert result["outscraper_maps"]["retryable"] is True
