from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import uuid
from pathlib import Path
from typing import Any

from .bundle import load_bundle
from .config import load_config
from .core import decide_route, match_evidence, normalize_job, score_fit, validate_live_status
from .cover_letter import render_cover_letter_and_verify
from .generation import create_generation_packet, export_packet, validate_generated_application
from .renderer import render_and_verify, render_ats_and_verify
from .safety import reject_fixture_payload


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


def _revision_root(artifact_dir: Path) -> Path:
    return artifact_dir / "revisions"


def _snapshot_current_package(artifact_dir: Path, record: dict[str, Any]) -> dict[str, Any] | None:
    """Copy the current validated package before accepting a new revision."""
    application = artifact_dir / "generated_application.json"
    if not application.is_file():
        return None
    revision_id = f"{stable_hash({'application': application.read_bytes().hex(), 'nonce': uuid.uuid4().hex})[:16]}"
    destination = _revision_root(artifact_dir) / revision_id
    destination.mkdir(parents=True, exist_ok=False)
    files: list[dict[str, str]] = []
    candidates = {"generated_application.json", "generation_packet.json", "pipeline_state.json"}
    for item in record.get("generated_artifacts", []):
        path = Path(str(item.get("path", "")))
        if path.is_file() and path.is_relative_to(artifact_dir):
            candidates.add(str(path.relative_to(artifact_dir)))
    submission = record.get("submission_package") or {}
    for value in submission.values():
        if isinstance(value, str):
            path = Path(value)
            if path.is_file() and path.is_relative_to(artifact_dir):
                candidates.add(str(path.relative_to(artifact_dir)))
    for relative in sorted(candidates):
        source = artifact_dir / relative
        if not source.is_file():
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        files.append({"path": relative, "sha256": hashlib.sha256(source.read_bytes()).hexdigest()})
    manifest = {"revision_id": revision_id, "files": files}
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return {"revision_id": revision_id, "path": str(destination), "files": files}


def _restore_revision(artifact_dir: Path, revision_id: str | None) -> dict[str, Any] | None:
    if not revision_id:
        return None
    source = _revision_root(artifact_dir) / revision_id
    if not source.is_dir():
        return None
    manifest_path = source / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {"files": []}
    for item in manifest.get("files", []):
        relative = str(item["path"])
        origin = source / relative
        target = artifact_dir / relative
        if origin.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(origin, target)
    return {"revision_id": source.name, "path": str(source), "files": manifest.get("files", [])}


