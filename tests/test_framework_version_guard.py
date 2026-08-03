from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "guard",
    [
        "check_framework_version.py",
        "lint_skills.py",
        "security_guards.py",
    ],
)
def test_repository_release_guards(guard: str) -> None:
    result = subprocess.run(
        [sys.executable, str(REPO / "tools" / guard)],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, f"guard={guard}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
