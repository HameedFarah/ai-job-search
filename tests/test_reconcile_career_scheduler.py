import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("reconcile_scheduler", ROOT / "tools/reconcile_career_scheduler.py")
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def manifest():
    return json.loads(mod.MANIFEST.read_text())


def test_profile_resolution_does_not_change_active_profile(tmp_path, monkeypatch):
    hermes = tmp_path / ".hermes"
    (hermes).mkdir()
    (hermes / "active_profile").write_text("agency\n")
    monkeypatch.setattr(mod, "DEFAULT_HERMES", hermes)
    assert mod.profile_home(None) == hermes / "profiles" / "agency"
    assert (hermes / "active_profile").read_text() == "agency\n"


def test_check_reports_missing_script_and_bounded_state(tmp_path, monkeypatch, capsys):
    hermes = tmp_path / ".hermes"
    target = hermes / "cron"
    target.mkdir(parents=True)
    (hermes / "active_profile").write_text("default\n")
    (target / "jobs.json").write_text(json.dumps({"jobs": []}))
    monkeypatch.setattr(mod, "DEFAULT_HERMES", hermes)
    monkeypatch.setattr(
        mod, "resolve_target_profile",
        lambda requested: ("default", {"running_gateway_profile": "default", "requested_profile": requested}),
    )
    assert mod.main(["--check"]) == 1
    output = json.loads(capsys.readouterr().out)
    for key in {
        "active_profile", "target_store", "matching_job_id", "schedule",
        "duplicates", "enabled_jobs_total", "runtime_script_bytes_match",
        "runtime_worktree", "runtime_authority", "host_timezone", "problems",
        "target_profile", "running_gateway_profile", "status",
    }:
        assert key in output, f"missing check key {key}"
    assert output["status"] == "drift"


def test_matching_rejects_inference_pins():
    data = manifest()
    job = {"name": data["name"], "schedule": {"expr": data["schedule"]}, "schedule_display": data["schedule"],
           "prompt": data["prompt"], "skills": data["skills"], "script": data["runtime_script"],
           "no_agent": data["no_agent"], "deliver": data["deliver"], "workdir": data["workdir"],
           "model": None, "provider": None}
    assert mod.matching(job, data)
    job["provider"] = "unexpected"
    assert not mod.matching(job, data)


def test_run_hermes_pins_profile_explicitly(tmp_path, monkeypatch):
    hermes = tmp_path / ".hermes"
    agency = hermes / "profiles" / "agency"
    agency.mkdir(parents=True)
    monkeypatch.setattr(mod, "DEFAULT_HERMES", hermes)
    monkeypatch.setattr(mod.shutil, "which", lambda name: "/usr/local/bin/hermes")
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        class Result:
            returncode = 0
        return Result()

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    mod.run_hermes(hermes, "pause", "legacy")
    mod.run_hermes(agency, "resume", "current")
    assert calls[0][0][:4] == ["/usr/local/bin/hermes", "--profile", "default", "cron"]
    assert calls[1][0][:4] == ["/usr/local/bin/hermes", "--profile", "agency", "cron"]
    assert calls[0][1]["env"]["HERMES_HOME"] == str(hermes)
    assert calls[1][1]["env"]["HERMES_HOME"] == str(agency)


def test_resolve_hermes_uses_maintained_fallback(tmp_path, monkeypatch):
    hermes = tmp_path / ".hermes"
    executable = hermes / "hermes-agent" / "venv" / "bin" / "hermes"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\n")
    executable.chmod(0o755)
    monkeypatch.setattr(mod, "DEFAULT_HERMES", hermes)
    monkeypatch.setattr(mod.shutil, "which", lambda name: None)
    monkeypatch.delenv(mod.HERMES_EXECUTABLE_ENV, raising=False)
    assert mod.resolve_hermes() == str(executable)


def test_resolve_hermes_fails_clearly_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "DEFAULT_HERMES", tmp_path / ".hermes")
    monkeypatch.setattr(mod.shutil, "which", lambda name: None)
    monkeypatch.delenv(mod.HERMES_EXECUTABLE_ENV, raising=False)
    with pytest.raises(FileNotFoundError, match="Hermes CLI not found"):
        mod.resolve_hermes()


