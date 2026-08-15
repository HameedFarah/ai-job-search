from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "projects/job-automation/config/runtime-bundle.v1.json"
CONSULTANTS = ROOT / "projects/job-automation/config/consultants-bookmarks.v1.json"


def stable_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


bundle = json.loads(BUNDLE.read_text(encoding="utf-8"))
data = CONSULTANTS.read_bytes()
found = False
for item in bundle["sources"]:
    if item["path"] == "projects/job-automation/config/consultants-bookmarks.v1.json":
        item["sha256"] = sha256_bytes(data)
        item["size"] = len(data)
        item["modified_ns"] = CONSULTANTS.stat().st_mtime_ns
        found = True
        break
if not found:
    raise SystemExit("consultants source record not found in runtime bundle")

bundle["source_hash"] = sha256_bytes(stable_json([
    {"path": item["path"], "sha256": item["sha256"]}
    for item in bundle["sources"]
]))
bundle["built_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
bundle.pop("bundle_hash", None)
bundle.pop("cache_reused", None)
bundle["bundle_hash"] = sha256_bytes(stable_json(bundle))
bundle["cache_reused"] = False
BUNDLE.write_text(json.dumps(bundle, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({
    "source_hash": bundle["source_hash"],
    "bundle_hash": bundle["bundle_hash"],
    "consultants_sha256": sha256_bytes(data),
    "consultants_size": len(data),
}, indent=2))
