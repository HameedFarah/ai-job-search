#!/usr/bin/env python3
"""Career Engine shared tracker.

Canonical paths are relative to this file's directory:
  data/jobs.csv
  data/jobs/<job-id>.json
  logs/events.jsonl
  artifacts/<job-id>/

The event log is append-only. Material changes require a non-empty comment.
Stdlib only.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from career_engine import safety as career_safety

CSV_FIELDS = [
    "job_id", "source", "external_job_id", "source_url", "company", "role",
    "location", "posting_date", "closing_date", "jd_hash", "full_jd_path",
    "first_seen", "last_seen", "ingested_by", "fit_score", "priority", "owner",
    "processing_status", "resume_status", "cover_letter_status", "pdf_status",
    "gmail_draft_status", "application_status", "outcome", "last_updated",
    "next_action", "notes",
]

EVENT_FIELDS = [
    "event_id", "timestamp", "actor", "entity_type", "entity_id", "action",
    "before", "after", "comment", "source_refs", "confidence",
    "requires_owner_review",
]

ACTORS = {"chatgpt", "hermes", "owner", "system"}
ENTITY_TYPES = {"job", "resume", "claim", "contact", "application", "connector", "system"}
ACTIONS = {"created", "updated", "reviewed", "rejected", "approved", "queued", "generated", "drafted", "failed", "retried"}
CONFIDENCE = {"high", "medium", "low"}
JSON_SECTIONS = {
    "full_job_description", "normalized_requirements", "provenance", "scoring",
    "evidence_matches", "processing_state", "generated_artifacts",
    "gmail_draft_reference", "submission_package", "resume_template_override",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def hash_job_description(value: str) -> str:
    return hashlib.sha256(normalize_text(value).lower().encode("utf-8")).hexdigest()


_LINKEDIN_SOURCE_ALIASES = {
    "linkedin",
    "linkedin-alert",
    "linkedin-alerts",
    "linkedin-public",
    "linkedin-public-search",
    "linkedin-search",
    "gmail-linkedin-alert",
}
_LINKEDIN_JOB_URL_RE = re.compile(
    r"https?://(?:[a-z]{2}\.)?(?:www\.)?linkedin\.com/jobs/view/(?:[^/?#]*-)?(?P<job_id>\d+)(?:[/?#]|$)",
    re.IGNORECASE,
)


def canonical_source_identity(source: str) -> str:
    """Return the identity namespace used for dedupe without rewriting provenance."""
    normalized = re.sub(r"[\s_]+", "-", normalize_text(source).lower())
    return "linkedin" if normalized in _LINKEDIN_SOURCE_ALIASES else normalized


def canonical_url_identity(source_url: str) -> str:
    """Normalize harmless URL variants while keeping different vacancies separate."""
    value = normalize_text(source_url)
    match = _LINKEDIN_JOB_URL_RE.search(value)
    if match:
        return f"linkedin-job:{match.group('job_id')}"
    return value.rstrip("/")


def stable_job_id(source: str, external_job_id: str, source_url: str, jd_hash: str) -> str:
    identity = external_job_id.strip() or canonical_url_identity(source_url) or "no-external-id"
    material = f"{canonical_source_identity(source)}|{identity}|{jd_hash}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:20]


def require_comment(comment: str) -> str:
    value = (comment or "").strip()
    if not value:
        raise ValueError("Every material edit requires a non-empty comment")
    return value


def validate_event_values(actor: str, entity_type: str, action: str, confidence: str) -> None:
    if actor not in ACTORS:
        raise ValueError(f"actor must be one of {sorted(ACTORS)}")
    if entity_type not in ENTITY_TYPES:
        raise ValueError(f"entity_type must be one of {sorted(ENTITY_TYPES)}")
    if action not in ACTIONS:
        raise ValueError(f"action must be one of {sorted(ACTIONS)}")
    if confidence not in CONFIDENCE:
        raise ValueError(f"confidence must be one of {sorted(CONFIDENCE)}")


class CareerTracker:
    def __init__(self, base_dir: Path | str | None = None):
        self.base_dir = Path(base_dir) if base_dir else Path(__file__).resolve().parent
        self.data_dir = self.base_dir / "data"
        self.jobs_dir = self.data_dir / "jobs"
        self.logs_dir = self.base_dir / "logs"
        self.artifacts_dir = self.base_dir / "artifacts"
        self.csv_path = self.data_dir / "jobs.csv"
        self.events_path = self.logs_dir / "events.jsonl"

    def ensure_layout(self) -> None:
        for path in (self.data_dir, self.jobs_dir, self.logs_dir, self.artifacts_dir):
            path.mkdir(parents=True, exist_ok=True)
        if not self.csv_path.exists():
            with self.csv_path.open("w", newline="", encoding="utf-8") as handle:
                csv.writer(handle).writerow(CSV_FIELDS)
        if not self.events_path.exists():
            self.events_path.touch()

    def _job_path(self, job_id: str) -> Path:
        return self.jobs_dir / f"{job_id}.json"

    def _artifact_path(self, job_id: str) -> Path:
        return self.artifacts_dir / job_id

    def list_rows(self) -> list[dict[str, str]]:
        self.ensure_layout()
        with self.csv_path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != CSV_FIELDS:
                raise ValueError("jobs.csv header does not match the canonical schema")
            return list(reader)

    def get_job(self, job_id: str) -> dict[str, Any]:
        path = self._job_path(job_id)
        if not path.exists():
            raise KeyError(f"Unknown job_id: {job_id}")
        return json.loads(path.read_text(encoding="utf-8"))

    def read_events(self, entity_id: str | None = None) -> list[dict[str, Any]]:
        self.ensure_layout()
        events: list[dict[str, Any]] = []
        for raw in self.events_path.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            event = json.loads(raw)
            if list(event.keys()) != EVENT_FIELDS:
                raise ValueError("events.jsonl contains a non-canonical event schema")
            if entity_id is None or event["entity_id"] == entity_id:
                events.append(event)
        return events

    def _atomic_json_write(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temp, path)

    def _write_rows(self, rows: list[dict[str, Any]]) -> None:
        self.ensure_layout()
        temp = self.csv_path.with_suffix(".csv.tmp")
        with temp.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({field: row.get(field, "") for field in CSV_FIELDS})
        os.replace(temp, self.csv_path)

    def _append_event(self, event: dict[str, Any]) -> None:
        self.ensure_layout()
        canonical = {field: event[field] for field in EVENT_FIELDS}
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(canonical, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def _make_event(
        self,
        *,
        actor: str,
        entity_type: str,
        entity_id: str,
        action: str,
        before: dict[str, Any],
        after: dict[str, Any],
        comment: str,
        source_refs: list[str] | None = None,
        confidence: str = "high",
        requires_owner_review: bool = False,
    ) -> dict[str, Any]:
        comment = require_comment(comment)
        validate_event_values(actor, entity_type, action, confidence)
        return {
            "event_id": str(uuid.uuid4()),
            "timestamp": utc_now(),
            "actor": actor,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "action": action,
            "before": before,
            "after": after,
            "comment": comment,
            "source_refs": source_refs or [],
            "confidence": confidence,
            "requires_owner_review": bool(requires_owner_review),
        }

    def _row_from_record(self, record: dict[str, Any]) -> dict[str, Any]:
        return {field: record["job"].get(field, "") for field in CSV_FIELDS}

    def _save_job_and_row(self, record: dict[str, Any]) -> None:
        self._atomic_json_write(self._job_path(record["job"]["job_id"]), record)
        rows = self.list_rows()
        new_row = self._row_from_record(record)
        replaced = False
        for index, row in enumerate(rows):
            if row["job_id"] == new_row["job_id"]:
                rows[index] = new_row
                replaced = True
                break
        if not replaced:
            rows.append(new_row)
        self._write_rows(rows)

    def _find_duplicate(self, source: str, external_job_id: str, source_url: str, jd_hash: str) -> dict[str, str] | None:
        source_identity = canonical_source_identity(source)
        url_identity = canonical_url_identity(source_url)
        for row in self.list_rows():
            external_match = (
                bool(external_job_id)
                and canonical_source_identity(row["source"]) == source_identity
                and row["external_job_id"] == external_job_id
                and row["jd_hash"] == jd_hash
            )
            url_match = (
                bool(source_url)
                and canonical_url_identity(row["source_url"]) == url_identity
                and row["jd_hash"] == jd_hash
            )
            if external_match or url_match:
                return row
        return None

    def ingest(
        self,
        payload: dict[str, Any],
        *,
        comment: str,
        actor: str = "chatgpt",
        source_refs: list[str] | None = None,
        confidence: str = "high",
    ) -> dict[str, Any]:
        comment = require_comment(comment)
        validate_event_values(actor, "job", "created", confidence)
        production_repo = Path(career_safety.__file__).resolve().parents[1]
        production_tracker = production_repo / "projects/job-automation"
        if self.base_dir.resolve() == production_tracker.resolve():
            career_safety.reject_fixture_payload(payload)
        self.ensure_layout()

        source = normalize_text(str(payload.get("source", "manual"))) or "manual"
        external_job_id = normalize_text(str(payload.get("external_job_id", "")))
        source_url = normalize_text(str(payload.get("source_url", "")))
        company = normalize_text(str(payload.get("company", "")))
        role = normalize_text(str(payload.get("role", "")))
        full_jd = normalize_text(str(payload.get("full_job_description", payload.get("job_description", ""))))
        if not company or not role or not full_jd:
            raise ValueError("company, role and full_job_description are required")

        jd_hash = hash_job_description(full_jd)
        now = utc_now()
        duplicate = self._find_duplicate(source, external_job_id, source_url, jd_hash)
        if duplicate:
            record = self.get_job(duplicate["job_id"])
            before = {"last_seen": record["job"]["last_seen"]}
            record["job"]["last_seen"] = now
            record["job"]["last_updated"] = now
            after = {"last_seen": now}
            event = self._make_event(
                actor=actor, entity_type="job", entity_id=duplicate["job_id"], action="reviewed",
                before=before, after=after, comment=comment, source_refs=source_refs,
                confidence=confidence, requires_owner_review=False,
            )
            record["history"].append(event)
            self._append_event(event)
            self._save_job_and_row(record)
            return {"result": "duplicate", "job_id": duplicate["job_id"], "record": record}

        job_id = stable_job_id(source, external_job_id, source_url, jd_hash)
        relative_json_path = f"projects/job-automation/data/jobs/{job_id}.json"
        owner = normalize_text(str(payload.get("owner", actor if actor in {"chatgpt", "hermes", "owner"} else "chatgpt"))) or "chatgpt"
        job = {
            "job_id": job_id,
            "source": source,
            "external_job_id": external_job_id,
            "source_url": source_url,
            "company": company,
            "role": role,
            "location": normalize_text(str(payload.get("location", ""))),
            "posting_date": normalize_text(str(payload.get("posting_date", ""))),
            "closing_date": normalize_text(str(payload.get("closing_date", ""))),
            "jd_hash": jd_hash,
            "full_jd_path": relative_json_path,
            "first_seen": now,
            "last_seen": now,
            "ingested_by": actor,
            "fit_score": payload.get("fit_score", ""),
            "priority": payload.get("priority", "unrated"),
            "owner": owner,
            "processing_status": payload.get("processing_status", "ingested"),
            "resume_status": payload.get("resume_status", "not_started"),
            "cover_letter_status": payload.get("cover_letter_status", "not_started"),
            "pdf_status": payload.get("pdf_status", "not_started"),
            "gmail_draft_status": payload.get("gmail_draft_status", "not_started"),
            "application_status": payload.get("application_status", "not_submitted"),
            "outcome": payload.get("outcome", ""),
            "last_updated": now,
            "next_action": payload.get("next_action", "Evaluate fit and prepare application if justified"),
            "notes": payload.get("notes", ""),
        }
        self._artifact_path(job_id).mkdir(parents=True, exist_ok=True)
        record = {
            "job": job,
            "full_job_description": full_jd,
            "normalized_requirements": payload.get("normalized_requirements", []),
            "provenance": payload.get("provenance", {"source": source, "source_url": source_url, "source_refs": source_refs or []}),
            "scoring": payload.get("scoring", {"fit_score": job["fit_score"], "rationale": [], "gaps": []}),
            "evidence_matches": payload.get("evidence_matches", []),
            "processing_state": payload.get("processing_state", {"owner": owner, "status": job["processing_status"]}),
            "generated_artifacts": payload.get("generated_artifacts", []),
            "gmail_draft_reference": payload.get("gmail_draft_reference"),
            "history": [],
        }
        event = self._make_event(
            actor=actor, entity_type="job", entity_id=job_id, action="created",
            before={}, after={"job": deepcopy(job)}, comment=comment,
            source_refs=source_refs, confidence=confidence, requires_owner_review=False,
        )
        record["history"].append(event)
        self._append_event(event)
        self._save_job_and_row(record)
        return {"result": "created", "job_id": job_id, "record": record}

    def update_job(
        self,
        job_id: str,
        changes: dict[str, Any],
        *,
        comment: str,
        actor: str = "chatgpt",
        action: str = "updated",
        source_refs: list[str] | None = None,
        confidence: str = "high",
        requires_owner_review: bool = False,
    ) -> dict[str, Any]:
        comment = require_comment(comment)
        validate_event_values(actor, "job", action, confidence)
        record = self.get_job(job_id)
        before: dict[str, Any] = {}
        after: dict[str, Any] = {}
        for key, value in changes.items():
            if key in CSV_FIELDS:
                before[key] = deepcopy(record["job"].get(key))
                record["job"][key] = value
                after[key] = deepcopy(value)
            elif key in JSON_SECTIONS:
                before[key] = deepcopy(record.get(key))
                record[key] = value
                after[key] = deepcopy(value)
            else:
                raise ValueError(f"Unsupported update field: {key}")
        if not after:
            raise ValueError("No changes supplied")
        now = utc_now()
        record["job"]["last_updated"] = now
        if "owner" in changes or "processing_status" in changes:
            processing_state = record.get("processing_state")
            if not isinstance(processing_state, dict):
                processing_state = {}
            processing_state["owner"] = record["job"]["owner"]
            processing_state["status"] = record["job"]["processing_status"]
            record["processing_state"] = processing_state
        event = self._make_event(
            actor=actor, entity_type="job", entity_id=job_id, action=action,
            before=before, after=after, comment=comment, source_refs=source_refs,
            confidence=confidence, requires_owner_review=requires_owner_review,
        )
        record["history"].append(event)
        self._append_event(event)
        self._save_job_and_row(record)
        return {"job_id": job_id, "record": record, "event": event}

    def queue_for_hermes(self, job_id: str, *, comment: str, actor: str = "chatgpt") -> dict[str, Any]:
        return self.update_job(
            job_id,
            {"owner": "hermes", "processing_status": "queued_for_hermes", "next_action": "Hermes continues processing"},
            comment=comment, actor=actor, action="queued", requires_owner_review=False,
        )

    def record_event(
        self,
        *,
        actor: str,
        entity_type: str,
        entity_id: str,
        action: str,
        before: dict[str, Any],
        after: dict[str, Any],
        comment: str,
        source_refs: list[str] | None = None,
        confidence: str = "high",
        requires_owner_review: bool = False,
    ) -> dict[str, Any]:
        event = self._make_event(
            actor=actor, entity_type=entity_type, entity_id=entity_id, action=action,
            before=before, after=after, comment=comment, source_refs=source_refs,
            confidence=confidence, requires_owner_review=requires_owner_review,
        )
        self._append_event(event)
        if entity_type == "job" and self._job_path(entity_id).exists():
            record = self.get_job(entity_id)
            record["history"].append(event)
            self._atomic_json_write(self._job_path(entity_id), record)
        return event


def parse_json_arg(raw: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Career Engine canonical shared tracker")
    parser.add_argument("--base-dir", default=str(Path(__file__).resolve().parent))
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser("ingest")
    ingest.add_argument("--payload", required=True, type=parse_json_arg)
    ingest.add_argument("--comment", required=True)
    ingest.add_argument("--actor", choices=sorted(ACTORS), default="chatgpt")

    update = sub.add_parser("update")
    update.add_argument("--job-id", required=True)
    update.add_argument("--changes", required=True, type=parse_json_arg)
    update.add_argument("--comment", required=True)
    update.add_argument("--actor", choices=sorted(ACTORS), default="chatgpt")
    update.add_argument("--action", choices=sorted(ACTIONS), default="updated")
    update.add_argument("--owner-review", action="store_true")

    queue = sub.add_parser("queue-hermes")
    queue.add_argument("--job-id", required=True)
    queue.add_argument("--comment", required=True)
    queue.add_argument("--actor", choices=sorted(ACTORS), default="chatgpt")

    listing = sub.add_parser("list")
    history = sub.add_parser("history")
    history.add_argument("--entity-id")

    event = sub.add_parser("event")
    event.add_argument("--actor", choices=sorted(ACTORS), required=True)
    event.add_argument("--entity-type", choices=sorted(ENTITY_TYPES), required=True)
    event.add_argument("--entity-id", required=True)
    event.add_argument("--action", choices=sorted(ACTIONS), required=True)
    event.add_argument("--before", default="{}", type=parse_json_arg)
    event.add_argument("--after", default="{}", type=parse_json_arg)
    event.add_argument("--comment", required=True)
    event.add_argument("--confidence", choices=sorted(CONFIDENCE), default="high")
    event.add_argument("--owner-review", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    tracker = CareerTracker(args.base_dir)
    if args.command == "ingest":
        result = tracker.ingest(args.payload, comment=args.comment, actor=args.actor)
    elif args.command == "update":
        result = tracker.update_job(args.job_id, args.changes, comment=args.comment, actor=args.actor, action=args.action, requires_owner_review=args.owner_review)
    elif args.command == "queue-hermes":
        result = tracker.queue_for_hermes(args.job_id, comment=args.comment, actor=args.actor)
    elif args.command == "list":
        result = tracker.list_rows()
    elif args.command == "history":
        result = tracker.read_events(args.entity_id)
    elif args.command == "event":
        result = tracker.record_event(actor=args.actor, entity_type=args.entity_type, entity_id=args.entity_id, action=args.action, before=args.before, after=args.after, comment=args.comment, confidence=args.confidence, requires_owner_review=args.owner_review)
    else:
        raise AssertionError(args.command)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