def test_apply_create_uses_create_supported_arguments(tmp_path, monkeypatch):
    data = manifest()
    hermes = tmp_path / ".hermes"
    agency = hermes / "profiles" / "agency"
    (agency / "cron").mkdir(parents=True)
    (agency / "cron/jobs.json").write_text(json.dumps({"jobs": []}))
    (hermes / "active_profile").parent.mkdir(parents=True, exist_ok=True)
    (hermes / "active_profile").write_text("agency\n")
    source = tmp_path / "source.py"
    source.write_text("print('ok')\n")
    data["source_script"] = "source.py"
    monkeypatch.setattr(mod, "DEFAULT_HERMES", hermes)
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    monkeypatch.setattr(mod, "ensure_runtime_worktree", lambda m: {"path": m["workdir"], "clean": True})
    monkeypatch.setattr(
        mod, "write_runtime_authority",
        lambda m: {"pointer": m["runtime_authority_pointer"], "written": True, "continuous": True},
    )
    monkeypatch.setattr(mod, "provision_skills", lambda manifest, target: None)
    monkeypatch.setattr(
        mod, "runtime_worktree_status",
        lambda m: {"path": m["workdir"], "ok": True, "problems": []},
    )
    monkeypatch.setattr(
        mod, "read_runtime_authority_status",
        lambda m: {"pointer": "p", "ok": True, "problems": []},
    )
    monkeypatch.setattr(mod, "host_timezone_name", lambda: data["timezone"])
    calls = []

    def fake_run(home, *args):
        calls.append((home, args))
        if args[0] == "create":
            payload = {"jobs": [{
                "id": "newid", "name": data["name"], "prompt": data["prompt"],
                "skills": data["skills"], "script": data["runtime_script"], "no_agent": False,
                "deliver": data["deliver"], "workdir": data["workdir"], "model": None, "provider": None,
                "schedule": {"expr": data["schedule"]}, "schedule_display": data["schedule"], "enabled": True,
            }]}
            (agency / "cron/jobs.json").write_text(json.dumps(payload))

    monkeypatch.setattr(mod, "run_hermes", fake_run)
    result = mod.apply(data, agency)
    create = next(args for _, args in calls if args[0] == "create")
    assert create[1] == data["schedule"]
    assert create[2] == data["prompt"]
    assert "--prompt" not in create
    assert "--agent" not in create
    assert result["status"] == "ok"


def test_apply_edit_replaces_skills_without_self_pausing_on_drift(tmp_path, monkeypatch):
    data = manifest()
    hermes = tmp_path / ".hermes"
    agency = hermes / "profiles" / "agency"
    (agency / "cron").mkdir(parents=True)
    existing = {
        "id": "current", "name": data["name"], "prompt": data["prompt"],
        "skills": ["old-skill"], "script": data["runtime_script"], "no_agent": False,
        "deliver": data["deliver"], "workdir": data["workdir"], "model": None, "provider": None,
        "schedule": {"expr": data["schedule"]}, "schedule_display": data["schedule"], "enabled": True,
    }
    (agency / "cron/jobs.json").write_text(json.dumps({"jobs": [existing]}))
    (hermes / "active_profile").write_text("agency\n")
    source = tmp_path / "source.py"
    source.write_text("print('ok')\n")
    data["source_script"] = "source.py"
    monkeypatch.setattr(mod, "DEFAULT_HERMES", hermes)
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    monkeypatch.setattr(mod, "ensure_runtime_worktree", lambda m: {"path": m["workdir"], "clean": True})
    monkeypatch.setattr(
        mod, "write_runtime_authority",
        lambda m: {"pointer": m["runtime_authority_pointer"], "written": True, "continuous": True},
    )
    monkeypatch.setattr(mod, "provision_skills", lambda manifest, target: None)
    monkeypatch.setattr(
        mod, "runtime_worktree_status",
        lambda m: {"path": m["workdir"], "ok": True, "problems": []},
    )
    monkeypatch.setattr(
        mod, "read_runtime_authority_status",
        lambda m: {"pointer": "p", "ok": True, "problems": []},
    )
    monkeypatch.setattr(mod, "host_timezone_name", lambda: data["timezone"])
    calls = []

    def fake_run(home, *args):
        calls.append((home, args))
        # Deliberately leave the stored job mismatched to exercise fail-visible
        # drift without allowing reconciliation to pause its primary target.

    monkeypatch.setattr(mod, "run_hermes", fake_run)
    result = mod.apply(data, agency)
    edit = next(args for _, args in calls if args[0] == "edit")
    assert "--clear-skills" not in edit
    assert "--add-skill" not in edit
    assert edit.count("--skill") == len(data["skills"])
    assert not any(args[0] == "pause" and args[1] == "current" for _, args in calls)
    assert result["status"] == "drift"


