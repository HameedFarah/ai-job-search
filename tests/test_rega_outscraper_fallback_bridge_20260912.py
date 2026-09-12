from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

WORKTREE = Path('/home/hameedo/projects/ai-job-search/.worktrees/rega-api-enrichment-20260911')
STATE = Path('/home/hameedo/projects/ai-job-search/runtime/acceptance/rega-api-enrichment-20260911')
RUNTIME_EXEC = Path('/home/hameedo/vps-infra-dev/scripts/infisical-vps/runtime_exec.py')
MANIFEST = WORKTREE / 'runtime/providers/outscraper.json'


def test_resume_unresolved_with_strict_outscraper_fallback():
    cmd = [
        sys.executable, str(RUNTIME_EXEC), '--manifest', str(MANIFEST), '--',
        sys.executable, str(WORKTREE / 'career-engine'), 'rega-enrich',
        '--root', str(STATE), '--apply', '--limit', '0', '--search-limit', '10000',
        '--hunter-cap', '0', '--prospeo-cap', '0', '--snov-cap', '0',
        '--use-all-available-provider-credit', '--use-outscraper-fallback',
    ]
    env = os.environ.copy()
    env['REGA_ENABLE_COMPANY_DOMAIN_LOOKUP'] = '0'
    completed = subprocess.run(cmd, cwd=WORKTREE, env=env, text=True, capture_output=True, timeout=21630)
    if completed.stdout:
        print(completed.stdout[-20000:])
    if completed.stderr:
        print(completed.stderr[-12000:])
    assert completed.returncode == 0
