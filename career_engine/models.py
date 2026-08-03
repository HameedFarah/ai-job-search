from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any


def to_data(value: Any) -> Any:
    if is_dataclass(value):
        return {key: to_data(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): to_data(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_data(item) for item in value]
    return value


@dataclass(slots=True)
class JobInput:
    company: str
    role: str
    full_job_description: str
    source: str = "manual"
    source_url: str = ""
    external_job_id: str = ""
    location: str = ""
    application_url: str = ""
    recipient: str = ""
    recipient_source: str = ""


@dataclass(slots=True)
class Requirement:
    id: str
    text: str
    priority: str
    category: str
    terms: list[str] = field(default_factory=list)


@dataclass(slots=True)
class EvidenceMatch:
    requirement_id: str
    status: str
    claim_ids: list[str]
    score: float
    note: str = ""


@dataclass(slots=True)
class FitScore:
    total: int
    recommendation: str
    subscores: dict[str, int]
    strengths: list[str]
    gaps: list[str]
    adjustment_ceiling: int


@dataclass(slots=True)
class RouteDecision:
    route: str
    recipient: str = ""
    recipient_source: str = ""
    application_url: str = ""
    blocker: str = ""


@dataclass(slots=True)
class ValidationFinding:
    code: str
    severity: str
    message: str
    location: str = ""


@dataclass(slots=True)
class PipelineState:
    job_id: str
    bundle_hash: str
    stage: str
    input_hashes: dict[str, str]
    outputs: dict[str, str]
    blockers: list[str] = field(default_factory=list)