def test_manifest_skill_sources_resolve_in_fresh_profile(tmp_path, monkeypatch):
    data = manifest()
    hermes = tmp_path / ".hermes"
    target = hermes / "profiles" / "agency"
    (target / "cron").mkdir(parents=True)
    (target / "cron/jobs.json").write_text(json.dumps({"jobs": []}))
    repo = tmp_path / "repo"
    global_skills = tmp_path / "global-skills"
    for skill, entry in data["skill_sources"].items():
        root = repo if entry["kind"] == "repo" else global_skills
        source = root / entry["path"]
        source.mkdir(parents=True)
        (source / "SKILL.md").write_text(f"---\nname: {skill}\n---\n")
    monkeypatch.setattr(mod, "ROOT", repo)
    monkeypatch.setattr(mod, "GLOBAL_SKILLS", global_skills)
    mod.provision_skills(data, target)
    assert sorted(p.name for p in (target / "skills").iterdir()) == sorted(data["skills"])
    assert all((target / "skills" / skill / "SKILL.md").is_file() for skill in data["skills"])


# --- Running gateway profile --------------------------------------------------

import subprocess  # noqa: E402


def write_proc(proc_root: Path, pid: int, argv: list[str]) -> None:
    entry = proc_root / str(pid)
    entry.mkdir(parents=True)
    (entry / "cmdline").write_bytes("\0".join(argv).encode())


def test_running_gateway_profile_detection(tmp_path):
    proc_root = tmp_path / "proc"
    # A non-hermes gateway process must be ignored.
    write_proc(proc_root, 101, ["/usr/bin/python3", "/opt/tool/gatewayd", "--port", "9"])
    assert mod.running_gateway_profile(proc_root) is None
    # Explicit "--profile X" form.
    write_proc(
        proc_root, 202,
        ["/usr/bin/python3", "/home/hameedo/.hermes/hermes-agent/venv/bin/hermes",
         "--profile", "agency", "gateway"],
    )
    assert mod.running_gateway_profile(proc_root) == "agency"
    # "--profile=X" form on a second candidate process.
    (proc_root / "202" / "cmdline").unlink()
    write_proc(proc_root, 303, ["hermes", "--profile=default", "gateway", "--foreground"])
    assert mod.running_gateway_profile(proc_root) == "default"
    # No hermes gateway at all.
    (proc_root / "303" / "cmdline").unlink()
    (proc_root / "101" / "cmdline").unlink()
    assert mod.running_gateway_profile(proc_root) is None


def test_resolve_target_profile_enforces_running_gateway(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "running_gateway_profile", lambda proc_root=None: "agency")
    profile, info = mod.resolve_target_profile(None)
    assert profile == "agency" and info["running_gateway_profile"] == "agency"
    profile, _ = mod.resolve_target_profile("agency")
    assert profile == "agency"
    with pytest.raises(mod.ProfileMismatchError, match="does not match"):
        mod.resolve_target_profile("default")
    monkeypatch.setattr(mod, "running_gateway_profile", lambda proc_root=None: None)
    with pytest.raises(mod.ProfileMismatchError, match="no running hermes gateway"):
        mod.resolve_target_profile(None)
    profile, _ = mod.resolve_target_profile("default")
    assert profile == "default"


