import json
import os
import subprocess
import sys
from pathlib import Path

from career_engine.rega_enrichment.providers import recruitment_contact, result

ROOT = Path(__file__).parents[1]

def test_provider_result_requires_evidence_and_provenance():
    assert not result("tomba", "candidate", value="x@y.com", source_url="https://x").usable
    item = recruitment_contact("tomba", "x@y.com", "https://x/careers", "Recruitment page", official=True, cost_status="trial")
    assert item.usable and item.provider == "tomba" and item.retrieved_at and item.cost_status == "trial"

def test_generic_contact_cannot_be_promoted():
    item = recruitment_contact("outscraper", "info@example.com", "https://example.com", "", official=False)
    assert item.status == "candidate" and not item.usable and item.evidence == ""

def test_probe_output_is_value_free_and_missing_credentials_do_not_call_network():
    env = {k: v for k, v in os.environ.items() if not k.endswith("_API_KEY") and k != "APIFY_USER_ID"}
    proc = subprocess.run([sys.executable, str(ROOT / "runtime/provider_probe.py")], cwd=ROOT, env=env, capture_output=True, text=True, timeout=20)
    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert all(v is False for v in payload["credential_presence"].values())
    assert payload["probes"] == []
    assert proc.stderr == ""

def test_dataforseo_header_does_not_leak_material():
    from runtime.provider_probe import _dataforseo_headers
    header = _dataforseo_headers("login:password")
    assert header["Authorization"].startswith("Basic ")
    assert "login" not in header["Authorization"] and "password" not in header["Authorization"]
