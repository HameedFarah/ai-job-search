import importlib.util
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "projects/job-automation/hermes/career-engine-daily-context.py"
SPEC = importlib.util.spec_from_file_location("career_daily_context", SCRIPT)
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def _result(args, *, stdout="", returncode=0):
    return subprocess.CompletedProcess(args=args, returncode=returncode, stdout=stdout, stderr="")


def test_source_preflight_fast_forwards_only_clean_behind_checkout(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    state = {"head": "old", "origin": "new"}
    calls = []

    def fake_git(root, *args, check=True):
        calls.append(args)
        if args[:2] == ("status", "--porcelain=v1"):
            return _result(args, stdout="")
        if args[:2] == ("fetch", "origin"):
            return _result(args)
        if args == ("rev-parse", "HEAD"):
            return _result(args, stdout=state["head"] + "\n")
        if args == ("rev-parse", "origin/master"):
            return _result(args, stdout=state["origin"] + "\n")
        if args[:2] == ("merge-base", "--is-ancestor"):
            return _result(args, returncode=0)
        if args == ("merge", "--ff-only", "origin/master"):
            state["head"] = state["origin"]
            return _result(args)
        raise AssertionError(args)

    monkeypatch.setattr(mod, "_git", fake_git)
    result = mod.ensure_canonical_source(tmp_path)
    assert result["status"] == "canonical"
    assert result["fast_forwarded"] is True
    assert result["head"] == "new"
    assert ("merge", "--ff-only", "origin/master") in calls


def test_source_preflight_refuses_dirty_checkout(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(
        mod,
        "_git",
        lambda root, *args, check=True: _result(args, stdout=" M career_engine/cli.py\n"),
    )
    with pytest.raises(RuntimeError, match="source changes"):
        mod.ensure_canonical_source(tmp_path)


def test_daily_context_treats_linkedin_and_freehire_as_source_adapters_not_scheduler_skills():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "attached LinkedIn-public, Freehire" not in text
    assert "they are source adapters, not scheduler skill attachments" in text
    assert "do not add or edit Hermes scheduler skills during a scan" in text


def test_source_preflight_refuses_ahead_or_diverged_checkout(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()

    def fake_git(root, *args, check=True):
        if args[:2] == ("status", "--porcelain=v1"):
            return _result(args, stdout="")
        if args[:2] == ("fetch", "origin"):
            return _result(args)
        if args == ("rev-parse", "HEAD"):
            return _result(args, stdout="local\n")
        if args == ("rev-parse", "origin/master"):
            return _result(args, stdout="remote\n")
        if args[:2] == ("merge-base", "--is-ancestor"):
            return _result(args, returncode=1)
        raise AssertionError(args)

    monkeypatch.setattr(mod, "_git", fake_git)
    with pytest.raises(RuntimeError, match="ahead/diverged"):
        mod.ensure_canonical_source(tmp_path)
