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
DATAFORSEO_MANIFEST = WORKTREE / 'runtime/providers/dataforseo.json'
OUTSCRAPER_MANIFEST = WORKTREE / 'runtime/providers/outscraper.json'


def run_phase(
    manifest: Path,
    extra: list[str],
    *,
    enable_company_domain_lookup: bool = False,
) -> subprocess.CompletedProcess[str]:
    cmd = [
        sys.executable, str(RUNTIME_EXEC), '--manifest', str(manifest), '--',
        sys.executable, str(WORKTREE / 'career-engine'), 'rega-enrich',
        '--root', str(STATE), '--apply', '--limit', '25', '--search-limit', '10000',
        '--hunter-cap', '60', '--prospeo-cap', '60', '--snov-cap', '500',
        '--use-all-available-provider-credit',
        *extra,
    ]
    env = os.environ.copy()
    env['REGA_ENABLE_COMPANY_DOMAIN_LOOKUP'] = '1' if enable_company_domain_lookup else '0'
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    for _batch in range(100):
        result = None
        for attempt in range(3):
            result = subprocess.run(cmd, cwd=WORKTREE, env=env, text=True, capture_output=True, timeout=3900)
            stdout_parts.append(result.stdout[-6000:])
            stderr_parts.append(result.stderr[-3000:])
            if result.returncode == 0:
                break
            transient = any(marker in result.stderr for marker in (
                'TimeoutError: The read operation timed out',
                'URLError',
                'RemoteDisconnected',
                'ConnectionResetError',
            ))
            if not transient or attempt == 2:
                return subprocess.CompletedProcess(cmd, result.returncode, '\n'.join(stdout_parts), '\n'.join(stderr_parts))
        summary = json.loads((STATE / 'summary.json').read_text())
        if int(summary.get('run_processed') or 0) < 25:
            return subprocess.CompletedProcess(cmd, 0, '\n'.join(stdout_parts), '\n'.join(stderr_parts))
    return subprocess.CompletedProcess(cmd, 75, '\n'.join(stdout_parts), 'phase exceeded 100 bounded batches')


def print_phase(label: str, result: subprocess.CompletedProcess[str]) -> None:
    if result.stdout:
        print(label + '\n' + result.stdout[-20000:])
    if result.stderr:
        print(label + '_STDERR\n' + result.stderr[-12000:])


def test_finish_free_then_identity_then_strict_outscraper_resolution():
    # Phase 1: exhaust the already-configured contact providers and allow the
    # existing Snov company-name -> domain clue path for high-value A/B rows.
    # The clue remains fail-closed: the first-party homepage must independently
    # match the REGA company before the domain is accepted.
    free = run_phase(CONTACT_MANIFEST, [], enable_company_domain_lookup=True)
    print_phase('FREE_PHASE', free)
    assert free.returncode == 0

    # Phase 2: identity recovery. DataForSEO is already implemented by the
    # resolver as an existing-credit clue only; every candidate still has to
    # pass the same current first-party identity checks before it is accepted.
    # Run this phase unconditionally: research_remaining is deliberately scoped
    # to the active resolver mode and is not a universal unresolved-company
    # count, so it must not be used to suppress a later fallback phase.
    identity = run_phase(
        DATAFORSEO_MANIFEST,
        [
            '--hunter-cap', '0', '--prospeo-cap', '0', '--snov-cap', '0',
            '--use-dataforseo-fallback',
        ],
    )
    print_phase('DATAFORSEO_PHASE', identity)
    assert identity.returncode == 0

    # Phase 3: strict final fallback for still-unresolved identities, held
    # candidates and confirmed domains without a usable route. Run it
    # unconditionally for the same reason: the phase itself decides which
    # checkpointed outcomes are eligible for an Outscraper retry.
    paid = run_phase(
        OUTSCRAPER_MANIFEST,
        [
            '--hunter-cap', '0', '--prospeo-cap', '0', '--snov-cap', '0',
            '--use-outscraper-fallback',
        ],
    )
    print_phase('OUTSCRAPER_PHASE', paid)
    assert paid.returncode == 0

    # Phase 4: now that strict identity recovery may have confirmed additional
    # official domains, revisit only those current confirmed domains with the
    # contact providers. This avoids wasting paid identity-search attempts on
    # domains we already know and converts fresh domain evidence into mailboxes.
    refresh = run_phase(
        CONTACT_MANIFEST,
        ['--refresh-contacts'],
    )
    print_phase('CONTACT_REFRESH_PHASE', refresh)
    assert refresh.returncode == 0

    summary = json.loads((STATE / 'summary.json').read_text())
    assert summary['status'] == 'scope_processed'
    assert summary['research_remaining'] == 0
    for bad in ('research_error', 'search_unavailable', 'provider_research_incomplete'):
        assert summary.get('outcomes', {}).get(bad, 0) == 0
    assert summary.get('sends', 0) == 0
    assert summary.get('queue_writes', 0) == 0
    assert summary.get('purchases', 0) == 0
