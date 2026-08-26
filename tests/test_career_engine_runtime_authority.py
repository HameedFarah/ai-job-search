"""Regression tests for the deterministic runtime authority binding.

A clean dedicated runtime worktree carries tracked code but no ignored
tracker state. The machine-generated pointer ``runtime/runtime-authority.json``
must bind every Career Engine entry point launched there to the canonical live
tracker base via ``career_engine.config.load_config`` without an exported env
var, while ``_load_tracker`` keeps loading the tracker IMPLEMENTATION from the
executing repository's clean source.
"""

import importlib.util
import json
import shutil
from pathlib import Path

import pytest

from career_engine.config import RUNTIME_AUTHORITY_POINTER, load_config
from career_engine.pipeline import _load_tracker


REPO = Path(__file__).parents[1]


def make_engine_root(tmp_path: Path) -> Path:
    """Minimal hermetic engine root carrying the tracked tracker implementation."""
    root = tmp_path / "runtime-worktree"
    tracker_dir = root / "projects/job-automation"
    config_dir = tracker_dir / "config"
    config_dir.mkdir(parents=True)
    shutil.copy2(REPO / "projects/job-automation/tracker.py", tracker_dir / "tracker.py")
    config = {
        "schema_version": 1,
        "vault": {"root": str(tmp_path / "vault")},
        "tracker_base": "projects/job-automation",
        "runtime_bundle": "projects/job-automation/config/runtime-bundle.v1.json",
    }
    (config_dir / "career-engine.v1.json").write_text(json.dumps(config), encoding="utf-8")
    return root


def write_pointer(root: Path, payload: dict | str) -> Path:
    pointer = root / RUNTIME_AUTHORITY_POINTER
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")
    return pointer


def test_pointer_binds_clean_source_to_live_authority(tmp_path):
    root = make_engine_root(tmp_path)
    live_base = tmp_path / "live-career-tracker"
    (live_base / "data").mkdir(parents=True)
    write_pointer(root, {"schema_version": 1, "tracker_base": str(live_base)})
    _, paths = load_config(root)
    assert paths.tracker_base == live_base.resolve()
    # The implementation still resolves from the executing repository's source.
    assert paths.tracker_source_path == (root / "projects/job-automation").resolve()


def test_explicit_env_override_wins_over_pointer(tmp_path, monkeypatch):
    root = make_engine_root(tmp_path)
    pointer_target = tmp_path / "pointer-target"
    env_target = tmp_path / "env-target"
    write_pointer(root, {"schema_version": 1, "tracker_base": str(pointer_target)})
    monkeypatch.setenv("CAREER_ENGINE_TRACKER_BASE", str(env_target))
    _, paths = load_config(root)
    assert paths.tracker_base == env_target.resolve()


def test_missing_pointer_falls_back_to_checkout_local_base(tmp_path):
    root = make_engine_root(tmp_path)
    _, paths = load_config(root)
    assert paths.tracker_base == root / "projects/job-automation"


@pytest.mark.parametrize(
    "payload",
    [
        "{not json",
        '{"schema_version": 2, "tracker_base": "/tmp/x"}',
        '{"schema_version": 1, "tracker_base": ""}',
        '{"schema_version": 1}',
        '["not", "an", "object"]',
    ],
)
def test_defective_pointers_fail_closed(tmp_path, payload):
    root = make_engine_root(tmp_path)
    write_pointer(root, payload)
    with pytest.raises(ValueError):
        load_config(root)


def test_pointer_to_missing_directory_fails_closed(tmp_path):
    root = make_engine_root(tmp_path)
    write_pointer(root, {"schema_version": 1, "tracker_base": str(tmp_path / "gone")})
    with pytest.raises(ValueError, match="does not exist"):
        load_config(root)


def test_load_tracker_uses_clean_source_implementation_and_live_state(tmp_path):
    root = make_engine_root(tmp_path)
    live_base = tmp_path / "live-career-tracker"
    live_base.mkdir()
    (live_base / "data/jobs").mkdir(parents=True)
    (live_base / "data/jobs.csv").write_text("job_id\n", encoding="utf-8")
    write_pointer(root, {"schema_version": 1, "tracker_base": str(live_base)})
    _, paths = load_config(root)
    tracker = _load_tracker(paths)
    assert Path(tracker.base_dir).resolve() == live_base.resolve(), (
        "tracker state must instantiate at the canonical live base"
    )
    spec = importlib.util.find_spec("career_engine_tracker")
    assert spec is None  # module was loaded under a private loader, not installed
    impl_file = Path(paths.tracker_source_path) / "tracker.py"
    assert impl_file.is_file(), "implementation must come from the executing clean source"
