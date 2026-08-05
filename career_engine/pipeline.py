from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any

from .bundle import load_bundle
from .config import load_config
from .core import decide_route, match_evidence, normalize_job, score_fit, validate_live_status
from .generation import create_generation_packet, export_packet, validate_generated_application
from .renderer import render_and_verify


def stable_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _load_tracker(paths: Any) -> Any:
    tracker_path = paths.tracker_base / "tracker.py"
    spec = importlib.util.spec_from_file_location("career_engine_tracker", tracker_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load tracker: {tracker_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.CareerTracker(paths.tracker_base)


def _write_stage(artifact_dir: Path, name: str, value: Any, input_hash: str) -> tuple[Path, bool]:
    path = artifact_dir / f"{name}.json"
    wrapper = {"input_hash": input_hash, "data": value}
    if path.is_file():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing.get("input_hash") == input_hash:
            return path, True
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(wrapper, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)
    return path, False


def read_stage(artifact_dir: Path, name: str) -> Any:
    path = artifact_dir / f"{name}.json"
    if not path.is_file():
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))["data"]


def prepare(payload: dict[str, Any], *, root: Path | None = None, actor: str = "chatgpt", force_weak: bool = False) -> dict[str, Any]:
    config, paths = load_config(root)
    bundle = load_bundle(root)
    normalized = normalize_job(payload, bundle["taxonomy"])
    tracker = _load_tracker(paths)
    ingest_payload = {
        "source": normalized["source"],
        "external_job_id": normalized["reference"],
        "source_url": normalized["source_url"],
        "company": normalized["company"],
        "role": normalized["role"],
        "location": normalized["location"],
        "full_job_description": normalized["full_job_description"],
        "processing_status": "normalizing",
        "next_action": "Run centralized Career Engine matching and scoring",
    }
    ingest = tracker.ingest(
        ingest_payload,
        comment="Central Career Engine ingest and deduplication",
        actor=actor,
        source_refs=[item for item in (normalized["source_url"],) if item],
    )
    job_id = ingest["job_id"]
    artifact_dir = paths.tracker_base / "artifacts" / job_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    outputs: dict[str, str] = {}
    reused: dict[str, bool] = {}

    path, cache = _write_stage(artifact_dir, "normalized_job", normalized, stable_hash({
        "jd": normalized["jd_hash"],
        "live_status": normalized["live_status"],
        "live_verified_at": normalized["live_verified_at"],
        "live_verification_source": normalized["live_verification_source"],
    }))
    outputs["normalized_job"] = str(path)
    reused["normalized_job"] = cache

    matches = match_evidence(normalized, bundle)
    path, cache = _write_stage(
        artifact_dir, "requirement_matrix", matches,
        stable_hash({"jd": normalized["jd_hash"], "bundle": bundle["bundle_hash"], "stage": "match"}),
    )
    outputs["requirement_matrix"] = str(path)
    reused["requirement_matrix"] = cache

    score = score_fit(normalized, matches, bundle)
    path, cache = _write_stage(
        artifact_dir, "fit_score", score,
        stable_hash({"matches": matches, "bundle": bundle["bundle_hash"], "stage": "score"}),
    )
    outputs["fit_score"] = str(path)
    reused["fit_score"] = cache

    route = decide_route(normalized, bundle)
    path, cache = _write_stage(
        artifact_dir, "route", route,
        stable_hash({
            "live_status": normalized.get("live_status"),
            "live_verified_at": normalized.get("live_verified_at"),
            "live_verification_source": normalized.get("live_verification_source"),
            "recipient": normalized.get("recipient"),
            "recipient_source": normalized.get("recipient_source"),
            "url": normalized.get("application_url"),
        }),
    )
    outputs["route"] = str(path)
    reused["route"] = cache

    blockers: list[str] = []
    live_status = normalized.get("live_status", "unverified")
    if live_status != "live":
        blockers.append(f"not_live:{live_status}")
    live_errors = [item["message"] for item in validate_live_status(normalized)]
    if live_errors:
        blockers.append("invalid_live_metadata:" + "; ".join(live_errors))
    # Threshold 80 (high_priority) is the credible-generation threshold: a
    # role only becomes generation-eligible when it scores 80+ AND is
    # live-verified. Credible (65-79) and selective (50-64) roles remain
    # trackable but do not receive generation packets unless the owner
    # explicitly forces a package (force_weak / owner override).
    threshold = config["scoring"]["thresholds"]["high_priority"]
    if score["total"] < threshold and not force_weak:
        blockers.append(f"below_generation_threshold:{score['total']}")
    if route["route"] == "unresolved":
        blockers.append("route_unresolved:" + route.get("blocker", ""))

    status = "blocked" if blockers else "generation_ready"
    if not blockers:
        packet = create_generation_packet(
            job_id=job_id, normalized_job=normalized, matches=matches,
            score=score, route=route, bundle=bundle,
        )
        packet_path = artifact_dir / "generation_packet.json"
        packet_hash = stable_hash({"packet": packet, "bundle": bundle["bundle_hash"]})
        wrapper_path, packet_cached = _write_stage(artifact_dir, "generation_packet.stage", packet, packet_hash)
        export_packet(packet, packet_path)
        outputs["generation_packet"] = str(packet_path)
        outputs["generation_packet_stage"] = str(wrapper_path)
        reused["generation_packet"] = packet_cached
    else:
        stale_packet = artifact_dir / "generation_packet.json"
        if stale_packet.exists():
            stale_packet.unlink()
        reused["generation_packet"] = False
    live_gate_blocked = any(
        item.startswith("not_live:") or item.startswith("invalid_live_metadata:")
        for item in blockers
    )
    tracker.update_job(
        job_id,
        {
            "fit_score": score["total"],
            "priority": score["recommendation"],
            "processing_status": status,
            "normalized_requirements": normalized["requirements"],
            "scoring": score,
            "evidence_matches": matches,
            "processing_state": {
                "owner": actor,
                "status": status,
                "bundle_hash": bundle["bundle_hash"],
                "live_status": live_status,
                "route": route,
                "blockers": blockers,
            },
            "generated_artifacts": [
                {"type": name, "path": path_value, "bundle_hash": bundle["bundle_hash"]}
                for name, path_value in outputs.items()
            ],
            "next_action": (
                "Owner confirms the vacancy is live (verification source and timestamp) before generation"
                if live_gate_blocked
                else ("Generate one structured application draft" if not blockers else "Resolve blockers before generation")
            ),
        },
        comment="Prepared centralized evidence-grounded Career Engine packet",
        actor=actor,
        requires_owner_review=bool(blockers),
    )
    state = {
        "schema_version": 1,
        "job_id": job_id,
        "bundle_hash": bundle["bundle_hash"],
        "stage": status,
        "live_status": live_status,
        "fit_score": score,
        "route": route,
        "blockers": blockers,
        "outputs": outputs,
        "cache_reused": reused,
    }
    _write_stage(artifact_dir, "pipeline_state", state, stable_hash(state))
    return state


