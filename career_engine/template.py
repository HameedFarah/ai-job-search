from __future__ import annotations

import base64
import hashlib
import json
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from .config import load_config


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _integrity(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "present": path.is_file(),
        "size_bytes": path.stat().st_size if path.is_file() else 0,
        "zip_valid": False,
        "zip_error": "",
    }
    if not path.is_file():
        return result
    try:
        with zipfile.ZipFile(path) as archive:
            bad = archive.testzip()
            result["zip_valid"] = bad is None
            if bad:
                result["zip_error"] = f"Corrupt member: {bad}"
    except Exception as exc:
        result["zip_error"] = f"{type(exc).__name__}: {exc}"
    return result


def status(*, root: Path | None = None) -> dict[str, Any]:
    config, paths = load_config(root)
    settings = config["template"]
    path = paths.repo_root / settings["repository_path"]
    expected = settings.get("expected_sha256", "")
    integrity = _integrity(path)
    actual = sha256_file(path) if path.is_file() else ""
    manifest = path.with_suffix(".manifest.json")
    return {
        "template_id": settings["id"],
        "version": settings["version"],
        "path": str(path),
        "expected_sha256": expected,
        "sha256": actual,
        "hash_matches": bool(actual and expected and actual == expected),
        "manifest": str(manifest),
        "manifest_present": manifest.is_file(),
        **integrity,
        "valid": bool(integrity["present"] and integrity["zip_valid"] and (not expected or actual == expected)),
    }


def install_from_transfer(*, root: Path | None = None, remove_parts: bool = True) -> dict[str, Any]:
    config, paths = load_config(root)
    settings = config["template"]
    destination = paths.repo_root / settings["repository_path"]
    transfer = destination.parent / ".transfer"
    parts = sorted(transfer.glob("part[0-9][0-9].b64"))
    if not parts:
        raise FileNotFoundError(f"No template transfer parts found in {transfer}")
    expected_names = [f"part{i:02d}.b64" for i in range(len(parts))]
    actual_names = [item.name for item in parts]
    if actual_names != expected_names:
        raise ValueError(f"Template transfer parts are not contiguous: {actual_names}")
    payload = "".join(item.read_text(encoding="utf-8").strip() for item in parts)
    data = base64.b64decode(payload, validate=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=destination.name + ".", suffix=".tmp", delete=False) as temp:
        temp_path = Path(temp.name)
        temp.write(data)
    integrity = _integrity(temp_path)
    actual = sha256_file(temp_path)
    expected = settings.get("expected_sha256", "")
    if not integrity["zip_valid"]:
        temp_path.unlink(missing_ok=True)
        raise ValueError(f"Reconstructed DOCX failed ZIP validation: {integrity['zip_error']}")
    if expected and actual != expected:
        temp_path.unlink(missing_ok=True)
        raise ValueError(f"Reconstructed template hash mismatch: expected {expected}, got {actual}")
    temp_path.replace(destination)
    manifest_path = destination.with_suffix(".manifest.json")
    manifest = {
        "schema_version": 1,
        "template_id": settings["id"],
        "version": settings["version"],
        "filename": destination.name,
        "sha256": actual,
        "size_bytes": destination.stat().st_size,
        "page_limit": settings["page_limit"],
        "headshot_required": settings["headshot_required"],
        "body_alignment": settings["body_alignment"],
        "google_drive_path": settings.get("google_drive_path", ""),
        "storage_box_path": settings.get("storage_box_path", ""),
        "immutable": True,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if remove_parts:
        for item in parts:
            item.unlink()
        for item in sorted(transfer.rglob("*"), reverse=True):
            if item.is_file():
                item.unlink()
            elif item.is_dir():
                try:
                    item.rmdir()
                except OSError:
                    pass
        try:
            transfer.rmdir()
        except OSError:
            pass
    return status(root=root)


def _run(command: list[str], *, timeout: int = 300) -> dict[str, Any]:
    completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    return {
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout[-3000:],
        "stderr": completed.stderr[-3000:],
    }


def sync_copies(*, root: Path | None = None) -> dict[str, Any]:
    config, paths = load_config(root)
    settings = config["template"]
    source = paths.repo_root / settings["repository_path"]
    current = status(root=root)
    if not current["valid"]:
        raise ValueError("Template must pass hash and ZIP integrity checks before sync")
    expected = current["sha256"]
    storage = Path(settings["storage_box_path"])
    storage.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, storage)
    storage_hash = sha256_file(storage)
    if storage_hash != expected:
        raise ValueError(f"Storage Box hash mismatch: {storage_hash}")
    drive_target = settings["google_drive_path"]
    drive_result = _run(["rclone", "copyto", str(source), drive_target], timeout=600)
    if drive_result["returncode"] != 0:
        raise RuntimeError("Google Drive copy failed: " + (drive_result["stderr"] or drive_result["stdout"]))
    check = _run(["rclone", "sha256sum", drive_target], timeout=300)
    drive_hash = ""
    if check["returncode"] == 0 and check["stdout"].strip():
        drive_hash = check["stdout"].strip().split()[0]
    if drive_hash and drive_hash != expected:
        raise ValueError(f"Google Drive hash mismatch: {drive_hash}")
    return {
        "source": str(source),
        "sha256": expected,
        "storage_box": {"path": str(storage), "sha256": storage_hash, "verified": storage_hash == expected},
        "google_drive": {"path": drive_target, "sha256": drive_hash, "verified": bool(drive_hash == expected), "copy": drive_result, "check": check},
    }
