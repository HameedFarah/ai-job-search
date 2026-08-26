from __future__ import annotations

import json
import inspect
import subprocess

from career_engine import generation


def test_routed_adapter_uses_central_dispatcher_and_writes_evidence(tmp_path, monkeypatch):
    packet = tmp_path / "generation_packet.json"
    packet.write_text("{}", encoding="utf-8")
    output = tmp_path / "generated_application.pending.json"

    repo_root = tmp_path / "runtime-repo"
    tracker_runtime = tmp_path / "tracker" / "runtime"
    repo_root.mkdir()
    tracker_runtime.mkdir(parents=True)
    monkeypatch.setattr(generation, "_dispatcher_runtime_paths", lambda: (repo_root, tracker_runtime / "model-routing-authority-cache"))

    def fake_run(command, **kwargs):
        assert command[0] == "/usr/bin/python3"
        assert command[1] == str(generation.CENTRAL_DISPATCHER)
        assert "--evidence-file" in command
        assert command[command.index("--cwd") + 1] == str(repo_root)
        assert command[command.index("--cache-dir") + 1] == str(tracker_runtime / "model-routing-authority-cache")
        assert "--model" not in command and "--provider" not in command
        output.write_text(json.dumps({"headline": "routed"}), encoding="utf-8")
        evidence = tmp_path / "model-routing-evidence.json"
        evidence.write_text("{\"route\":\"test\"}", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(generation.subprocess, "run", fake_run)
    result = generation.run_adapter("opencode", packet, output, model="owner/debug-model")
    assert result["returncode"] == 0
    assert result["evidence_exists"] is True


def test_dispatcher_failure_is_fail_closed(tmp_path, monkeypatch):
    packet = tmp_path / "generation_packet.json"
    packet.write_text("{}", encoding="utf-8")
    output = tmp_path / "generated_application.pending.json"
    repo_root = tmp_path / "runtime-repo"
    tracker_runtime = tmp_path / "tracker" / "runtime"
    repo_root.mkdir()
    tracker_runtime.mkdir(parents=True)
    monkeypatch.setattr(generation, "_dispatcher_runtime_paths", lambda: (repo_root, tracker_runtime / "model-routing-authority-cache"))

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 17, "", "dispatcher failed")

    monkeypatch.setattr(generation.subprocess, "run", fake_run)
    result = generation.run_adapter("hermes", packet, output)
    assert result["returncode"] == 17
    assert result["output_exists"] is False


def test_manual_adapter_is_unchanged(tmp_path):
    packet = tmp_path / "packet.json"
    output = tmp_path / "output.json"
    result = generation.run_adapter("manual", packet, output, provider="owner", model="debug")
    assert result == {"adapter": "manual", "packet": str(packet), "output": str(output), "executed": False}


def test_generation_has_no_direct_model_default():
    assert "deepseek-v4-flash" not in inspect.getsource(generation.run_adapter)
