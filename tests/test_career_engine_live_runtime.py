from pathlib import Path

import pytest

from career_engine.bundle import build_bundle, bundle_status
from career_engine.cli import doctor


VAULT_PROFILE = Path("/home/hameedo/obsidian/HermesOpsVault/projects/job-automation/career-engine-profile.v1.json")


@pytest.mark.skipif(not VAULT_PROFILE.is_file(), reason="Live Career Engine Vault is unavailable")
def test_live_runtime_bundle_builds_from_canonical_vault() -> None:
    bundle = build_bundle()
    assert bundle["schema_version"] == 1
    assert bundle["bundle_hash"]
    assert len(bundle["claims"]) >= 15
    assert bundle_status()["current"] is True
    result = doctor()
    assert result["valid"] is True