def test_main_apply_fails_closed_on_profile_mismatch(tmp_path, monkeypatch, capsys):
    data = manifest()
    hermes = tmp_path / ".hermes"
    agency = hermes / "profiles" / "agency"
    (agency / "cron").mkdir(parents=True)
    (agency / "cron/jobs.json").write_text(json.dumps({"jobs": []}))
    monkeypatch.setattr(mod, "DEFAULT_HERMES", hermes)
    monkeypatch.setattr(mod, "MANIFEST", tmp_path / "manifest.json")
    monkeypatch.setattr(mod, "running_gateway_profile", lambda proc_root=None: "agency")
    # requested profile 'default' != running gateway 'agency' -> exit 2 before any mutation
    assert mod.main(["--apply", "--profile", "default"]) == 2
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "error"
    assert "does not match" in output["error"]
    assert json.loads((agency / "cron/jobs.json").read_text())["jobs"] == []


# --- Dedicated runtime worktree provisioning ---------------------------------

TARGET_SHA = "b" * 40


class FakeRuntimeGit:
    """Scripted stand-in for reconcile_scheduler._run_git."""

    def __init__(self, runtime_path, *, exists=True, valid=True, registered=True,
                 dirty=False, head="a" * 40, local_ref=True, fetch_fails=False,
                 ahead=0):
        self.runtime_path = Path(runtime_path).resolve()
        self.exists = exists
        self.valid = valid
        self.registered = registered
        self.dirty_entries = [" M tracked.py\n"] if dirty else []
        self.head = head
        self.local_ref = local_ref
        self.fetch_fails = fetch_fails
        self.ahead = ahead
        self.calls: list[tuple] = []

    def __call__(self, args, *, cwd=None, timeout=60, check=True):
        argv = list(args)
        self.calls.append((tuple(argv), str(cwd) if cwd else None))

        def done(rc=0, out="", err=""):
            proc = subprocess.CompletedProcess(argv, rc, out, err)
            if check and rc != 0:
                raise subprocess.CalledProcessError(rc, argv, output=out, stderr=err)
            return proc

        at_runtime = cwd is not None and Path(cwd).resolve() == self.runtime_path
        if argv[:2] == ["fetch", "--quiet"]:
            if self.fetch_fails:
                return done(128, "", "network unreachable")
            return done()
        if argv[:2] == ["rev-parse", "--verify"]:
            if self.local_ref:
                return done(0, TARGET_SHA + "\n")
            return done(128, "", "unknown revision")
        if argv[:3] == ["worktree", "add", "--detach"]:
            self.exists = True
            self.valid = True
            self.registered = True
            self.head = TARGET_SHA
            return done()
        if argv[:1] == ["worktree"]:
            blocks = f"worktree {self.runtime_path}\ndetached\n\n" if self.registered else ""
            return done(0, blocks)
        if not at_runtime or not self.exists or not self.valid:
            return done(128, "", "fatal: not a git repository")
        if argv == ["rev-parse", "--is-inside-work-tree"]:
            return done(0, "true\n")
        if argv == ["rev-parse", "--show-toplevel"]:
            return done(0, str(self.runtime_path) + "\n")
        if argv == ["status", "--porcelain=v1"]:
            return done(0, "".join(self.dirty_entries))
        if argv == ["rev-parse", "HEAD"]:
            return done(0, self.head + "\n")
        if argv[1:2] == ["rev-list"] and argv[2] == "--count":
            spec = argv[-1]
            ahead_side = spec.startswith(TARGET_SHA)
            count = self.ahead if ahead_side else 0
            return done(0, f"{count}\n")
        if argv == ["merge", "--ff-only", mod.ORIGIN_REF]:
            self.head = TARGET_SHA
            return done()
        return done()


