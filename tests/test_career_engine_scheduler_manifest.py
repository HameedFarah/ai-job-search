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
    # The scan executes from a dedicated clean runtime worktree; the mutable
    # developer checkout is never the cron execution source.
    assert manifest["workdir"] == "/home/hameedo/projects/ai-job-search-daily-runtime"
    assert manifest["workdir"] != "/home/hameedo/projects/ai-job-search"
    # Runtime authority binding: ignored pointer inside the runtime worktree
    # binds every entry point to the canonical live tracker base.
    assert manifest["runtime_authority_pointer"] == "runtime/runtime-authority.json"
    assert manifest["tracker_authority_base"] == (
        "/home/hameedo/projects/ai-job-search/projects/job-automation"
    )
    assert (ROOT / manifest["source_script"]).is_file()
    assert manifest["runtime_script"] == "career-engine-daily-context.py"
    # Source scanners are invoked explicitly by the runtime script. Do not
    # preload their ambiguous canonical names: Hermes may resolve duplicate
    # names across repository/global external_dirs and skip both scanners.
    assert "linkedin-search" not in manifest["skills"]
    assert "freehire-search" not in manifest["skills"]
    assert "linkedin-search" not in manifest["skill_sources"]
    assert "freehire-search" not in manifest["skill_sources"]
    assert "repository-owned source CLI" in manifest["prompt"]
    assert "model" not in manifest and "provider" not in manifest
    assert "runtime_job_id" not in manifest
    assert "all eligible jobs" in manifest["prompt"]
    assert "unsent email drafts or portal packages" in manifest["prompt"]
    assert "Never send/contact/submit." in manifest["prompt"]
