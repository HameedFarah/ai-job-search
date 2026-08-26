from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from career_engine.generation import run_adapter


def _write_stage(command, *, payload=None):
    prompt = command[-1]
    stage = re.search(r"to (/.+?)\. Do not modify", prompt).group(1)
    if payload is not None:
        with open(stage, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
    return subprocess.CompletedProcess(command, 0, "", "")


def test_routed_output_is_promoted_and_stage_cleaned(tmp_path, monkeypatch):
    output = tmp_path / "tracker" / "generated_application.pending.json"

    def dispatch(command, **kwargs):
        assert kwargs["cwd"].is_dir()
        return _write_stage(command, payload={"job_id": "fixture-job"})

    monkeypatch.setattr(subprocess, "run", dispatch)
    result = run_adapter("hermes", tmp_path / "packet.json", output)

    assert json.loads(output.read_text()) == {"job_id": "fixture-job"}
    assert result["output_exists"] is True
    assert not list(Path.cwd().glob(".career-generation-*.json"))


@pytest.mark.parametrize("stage_mode", ["missing", "invalid"])
def test_successful_route_without_valid_stage_fails_closed(tmp_path, monkeypatch, stage_mode):
    output = tmp_path / "tracker" / "generated.json"

    def dispatch(command, **kwargs):
        return _write_stage(command, payload=None if stage_mode == "missing" else ["not", "an", "object"])

    monkeypatch.setattr(subprocess, "run", dispatch)
    with pytest.raises(RuntimeError, match="staged output"):
        run_adapter("hermes", tmp_path / "packet.json", output)

    assert not output.exists()
    assert not list(Path.cwd().glob(".career-generation-*.json"))


def test_manual_adapter_remains_unexecuted_and_does_not_stage(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: pytest.fail("manual must not dispatch"))
    output = tmp_path / "generated.json"

    result = run_adapter("manual", tmp_path / "packet.json", output)

    assert result == {
        "adapter": "manual",
        "packet": str(tmp_path / "packet.json"),
        "output": str(output),
        "executed": False,
    }
