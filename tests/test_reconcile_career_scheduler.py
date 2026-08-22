import importlib.util
import json
from pathlib import Path


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
