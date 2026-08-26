"""Regression tests for the fail-closed daily context preflight.

Hermes runs ``career-engine-daily-context.py`` from the dedicated clean runtime
worktree and may still launch an agent when the script exits non-zero, so a
failed preflight must emit an explicit BLOCKED context ordering the agent to
stop instead of leaving room for improvised scanning. Career Engine code is
imported only after both gates pass: source sync (clean + origin/master,
fast-forward-only) and the runtime authority pointer binding to a continuous
live tracker base.
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "projects/job-automation/hermes/career-engine-daily-context.py"
ORIGIN_SHA = "b" * 40
LOCAL_SHA = "a" * 40


def load_context_module(tmp_path, monkeypatch):
    """Load the context script with REPO_ROOT bound to an isolated tmp cwd."""
    monkeypatch.chdir(tmp_path)
    spec = importlib.util.spec_from_file_location("career_engine_daily_context_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeGit:
    """Scripted stand-in for the module-level _git helper."""

    def __init__(self, *, valid=True, dirty=False, head=LOCAL_SHA,
                 fetch_fails=False, behind=False):
        self.valid = valid
        self.dirty_lines = [" M tracked.py\n", "?? stray.txt\n"] if dirty else []
        self.head = LOCAL_SHA if behind else head
        self.fetch_fails = fetch_fails
        self.behind = behind
        self.calls: list[tuple] = []

    def __call__(self, root, *args, check=True):
        argv = list(args)
        self.calls.append(tuple(argv))

        def done(rc=0, out="", err=""):
            proc = subprocess.CompletedProcess(argv, rc, out, err)
            if check and rc != 0:
                raise subprocess.CalledProcessError(rc, argv, output=out, stderr=err)
            return proc

        if not self.valid:
            return done(128, "", "fatal: not a git repository")
        if argv[:1] == ["fetch"]:
            if self.fetch_fails:
                return done(128, "", "could not resolve host")
            return done()
        if argv[:1] == ["status"]:
            return done(0, "".join(self.dirty_lines))
        if argv[:2] == ["merge-base", "--is-ancestor"]:
            return done(0 if self.behind else 1)
        if argv == ["rev-parse", "HEAD"]:
            return done(0, self.head + "\n")
        if argv == ["rev-parse", "origin/master"]:
            return done(0, ORIGIN_SHA + "\n")
        if argv == ["merge", "--ff-only", "origin/master"]:
            self.head = ORIGIN_SHA
            return done()
        raise AssertionError(f"unexpected git call: {argv}")


def install_git(module, fake):
    module._git = fake


def make_live_base(tmp_path: Path, *, records: int = 3) -> Path:
    base = tmp_path / "live-career-tracker"
    (base / "data/jobs").mkdir(parents=True)
    (base / "data/jobs.csv").write_text("job_id\n", encoding="utf-8")
    for index in range(records):
        (base / "data/jobs" / f"job{index}.json").write_text("{}", encoding="utf-8")
    return base


def write_pointer(root: Path, base: Path) -> Path:
    pointer = root / "runtime/runtime-authority.json"
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(json.dumps({"schema_version": 1, "tracker_base": str(base)}), encoding="utf-8")
    return pointer


def forbid_career_engine_import(monkeypatch):
    real_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

    def guarded(name, *args, **kwargs):
        if name.split(".")[0] == "career_engine":
            raise AssertionError(f"career_engine import attempted after blocked preflight: {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", guarded)


def test_blocked_context_emitted_when_runtime_not_a_checkout(tmp_path, monkeypatch, capsys):
    mod = load_context_module(tmp_path, monkeypatch)
    forbid_career_engine_import(monkeypatch)
    assert mod.main() == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "blocked"
    assert payload["do_not_scan"] is True
    assert "STOP - DO NOT SCAN" in payload["instruction"]
    assert "/tmp" in payload["instruction"]
    assert "Never" in payload["instruction"]
    assert payload["send_or_submit"] is False


def test_blocked_context_emitted_when_dirty(tmp_path, monkeypatch, capsys):
    mod = load_context_module(tmp_path, monkeypatch)
    (tmp_path / ".git").mkdir()
    write_pointer(tmp_path, make_live_base(tmp_path))
    install_git(mod, FakeGit(dirty=True))
    forbid_career_engine_import(monkeypatch)
    assert mod.main() == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "blocked" and payload["do_not_scan"] is True
    assert "source changes" in payload["error"] or "dirty" in payload["error"]


def test_blocked_context_emitted_when_authority_pointer_missing(tmp_path, monkeypatch, capsys):
    mod = load_context_module(tmp_path, monkeypatch)
    (tmp_path / ".git").mkdir()
    install_git(mod, FakeGit(head=ORIGIN_SHA))
    forbid_career_engine_import(monkeypatch)
    assert mod.main() == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "blocked"
    assert "authority pointer missing" in payload["error"]


def test_blocked_context_emitted_when_bound_tracker_is_empty(tmp_path, monkeypatch, capsys):
    mod = load_context_module(tmp_path, monkeypatch)
    (tmp_path / ".git").mkdir()
    empty_base = tmp_path / "empty-tracker"
    (empty_base / "data/jobs").mkdir(parents=True)  # no jobs.csv, no records
    write_pointer(tmp_path, empty_base)
    install_git(mod, FakeGit(head=ORIGIN_SHA))
    forbid_career_engine_import(monkeypatch)
    assert mod.main() == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "blocked"
    assert "continuity census" in payload["error"]
    assert "second tracker" in payload["error"]


def test_canonical_context_carries_source_sync_and_authority(tmp_path, monkeypatch, capsys):
    mod = load_context_module(tmp_path, monkeypatch)
    (tmp_path / ".git").mkdir()
    live_base = make_live_base(tmp_path)
    write_pointer(tmp_path, live_base)
    install_git(mod, FakeGit(head=ORIGIN_SHA))
    _fake_career_engine(monkeypatch)
    monkeypatch.setattr(
        mod, "reconcile_applied_mail",
        lambda: {"status": "ok", "applied_jobs": 0, "matches": []},
    )
    assert mod.main() == 0
    context = json.loads(capsys.readouterr().out)
    assert context["source_sync"]["status"] == "canonical"
    assert context["source_sync"]["head"] == ORIGIN_SHA
    authority = context["runtime_authority"]
    assert authority["status"] == "canonical"
    assert authority["jobs_csv"] is True
    assert authority["job_records"] >= 1
    assert Path(authority["tracker_base"]) == live_base
    assert context["send_or_submit"] is False
    assert "run --all" in " ".join(context["workflow"])


def test_preflight_runs_before_bundle_load_when_behind(tmp_path, monkeypatch, capsys):
    """A strictly-behind clean runtime fast-forwards before any engine import."""
    mod = load_context_module(tmp_path, monkeypatch)
    (tmp_path / ".git").mkdir()
    write_pointer(tmp_path, make_live_base(tmp_path))
    fake = FakeGit(behind=True)
    install_git(mod, fake)
    calls: list[tuple] = []
    original = mod.read_runtime_authority

    def spy(root):
        calls.append(("authority_after_sync", len(fake.calls)))
        return original(root)

    monkeypatch.setattr(mod, "read_runtime_authority", spy)
    _fake_career_engine(monkeypatch)
    monkeypatch.setattr(mod, "reconcile_applied_mail", lambda: {"status": "ok"})
    assert mod.main() == 0
    sync_calls = [c for c in fake.calls if c[0][0] in {"fetch", "status"}]
    assert calls and calls[0][1] >= len(sync_calls), (
        "authority validation must happen only after the bounded source sync"
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["source_sync"]["fast_forwarded"] is True


def _fake_career_engine(monkeypatch):
    import types

    class Bundle:
        @staticmethod
        def load_bundle(_root):
            return {
                "bundle_hash": "test-bundle-hash",
                "config": {"daily_scanner": {"minimum_score_for_generation": 70}},
            }

    package = types.ModuleType("career_engine")
    bundle = types.ModuleType("career_engine.bundle")
    bundle.load_bundle = Bundle.load_bundle  # type: ignore[attr-defined]
    package.bundle = bundle  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "career_engine", package)
    monkeypatch.setitem(sys.modules, "career_engine.bundle", bundle)
    return package
