from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class Paths:
    repo_root: Path
    config_path: Path
    taxonomy_path: Path
    evidence_path: Path
    generated_schema_path: Path
    runtime_schema_path: Path
    tracker_base: Path
    runtime_bundle_path: Path
    vault_root: Path
    ats_template_path: Path


def repo_root() -> Path:
    override = os.environ.get("CAREER_ENGINE_REPO_ROOT", "").strip()
    return Path(override).expanduser().resolve() if override else Path(__file__).resolve().parents[1]


def load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"Required Career Engine file is missing: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path}: {exc}") from exc


def load_config(root: Path | None = None) -> tuple[dict[str, Any], Paths]:
    root = (root or repo_root()).resolve()
    config_dir = root / "projects/job-automation/config"
    config_path = config_dir / "career-engine.v1.json"
    config = load_json(config_path)
    if config.get("schema_version") != 1:
        raise ValueError("Unsupported Career Engine config schema")
    vault_override = os.environ.get("CAREER_ENGINE_VAULT_ROOT", "").strip()
    vault_root = Path(vault_override or config["vault"]["root"]).expanduser().resolve()
    # The tracker base holds the live CareerTracker data and generated artifact
    # authority. Clean source worktrees (git worktrees) carry the tracked code
    # and config but not the ignored runtime authority, so an operator can point
    # this run at the live tracker base without copying runtime data into Git.
    tracker_override = os.environ.get("CAREER_ENGINE_TRACKER_BASE", "").strip()
    tracker_base = (
        Path(tracker_override).expanduser().resolve()
        if tracker_override
        else root / config["tracker_base"]
    )
    paths = Paths(
        repo_root=root,
        config_path=config_path,
        taxonomy_path=config_dir / "requirements-taxonomy.v1.json",
        evidence_path=config_dir / "evidence-index.v1.json",
        generated_schema_path=config_dir / "generated_application.schema.json",
        runtime_schema_path=config_dir / "runtime-bundle.schema.json",
        tracker_base=tracker_base,
        runtime_bundle_path=root / config["runtime_bundle"],
        vault_root=vault_root,
        ats_template_path=config_dir / "ats-linear-template.v1.json",
    )
    return config, paths


def validate_required_files(config: dict[str, Any], paths: Paths, *, require_vault: bool = True) -> list[str]:
    missing: list[str] = []
    for path in (
        paths.config_path,
        paths.taxonomy_path,
        paths.evidence_path,
        paths.generated_schema_path,
        paths.runtime_schema_path,
        paths.generated_schema_path.parent / "hermes-review-diff.schema.json",
        paths.tracker_base / "tracker.py",
    ):
        if not path.is_file():
            missing.append(str(path))
    if require_vault:
        for relative in config["vault"]["sources"]:
            path = paths.vault_root / relative
            if not path.is_file():
                missing.append(str(path))
    return missing
