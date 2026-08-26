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
    tracker_source_path: Path
    runtime_bundle_path: Path
    vault_root: Path
    ats_template_path: Path


# Machine-generated pointer (written by tools/reconcile_career_scheduler.py) that
# binds a clean runtime worktree to the canonical live CareerTracker state
# directory. Git-ignored; read centrally by load_config so every Career Engine
# entry point launched from the dedicated runtime binds to the one mutable live
# tracker without depending on an exported environment variable.
RUNTIME_AUTHORITY_POINTER = "runtime/runtime-authority.json"


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


def _pointer_tracker_base(root: Path) -> Path | None:
    """Resolve the runtime authority pointer, failing closed on any defect.

    Returns None when no pointer is deployed (normal developer checkout).
    A malformed, unsupported, empty or missing-target pointer is an error, never
    a silent fallback onto an empty second tracker.
    """
    pointer = root / RUNTIME_AUTHORITY_POINTER
    if not pointer.is_file():
        return None
    try:
        payload = json.loads(pointer.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid runtime authority pointer {pointer}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError(f"Unsupported runtime authority pointer schema in {pointer}")
    base = str(payload.get("tracker_base", "")).strip()
    if not base:
        raise ValueError(f"Runtime authority pointer has an empty tracker_base: {pointer}")
    resolved = Path(base).expanduser().resolve()
    if not resolved.is_dir():
        raise ValueError(f"Runtime authority pointer target does not exist: {resolved}")
    return resolved


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
    # and config but not the ignored runtime authority. Binding resolution is:
    # explicit CAREER_ENGINE_TRACKER_BASE override, then the machine-generated
    # runtime authority pointer, then the checkout-local tracker base.
    tracker_override = os.environ.get("CAREER_ENGINE_TRACKER_BASE", "").strip()
    if tracker_override:
        tracker_base = Path(tracker_override).expanduser().resolve()
    else:
        pointer_base = _pointer_tracker_base(root)
        tracker_base = pointer_base if pointer_base is not None else root / config["tracker_base"]
    source_tracker_dir = (root / config["tracker_base"]).resolve()
    paths = Paths(
        repo_root=root,
        config_path=config_path,
        taxonomy_path=config_dir / "requirements-taxonomy.v1.json",
        evidence_path=config_dir / "evidence-index.v1.json",
        generated_schema_path=config_dir / "generated_application.schema.json",
        runtime_schema_path=config_dir / "runtime-bundle.schema.json",
        tracker_base=tracker_base,
        tracker_source_path=source_tracker_dir,
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
        # The tracker IMPLEMENTATION must come from the executing repository's
        # clean tracked source; state lives separately at paths.tracker_base.
        paths.tracker_source_path / "tracker.py",
    ):
        if not path.is_file():
            missing.append(str(path))
    if require_vault:
        for relative in config["vault"]["sources"]:
            path = paths.vault_root / relative
            if not path.is_file():
                missing.append(str(path))
    return missing
