from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

import career_engine.generation as generation
from career_engine.generation import run_adapter


def _write_stage(command, *, payload=None):
    prompt_file = Path(command[command.index("--prompt-file") + 1])
    prompt = prompt_file.read_text(encoding="utf-8")
    stage = Path(re.search(r"to (/.+?)\. Follow the packet schema", prompt).group(1))
    if payload is not None:
        stage.write_text(json.dumps(payload), encoding="utf-8")
    return subprocess.CompletedProcess(command, 0, "", "")


def _configure_runtime(tmp_path, monkeypatch):
    repo_root = tmp_path / "runtime"
    repo_root.mkdir()
    cache_dir = tmp_path / "tracker" / "runtime" / "model-routing-authority-cache"
    monkeypatch.setattr(generation, "_dispatcher_runtime_paths", lambda: (repo_root, cache_dir))
    return repo_root


def test_routed_output_is_promoted_and_stage_cleaned(tmp_path, monkeypatch):
    output = tmp_path / "tracker" / "generated_application.pending.json"
    repo_root = _configure_runtime(tmp_path, monkeypatch)

    def dispatch(command, **kwargs):
        assert command[command.index("--cwd") + 1] == str(repo_root)
        return _write_stage(command, payload={"job_id": "fixture-job"})

    monkeypatch.setattr(subprocess, "run", dispatch)
    result = run_adapter("hermes", tmp_path / "packet.json", output)

    assert json.loads(output.read_text()) == {"job_id": "fixture-job"}
    assert result["output_exists"] is True
    assert not list(repo_root.glob(".career-generation-*.json"))


@pytest.mark.parametrize("stage_mode", ["missing", "invalid"])
def test_successful_route_without_valid_stage_fails_closed(tmp_path, monkeypatch, stage_mode):
    output = tmp_path / "tracker" / "generated.json"
    repo_root = _configure_runtime(tmp_path, monkeypatch)

    def dispatch(command, **kwargs):
        return _write_stage(command, payload=None if stage_mode == "missing" else ["not", "an", "object"])

    monkeypatch.setattr(subprocess, "run", dispatch)
    with pytest.raises(RuntimeError, match="staged output"):
        run_adapter("hermes", tmp_path / "packet.json", output)

    assert not output.exists()
    assert not list(repo_root.glob(".career-generation-*.json"))


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
