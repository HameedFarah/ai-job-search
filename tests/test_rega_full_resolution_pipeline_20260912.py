from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

WORKTREE = Path('/home/hameedo/projects/ai-job-search/.worktrees/rega-api-enrichment-20260911')
STATE = Path('/home/hameedo/projects/ai-job-search/runtime/acceptance/rega-api-enrichment-20260911')
RUNTIME_EXEC = Path('/home/hameedo/vps-infra-dev/scripts/infisical-vps/runtime_exec.py')
CONTACT_MANIFEST = WORKTREE / 'projects/job-automation/config/rega-contact-runtime-manifest.json'
OUTSCRAPER_MANIFEST = WORKTREE / 'runtime/providers/outscraper.json'


def run_phase(manifest: Path, extra: list[str]) -> subprocess.CompletedProcess[str]:
    cmd = [
        sys.executable, str(RUNTIME_EXEC), '--manifest', str(manifest), '--',
        sys.executable, str(WORKTREE / 'career-engine'), 'rega-enrich',
        '--root', str(STATE), '--apply', '--limit', '0', '--search-limit', '10000',
        '--hunter-cap', '60', '--prospeo-cap', '60', '--snov-cap', '500',
        '--use-all-available-provider-credit',
        *extra,
    ]
    env = os.environ.copy()
    env['REGA_ENABLE_COMPANY_DOMAIN_LOOKUP'] = '0'
    return subprocess.run(cmd, cwd=WORKTREE, env=env, text=True, capture_output=True, timeout=21630)


def test_finish_free_then_strict_outscraper_resolution():
    free = run_phase(CONTACT_MANIFEST, [])
    if free.stdout:
        print('FREE_PHASE\n' + free.stdout[-20000:])
    if free.stderr:
        print('FREE_STDERR\n' + free.stderr[-12000:])
    assert free.returncode == 0

    paid = run_phase(OUTSCRAPER_MANIFEST, ['--hunter-cap', '0', '--prospeo-cap', '0', '--snov-cap', '0', '--use-outscraper-fallback'])
    if paid.stdout:
        print('OUTSCRAPER_PHASE\n' + paid.stdout[-20000:])
    if paid.stderr:
        print('OUTSCRAPER_STDERR\n' + paid.stderr[-12000:])
    assert paid.returncode == 0

    summary = json.loads((STATE / 'summary.json').read_text())
    assert summary['status'] == 'scope_processed'
    assert summary['research_remaining'] == 0
    for bad in ('research_error', 'search_unavailable', 'provider_research_incomplete'):
        assert summary.get('outcomes', {}).get(bad, 0) == 0
    assert summary.get('sends', 0) == 0
    assert summary.get('purchases', 0) == 0
