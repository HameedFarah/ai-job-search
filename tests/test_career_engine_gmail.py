from __future__ import annotations

from email import policy
from email.parser import BytesParser
from pathlib import Path

import pytest

from career_engine.gmail import build_application_message


def test_application_message_uses_one_selected_cv_attachment(tmp_path: Path) -> None:
    selected_cv = tmp_path / "Abdelhamid_Farah_CV_Senior_Design_Manager.pdf"
    selected_cv.write_bytes(b"%PDF-1.4\nselected-cv\n")

    raw = build_application_message(
        recipient="careers@example.com",
        subject="Abdelhamid Farah - Senior Design Manager",
        body="Dear Hiring Manager,\n\nPlease find my CV attached.\n\nKind regards,\nAbdelhamid Farah",
        pdf_path=selected_cv,
        sender="hameedo@gmail.com",
    )
    message = BytesParser(policy=policy.default).parsebytes(raw)
    attachments = [part for part in message.walk() if part.get_filename()]

    assert message["From"] == "hameedo@gmail.com"
    assert message["To"] == "careers@example.com"
    assert message["Subject"] == "Abdelhamid Farah - Senior Design Manager"
    assert len(attachments) == 1
    assert attachments[0].get_filename() == selected_cv.name


def test_application_message_rejects_other_sender(tmp_path: Path) -> None:
    selected_cv = tmp_path / "cv.pdf"
    selected_cv.write_bytes(b"%PDF-1.4\n")
    with pytest.raises(ValueError, match="hameedo@gmail.com"):
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
            sender="hameedo@gmail.com",
        )
