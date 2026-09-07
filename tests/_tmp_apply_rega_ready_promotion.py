from __future__ import annotations
import json
from pathlib import Path
from career_engine.incremental_outreach_replenisher import run_replenishment

def test_apply_rega_ready_promotion():
    result=run_replenishment(
        candidates_path=Path('runtime/acceptance/rega-ready-promotion-20260907/candidates.jsonl'),
        batch_size=10,
        apply=True,
        monitor_dir='runtime/acceptance/rega-ready-promotion-20260907',
        journal_path='runtime/acceptance/rega-ready-promotion-20260907/replenisher-journal.json',
    )
    print(json.dumps(result,ensure_ascii=False,indent=2))
    assert result.get('ok') is True
    assert result.get('sends')==0
