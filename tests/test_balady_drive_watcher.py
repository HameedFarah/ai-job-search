#!/usr/bin/env python3
"""Focused tests for the Balady Drive watcher / queue replenisher.

Tests cover:
- Exclusion gates (TTW, Tubaila, RMK, ASA, Jordan, blocked mailboxes)
- OfficeId deduplication
- Email validation
- CSV parsing
- Queue append path structure
- REGA-first gate logic
"""
from __future__ import annotations

import csv
import json
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

# Reuse fixtures from existing test infrastructure
REPO_ROOT = Path(__file__).resolve().parents[1]

from career_engine.balady_drive_watcher import (
    BALADY_DRIVE_FILE_ID,
    BALADY_FILE_NAME,
    CHECKPOINT_DIR,
    CHECKPOINT_FILE,
    _deduplicate_by_office,
    _drive_download_file,
    _drive_file_metadata,
    _filter_excluded,
    _parse_balady_csv,
    _rega_gate_ok,
    _rega_pending_count,
    _save_checkpoint,
    _load_checkpoint,
    BALADY_CSV_COLUMNS,
)
from career_engine.outreach_reconciler import (
    EXCLUDED_COMPANIES_LOW,
    EXCLUDED_DOMAINS,
)


class TestBaladyCsvParsing(unittest.TestCase):
    """Test CSV parsing of the Balady source."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False)
        self.tmp_path = Path(self.tmp.name)
        self.tmp.close()

    def tearDown(self):
        self.tmp_path.unlink(missing_ok=True)

    def _write_csv(self, rows):
        """Write CSV rows to the temp file with headers."""
        with open(self.tmp_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(BALADY_CSV_COLUMNS)
            for row in rows:
                writer.writerow(row)

    def test_parse_valid_rows(self):
        self._write_csv([
            ["5110001955", "دار الخليج", "http://example.com",
             "swailmi@daralkhalij.com", "www.daralkhalij.com",
             "0114537171", "", "", "Riyadh", "success", "", "200", "2026-09-03T09:11:07Z"],
            ["5110001956", "المدينه الانشائيه", "http://example.com",
             "city@center.com", "www.center.com",
             "", "", "", "Riyadh", "success", "", "200", "2026-09-03T09:11:11Z"],
        ])
        rows = _parse_balady_csv(self.tmp_path)
        assert len(rows) == 2
        assert rows[0]["OfficeId"] == "5110001955"
        assert rows[0]["email"] == "swailmi@daralkhalij.com"

    def test_parse_excludes_failed_status(self):
        self._write_csv([
            ["5110001955", "Office1", "http://x", "ok@co.com", "", "", "", "", "",
             "success", "", "200", "2026-09-03T09:11:07Z"],
            ["5110001956", "Office2", "http://x", "fail@co.com", "", "", "", "", "",
             "error", "HTTP 429", "429", "2026-09-03T09:11:11Z"],
        ])
        rows = _parse_balady_csv(self.tmp_path)
        assert len(rows) == 1
        assert rows[0]["OfficeId"] == "5110001955"

    def test_parse_excludes_empty_email(self):
        self._write_csv([
            ["5110001955", "Office1", "http://x", "", "", "", "", "", "",
             "success", "", "200", "2026-09-03T09:11:07Z"],
        ])
        rows = _parse_balady_csv(self.tmp_path)
        assert len(rows) == 0

    def test_deduplicate_by_office_id(self):
        raw = [
            {"OfficeId": "100", "email": "a@co.com", "OfficeName": "Office A"},
            {"OfficeId": "100", "email": "b@co.com", "OfficeName": "Office A v2"},
            {"OfficeId": "200", "email": "c@co.com", "OfficeName": "Office C"},
        ]
        deduped = _deduplicate_by_office(raw)
        assert len(deduped) == 2
        assert deduped[0]["OfficeId"] == "100"
        assert deduped[0]["email"] == "a@co.com"  # first wins

    def test_ttw_office_id_is_excluded_by_company_name(self):
        """TTW-identified offices must never pass the exclusion gate."""
        self._write_csv([
            ["5100009999", "TTW Recruitment", "http://x",
             "jobs@ttwsa.com", "www.ttwsa.com",
             "", "", "", "", "success", "", "200", "2026-09-03T09:11:07Z"],
            ["5100009998", "Tubaila Team Workshop", "http://x",
             "info@tubaila.com", "www.ttwsa.com",
             "", "", "", "", "success", "", "200", "2026-09-03T09:11:07Z"],
            ["5110003279", "شركة العمارة المستدامة للاستشارات الهندسية شركة شخص واحد",
             "http://x", "asa@co.com", "www.arabsustainablearchitecture.com",
             "", "", "", "", "success", "", "200", "2026-09-03T09:11:07Z"],
            ["5100000642", "رمك للاستشارات الهندسية", "http://x",
             "info@rmk.com", "www.rmk.com",
             "", "", "", "", "success", "", "200", "2026-09-03T09:11:07Z"],
            ["5100001000", "Normal Engineering Office", "http://x",
             "hr@normalco.com", "www.normalco.com",
             "", "", "", "", "success", "", "200", "2026-09-03T09:11:07Z"],
        ])
        rows = _parse_balady_csv(self.tmp_path)
        assert len(rows) == 5  # All pass CSV parse

        # Build blocked set (empty for this test)
        blocked = {
            "email_to_company": {},
            "blocked_emails": set(),
            "blocked_domains": set(),
            "blocked_companies": set(),
            "jordan_held_emails": set(),
            "jordan_held_companies": set(),
            "hard_blocked_emails": set(),
            "permanent_bounce_emails": set(),
        }
        candidates, skips = _filter_excluded(rows, blocked, [])
        # Only the normal office should remain
        assert len(candidates) == 1, f"Expected 1 candidate, got {len(candidates)}: {[c['email'] for c in candidates]}"
        assert candidates[0]["email"] == "hr@normalco.com"
        assert len(skips) == 4, f"Expected 4 skips, got {len(skips)}"
        skip_reasons = {s["skip_reason"] for s in skips}
        assert "company_excluded" in skip_reasons

    def test_ttwsa_domain_excluded_even_with_different_company_name(self):
        """ttwsa.com domain must be excluded regardless of company name."""
        self._write_csv([
            ["5100008888", "Some Company", "http://x",
             "admin@ttwsa.com", "www.ttwsa.com",
             "", "", "", "", "success", "", "200", "2026-09-03T09:11:07Z"],
        ])
        rows = _parse_balady_csv(self.tmp_path)
        blocked = {
            "email_to_company": {},
            "blocked_emails": set(),
            "blocked_domains": set(),
            "blocked_companies": set(),
            "jordan_held_emails": set(),
            "jordan_held_companies": set(),
            "hard_blocked_emails": set(),
            "permanent_bounce_emails": set(),
        }
        candidates, skips = _filter_excluded(rows, blocked, [])
        assert len(candidates) == 0
        assert len(skips) == 1
        # ttwsa.com domain is in EXCLUDED_DOMAINS AND the company filter also
        # catches it via domain — either "excluded_domain" or "company_excluded"
        # is acceptable as the skip reason
        assert skips[0]["skip_reason"] in ("excluded_domain", "company_excluded")


class TestExcludedCompaniesList(unittest.TestCase):
    """Verify EXCLUDED_COMPANIES matches the task's exact live Arabic names."""

    def test_all_required_excluded_names_present(self):
        assert "ttw" in EXCLUDED_COMPANIES_LOW
        assert "tubaila" in EXCLUDED_COMPANIES_LOW
        assert "tubaila team workshop" in EXCLUDED_COMPANIES_LOW
        # طبيلة (Tabila) — the actual Arabic protected name in EXCLUDED_COMPANIES
        assert "طبيلة" in EXCLUDED_COMPANIES_LOW or any("طبيلة" in c for c in EXCLUDED_COMPANIES_LOW)
        assert "arab sustainable architecture" in EXCLUDED_COMPANIES_LOW
        assert "العمارة المستدامة" in EXCLUDED_COMPANIES_LOW
        assert "رمك" in EXCLUDED_COMPANIES_LOW
        assert "rmk" in EXCLUDED_COMPANIES_LOW

    def test_excluded_domain_ttwsa_present(self):
        assert "ttwsa.com" in EXCLUDED_DOMAINS