def test_ensure_runtime_worktree_provisions_missing_worktree(tmp_path, monkeypatch):
    runtime = tmp_path / "ai-job-search-daily-runtime"
    data = manifest()
    data["workdir"] = str(runtime)
    fake = FakeRuntimeGit(runtime, exists=False)
    monkeypatch.setattr(mod, "_run_git", fake)
    info = mod.ensure_runtime_worktree(data)
    provisions = [c for c in fake.calls if c[0][:2] == ("worktree", "add")]
    assert provisions == [(("worktree", "add", "--detach", str(runtime), TARGET_SHA), str(mod.ROOT))]
    assert info["provisioned"] is True and info["clean"] is True
    assert info["head"] == TARGET_SHA


def test_ensure_runtime_worktree_refuses_dirty_without_mutation(tmp_path, monkeypatch):
    runtime = tmp_path / "dirty-runtime"
    data = manifest()
    data["workdir"] = str(runtime)
    fake = FakeRuntimeGit(runtime, dirty=True)
    monkeypatch.setattr(mod, "_run_git", fake)
    with pytest.raises(mod.RuntimeWorktreeError, match="refusing to reset/clean/stash"):
        mod.ensure_runtime_worktree(data)
    assert not any(c[0][0] == "merge" for c in fake.calls)


def test_ensure_runtime_worktree_never_detaches_backward_from_ahead(tmp_path, monkeypatch):
    runtime = tmp_path / "ahead-runtime"
    data = manifest()
    data["workdir"] = str(runtime)
    fake = FakeRuntimeGit(runtime)
    monkeypatch.setattr(mod, "_run_git", fake)
    monkeypatch.setattr(mod, "_rev_list_count",
                        lambda spec, path: 2 if spec.startswith(TARGET_SHA) else 0)
    with pytest.raises(mod.RuntimeWorktreeError, match="detach backward"):
        mod.ensure_runtime_worktree(data)
    assert not any(c[0][0] == "merge" for c in fake.calls)
    assert not any(c[0][0] == "checkout" for c in fake.calls)


def test_ensure_runtime_worktree_fast_forwards_strictly_behind(tmp_path, monkeypatch):
    runtime = tmp_path / "behind-runtime"
    data = manifest()
    data["workdir"] = str(runtime)
    fake = FakeRuntimeGit(runtime, head="a" * 40)
    monkeypatch.setattr(mod, "_run_git", fake)
    # behind-only: HEAD..target counts 3, target..HEAD counts 0
    monkeypatch.setattr(mod, "_rev_list_count",
                        lambda spec, path: 3 if spec.startswith("HEAD..") else 0)
    info = mod.ensure_runtime_worktree(data)
    merges = [c for c in fake.calls if c[0][0] == "merge"]
    assert merges and merges[0][0] == ("merge", "--ff-only", mod.ORIGIN_REF)
    assert info["fast_forwarded"] is True and info["head"] == TARGET_SHA


def test_ensure_runtime_worktree_rejects_diverged(tmp_path, monkeypatch):
    runtime = tmp_path / "diverged-runtime"
    data = manifest()
    data["workdir"] = str(runtime)
    fake = FakeRuntimeGit(runtime, head="a" * 40)
    monkeypatch.setattr(mod, "_run_git", fake)
    monkeypatch.setattr(mod, "_rev_list_count",
                        lambda spec, path: 2 if spec.startswith(TARGET_SHA) else 3)
    with pytest.raises(mod.RuntimeWorktreeError, match="diverged"):
        mod.ensure_runtime_worktree(data)
    assert not any(c[0][0] == "merge" for c in fake.calls)


# --- Runtime authority binding -----------------------------------------------


def make_live_tracker(base: Path, records: int = 4) -> None:
    (base / "data/jobs").mkdir(parents=True, exist_ok=True)
    (base / "data/jobs.csv").write_text("job_id\n", encoding="utf-8")
    for index in range(records):
        (base / "data/jobs" / f"job{index}.json").write_text("{}", encoding="utf-8")


