#!/usr/bin/env python3
"""Reconcile the canonical Career Engine schedule into Hermes profile stores."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "projects/job-automation/config/career-engine-scheduler.v1.json"
DEFAULT_HERMES = Path.home() / ".hermes"
GLOBAL_SKILLS = DEFAULT_HERMES / "skills"
HERMES_EXECUTABLE_ENV = "HERMES_EXECUTABLE"


def resolve_hermes() -> str:
    """Resolve Hermes deterministically for interactive, cron, and systemd callers."""
    def is_executable(candidate: Path) -> bool:
        try:
            return candidate.is_file() and bool(candidate.stat().st_mode & 0o111)
        except OSError:
            return False

    override = os.environ.get(HERMES_EXECUTABLE_ENV)
    if override:
        candidate = Path(override).expanduser()
        if is_executable(candidate):
            return str(candidate)
        raise FileNotFoundError(
            f"{HERMES_EXECUTABLE_ENV} does not point to an executable: {candidate}"
        )

    discovered = shutil.which("hermes")
    if discovered:
        return discovered

    candidate = DEFAULT_HERMES / "hermes-agent" / "venv" / "bin" / "hermes"
    if is_executable(candidate):
        return str(candidate)
    raise FileNotFoundError(
        "Hermes CLI not found: set HERMES_EXECUTABLE, install hermes on PATH, "
        f"or provide the maintained install at {candidate}"
    )


def profile_home(profile: str | None) -> Path:
    active_file = DEFAULT_HERMES / "active_profile"
    active = active_file.read_text(encoding="utf-8").strip() if active_file.exists() else "default"
    name = profile or active or "default"
    return DEFAULT_HERMES if name == "default" else DEFAULT_HERMES / "profiles" / name


def stores() -> list[Path]:
    result = [DEFAULT_HERMES / "cron/jobs.json"]
    result.extend(sorted(DEFAULT_HERMES.glob("profiles/*/cron/jobs.json")))
    return [p for p in result if p.is_file()]


def jobs(store: Path) -> list[dict]:
    try:
        payload = json.loads(store.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return payload.get("jobs", []) if isinstance(payload, dict) else []


def matching(job: dict, manifest: dict) -> bool:
    schedule = job.get("schedule", {})
    return (job.get("name") == manifest["name"] and schedule.get("expr") == manifest["schedule"]
            and job.get("schedule_display") == manifest["schedule"] and job.get("prompt") == manifest["prompt"]
            and job.get("skills", []) == manifest["skills"] and job.get("script") == manifest["runtime_script"]
            and job.get("no_agent") is manifest["no_agent"] and job.get("deliver") == manifest["deliver"]
            and job.get("workdir") == manifest["workdir"] and not job.get("model") and not job.get("provider"))


def profile_name_for_home(home: Path) -> str:
    if home.resolve() == DEFAULT_HERMES.resolve():
        return "default"
    profiles_root = (DEFAULT_HERMES / "profiles").resolve()
    try:
        rel = home.resolve().relative_to(profiles_root)
    except ValueError as exc:
        raise ValueError(f"unsupported Hermes profile home: {home}") from exc
    if len(rel.parts) != 1:
        raise ValueError(f"unsupported Hermes profile home: {home}")
    return rel.parts[0]


def run_hermes(home: Path, *args: str) -> None:
    """Run Hermes against one explicit profile without changing sticky state."""
    env = os.environ.copy()
    env["HERMES_HOME"] = str(home)
    profile = profile_name_for_home(home)
    subprocess.run(
        [resolve_hermes(), "--profile", profile, "cron", *args],
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )


def atomic_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as tmp:
        tmp_path = Path(tmp.name)
        with source.open("rb") as src:
            shutil.copyfileobj(src, tmp)
        tmp.flush()
        os.fsync(tmp.fileno())
    os.replace(tmp_path, target)


def skill_source(manifest: dict, skill: str) -> Path:
    entry = manifest["skill_sources"][skill]
    root = ROOT if entry["kind"] == "repo" else GLOBAL_SKILLS
    return root / entry["path"]


def provision_skills(manifest: dict, target: Path) -> None:
    """Expose manifest-owned skill sources to Hermes without copying authorities."""
    for skill in manifest["skills"]:
        source = skill_source(manifest, skill)
        if not (source / "SKILL.md").is_file():
            raise FileNotFoundError(f"maintained skill source missing: {skill}: {source}")
        link = target / "skills" / skill
        if link.is_symlink() and link.resolve() == source.resolve():
            continue
        if link.exists() or link.is_symlink():
            if link.is_dir() and not link.is_symlink():
                shutil.rmtree(link)
            else:
                link.unlink()
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(source, target_is_directory=True)


def inspect(manifest: dict, target: Path) -> dict:
    target_store = target / "cron/jobs.json"
    target_jobs = jobs(target_store)
    target_named = [j for j in target_jobs if j.get("name") == manifest["name"]]
    target_matches = [j for j in target_named if matching(j, manifest)]
    duplicates = []
    for store in stores():
        for job in jobs(store):
            if job.get("name") == manifest["name"] and job.get("enabled") and store != target_store:
                duplicates.append({"store": str(store), "job_id": job.get("id")})
    script = target / "scripts" / manifest["runtime_script"]
    target_enabled_named = [j for j in target_named if j.get("enabled")]
    return {"target_store": str(target_store), "matching_job_id": target_matches[0].get("id") if target_matches else None,
            "schedule": manifest["schedule"], "duplicates": duplicates,
            "status": "ok" if len(target_matches) == 1 and len(target_enabled_named) == 1 and target_matches[0].get("enabled") and script.is_file() and not duplicates else "drift"}


def apply(manifest: dict, target: Path) -> dict:
    provision_skills(manifest, target)
    source = ROOT / manifest["source_script"]
    runtime = target / "scripts" / manifest["runtime_script"]
    if not runtime.exists() or runtime.read_bytes() != source.read_bytes():
        atomic_copy(source, runtime)
    target_jobs = jobs(target / "cron/jobs.json")
    named = [j for j in target_jobs if j.get("name") == manifest["name"]]
    job = next((j for j in named if matching(j, manifest)), None) or (named[0] if named else None)
    edit_args = [
        "--schedule", manifest["schedule"],
        "--name", manifest["name"],
        "--prompt", manifest["prompt"],
        "--deliver", manifest["deliver"],
        "--script", manifest["runtime_script"],
        "--workdir", manifest["workdir"],
        "--agent",
        "--model", "",
        "--provider", "",
    ]
    for skill in manifest["skills"]:
        edit_args += ["--skill", skill]
    primary_id = job.get("id") if job else None
    if job:
        run_hermes(target, "edit", job["id"], *edit_args)
        if not job.get("enabled"):
            run_hermes(target, "resume", job["id"])
    else:
        create_args = [
            "create", manifest["schedule"], manifest["prompt"],
            "--name", manifest["name"],
            "--deliver", manifest["deliver"],
            "--script", manifest["runtime_script"],
            "--workdir", manifest["workdir"],
        ]
        for skill in manifest["skills"]:
            create_args += ["--skill", skill]
        run_hermes(target, *create_args)
        primary_id = next((j.get("id") for j in jobs(target / "cron/jobs.json")
                           if j.get("name") == manifest["name"] and j.get("enabled")), None)
    target_store = target / "cron/jobs.json"
    for store in stores():
        for candidate in jobs(store):
            if candidate.get("name") != manifest["name"] or not candidate.get("enabled"):
                continue
            if store == target_store and candidate.get("id") == primary_id:
                continue
            run_hermes(store.parents[1], "pause", candidate["id"])
    return inspect(manifest, target)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--profile")
    args = parser.parse_args(argv)
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    target = profile_home(args.profile)
    result = inspect(manifest, target) if args.check else apply(manifest, target)
    active_file = DEFAULT_HERMES / "active_profile"
    active = active_file.read_text(encoding="utf-8").strip() if active_file.exists() else "default"
    result.update({"active_profile": active})
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