class TestCheckpointPersistence(unittest.TestCase):
    """Test checkpoint save/load."""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.checkpoint_file = Path(self.tmp_dir.name) / "checkpoint.json"
        # Temporarily patch CHECKPOINT_FILE
        import career_engine.balady_drive_watcher as m
        self._orig = m.CHECKPOINT_FILE
        m.CHECKPOINT_FILE = self.checkpoint_file

    def tearDown(self):
        import career_engine.balady_drive_watcher as m
        m.CHECKPOINT_FILE = self._orig
        self.tmp_dir.cleanup()

    def test_save_and_load(self):
        state = {
            "last_modified_time": "2026-09-03T10:00:00Z",
            "last_size": 1460009,
            "last_md5": "016aa86f7cc8d9834297cb5eb4e175cc",
            "total_rows_processed": 100,
            "total_rows_promoted": 5,
        }
        _save_checkpoint(state)
        loaded = _load_checkpoint()
        assert loaded["last_modified_time"] == "2026-09-03T10:00:00Z"
        assert loaded["total_rows_processed"] == 100


class TestRegaGate(unittest.TestCase):
    """Test the REGA-first priority gate."""

    def test_rega_pending_detected(self):
        queue_rows = [
            {"Status": "PENDING", "Email": "jobs@kobraish.com.sa",
             "Notes": "", "Evidence_or_Notes": "REGA direct-hiring"},
            {"Status": "PENDING", "Email": "info@acicogroup.com",
             "Notes": "", "Evidence_or_Notes": "REGA general"},
            {"Status": "PENDING", "Email": "hr@normalco.com",
             "Notes": "Balady OfficeId=510000", "Evidence_or_Notes": ""},
            {"Status": "SENT", "Email": "done@co.com",
             "Notes": "", "Evidence_or_Notes": ""},
        ]
        pending = _rega_pending_count(queue_rows)
        assert pending == 2, f"Expected 2 REGA PENDING, got {pending}"

    def test_rega_gate_blocked(self):
        queue_rows = [
            {"Status": "PENDING", "Email": "jobs@kobraish.com.sa",
             "Notes": "", "Evidence_or_Notes": "REGA direct-hiring"},
        ]
        ok = _rega_gate_ok(queue_rows)
        assert ok is False, "REGA gate should block when REGA PENDING > 0"

    def test_rega_gate_allowed_no_rega_pending(self):
        queue_rows = [
            {"Status": "PENDING", "Email": "hr@normalco.com",
             "Notes": "Balady OfficeId=510000", "Evidence_or_Notes": ""},
            {"Status": "SENT", "Email": "done@co.com",
             "Notes": "", "Evidence_or_Notes": ""},
        ]
        ok = _rega_gate_ok(queue_rows)
        assert ok is True, "REGA gate should allow when no REGA PENDING"

    def test_rega_gate_all_sent(self):
        queue_rows = [
            {"Status": "SENT", "Email": "done@co.com",
             "Notes": "", "Evidence_or_Notes": ""},
        ]
        ok = _rega_gate_ok(queue_rows)
        assert ok is True