def test_write_runtime_authority_binds_live_base_and_validates_continuity(tmp_path):
    data = manifest()
    runtime = tmp_path / "runtime-worktree"
    runtime.mkdir()
    data["workdir"] = str(runtime)
    live_base = tmp_path / "primary-live"
    make_live_tracker(live_base)
    data["tracker_authority_base"] = str(live_base)
    result = mod.write_runtime_authority(data)
    pointer = runtime / data["runtime_authority_pointer"]
    payload = json.loads(pointer.read_text())
    assert payload["schema_version"] == 1
    assert payload["tracker_base"] == str(live_base)
    assert result["continuous"] is True and result["written"] is True


def test_write_runtime_authority_refuses_empty_or_missing_tracker(tmp_path):
    data = manifest()
    runtime = tmp_path / "runtime-worktree"
    runtime.mkdir()
    data["workdir"] = str(runtime)
    empty_base = tmp_path / "empty-primary"
    (empty_base / "data/jobs").mkdir(parents=True)  # no jobs.csv, no records
    data["tracker_authority_base"] = str(empty_base)
    with pytest.raises(mod.RuntimeWorktreeError, match="second empty tracker"):
        mod.write_runtime_authority(data)
    assert not (runtime / data["runtime_authority_pointer"]).exists()
    missing_csv = tmp_path / "csvless"
    make_live_tracker(missing_csv)
    (missing_csv / "data/jobs.csv").unlink()
    data["tracker_authority_base"] = str(missing_csv)
    with pytest.raises(mod.RuntimeWorktreeError, match="continuity census"):
        mod.write_runtime_authority(data)


def test_authority_status_detects_missing_wrong_and_empty_binding(tmp_path):
    data = manifest()
    runtime = tmp_path / "runtime-worktree"
    runtime.mkdir()
    data["workdir"] = str(runtime)
    live_base = tmp_path / "primary-live"
    make_live_tracker(live_base)
    other = tmp_path / "other-tracker"
    make_live_tracker(other)
    data["tracker_authority_base"] = str(live_base)
    missing = mod.read_runtime_authority_status(data)
    assert missing["ok"] is False and any("pointer missing" in p for p in missing["problems"])
    (runtime / data["runtime_authority_pointer"]).parent.mkdir(parents=True, exist_ok=True)
    (runtime / data["runtime_authority_pointer"]).write_text(json.dumps(
        {"schema_version": 1, "tracker_base": str(other)}))
    wrong = mod.read_runtime_authority_status(data)
    assert wrong["ok"] is False and any("instead of canonical" in p for p in wrong["problems"])
    # The canonical expectation itself fails the continuity census (live state
    # lost after binding): pointer matches expectation but must still drift.
    empty_bound = tmp_path / "bound-empty"
    (empty_bound / "data/jobs").mkdir(parents=True)
    data["tracker_authority_base"] = str(empty_bound)
    (runtime / data["runtime_authority_pointer"]).write_text(json.dumps(
        {"schema_version": 1, "tracker_base": str(empty_bound)}))
    empty = mod.read_runtime_authority_status(data)
    assert empty["ok"] is False and any("continuity census" in p for p in empty["problems"])
    data["tracker_authority_base"] = str(live_base)
    (runtime / data["runtime_authority_pointer"]).write_text(json.dumps(
        {"schema_version": 1, "tracker_base": str(live_base)}))
    ok_status = mod.read_runtime_authority_status(data)
    assert ok_status["ok"] is True and ok_status["job_records"] >= 1


# --- Extended inspection checks -----------------------------------------------


def _store_with_job(tmp_path, monkeypatch, data, *, script_bytes=b"different"):
    hermes = tmp_path / ".hermes"
    agency = hermes / "profiles" / "agency"
    (agency / "cron").mkdir(parents=True)
    (agency / "scripts").mkdir(parents=True)
    job = {"id": "primary", "name": data["name"], "prompt": data["prompt"],
           "skills": data["skills"], "script": data["runtime_script"], "no_agent": data["no_agent"],
           "deliver": data["deliver"], "workdir": data["workdir"], "model": None, "provider": None,
           "schedule": {"expr": data["schedule"]}, "schedule_display": data["schedule"], "enabled": True}
    (agency / "cron/jobs.json").write_text(json.dumps({"jobs": [job]}))
    deployed = agency / "scripts" / data["runtime_script"]
    deployed.write_bytes(script_bytes)
    (hermes / "cron").mkdir(parents=True)
    (hermes / "cron/jobs.json").write_text(json.dumps({"jobs": []}))
    monkeypatch.setattr(mod, "DEFAULT_HERMES", hermes)
    return agency


