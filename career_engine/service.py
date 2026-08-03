from __future__ import annotations

from pathlib import Path
from typing import Any

from .bundle import bundle_status, load_bundle
from .generation import validate_generated_application
from .pipeline import import_generated, prepare, status


def get_bundle_info(*, root: Path | None = None) -> dict[str, Any]:
    bundle = load_bundle(root)
    return {
        "schema_version": bundle["schema_version"],
        "engine_version": bundle["engine_version"],
        "bundle_hash": bundle["bundle_hash"],
        "source_hash": bundle["source_hash"],
        "sources": bundle["sources"],
        "status": bundle_status(root),
    }


def prepare_job(request: dict[str, Any], *, root: Path | None = None, actor: str = "system") -> dict[str, Any]:
    """Stable JSON service boundary used by ChatGPT, Hermes, scanners and future APIs."""
    return prepare(request, root=root, actor=actor, force_weak=bool(request.get("force_weak", False)))


def get_job_status(job_id: str, *, root: Path | None = None) -> dict[str, Any]:
    return status(job_id, root=root)


def validate_application(application: dict[str, Any], packet: dict[str, Any], *, root: Path | None = None) -> dict[str, Any]:
    bundle = load_bundle(root)
    findings = validate_generated_application(application, packet, bundle)
    return {
        "valid": not any(item.get("severity") == "error" for item in findings),
        "findings": findings,
        "bundle_hash": bundle["bundle_hash"],
    }


def import_application(job_id: str, file_path: str, *, root: Path | None = None, actor: str = "system") -> dict[str, Any]:
    return import_generated(job_id, Path(file_path), root=root, actor=actor)