def status(job_id: str, *, root: Path | None = None) -> dict[str, Any]:
    _, paths = load_config(root)
    tracker = _load_tracker(paths)
    record = tracker.get_job(job_id)
    artifact_dir = paths.tracker_base / "artifacts" / job_id
    state_path = artifact_dir / "pipeline_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))["data"] if state_path.is_file() else None
    return {"job": record["job"], "processing_state": record.get("processing_state"), "pipeline_state": state}


def import_generated(job_id: str, application_path: Path, *, root: Path | None = None, actor: str = "chatgpt") -> dict[str, Any]:
    bundle = load_bundle(root)
    _, paths = load_config(root)
    artifact_dir = paths.tracker_base / "artifacts" / job_id
    packet = json.loads((artifact_dir / "generation_packet.json").read_text(encoding="utf-8"))
    application = json.loads(application_path.read_text(encoding="utf-8"))
    findings = validate_generated_application(application, packet, bundle)
    errors = [item for item in findings if item.get("severity") == "error"]
    destination = artifact_dir / "generated_application.json"
    if not errors:
        destination.write_text(json.dumps(application, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tracker = _load_tracker(paths)
    tracker.update_job(
        job_id,
        {
            "processing_status": "generated_content_valid" if not errors else "generated_content_rejected",
            "next_action": "Render approved template" if not errors else "Correct generated content against validation findings",
        },
        comment="Validated structured LLM application output against the central bundle",
        actor=actor,
        action="generated" if not errors else "rejected",
        requires_owner_review=True,
    )
    return {"valid": not errors, "findings": findings, "saved_to": str(destination) if not errors else ""}


def finalize_render(job_id: str, *, root: Path | None = None, actor: str = "chatgpt") -> dict[str, Any]:
    """Render the validated application and move the tracker to owner review.

    A successful render never means permission to submit. The final state is
    deliberately ``awaiting_owner_approval`` and the tracker owner becomes the
    human owner, preserving the external-action gate for both portal and email
    routes.
    """
    _, paths = load_config(root)
    artifact_dir = paths.tracker_base / "artifacts" / job_id
    application_path = artifact_dir / "generated_application.json"
    packet_path = artifact_dir / "generation_packet.json"
    if not application_path.is_file() or not packet_path.is_file():
        return {"job_id": job_id, "valid": False, "blocker": "generated_application_missing"}

    application = json.loads(application_path.read_text(encoding="utf-8"))
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    result = render_and_verify(job_id, application, packet, root=root)
    tracker = _load_tracker(paths)
    record = tracker.get_job(job_id)
    existing_artifacts = list(record.get("generated_artifacts", []))
    final_artifacts: list[dict[str, Any]] = []
    if result.get("valid"):
        docx = result.get("docx", {})
        verification = result.get("verification", {})
        if docx.get("docx"):
            final_artifacts.append({
                "type": "final_docx",
                "path": docx["docx"],
                "sha256": docx.get("sha256", ""),
                "bundle_hash": packet.get("bundle_hash", ""),
            })
        if verification.get("pdf"):
            final_artifacts.append({
                "type": "final_pdf",
                "path": verification["pdf"],
                "sha256": verification.get("sha256", ""),
                "bundle_hash": packet.get("bundle_hash", ""),
            })
        processing_state = dict(record.get("processing_state") or {})
        processing_state.update({
            "owner": "owner",
            "status": "awaiting_owner_approval",
            "bundle_hash": packet.get("bundle_hash", ""),
            "external_action_allowed": False,
        })
        tracker.update_job(
            job_id,
            {
                "owner": "owner",
                "processing_status": "awaiting_owner_approval",
                "next_action": "Owner reviews the final CV and explicitly approves any portal submission or email action",
                "processing_state": processing_state,
                "generated_artifacts": existing_artifacts + final_artifacts,
            },
            comment="Rendered and verified the final two-page application package; external action remains blocked pending explicit owner approval",
            actor=actor,
            action="drafted",
            source_refs=[item["path"] for item in final_artifacts],
            requires_owner_review=True,
        )
    else:
        processing_state = dict(record.get("processing_state") or {})
        processing_state.update({"status": "render_rejected", "external_action_allowed": False})
        tracker.update_job(
            job_id,
            {
                "processing_status": "render_rejected",
                "next_action": "Correct rendering or PDF validation findings before owner review",
                "processing_state": processing_state,
            },
            comment="Rejected rendered application package because deterministic PDF or layout validation failed",
            actor=actor,
            action="rejected",
            requires_owner_review=True,
        )
    return result