def test_inspect_flags_script_byte_drift_timezone_and_duplicates(tmp_path, monkeypatch):
    data = manifest()
    agency = _store_with_job(tmp_path, monkeypatch, data, script_bytes=b"stale bytes")
    monkeypatch.setattr(mod, "host_timezone_name", lambda: "Europe/London")
    drifted = mod.inspect(data, agency, gateway_profile="agency")
    assert drifted["status"] == "drift"
    assert drifted["runtime_script_bytes_match"] is False
    assert any("bytes drifted" in p for p in drifted["problems"])
    assert drifted["host_timezone"]["ok"] is False
    assert any("host timezone" in p for p in drifted["problems"])
    assert any("runtime worktree" in p for p in drifted["problems"])  # workdir absent in tmp
    # Correct deployed bytes flip the byte-drift problem off.
    source = mod.ROOT / data["source_script"]
    (agency / "scripts" / data["runtime_script"]).write_bytes(source.read_bytes())
    correct = mod.inspect(data, agency, gateway_profile="agency")
    assert correct["runtime_script_bytes_match"] is True
    assert not any("bytes drifted" in p for p in correct["problems"])


def test_inspect_requires_exactly_one_enabled_job_across_all_stores(tmp_path, monkeypatch):
    data = manifest()
    agency = _store_with_job(tmp_path, monkeypatch, data)
    source = mod.ROOT / data["source_script"]
    (agency / "scripts" / data["runtime_script"]).write_bytes(source.read_bytes())
    other_store = mod.DEFAULT_HERMES / "profiles" / "legacy"
    (other_store / "cron").mkdir(parents=True)
    duplicate = {"id": "dup", "name": data["name"], "enabled": True}
    (other_store / "cron/jobs.json").write_text(json.dumps({"jobs": [duplicate]}))
    monkeypatch.setattr(mod, "host_timezone_name", lambda: data["timezone"])
    monkeypatch.setattr(mod, "runtime_worktree_status",
                        lambda m: {"path": m["workdir"], "ok": True, "problems": []})
    monkeypatch.setattr(mod, "read_runtime_authority_status",
                        lambda m: {"pointer": "p", "ok": True, "problems": []})
    drifted = mod.inspect(data, agency, gateway_profile="agency")
    assert drifted["status"] == "drift"
    assert drifted["enabled_jobs_total"] == 2
    assert any("exactly one allowed" in p for p in drifted["problems"])
    assert any(d["job_id"] == "dup" for d in drifted["duplicates"])
    (other_store / "cron/jobs.json").write_text(json.dumps(
        {"jobs": [dict(duplicate, enabled=False)]}))
    converged = mod.inspect(data, agency, gateway_profile="agency")
    assert converged["enabled_jobs_total"] == 1
    assert converged["duplicates"] == []
    assert converged["status"] == "ok"


def test_inspect_flags_wrong_profile_store(tmp_path, monkeypatch):
    data = manifest()
    agency = _store_with_job(tmp_path, monkeypatch, data)
    monkeypatch.setattr(mod, "host_timezone_name", lambda: data["timezone"])
    monkeypatch.setattr(mod, "runtime_worktree_status",
                        lambda m: {"path": m["workdir"], "ok": True, "problems": []})
    monkeypatch.setattr(mod, "read_runtime_authority_status",
                        lambda m: {"pointer": "p", "ok": True, "problems": []})
    flagged = mod.inspect(data, agency, gateway_profile="default")
    assert flagged["status"] == "drift"
    assert any("running gateway profile" in p for p in flagged["problems"])
