"""Persistent deterministic search cache keyed by query_id."""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CACHE_VERSION = 1

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

def default_cache_dir() -> Path:
    # Repo-local cache, gitignored but persistent; fallback to /tmp if not writable
    repo_root = Path(__file__).resolve().parents[2]
    p = repo_root / ".cache" / "rega-search-cache"
    try:
        p.mkdir(parents=True, exist_ok=True)
        return p
    except Exception:
        return Path("/tmp/rega-search-cache")

def _query_normalized(query: str) -> str:
    # Normalized for cache key stability: lower, strip, collapse whitespace
    return " ".join(query.strip().lower().split())

def cache_path_for(query_id: str, cache_dir: Path | None = None) -> Path:
    d = cache_dir or default_cache_dir()
    # query_id is already company_id:hash, safe for filename
    safe = query_id.replace(":", "_").replace("/", "_")
    return d / f"{safe}.json"

def load_cached(query_id: str, normalized_query: str, cache_dir: Path | None = None) -> dict[str, Any] | None:
    p = cache_path_for(query_id, cache_dir)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        # Validate cache version and normalized query matches (detect query text change)
        if data.get("cache_version") != CACHE_VERSION:
            return None
        if data.get("normalized_query") != _query_normalized(normalized_query):
            return None
        return data
    except Exception:
        return None

def store_cache(
    query_id: str,
    company_id: str,
    license_no: str,
    query_text: str,
    normalized_query: str,
    backend: str,
    results: list[dict[str, Any]],
    cache_dir: Path | None = None,
) -> Path:
    d = cache_dir or default_cache_dir()
    d.mkdir(parents=True, exist_ok=True)
    p = cache_path_for(query_id, d)
    payload = {
        "cache_version": CACHE_VERSION,
        "query_id": query_id,
        "company_id": company_id,
        "license_no": license_no,
        "query_text": query_text,
        "normalized_query": _query_normalized(normalized_query),
        "backend": backend,
        "results": results,
        "result_count": len(results),
        "retrieved_at": utc_now(),
        "retrieved_at_epoch": int(time.time()),
    }
    # Atomic write
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.rename(p)
    return p

def cache_stats(cache_dir: Path | None = None) -> dict[str, Any]:
    d = cache_dir or default_cache_dir()
    if not d.is_dir():
        return {"hits": 0, "misses": 0, "files": 0, "dir": str(d)}
    files = list(d.glob("*.json"))
    return {"files": len(files), "dir": str(d)}

def clear_cache(cache_dir: Path | None = None) -> int:
    d = cache_dir or default_cache_dir()
    if not d.is_dir():
        return 0
    n = 0
    for p in d.glob("*.json"):
        try:
            p.unlink()
            n += 1
        except Exception:
            pass
    return n
