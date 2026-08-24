from __future__ import annotations

from email import policy
from email.parser import BytesParser
from pathlib import Path
from types import SimpleNamespace

import pytest

import career_engine.gmail as gmail
from career_engine.gmail import build_application_message


def test_application_message_uses_one_selected_cv_attachment(tmp_path: Path) -> None:
    selected_cv = tmp_path / "Abdelhamid_Farah_CV_Senior_Design_Manager.pdf"
    selected_cv.write_bytes(b"%PDF-1.4\nselected-cv\n")

    raw = build_application_message(
        recipient="careers@example.com",
        subject="Abdelhamid Farah - Senior Design Manager",
        body="Dear Hiring Manager,\n\nPlease find my CV attached.\n\nKind regards,\nAbdelhamid Farah",
        pdf_path=selected_cv,
        sender="hameedfarah@gmail.com",
    )
    message = BytesParser(policy=policy.default).parsebytes(raw)
    attachments = [part for part in message.walk() if part.get_filename()]

    assert message["From"] == "hameedfarah@gmail.com"
    assert message["To"] == "careers@example.com"
    assert message["Subject"] == "Abdelhamid Farah - Senior Design Manager"
    assert len(attachments) == 1
    assert attachments[0].get_filename() == selected_cv.name


def test_application_message_rejects_other_sender(tmp_path: Path) -> None:
    selected_cv = tmp_path / "cv.pdf"
    selected_cv.write_bytes(b"%PDF-1.4\n")
    with pytest.raises(ValueError, match="hameedfarah@gmail.com"):
        build_application_message(
            recipient="careers@example.com",
            subject="Abdelhamid Farah - Design Manager",
            body="Application body",
            pdf_path=selected_cv,
            sender="other@example.com",
        )


def test_application_message_rejects_missing_recipient(tmp_path: Path) -> None:
    selected_cv = tmp_path / "cv.pdf"
    selected_cv.write_bytes(b"%PDF-1.4\n")
    with pytest.raises(ValueError, match="verified application recipient"):
        build_application_message(
            recipient="",
            subject="Abdelhamid Farah - Design Manager",
            body="Application body",
            pdf_path=selected_cv,
            sender="hameedfarah@gmail.com",
        )


def test_large_draft_payload_uses_gmail_rest_not_gws_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def fake_api(method, url, payload, *, timeout=120):
        calls.append((method, url, payload, timeout))
        return {"id": "draft-large", "message": {"id": "message-large"}}

    monkeypatch.setattr(gmail, "_gmail_api_json", fake_api)
    monkeypatch.setattr(gmail, "run_gws", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("gws argv path must not be used")))
    saved, action = gmail._save_draft_payload("x" * 40000)
    assert action == "created"
    assert saved["id"] == "draft-large"
    assert calls[0][0] == "POST"
    assert calls[0][1].endswith("/users/me/drafts")
    assert calls[0][2]["message"]["raw"] == "x" * 40000

def test_large_send_payload_uses_gmail_rest_not_gws_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    monkeypatch.setattr(gmail, "_gmail_api_json", lambda method, url, payload, **kwargs: calls.append((method, url, payload)) or {"id": "sent-large"})
    monkeypatch.setattr(gmail, "run_gws", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("large send must not use gws argv")))
    result = gmail.send_application_message(b"x" * 40000)
    assert result["id"] == "sent-large"
    assert calls[0][0:2] == ("POST", "https://gmail.googleapis.com/gmail/v1/users/me/messages/send")
    assert "raw" in calls[0][2]


def test_large_existing_draft_payload_uses_rest_update(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def fake_api(method, url, payload, *, timeout=120):
        calls.append((method, url, payload))
        return {"id": "draft-existing"}

    monkeypatch.setattr(gmail, "_gmail_api_json", fake_api)
    saved, action = gmail._save_draft_payload("x" * 40000, existing_draft_id="draft-existing")
    assert action == "updated"
    assert saved["id"] == "draft-existing"
    assert calls[0][0] == "PUT"
    assert calls[0][1].endswith("/users/me/drafts/draft-existing")


def test_small_draft_payload_keeps_gws_path(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = []

    def fake_gws(args, *, timeout=120):
        seen.append(args)
        return {"id": "draft-small"}

    monkeypatch.setattr(gmail, "run_gws", fake_gws)
    monkeypatch.setattr(gmail, "_gmail_api_json", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("REST not expected")))
    saved, action = gmail._save_draft_payload("small")
    assert action == "created"
    assert saved["id"] == "draft-small"
    assert seen[0][:3] == ["gmail", "users", "drafts"]


def test_gws_oauth_export_failure_never_echoes_secret_values(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "sensitive-refresh-token-value"
    monkeypatch.setattr(
        gmail.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout=secret, stderr=secret),
    )
    with pytest.raises(RuntimeError) as exc_info:
        gmail._gws_oauth_credentials()
    assert secret not in str(exc_info.value)


def test_verify_draft_normalizes_transport_header_whitespace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    selected_cv = tmp_path / "cv.pdf"
    selected_cv.write_bytes(b"%PDF-1.4\nselected-cv\n")
    body = "Application body"
    raw = build_application_message(
        recipient="careers@example.com",
        subject="Long Subject For Transport Folding",
        body=body,
        pdf_path=selected_cv,
        sender="hameedfarah@gmail.com",
    )
    raw = raw.replace(
        b"Subject: Long Subject For Transport Folding",
        b"Subject:  Long Subject For Transport Folding",
        1,
    )
    encoded = gmail._b64url_encode(raw)
    monkeypatch.setattr(
        gmail,
        "run_gws",
        lambda *args, **kwargs: {
            "id": "draft-1",
            "message": {"id": "message-1", "raw": encoded, "labelIds": ["DRAFT"]},
        },
    )
    result = gmail.verify_draft(
        "draft-1",
        expected_recipient="careers@example.com",
        expected_sender="hameedfarah@gmail.com",
        expected_subject="Long Subject For Transport Folding",
        expected_body=body,
        expected_pdf=selected_cv,
        recipient_source="https://example.com/careers",
    )
    assert result["verified"] is True
    assert result["subject"] == "Long Subject For Transport Folding"
    assert result["sent"] is False