def prepare(payload: dict[str, Any], *, root: Path | None = None, actor: str = "chatgpt", force_weak: bool = False) -> dict[str, Any]:
    config, paths = load_config(root)
    production_root = Path(__file__).resolve().parents[1]
    if paths.repo_root.resolve() == production_root.resolve():
        reject_fixture_payload(payload)
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
        "posting_date": normalized["posting_date"],
        "posting_date_precision": normalized["posting_date_precision"],
        "posting_date_source": normalized["posting_date_source"],
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
        stable_hash({"jd": normalized["jd_hash"], "bundle": bundle["bundle_hash"], "stage": "match", "algorithm": 2}),
    )
    outputs["requirement_matrix"] = str(path)
    reused["requirement_matrix"] = cache

    score = score_fit(normalized, matches, bundle)
    path, cache = _write_stage(
        artifact_dir, "fit_score", score,
        stable_hash({"matches": matches, "bundle": bundle["bundle_hash"], "stage": "score", "algorithm": 2}),
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
    warnings: list[str] = []
    live_status = normalized.get("live_status", "unverified")
    if live_status == "closed":
        blockers.append("vacancy_closed")
    elif live_status != "live":
        warnings.append(f"live_status_unverified:{live_status}")
    live_errors = [item["message"] for item in validate_live_status(normalized)]
    if live_errors:
        warnings.append("invalid_live_metadata:" + "; ".join(live_errors))
    # Threshold 70 (high_priority) is the credible-generation threshold.
    # Verification affects confidence and later external-action checks, but it
    # is not required to score or prepare an application package. Roles known
    # to be closed remain blocked. Credible (65-69) and selective (50-64)
    # roles remain trackable unless the owner explicitly forces a package.
    threshold = config["scoring"]["thresholds"]["high_priority"]
    if score["total"] < threshold and not force_weak:
        blockers.append(f"below_generation_threshold:{score['total']}")
    if route["route"] == "unresolved":
        route_message = "route_unresolved:" + route.get("blocker", "")
        if normalized.get("source") == "owner_dashboard":
            # Owner-pasted JDs are allowed to produce an internal review package
            # before an employer route is known. The unresolved route remains a
            # warning and the rendered package still carries
            # external_action_allowed=False; nothing can be sent/submitted from
            # this preparation-only path.
            warnings.append("preparation_only_" + route_message)
        else:
            blockers.append(route_message)

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
                "warnings": warnings,
            },
            "generated_artifacts": [
                {"type": name, "path": path_value, "bundle_hash": bundle["bundle_hash"]}
                for name, path_value in outputs.items()
            ],
            "next_action": (
                "Generate one structured application draft; retain verification warning for owner review"
                if not blockers and warnings
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
        "warnings": warnings,
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
    tracker = _load_tracker(paths)
    record = tracker.get_job(job_id)
    revision = None
    if not errors:
        revision = _snapshot_current_package(artifact_dir, record)
        destination.write_text(json.dumps(application, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    processing_state = dict(record.get("processing_state") or {})
    if revision:
        processing_state["pending_revision_id"] = revision["revision_id"]
    tracker.update_job(
        job_id,
        {
            "processing_status": "generated_content_valid" if not errors else "generated_content_rejected",
            "next_action": "Render approved template" if not errors else "Correct generated content against validation findings",
            "processing_state": processing_state,
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
    ats_result = (
        render_ats_and_verify(job_id, application, packet, root=root)
        if result.get("valid")
        else {"valid": False, "blocker": "sidebar_render_failed"}
    )
    cover_letter_result = (
        render_cover_letter_and_verify(job_id, application, packet, root=root)
        if result.get("valid") and ats_result.get("valid")
        else {"valid": False, "blocker": "cv_render_failed"}
    )
    result["sidebar_valid"] = bool(result.get("valid"))
    result["ats"] = ats_result
    result["cover_letter"] = cover_letter_result
    result["valid"] = bool(
        result.get("sidebar_valid")
        and ats_result.get("valid")
        and cover_letter_result.get("valid")
    )
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
                "variant": "modern-executive-sidebar",
                "path": docx["docx"],
                "sha256": docx.get("sha256", ""),
                "bundle_hash": packet.get("bundle_hash", ""),
            })
        if verification.get("pdf"):
            final_artifacts.append({
                "type": "final_pdf",
                "variant": "modern-executive-sidebar",
                "path": verification["pdf"],
                "sha256": verification.get("sha256", ""),
                "bundle_hash": packet.get("bundle_hash", ""),
            })
        ats_docx = ats_result.get("docx", {})
        ats_verification = ats_result.get("verification", {})
        if ats_docx.get("docx"):
            final_artifacts.append({
                "type": "ats_docx",
                "variant": "ats-linear",
                "path": ats_docx["docx"],
                "sha256": ats_docx.get("sha256", ""),
                "bundle_hash": packet.get("bundle_hash", ""),
            })
        if ats_verification.get("pdf"):
            final_artifacts.append({
                "type": "ats_pdf",
                "variant": "ats-linear",
                "path": ats_verification["pdf"],
                "sha256": ats_verification.get("sha256", ""),
                "bundle_hash": packet.get("bundle_hash", ""),
            })
        cover_docx = cover_letter_result.get("docx", {})
        cover_verification = cover_letter_result.get("verification", {})
        if cover_docx.get("docx"):
            final_artifacts.append({
                "type": "cover_letter_docx",
                "path": cover_docx["docx"],
                "sha256": cover_docx.get("sha256", ""),
                "bundle_hash": packet.get("bundle_hash", ""),
            })
        if cover_verification.get("pdf"):
            final_artifacts.append({
                "type": "cover_letter_pdf",
                "path": cover_verification["pdf"],
                "sha256": cover_verification.get("sha256", ""),
                "bundle_hash": packet.get("bundle_hash", ""),
            })
        processing_state = dict(record.get("processing_state") or {})
        route_name = str(packet.get("application_route", {}).get("route", "unresolved"))
        default_variant = str(
            packet.get("email_draft_policy", {}).get(
                "default_resume_variant",
                "modern-executive-sidebar" if route_name == "email" else "ats-linear",
            )
        )
        selected_variant = str(
            processing_state.get("selected_resume_variant")
            or record.get("resume_template_override")
            or default_variant
        )
        if selected_variant not in {"modern-executive-sidebar", "ats-linear"}:
            selected_variant = default_variant
        selected_pdf_type = "final_pdf" if selected_variant == "modern-executive-sidebar" else "ats_pdf"
        selected_docx_type = "final_docx" if selected_variant == "modern-executive-sidebar" else "ats_docx"
        selected_pdf = next((item for item in final_artifacts if item.get("type") == selected_pdf_type), {})
        selected_docx = next((item for item in final_artifacts if item.get("type") == selected_docx_type), {})
        cover_pdf = next((item for item in final_artifacts if item.get("type") == "cover_letter_pdf"), {})
        cover_docx_artifact = next((item for item in final_artifacts if item.get("type") == "cover_letter_docx"), {})
        submission_package = {
            "route": route_name,
            "default_resume_variant": default_variant,
            "selected_resume_variant": selected_variant,
            "owner_override": selected_variant != default_variant,
            "attachment_count": 1 if route_name == "email" else 0,
            "selected_cv_pdf": selected_pdf.get("path", ""),
            "selected_cv_pdf_sha256": selected_pdf.get("sha256", ""),
            "selected_cv_docx": selected_docx.get("path", ""),
            "selected_cv_docx_sha256": selected_docx.get("sha256", ""),
            "cover_letter_pdf": cover_pdf.get("path", ""),
            "cover_letter_pdf_sha256": cover_pdf.get("sha256", ""),
            "cover_letter_docx": cover_docx_artifact.get("path", ""),
            "cover_letter_docx_sha256": cover_docx_artifact.get("sha256", ""),
            "email_account": packet.get("email_draft_policy", {}).get("account", "hameedo@gmail.com"),
            "email_sender": packet.get("email_draft_policy", {}).get("sender", "hameedfarah@gmail.com"),
            "email_subject": packet.get("email_draft_policy", {}).get("expected_subject", ""),
        }
        result["submission_package"] = submission_package
        processing_state.update({
            "owner": "owner",
            "status": "awaiting_owner_approval",
            "bundle_hash": packet.get("bundle_hash", ""),
            "external_action_allowed": False,
            "selected_resume_variant": selected_variant,
            "submission_package": submission_package,
        })
        tracker.update_job(
            job_id,
            {
                "owner": "owner",
                "processing_status": "awaiting_owner_approval",
                "next_action": "Owner reviews the selected CV and generated cover letter, then explicitly approves any portal submission or email action",
                "processing_state": processing_state,
                "submission_package": submission_package,
                "generated_artifacts": existing_artifacts + final_artifacts,
            },
            comment="Rendered and verified both CV variants plus the cover letter, selected exactly one route-specific CV for submission, and kept external action blocked pending explicit owner approval",
            actor=actor,
            action="drafted",
            source_refs=[item["path"] for item in final_artifacts],
            requires_owner_review=True,
        )
        pending_revision_id = str((record.get("processing_state") or {}).get("pending_revision_id", ""))
        if pending_revision_id:
            tracker.record_event(actor=actor, entity_type="application", entity_id=job_id,
                action="generated", before={"revision_id": pending_revision_id},
                after={"status": "validated_and_rendered"},
                comment="Accepted validated application revision and preserved prior package",
                confidence="high", requires_owner_review=True)
            processing_state["pending_revision_id"] = ""
            tracker.update_job(job_id, {"processing_state": processing_state}, comment="Cleared accepted revision association", actor=actor)
    else:
        pending_revision_id = str((record.get("processing_state") or {}).get("pending_revision_id", ""))
        restored = _restore_revision(artifact_dir, pending_revision_id)
        processing_state = dict(record.get("processing_state") or {})
        processing_state.update({"status": "render_rejected", "external_action_allowed": False, "pending_revision_id": ""})
        tracker.update_job(
            job_id,
            {
                "processing_status": "render_rejected",
                "next_action": "Correct CV or cover-letter rendering/validation findings before owner review",
                "processing_state": processing_state,
            },
            comment="Rejected rendered application package because deterministic CV, cover-letter, PDF or layout validation failed",
            actor=actor,
            action="rejected",
            requires_owner_review=True,
        )
        result["restored_revision"] = restored
    return result
