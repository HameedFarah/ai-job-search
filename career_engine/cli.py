from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .bundle import build_bundle, bundle_status, load_bundle
from .config import load_config, validate_required_files
from .generation import run_adapter
from .pipeline import finalize_render, import_generated, prepare, read_stage, status
from .renderer import build_render_input
from .scanner import run_scan, write_report
from .template import install_from_transfer, status as template_status, sync_copies

EXIT_READY = 0
EXIT_OWNER_INPUT = 10
EXIT_WEAK_FIT = 20
EXIT_POLICY = 30
EXIT_ROUTE = 40
EXIT_TEMPLATE = 50
EXIT_SYSTEM = 70


def emit(value: Any, *, human: bool = False) -> None:
    if human and isinstance(value, dict):
        for key in ("job_id", "stage", "bundle_hash", "valid", "current", "blockers"):
            if key in value:
                print(f"{key}: {value[key]}")
        if not any(key in value for key in ("job_id", "stage", "valid", "current")):
            print(json.dumps(value, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(value, ensure_ascii=False, indent=2))


def doctor(root: Path | None = None) -> dict[str, Any]:
    config, paths = load_config(root)
    missing = validate_required_files(config, paths, require_vault=True)
    bundle = bundle_status(root) if not missing else {"valid": False, "current": False}
    template = template_status(root=root)
    from .renderer import render_tooling
    tooling = render_tooling()
    return {
        "valid": not missing and bool(template.get("valid")),
        "missing": missing,
        "bundle": bundle,
        "template": {
            **template,
            "required_for_render": True,
        },
        "render_tooling": tooling,
        "tracker": {
            "base": str(paths.tracker_base),
            "present": (paths.tracker_base / "tracker.py").is_file(),
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="career-engine", description="Centralized evidence-grounded Career Engine")
    parser.add_argument("--human", action="store_true", help="Concise human-readable output")

    def add_common(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument(
            "--human", action="store_true", default=argparse.SUPPRESS,
            help="Concise human-readable output",
        )

    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor")
    add_common(doctor)

    bundle = sub.add_parser("bundle")
    add_common(bundle)
    bundle_sub = bundle.add_subparsers(dest="bundle_command", required=True)
    for name in ("build", "status", "validate"):
        subparser = bundle_sub.add_parser(name)
        add_common(subparser)

    template = sub.add_parser("template")
    add_common(template)
    template_sub = template.add_subparsers(dest="template_command", required=True)
    template_status_parser = template_sub.add_parser("status")
    add_common(template_status_parser)
    install = template_sub.add_parser("install")
    add_common(install)
    install.add_argument("--keep-parts", action="store_true")
    sync = template_sub.add_parser("sync")
    add_common(sync)

    prep = sub.add_parser("prepare")
    add_common(prep)
    prep.add_argument("--jd-file", required=True)
    prep.add_argument("--company", required=True)
    prep.add_argument("--role", required=True)
    prep.add_argument("--source", default="manual")
    prep.add_argument("--source-url", default="")
    prep.add_argument("--external-job-id", default="")
    prep.add_argument("--location", default="")
    prep.add_argument("--application-url", default="")
    prep.add_argument("--recipient", default="")
    prep.add_argument("--recipient-source", default="")
    prep.add_argument("--live-status", choices=("live", "closed", "unverified"), default="unverified")
    prep.add_argument("--live-verified-at", default="")
    prep.add_argument("--live-verification-source", default="")
    prep.add_argument("--actor", choices=("chatgpt", "hermes", "owner", "system"), default="chatgpt")
    prep.add_argument("--force-weak", action="store_true")

    stat = sub.add_parser("status")
    add_common(stat)
    stat.add_argument("--job-id", required=True)

    score = sub.add_parser("score")
    add_common(score)
    score.add_argument("--job-id", required=True)

    route = sub.add_parser("route")
    add_common(route)
    route.add_argument("--job-id", required=True)

    generation = sub.add_parser("generate")
    add_common(generation)
    generation_sub = generation.add_subparsers(dest="generation_command", required=True)
    export = generation_sub.add_parser("export")
    add_common(export)
    export.add_argument("--job-id", required=True)
    run = generation_sub.add_parser("run")
    add_common(run)
    run.add_argument("--job-id", required=True)
    run.add_argument("--adapter", choices=("manual", "opencode", "hermes"), default="manual")
    run.add_argument("--provider", default="")
    run.add_argument("--model", default="")
    imported = generation_sub.add_parser("import")
    add_common(imported)
    imported.add_argument("--job-id", required=True)
    imported.add_argument("--file", required=True)
    imported.add_argument("--actor", choices=("chatgpt", "hermes", "owner", "system"), default="chatgpt")

    validate = sub.add_parser("validate")
    add_common(validate)
    validate.add_argument("--job-id", required=True)

    render = sub.add_parser("render")
    add_common(render)
    render.add_argument("--job-id", required=True)

    package = sub.add_parser("package")
    add_common(package)
    package.add_argument("--job-id", required=True)

    scanner = sub.add_parser("scanner")
    add_common(scanner)
    scanner_sub = scanner.add_subparsers(dest="scanner_command", required=True)
    ingest = scanner_sub.add_parser("ingest")
    add_common(ingest)
    ingest.add_argument("--file", required=True)
    ingest.add_argument("--scanner-id", choices=("hermes_scanner", "chatgpt_scanner"), default="hermes_scanner")
    ingest.add_argument("--output", default="")
    return parser


def _artifact_dir(job_id: str) -> Path:
    _, paths = load_config()
    return paths.tracker_base / "artifacts" / job_id


def _generated_application(job_id: str) -> dict[str, Any]:
    generated = _artifact_dir(job_id) / "generated_application.json"
    if not generated.is_file():
        return {}
    return json.loads(generated.read_text(encoding="utf-8"))


def _generation_packet(job_id: str) -> dict[str, Any]:
    packet = _artifact_dir(job_id) / "generation_packet.json"
    if not packet.is_file():
        return {}
    return json.loads(packet.read_text(encoding="utf-8"))


def _cmd_score(job_id: str) -> dict[str, Any]:
    artifact_dir = _artifact_dir(job_id)
    if not (artifact_dir / "fit_score.json").is_file():
        return {"job_id": job_id, "reason": "job_not_prepared"}
    fit_score = read_stage(artifact_dir, "fit_score")
    return {"job_id": job_id, "fit_score": fit_score}


def _cmd_route(job_id: str) -> dict[str, Any]:
    artifact_dir = _artifact_dir(job_id)
    if not (artifact_dir / "route.json").is_file():
        return {"job_id": job_id, "reason": "job_not_prepared"}
    route = read_stage(artifact_dir, "route")
    return {"job_id": job_id, "route": route}


def _cmd_render(job_id: str) -> dict[str, Any]:
    return finalize_render(job_id)


def _cmd_package(job_id: str) -> dict[str, Any]:
    application = _generated_application(job_id)
    packet = _generation_packet(job_id)
    route = read_stage(_artifact_dir(job_id), "route")
    render_input = build_render_input(job_id, application, packet) if application and packet else {"path": "", "render_input": {}}
    return {
        "job_id": job_id,
        "route": route,
        "render_input": render_input["path"],
        "generated_application": str(_artifact_dir(job_id) / "generated_application.json"),
        "outward_filename": packet.get("outward_filename", "") if packet else "",
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "doctor":
            result = doctor()
            emit(result, human=args.human)
            if not result["valid"]:
                return EXIT_SYSTEM
            if not result["template"]["present"]:
                return EXIT_TEMPLATE
            return EXIT_READY
        if args.command == "bundle":
            if args.bundle_command == "build":
                result = build_bundle()
            elif args.bundle_command == "status":
                result = bundle_status()
            else:
                result = bundle_status()
                result["validated"] = bool(result.get("valid") and result.get("current"))
            emit(result, human=args.human)
            return EXIT_READY if result.get("current", True) and result.get("valid", True) else EXIT_SYSTEM
        if args.command == "template":
            if args.template_command == "status":
                result = template_status()
            elif args.template_command == "install":
                result = install_from_transfer(remove_parts=not args.keep_parts)
            else:
                result = sync_copies()
            emit(result, human=args.human)
            return EXIT_READY if result.get("valid", True) or args.template_command == "sync" else EXIT_TEMPLATE
        if args.command == "prepare":
            text = Path(args.jd_file).read_text(encoding="utf-8")
            payload = {
                "company": args.company,
                "role": args.role,
                "full_job_description": text,
                "source": args.source,
                "source_url": args.source_url,
                "external_job_id": args.external_job_id,
                "location": args.location,
                "application_url": args.application_url,
                "recipient": args.recipient,
                "recipient_source": args.recipient_source,
                "live_status": args.live_status,
                "live_verified_at": args.live_verified_at,
                "live_verification_source": args.live_verification_source,
            }
            result = prepare(payload, actor=args.actor, force_weak=args.force_weak)
            emit(result, human=args.human)
            if any(item.startswith("weak_fit:") for item in result["blockers"]):
                return EXIT_WEAK_FIT
            if any(item.startswith("route_unresolved:") for item in result["blockers"]):
                return EXIT_ROUTE
            return EXIT_READY
        if args.command == "status":
            result = status(args.job_id)
            emit(result, human=args.human)
            return EXIT_READY
        if args.command == "score":
            result = _cmd_score(args.job_id)
            emit(result, human=args.human)
            if result.get("reason") == "job_not_prepared":
                return EXIT_OWNER_INPUT
            if result["fit_score"]["recommendation"] == "weak":
                return EXIT_WEAK_FIT
            return EXIT_READY
        if args.command == "route":
            result = _cmd_route(args.job_id)
            emit(result, human=args.human)
            if result.get("reason") == "job_not_prepared":
                return EXIT_OWNER_INPUT
            return EXIT_ROUTE if result["route"]["route"] == "unresolved" else EXIT_READY
        if args.command == "render":
            result = _cmd_render(args.job_id)
            emit(result, human=args.human)
            if not result["valid"]:
                if result.get("blocker") == "generated_application_missing":
                    return EXIT_OWNER_INPUT
                if not result.get("docx"):
                    return EXIT_SYSTEM
                return EXIT_POLICY
            return EXIT_READY
        if args.command == "package":
            result = _cmd_package(args.job_id)
            emit(result, human=args.human)
            if result["route"]["route"] == "unresolved":
                return EXIT_ROUTE
            if not Path(result["generated_application"]).is_file():
                return EXIT_OWNER_INPUT
            return EXIT_READY
        if args.command == "scanner":
            config, paths = load_config()
            report = run_scan(Path(args.file), root=paths.repo_root, scanner_id=args.scanner_id)
            if args.output:
                write_report(report, Path(args.output))
            emit(report, human=args.human)
            return EXIT_READY
        if args.command == "generate":
            config, paths = load_config()
            artifact_dir = paths.tracker_base / "artifacts" / args.job_id
            packet = artifact_dir / "generation_packet.json"
            if args.generation_command == "export":
                result = {"job_id": args.job_id, "packet": str(packet), "exists": packet.is_file()}
                emit(result, human=args.human)
                return EXIT_READY if packet.is_file() else EXIT_SYSTEM
            if args.generation_command == "run":
                output = artifact_dir / "generated_application.pending.json"
                result = run_adapter(args.adapter, packet, output, provider=args.provider, model=args.model)
                emit(result, human=args.human)
                return EXIT_READY if not result.get("executed") or result.get("returncode") == 0 else EXIT_SYSTEM
            result = import_generated(args.job_id, Path(args.file), actor=args.actor)
            emit(result, human=args.human)
            return EXIT_READY if result["valid"] else EXIT_POLICY
        if args.command == "validate":
            config, paths = load_config()
            artifact_dir = paths.tracker_base / "artifacts" / args.job_id
            generated = artifact_dir / "generated_application.json"
            if not generated.is_file():
                result = {"valid": False, "reason": "generated_application_missing"}
                emit(result, human=args.human)
                return EXIT_OWNER_INPUT
            bundle = load_bundle()
            packet = json.loads((artifact_dir / "generation_packet.json").read_text(encoding="utf-8"))
            from .generation import validate_generated_application
            findings = validate_generated_application(json.loads(generated.read_text(encoding="utf-8")), packet, bundle)
            result = {"valid": not any(item["severity"] == "error" for item in findings), "findings": findings}
            emit(result, human=args.human)
            return EXIT_READY if result["valid"] else EXIT_POLICY
        raise AssertionError(args.command)
    except Exception as exc:
        emit({"error": type(exc).__name__, "message": str(exc)}, human=args.human)
        return EXIT_SYSTEM


if __name__ == "__main__":
    sys.exit(main())
