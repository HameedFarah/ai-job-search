#!/usr/bin/env python3
"""
Reconstruct Balady engineering-office source registry from the official directory.
Uses the least-request method: POST to LoadData with XSRF token from the page.
Cap at 2000 rows per request → 3 requests total for ~5,374 offices.
No detail-page crawling, no enrichment, no mail.
"""
import csv
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

# ── Configuration ──────────────────────────────────────────────────────────
SOURCE_URL = "https://apps.balady.gov.sa/Eservices/Inquiries/InquiryEngOffices"
LOAD_DATA_URL = SOURCE_URL + "/LoadData"
ACCEPTANCE_DIR = Path(
    "/home/hameedo/projects/ai-job-search-daily-runtime/runtime/acceptance"
)
PAGE_SIZE = 2000
MAX_REQUESTS = 5  # safety cap
TIMEOUT = 60  # seconds per request

# ── Helper: extract XSRF token from cached HTML ────────────────────────────
def load_xsrf_token():
    html_path = Path("/tmp/balady_page.html")
    if not html_path.exists():
        print("ERROR: /tmp/balady_page.html not found — page not scraped.", file=sys.stderr)
        sys.exit(1)
    text = html_path.read_text(encoding="utf-8")
    import re
    # Token is in inline JS: xhr.setRequestHeader("X-XSRF-TOKEN", "TOKEN_VALUE")
    m = re.search(r'setRequestHeader\s*\(\s*"X-XSRF-TOKEN"\s*,\s*"([^"]+)"', text)
    if m:
        return m.group(1)
    print("ERROR: XSRF token not found in page HTML.", file=sys.stderr)
    sys.exit(1)


def fetch_page(session, token, start, page_num):
    """Fetch one page of records. Returns (data_list, recordsFiltered)."""
    resp = session.post(
        LOAD_DATA_URL,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Requested-With": "XMLHttpRequest",
            "X-XSRF-TOKEN": token,
            "Referer": SOURCE_URL,
        },
        data={
            "draw": str(page_num),
            "start": str(start),
            "length": str(PAGE_SIZE),
            "regionId": "",
            "cityId": "",
            "textSearch": "",
            "activity": "",
        },
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    body = resp.json()
    if "errorList" in body:
        raise RuntimeError(f"Server error: {body['errorList']}")
    records_filtered = body.get("recordsFiltered", 0)
    data = body.get("data", [])
    print(f"  Page {page_num}: start={start}, got {len(data)} rows (filtered: {records_filtered})", flush=True)
    return data, records_filtered


def main():
    token = load_xsrf_token()
    session = requests.Session()
    session.headers.update({"User-Agent": "Balady-source-recovery-agent"})

    print(f"Fetching Balady engineering-office directory from {SOURCE_URL}", flush=True)
    all_records = []
    records_filtered = None

    for page_num in range(1, MAX_REQUESTS + 1):
        start = (page_num - 1) * PAGE_SIZE
        try:
            data, records_filtered = fetch_page(session, token, start, page_num)
        except requests.RequestException as e:
            print(f"  Request error on page {page_num}: {e}", file=sys.stderr)
            print(f"  PARTIAL: collected {len(all_records)} rows so far.", file=sys.stderr)
            break
        if not data:
            print(f"  No more data at start={start}.", flush=True)
            break
        all_records.extend(data)
        if len(data) < PAGE_SIZE:
            break  # last page
        if page_num < MAX_REQUESTS:
            time.sleep(0.3)  # respect rate limits

    total_fetched = len(all_records)
    print(f"\nTotal records fetched: {total_fetched} (server says filtered: {records_filtered})", flush=True)

    # Deduplicate by OfficeId (primary key)
    seen_ids = set()
    unique = []
    dups = 0
    for rec in all_records:
        oid = rec.get("OfficeId", "")
        if oid and oid not in seen_ids:
            seen_ids.add(oid)
            unique.append(rec)
        else:
            dups += 1
    print(f"Unique offices: {len(unique)}, duplicates removed: {dups}", flush=True)

    # Build structured output
    now_utc = datetime.now(timezone.utc).isoformat()
    records_out = []
    for rec in unique:
        hashed = rec.get("HashedOfficeId", "")
        # Create a deterministic hash for OfficeId for privacy
        office_hash = hashlib.sha256((rec.get("OfficeId", "") + "|balady").encode()).hexdigest()[:16]
        records_out.append({
            "OfficeId": rec.get("OfficeId", ""),
            "HashedOfficeId": hashed,
            "OfficeIdHash": office_hash,
            "OfficeName": rec.get("OfficeName", ""),
            "MobileNo": rec.get("MobileNo") or None,
            "ClassificationGrade": rec.get("ClassificationGrade") or None,
            "ClassificationStatus": rec.get("ClassificationStatus") or None,
            "X": rec.get("X") or None,
            "Y": rec.get("Y") or None,
            "LogoUrl": rec.get("LogoUrl") or None,
            "retrieved_at_utc": now_utc,
        })

    # ── Write artifacts ──────────────────────────────────────────────────
    ACCEPTANCE_DIR.mkdir(parents=True, exist_ok=True)

    # CSV — the main portable artifact
    csv_path = ACCEPTANCE_DIR / "balady_engineering_offices_source.csv"
    fieldnames = [
        "OfficeId", "HashedOfficeId", "OfficeIdHash", "OfficeName",
        "MobileNo", "ClassificationGrade", "ClassificationStatus",
        "X", "Y", "LogoUrl", "retrieved_at_utc",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(records_out)
    print(f"CSV written: {csv_path} ({csv_path.stat().st_size:,} bytes)", flush=True)

    # JSON — full structured artifact
    json_path = ACCEPTANCE_DIR / "balady_engineering_offices_source.json"
    payload = {
        "metadata": {
            "source_url": SOURCE_URL,
            "load_data_endpoint": LOAD_DATA_URL,
            "retrieved_at_utc": now_utc,
            "total_server_records": records_filtered,
            "records_fetched": total_fetched,
            "records_unique": len(records_out),
            "page_size": PAGE_SIZE,
            "pages_requested": (total_fetched + PAGE_SIZE - 1) // PAGE_SIZE,
            "method": "POST LoadData with XSRF token; directory list only",
            "fields": fieldnames,
        },
        "records": records_out,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"JSON written: {json_path} ({json_path.stat().st_size:,} bytes)", flush=True)

    # Metadata summary
    meta_path = ACCEPTANCE_DIR / "balady_engineering_offices_source_metadata.json"
    meta_path.write_text(json.dumps(payload["metadata"], ensure_ascii=False, indent=2), encoding="utf-8")

    # ── Report ───────────────────────────────────────────────────────────
    print(f"\n{'='*60}", flush=True)
    print(f"RECOVERY SUMMARY", flush=True)
    print(f"{'='*60}", flush=True)
    print(f"Source URL:            {SOURCE_URL}", flush=True)
    print(f"Server records:        {records_filtered}", flush=True)
    print(f"Records fetched:       {total_fetched}", flush=True)
    print(f"Unique OfficeIds:      {len(records_out)}", flush=True)
    print(f"Historical baseline:   5,283", flush=True)
    print(f"Difference:            {len(records_out) - 5283:+d} ({(len(records_out)/5283-1)*100:.1f}%)", flush=True)
    print(f"Retrieved at:          {now_utc}", flush=True)
    print(f"Artifacts:", flush=True)
    print(f"  {csv_path}", flush=True)
    print(f"  {json_path}", flush=True)
    print(f"  {meta_path}", flush=True)
    print(f"{'='*60}", flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