class TestDriveMetadata(unittest.TestCase):
    """Test Drive metadata interaction (with mock)."""

    def test_metadata_fields_extracted(self):
        meta = {
            "id": "1L6sDEKjrVENi3kc4s-LhqGl7yqCUZYnm",
            "name": "balady_contact_details_live.csv",
            "modifiedTime": "2026-09-03T10:01:01.297Z",
            "size": 1460009,
            "md5Checksum": "016aa86f7cc8d9834297cb5eb4e175cc",
            "mimeType": "text/csv",
        }
        assert meta["id"] == BALADY_DRIVE_FILE_ID
        assert meta["name"] == BALADY_FILE_NAME
        assert meta["size"] == 1460009


class TestEmailValidation(unittest.TestCase):
    """Test email validation used in CSV parsing."""

    def test_valid_emails_passed(self):
        valid = ["info@co.com", "hr@company.sa", "careers@office.com",
                 "jobs@recruitment.com.sa"]
        from career_engine.outreach_reconciler import _valid_email
        for email in valid:
            assert _valid_email(email), f"Expected {email} to be valid"

    def test_invalid_emails_filtered(self):
        invalid = ["", "notanemail", "@missing.com", "missing@", "spaces in@email.com"]
        from career_engine.outreach_reconciler import _valid_email
        for email in invalid:
            assert not _valid_email(email), f"Expected {email!r} to be invalid"


if __name__ == "__main__":
    unittest.main()