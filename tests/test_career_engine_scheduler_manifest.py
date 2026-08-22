import json
from pathlib import Path


ROOT = Path(__file__).parents[1]
MANIFEST = ROOT / "projects/job-automation/config/career-engine-scheduler.v1.json"


def test_career_scheduler_manifest_is_reproducible_desired_state():
    manifest = json.loads(MANIFEST.read_text())
    assert manifest["schema_version"] == 1
    assert manifest["authority_key"] == "career-engine.hermes.scheduler.v1"
    assert manifest["name"] == "Career Engine Daily Scan"
    assert manifest["schedule"] == "0 9 * * *"
    assert manifest["timezone"] == "Asia/Riyadh"
    assert manifest["no_agent"] is False
    assert manifest["deliver"] == "local"
    assert manifest["workdir"] == "/home/hameedo/projects/ai-job-search"
    assert (ROOT / manifest["source_script"]).is_file()
    assert manifest["runtime_script"] == "career-engine-daily-context.py"
    assert "model" not in manifest and "provider" not in manifest
    assert "runtime_job_id" not in manifest
    assert "Never send/contact/submit." in manifest["prompt"]
