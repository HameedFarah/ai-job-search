from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Paths, load_config, load_json, validate_required_files


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def stable_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def source_record(path: Path, root: Path) -> dict[str, Any]:
    data = path.read_bytes()
    try:
        display = str(path.relative_to(root))
    except ValueError:
        display = str(path)
    return {
        "path": display,
        "sha256": sha256_bytes(data),
        "size": len(data),
        "modified_ns": path.stat().st_mtime_ns,
    }


def bundle_inputs(config: dict[str, Any], paths: Paths) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    missing = validate_required_files(config, paths, require_vault=True)
    if missing:
        raise FileNotFoundError("Missing Career Engine sources:\n" + "\n".join(missing))
    source_paths = [paths.config_path, paths.taxonomy_path, paths.generated_schema_path, paths.runtime_schema_path]
    source_paths.extend(paths.vault_root / relative for relative in config["vault"]["sources"])
    sources = [source_record(path, paths.repo_root if path.is_relative_to(paths.repo_root) else paths.vault_root) for path in source_paths]
    taxonomy = load_json(paths.taxonomy_path)
    profile_path = paths.vault_root / config["vault"]["profile_bundle"]
    profile = load_json(profile_path)
    return sources, taxonomy, profile


def calculate_source_hash(sources: list[dict[str, Any]]) -> str:
    return sha256_bytes(stable_json([{"path": item["path"], "sha256": item["sha256"]} for item in sources]))


def build_bundle(root: Path | None = None, *, force: bool = False) -> dict[str, Any]:
    config, paths = load_config(root)
    sources, taxonomy, profile = bundle_inputs(config, paths)
    source_hash = calculate_source_hash(sources)
    if not force and paths.runtime_bundle_path.is_file():
        current = load_json(paths.runtime_bundle_path)
        if current.get("source_hash") == source_hash:
            current["cache_reused"] = True
            return current
    writing_rules = [
        rule for rule in profile.get("writing_rules", [])
        if "hameedo@gmail.com" not in str(rule).lower()
    ]
    writing_rules.append(
        "Expose only hameedfarah@gmail.com in outward career material. Store Gmail drafts in hameedo@gmail.com, but use hameedfarah@gmail.com as the outward sender identity."
    )
    body = {
        "schema_version": 1,
        "engine_version": config["engine_version"],
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_hash": source_hash,
        "sources": sources,
        "config": config,
        "taxonomy": taxonomy,
        "identity": {**profile.get("identity", {}), **config.get("identity", {})},
        "career_chronology": profile.get("career_chronology", []),
        "claims": profile.get("claims", []),
        "writing_rules": writing_rules,
        "policy_overrides": profile.get("policy_overrides", {}),
    }
    body["bundle_hash"] = sha256_bytes(stable_json(body))
    body["cache_reused"] = False
    paths.runtime_bundle_path.parent.mkdir(parents=True, exist_ok=True)
    temp = paths.runtime_bundle_path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(paths.runtime_bundle_path)
    return body


def bundle_status(root: Path | None = None) -> dict[str, Any]:
    config, paths = load_config(root)
    missing = validate_required_files(config, paths, require_vault=True)
    if missing:
        return {"valid": False, "missing": missing, "current": False}
    sources, _, _ = bundle_inputs(config, paths)
    expected = calculate_source_hash(sources)
    if not paths.runtime_bundle_path.is_file():
        return {"valid": True, "current": False, "reason": "bundle_missing", "expected_source_hash": expected}
    bundle = load_json(paths.runtime_bundle_path)
    return {
        "valid": bundle.get("schema_version") == 1,
        "current": bundle.get("source_hash") == expected,
        "bundle_hash": bundle.get("bundle_hash", ""),
        "source_hash": bundle.get("source_hash", ""),
        "expected_source_hash": expected,
    }


def load_bundle(root: Path | None = None) -> dict[str, Any]:
    status = bundle_status(root)
    if not status.get("current"):
        return build_bundle(root)
    _, paths = load_config(root)
    return load_json(paths.runtime_bundle_path)
