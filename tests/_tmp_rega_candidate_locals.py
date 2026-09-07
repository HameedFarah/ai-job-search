from __future__ import annotations
import json
from pathlib import Path
from collections import Counter
P=Path('runtime/acceptance/rega-ready-promotion-20260907/candidates.jsonl')
def test_locals():
    rows=[json.loads(x) for x in P.read_text().splitlines() if x.strip()]
    locals=Counter(r['email'].split('@',1)[0] for r in rows)
    print(json.dumps({'count':len(rows),'locals':dict(sorted(locals.items()))},ensure_ascii=False,indent=2))
    assert rows
