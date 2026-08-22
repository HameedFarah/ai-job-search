from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _load_wrapper():
    path = Path("projects/job-automation/hermes_scanner.py")
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("hermes_scanner_under_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_hermes_entry_point_publishes_review_bundle(monkeypatch, tmp_path, capsys):
    wrapper = _load_wrapper()
    calls = []

    monkeypatch.setattr(wrapper, "load_bundle", lambda root: {"taxonomy": {}})
    monkeypatch.setattr(wrapper, "load_config", lambda root: ({}, object()))
    monkeypatch.setattr(wrapper, "_load_tracker", lambda paths: object())
    monkeypatch.setattr(wrapper, "reconcile_existing_non_target_jobs", lambda *args, **kwargs: {})
    monkeypatch.setattr(wrapper, "run_scan", lambda *args, **kwargs: {"scanner_id": "hermes_scanner"})
    monkeypatch.setattr(wrapper, "_build_review_bundle", lambda report: {"report": report})
    monkeypatch.setattr(
        wrapper,
        "_publish_review_bundle",
        lambda bundle: calls.append(bundle) or {"status": "published", "commit": "abc123"},
    )
    monkeypatch.setattr(wrapper, "write_report", lambda report, output: json.dumps(report))

    wrapper.main(["--input", str(tmp_path / "input.json")])

    report = json.loads(capsys.readouterr().out)
    assert report["scanner_id"] == "hermes_scanner"
    assert report["review_bundle_publication"] == {"status": "published", "commit": "abc123"}
    assert calls == [{"report": report}]
