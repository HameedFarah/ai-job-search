import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pytest

from career_engine.gmail import _b64url_encode
from runtime.outreach_campaign_controller import (
    _seconds_until_window,
    _verify_message_payload,
    build_raw,
    confirmation_token,
    load_queue,
)


def _item(tmp_path: Path) -> dict:
    attachments = []
    for name, body in (("cv.pdf", b"%PDF-cv"), ("portfolio.pdf", b"%PDF-portfolio")):
        path = tmp_path / name
        path.write_bytes(body)
        attachments.append({
            "filename": name,
            "path": str(path),
            "sha256": hashlib.sha256(body).hexdigest(),
            "size_bytes": len(body),
        })
    return {
        "queue_id": "q1",
        "email": "jobs@example.com",
        "sender": "hameedfarah@gmail.com",
        "subject": "Abdelhamid Farah | Senior Design & Project Leadership",
        "body": "Dear Hiring Team,\nBody\n",
        "attachments": attachments,
    }


def test_build_and_verify_two_attachment_mime(tmp_path):
    item = _item(tmp_path)
    raw = build_raw(item)
    verified = _verify_message_payload({"id": "m1", "threadId": "t1", "raw": _b64url_encode(raw), "labelIds": ["DRAFT"]}, item, require_sent=False)
    assert verified["sender"] == "hameedfarah@gmail.com"
    assert verified["recipient"] == "jobs@example.com"
    assert verified["attachment_count"] == 2


def test_queue_requires_two_hashed_pdfs_and_unique_identity(tmp_path):
    import json
    item = _item(tmp_path)
    queue = tmp_path / "queue.json"
    queue.write_text(json.dumps({"queue": [item]}), encoding="utf-8")
    assert load_queue(queue)[0]["queue_id"] == "q1"
    item["attachments"][0]["sha256"] = "0" * 64
    queue.write_text(json.dumps({"queue": [item]}), encoding="utf-8")
    with pytest.raises(RuntimeError):
        load_queue(queue)


def test_confirmation_token_is_bound_to_queue_bytes(tmp_path):
    queue = tmp_path / "queue.json"
    queue.write_text('{"queue":[]}', encoding="utf-8")
    first = confirmation_token(queue)
    queue.write_text('{"queue":[1]}', encoding="utf-8")
    assert first != confirmation_token(queue)


def test_riyadh_send_window():
    # 08:00 UTC = 11:00 Riyadh.
    inside = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
    before = datetime(2026, 9, 1, 7, 30, tzinfo=timezone.utc)
    after = datetime(2026, 9, 1, 18, 0, tzinfo=timezone.utc)
    assert _seconds_until_window(inside, 11, 20) == 0
    assert 1700 <= _seconds_until_window(before, 11, 20) <= 1900
    assert _seconds_until_window(after, 11, 20) > 0
