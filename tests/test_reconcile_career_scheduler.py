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
    assert mod.main(["--check"]) == 1
    output = json.loads(capsys.readouterr().out)
    assert set(output) == {"active_profile", "target_store", "matching_job_id", "schedule", "duplicates", "status"}
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
    monkeypatch.setattr(mod, "provision_skills", lambda manifest, target: None)
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
    monkeypatch.setattr(mod, "provision_skills", lambda manifest, target: None)
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
