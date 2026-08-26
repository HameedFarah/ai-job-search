#!/usr/bin/env python3
"""Reconcile the canonical Career Engine schedule into Hermes profile stores.

Canonical/default operation targets the profile actually consumed by the
RUNNING Hermes gateway process (detected from ``--profile`` in its command
line). An explicit ``--profile`` is supported but must match the running
gateway; a mismatch fails closed because editing another store would have no
effect on what the gateway executes. Exactly one enabled Career Engine Daily
Scan may exist across ALL stores; reconciliation pauses duplicates.

The daily scan itself runs from a dedicated clean runtime worktree
(``workdir``). Reconciliation provisions it with fast-forward-only semantics
(absent: created from origin/master; strictly behind: ``merge --ff-only``;
dirty/ahead/diverged: refused, never reset/clean/stash/detach-backward) and
writes the git-ignored runtime authority pointer binding every Career Engine
entry point launched from that worktree to the canonical live tracker base.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "projects/job-automation/config/career-engine-scheduler.v1.json"
DEFAULT_HERMES = Path.home() / ".hermes"
GLOBAL_SKILLS = DEFAULT_HERMES / "skills"
HERMES_EXECUTABLE_ENV = "HERMES_EXECUTABLE"
ORIGIN_REF = "origin/master"
GIT_TIMEOUT_SECONDS = 60


class RuntimeWorktreeError(RuntimeError):
    """Raised when the dedicated runtime worktree cannot be provisioned safely."""


class ProfileMismatchError(RuntimeError):
    """Raised when the requested profile does not match the running gateway."""


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


def atomic_write_json(target: Path, payload: dict) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=target.parent, delete=False, encoding="utf-8") as tmp:
        tmp_path = Path(tmp.name)
        tmp.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
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


# --- Running gateway profile -------------------------------------------------


def running_gateway_profile(proc_root: Path = Path("/proc")) -> str | None:
    """Detect the ``--profile`` the RUNNING hermes gateway was launched with.

    The agency store may list an enabled job while the running gateway consumes
    a different profile store entirely; desired state must target what actually
    executes. Returns None when no hermes gateway process is detectable.
    """
    try:
        entries = sorted(proc_root.iterdir())
    except OSError:
        return None
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        parts = [p for p in raw.decode("utf-8", "replace").split("\0") if p]
        if not parts or not any("hermes" in p for p in parts):
            continue
        if not any("gateway" in p for p in parts):
            continue
        for index, part in enumerate(parts):
            if part == "--profile" and index + 1 < len(parts):
                return parts[index + 1]
            if part.startswith("--profile="):
                return part.split("=", 1)[1]
    return None


def resolve_target_profile(requested: str | None) -> tuple[str, dict[str, object]]:
    """Resolve the store profile to reconcile against the running gateway.

    Default operation targets the running gateway profile and fails closed when
    it cannot be detected; an explicit request must match the running gateway.
    """
    gateway = running_gateway_profile()
    info: dict[str, object] = {
        "running_gateway_profile": gateway,
        "requested_profile": requested,
    }
    if requested:
        if gateway is not None and requested != gateway:
            raise ProfileMismatchError(
                f"requested profile {requested!r} does not match the running gateway profile "
                f"{gateway!r}; refusing to edit a store the gateway does not execute"
            )
        return requested, info
    if gateway is None:
        raise ProfileMismatchError(
            "no running hermes gateway process detected; pass --profile explicitly to "
            "target a store deliberately"
        )
    return gateway, info


# --- Dedicated runtime worktree ----------------------------------------------


def _run_git(
    args: list[str],
    *,
    cwd: Path | None = None,
    timeout: int = GIT_TIMEOUT_SECONDS,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=check,
    )


def _worktree_registered(repo_root: Path, path: Path) -> bool:
    listing = _run_git(["worktree", "list", "--porcelain"], cwd=repo_root).stdout
    for block in listing.split("\n\n"):
        for line in block.splitlines():
            if line.startswith("worktree ") and Path(line.split(None, 1)[1]).resolve() == path.resolve():
                return True
    return False


def _dirty_count(path: Path) -> int:
    stdout = _run_git(["status", "--porcelain=v1"], cwd=path).stdout
    return len([line for line in stdout.splitlines() if line.strip()])


def _rev_list_count(range_spec: str, path: Path) -> int:
    completed = _run_git(["rev-list", "--count", range_spec], cwd=path, check=False)
    if completed.returncode != 0:
        raise RuntimeWorktreeError(
            f"cannot evaluate commit position for {range_spec}: {(completed.stderr or '').strip()[:200]}"
        )
    return int(completed.stdout.strip() or 0)


def resolve_origin_master(repo_root: Path, info: dict[str, object]) -> str:
    """Resolve origin/master after a best-effort bounded fetch; fail visibly offline."""
    try:
        _run_git(["rev-parse", "--verify", ORIGIN_REF], cwd=repo_root)
        local_ref = True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        local_ref = False
    fetch_ok = True
    try:
        _run_git(["fetch", "--quiet", "origin", "master"], cwd=repo_root)
        info["fetched"] = True
    except subprocess.TimeoutExpired as exc:
        fetch_ok = False
        info["fetched"] = False
        info["fetch_note"] = f"fetch timed out after {GIT_TIMEOUT_SECONDS}s: {exc}"
    except subprocess.CalledProcessError as exc:
        fetch_ok = False
        info["fetched"] = False
        detail = (exc.stderr or exc.stdout or "fetch failed").strip()
        info["fetch_note"] = detail[:300]
    if not local_ref and not fetch_ok:
        raise RuntimeWorktreeError(
            f"cannot resolve {ORIGIN_REF} for runtime provisioning: no local ref and fetch failed "
            f"({info.get('fetch_note')})"
        )
    target = _run_git(["rev-parse", "--verify", ORIGIN_REF], cwd=repo_root).stdout.strip()
    info["fetch_ok"] = fetch_ok
    info["origin_master"] = target
    return target


def _require_valid_registered_worktree(path: Path) -> None:
    try:
        inside = _run_git(["rev-parse", "--is-inside-work-tree"], cwd=path).stdout.strip()
        toplevel = Path(_run_git(["rev-parse", "--show-toplevel"], cwd=path).stdout.strip()).resolve()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise RuntimeWorktreeError(
            f"{path} exists but is not a usable git worktree; inspect it manually"
        ) from exc
    if inside != "true" or toplevel != path:
        raise RuntimeWorktreeError(
            f"{path} is not the root of a git worktree (toplevel={toplevel})"
        )
    if not _worktree_registered(ROOT, path):
        raise RuntimeWorktreeError(
            f"{path} is a git worktree but is not registered to this repository ({ROOT})"
        )


def ensure_runtime_worktree(manifest: dict) -> dict:
    """Ensure the dedicated runtime workdir tracks origin/master fast-forward-only.

    A missing directory is provisioned as a detached worktree of ROOT at
    origin/master. An existing path must be a clean registered worktree of this
    repository: strictly-behind heads converge with ``merge --ff-only``;
    dirty, ahead or diverged states are refused. Dirty work is never reset,
    cleaned or stashed, and an ahead source is never detached backward.
    """
    path = Path(manifest["workdir"]).expanduser().resolve()
    info: dict[str, object] = {"path": str(path), "origin_ref": ORIGIN_REF}
    target = resolve_origin_master(ROOT, info)
    provisioned = False
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        _run_git(["worktree", "add", "--detach", str(path), target], cwd=ROOT)
        provisioned = True
    else:
        _require_valid_registered_worktree(path)
    dirty = _dirty_count(path)
    if dirty:
        info["dirty_entries"] = dirty
        raise RuntimeWorktreeError(
            f"runtime worktree {path} is dirty ({dirty} entries); refusing to reset/clean/stash. "
            "Resolve it manually before applying the schedule."
        )
    head = _run_git(["rev-parse", "HEAD"], cwd=path).stdout.strip()
    ahead = _rev_list_count(f"{target}..HEAD", path)
    behind = _rev_list_count(f"HEAD..{target}", path)
    if ahead and behind:
        raise RuntimeWorktreeError(
            f"runtime worktree {path} diverged from {ORIGIN_REF} "
            f"(ahead {ahead}, behind {behind}); refusing to reset/rebase."
        )
    if ahead:
        raise RuntimeWorktreeError(
            f"runtime worktree {path} is {ahead} commits ahead of {ORIGIN_REF}; refusing to "
            "detach backward. Move or publish those commits manually."
        )
    fast_forwarded = False
    if behind:
        _run_git(["merge", "--ff-only", ORIGIN_REF], cwd=path)
        fast_forwarded = True
    head_after = _run_git(["rev-parse", "HEAD"], cwd=path).stdout.strip()
    still_dirty = _dirty_count(path)
    if still_dirty:
        raise RuntimeWorktreeError(
            f"runtime worktree {path} became dirty while converging to {ORIGIN_REF}"
        )
    if head_after != target:
        raise RuntimeWorktreeError(
            f"runtime worktree HEAD {head_after} does not match {ORIGIN_REF} {target}"
        )
    info.update({
        "provisioned": provisioned,
        "fast_forwarded": fast_forwarded,
        "ahead_before": ahead,
        "behind_before": behind,
        "head": head_after,
        "clean": True,
    })
    return info


def runtime_worktree_status(manifest: dict) -> dict[str, Any]:
    """Offline inspection of the dedicated runtime worktree (no fetch, no mutation)."""
    path = Path(manifest["workdir"]).expanduser().resolve()
    problems: list[str] = []
    details: dict[str, object] = {"path": str(path)}
    if not path.exists():
        problems.append("missing")
        details.update({"ok": False, "problems": problems})
        return details
    try:
        _require_valid_registered_worktree(path)
    except RuntimeWorktreeError as exc:
        problems.append(str(exc))
        details.update({"ok": False, "problems": problems})
        return details
    dirty = _dirty_count(path)
    details["dirty_entries"] = dirty
    if dirty:
        problems.append(f"dirty ({dirty} entries)")
    try:
        local_target = _run_git(["rev-parse", "--verify", ORIGIN_REF], cwd=ROOT).stdout.strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        problems.append(f"{ORIGIN_REF} not resolvable locally (fetch pending)")
        details.update({"ok": False, "problems": problems})
        return details
    head = _run_git(["rev-parse", "HEAD"], cwd=path).stdout.strip()
    details["head"] = head
    details["origin_master_local"] = local_target
    if head != local_target:
        ahead = _rev_list_count(f"{local_target}..HEAD", path)
        behind = _rev_list_count(f"HEAD..{local_target}", path)
        details.update({"ahead": ahead, "behind": behind})
        if ahead and behind:
            problems.append(f"diverged from {ORIGIN_REF} (ahead {ahead}, behind {behind})")
        elif ahead:
            problems.append(f"ahead of {ORIGIN_REF} by {ahead} commits")
        else:
            details["behind_only"] = True
    details["ok"] = not problems
    details["problems"] = problems
    return details


# --- Runtime authority binding ------------------------------------------------


def live_authority_census(base: Path) -> dict[str, object]:
    """Bounded state-continuity census of a candidate live tracker base."""
    jobs_csv = (base / "data/jobs.csv").is_file()
    jobs_dir = base / "data/jobs"
    records = sorted(jobs_dir.glob("*.json")) if jobs_dir.is_dir() else []
    continuous = bool(jobs_csv and records)
    return {
        "tracker_base": str(base),
        "jobs_csv": jobs_csv,
        "job_records": len(records),
        "continuous": continuous,
    }


def write_runtime_authority(manifest: dict) -> dict[str, object]:
    """Bind the clean runtime worktree to the canonical live tracker base.

    The pointer is written into the ignored ``runtime/`` directory of the
    dedicated runtime worktree so career_engine.config.load_config binds every
    entry point launched there to the one mutable live tracker. The live base
    must pass the continuity census first: an empty or absent tracker is never
    copied or initialized into a second data store.
    """
    base = Path(manifest["tracker_authority_base"]).expanduser().resolve()
    census = live_authority_census(base)
    if not census["continuous"]:
        raise RuntimeWorktreeError(
            f"live tracker authority at {base} fails continuity census "
            f"(jobs.csv={census['jobs_csv']}, job records={census['job_records']}); refusing "
            "to copy or initialize a second empty tracker"
        )
    pointer = Path(manifest["workdir"]).expanduser().resolve() / manifest["runtime_authority_pointer"]
    payload = {
        "schema_version": 1,
        "tracker_base": str(base),
        "generated_by": "tools/reconcile_career_scheduler.py",
    }
    atomic_write_json(pointer, payload)
    return {
        "pointer": str(pointer),
        "written": True,
        **{key: value for key, value in payload.items()},
        "jobs_csv": census["jobs_csv"],
        "job_records": census["job_records"],
        "continuous": census["continuous"],
    }


def read_runtime_authority_status(manifest: dict) -> dict[str, Any]:
    """Offline validation of the deployed runtime authority pointer (--check)."""
    problems: list[str] = []
    workdir = Path(manifest["workdir"]).expanduser().resolve()
    pointer = workdir / manifest["runtime_authority_pointer"]
    expected_base = Path(manifest["tracker_authority_base"]).expanduser().resolve()
    details: dict[str, object] = {"pointer": str(pointer), "expected_tracker_base": str(expected_base)}
    if not pointer.is_file():
        problems.append("runtime authority pointer missing")
        details.update({"ok": False, "problems": problems})
        return details
    try:
        payload = json.loads(pointer.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        problems.append(f"invalid pointer JSON: {exc}")
        details.update({"ok": False, "problems": problems})
        return details
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        problems.append("unsupported pointer schema")
    bound = str(payload.get("tracker_base", "")).strip() if isinstance(payload, dict) else ""
    if not bound:
        problems.append("pointer tracker_base empty")
    else:
        resolved = Path(bound).expanduser().resolve()
        details["bound_tracker_base"] = str(resolved)
        if resolved != expected_base:
            problems.append(f"pointer binds {resolved} instead of canonical {expected_base}")
        elif not resolved.is_dir():
            problems.append(f"pointer target does not exist: {resolved}")
        else:
            census = live_authority_census(resolved)
            details["jobs_csv"] = census["jobs_csv"]
            details["job_records"] = census["job_records"]
            if not census["continuous"]:
                problems.append(
                    f"bound tracker base fails continuity census "
                    f"(jobs.csv={census['jobs_csv']}, job records={census['job_records']})"
                )
    details["ok"] = not problems
    details["problems"] = problems
    return details


# --- Host timezone ------------------------------------------------------------


def host_timezone_name() -> str | None:
    """Best-effort effective host timezone name (Hermes cron has no per-job field)."""
    tz = os.environ.get("TZ", "").strip()
    if tz:
        return tz
    try:
        configured = Path("/etc/timezone").read_text(encoding="utf-8").strip()
        if configured:
            return configured
    except OSError:
        pass
    localtime = Path("/etc/localtime")
    if localtime.is_symlink():
        target = os.path.realpath(localtime)
        marker = "/zoneinfo/"
        if marker in target:
            return target.split(marker, 1)[1]
        return Path(target).name
    return None


# --- Inspection / application -------------------------------------------------


def inspect(manifest: dict, target: Path, *, gateway_profile: str | None = None) -> dict:
    target_store = target / "cron/jobs.json"
    target_jobs = jobs(target_store)
    target_named = [j for j in target_jobs if j.get("name") == manifest["name"]]
    target_matches = [j for j in target_named if matching(j, manifest)]
    target_enabled_named = [j for j in target_named if j.get("enabled")]
    duplicates: list[dict[str, object]] = []
    enabled_total = len(target_enabled_named)
    for store in stores():
        if store == target_store:
            continue
        store_enabled = [
            j for j in jobs(store)
            if j.get("name") == manifest["name"] and j.get("enabled")
        ]
        enabled_total += len(store_enabled)
        duplicates.extend({"store": str(store), "job_id": j.get("id")} for j in store_enabled)
    script_source = ROOT / manifest["source_script"]
    deployed = target / "scripts" / manifest["runtime_script"]
    bytes_match = (
        script_source.is_file()
        and deployed.is_file()
        and deployed.read_bytes() == script_source.read_bytes()
    )
    worktree = runtime_worktree_status(manifest)
    authority = read_runtime_authority_status(manifest)
    timezone_name = host_timezone_name()
    timezone_expected = manifest.get("timezone")
    timezone_ok = timezone_name == timezone_expected
    problems: list[str] = []
    if len(target_matches) != 1:
        problems.append("no single canonical matching job in target store")
    if len(target_enabled_named) != 1:
        problems.append(
            f"target store has {len(target_enabled_named)} enabled '{manifest['name']}' jobs"
        )
    if enabled_total != 1:
        problems.append(
            f"{enabled_total} enabled '{manifest['name']}' jobs across all stores; exactly one allowed"
        )
    if duplicates:
        problems.append(f"enabled duplicates in other stores: {len(duplicates)}")
    if not deployed.is_file():
        problems.append("deployed runtime script missing")
    elif not bytes_match:
        problems.append("deployed runtime script bytes drifted from committed source")
    if not worktree["ok"]:
        problems.extend(f"runtime worktree: {problem}" for problem in worktree["problems"])
    if not authority["ok"]:
        problems.extend(f"runtime authority: {problem}" for problem in authority["problems"])
    if not timezone_ok:
        problems.append(
            f"effective host timezone {timezone_name!r} != expected {timezone_expected!r}"
        )
    if gateway_profile is not None:
        try:
            target_profile = profile_name_for_home(target)
        except ValueError:
            target_profile = str(target)
        if target_profile != gateway_profile:
            problems.append(
                f"target store profile {target_profile!r} != running gateway profile {gateway_profile!r}"
            )
    return {
        "target_store": str(target_store),
        "matching_job_id": target_matches[0].get("id") if target_matches else None,
        "schedule": manifest["schedule"],
        "duplicates": duplicates,
        "enabled_jobs_total": enabled_total,
        "runtime_script_bytes_match": bytes_match,
        "runtime_worktree": worktree,
        "runtime_authority": authority,
        "host_timezone": {
            "name": timezone_name,
            "expected": timezone_expected,
            "ok": timezone_ok,
        },
        "problems": problems,
        "status": "ok" if not problems else "drift",
    }


def apply(manifest: dict, target: Path, *, gateway_profile: str | None = None) -> dict:
    # Provision/update the dedicated runtime worktree with ff-only semantics and
    # bind its runtime authority before touching the Hermes store. Runtime
    # preflight remains authoritative at scan time; this provisioning uses the
    # same convergence rules so the two never disagree.
    ensure_runtime_worktree(manifest)
    write_runtime_authority(manifest)
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
    return inspect(manifest, target, gateway_profile=gateway_profile)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--profile")
    args = parser.parse_args(argv)
    try:
        profile, profile_info = resolve_target_profile(args.profile)
    except ProfileMismatchError as exc:
        print(json.dumps(
            {"status": "error", "error": f"profile mismatch: {exc}", "send_or_submit": False},
            sort_keys=True,
        ))
        return 2
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    try:
        target = profile_home(profile)
        if args.check:
            result = inspect(manifest, target, gateway_profile=profile)
        else:
            result = apply(manifest, target, gateway_profile=profile)
    except (RuntimeWorktreeError, FileNotFoundError, subprocess.SubprocessError, OSError, ValueError) as exc:
        print(json.dumps(
            {"status": "error", "error": f"{type(exc).__name__}: {exc}", "send_or_submit": False},
            sort_keys=True,
        ))
        return 2
    active_file = DEFAULT_HERMES / "active_profile"
    active = active_file.read_text(encoding="utf-8").strip() if active_file.exists() else "default"
    result.update({"active_profile": active, "target_profile": profile, **profile_info})
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
