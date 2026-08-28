from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .core import NUMBER_RE, outward_filename, select_metric_claims, validate_bullet_numbers, validate_text


YEAR_TOKEN_RE = re.compile(r"\b(?:19|20)\d{2}\b")
DATE_RANGE_RE = re.compile(r"\b((?:19|20)\d{2})\s*[-–—]\s*((?:19|20)\d{2})\b")
FUNCTION_WORDS = {
    "and", "the", "for", "with", "from", "that", "this", "your", "you", "our",
    "are", "will", "have", "has", "into", "over", "under", "across", "through",
    "but", "or", "not", "all", "any", "both", "each", "more", "most", "other",
    "some", "such", "than", "too", "very", "can", "may", "must", "shall",
    "should", "using", "within", "between", "during",
}


def _significant_terms(text: str) -> list[str]:
    tokens = re.findall(r"[a-z][a-z0-9+.-]{2,}", text.lower())
    return [token for token in tokens if len(token) >= 4 and token not in FUNCTION_WORDS]


def _chronology_spans(bundle: dict[str, Any]) -> tuple[list[tuple[int, int]], int]:
    current_year = datetime.now(timezone.utc).year
    spans: list[tuple[int, int]] = []
    for entry in bundle.get("career_chronology", []):
        try:
            start = int(str(entry.get("start", ""))[:4])
        except ValueError:
            continue
        end_raw = str(entry.get("end", "")).strip()
        end = current_year if end_raw in ("", "present", "current") else int(end_raw[:4])
        spans.append((start, end))
    return spans, current_year


def _check_chronology(text: str, bundle: dict[str, Any]) -> list[dict[str, Any]]:
    """Reject generated dates or date ranges outside the verified career chronology."""
    spans, current_year = _chronology_spans(bundle)
    if not spans:
        return []
    findings: list[dict[str, Any]] = []
    for match in DATE_RANGE_RE.finditer(text):
        start, end = int(match.group(1)), int(match.group(2))
        if not any(span_start <= start and end <= span_end for span_start, span_end in spans):
            findings.append({
                "code": "chronology_range",
                "severity": "error",
                "message": f"Employment date range '{match.group(0)}' conflicts with the verified career chronology",
            })
    min_start = min(start for start, _ in spans)
    max_end = max(end for _, end in spans)
    for match in YEAR_TOKEN_RE.finditer(text):
        year = int(match.group(0))
        if year < min_start or year > max_end:
            findings.append({
                "code": "unsupported_year",
                "severity": "error",
                "message": f"Year '{year}' is outside the verified career chronology ({min_start}-{max_end})",
            })
    return findings


def _check_required_coverage(application: dict[str, Any], packet: dict[str, Any]) -> list[dict[str, Any]]:
    """Reject a mandatory gap requirement that is neither addressed nor acknowledged."""
    requirements = {item.get("id"): item for item in packet.get("vacancy", {}).get("requirements", [])}
    gaps = [
        match for match in packet.get("requirement_matrix", [])
        if match.get("status") == "gap"
        and requirements.get(match.get("requirement_id", {}), {}).get("priority") == "mandatory"
    ]
    if not gaps:
        return []
    bullets = [
        str(item.get("text", "")) for section in ("current_role_bullets", "earlier_role_bullets")
        for item in application.get(section, [])
    ]
    acknowledged = [str(item) for item in application.get("acknowledged_gaps", [])]
    corpus = " ".join(bullets + acknowledged).lower()
    findings: list[dict[str, Any]] = []
    for gap in gaps:
        requirement = requirements[gap["requirement_id"]]
        terms = _significant_terms(requirement.get("text", ""))
        if terms and not any(term in corpus for term in terms):
            findings.append({
                "code": "unaddressed_requirement",
                "severity": "error",
                "message": f"Mandatory requirement is a gap but is neither addressed nor acknowledged: {requirement.get('text', '')[:160]}",
            })
    return findings


def _check_current_title_placement(application: dict[str, Any], bundle: dict[str, Any]) -> list[dict[str, Any]]:
    """The official current chronology title must appear only in the chronology section."""
    title = str(bundle.get("config", {}).get("identity", {}).get("current_chronology_title", "")).strip()
    if not title:
        return []
    headline = str(application.get("headline", ""))
    profile = application.get("leadership_profile", {})
    profile_text = str(profile.get("text", "")) if isinstance(profile, dict) else ""
    if title.lower() in headline.lower() or title.lower() in profile_text.lower():
        return [{
            "code": "current_title_misplaced",
            "severity": "error",
            "message": f"The official current chronology title '{title}' must only appear in the chronology section",
        }]
    return []


def _normalize_number(raw: str) -> str:
    """Normalize a numeric figure for comparison: 'SAR 21.94 million' -> '21.94m'."""
    value = raw.strip().lower().replace("sar ", "").replace(",", "")
    value = re.sub(r"\s+", "", value)
    return value.replace("million", "m").replace("billion", "b")


def _bullet_signature(item: dict[str, Any]) -> tuple[set[str], set[str], set[str]]:
    """Extract the numbers, subject terms and claim IDs a bullet rests on."""
    text = str(item.get("text", ""))
    numbers = {_normalize_number(match) for match in NUMBER_RE.findall(text)}
    subjects = set(_significant_terms(text))
    claims = set(item.get("claim_ids", []))
    return numbers, subjects, claims


def _check_bullet_redundancy(application: dict[str, Any]) -> list[dict[str, Any]]:
    """Reject materially redundant bullets that restate the same evidence, numbers,
    subject and outcome without adding a distinct responsibility or result.

    The comparison is bullet-to-bullet within the same role section only. Legitimate
    reuse of one claim across the profile, metric boxes, a role bullet and the cover
    email is intentionally not flagged.
    """
    findings: list[dict[str, Any]] = []
    for section in ("current_role_bullets", "earlier_role_bullets"):
        bullets = application.get(section, [])
        if not isinstance(bullets, list):
            continue
        for left_index in range(len(bullets)):
            left = bullets[left_index]
            if not isinstance(left, dict):
                continue
            left_numbers, left_subjects, left_claims = _bullet_signature(left)
            for right_index in range(left_index + 1, len(bullets)):
                right = bullets[right_index]
                if not isinstance(right, dict):
                    continue
                right_numbers, right_subjects, right_claims = _bullet_signature(right)
                shared_numbers = left_numbers & right_numbers
                if not shared_numbers:
                    continue
                shared_claims = left_claims & right_claims
                smaller = min(len(left_claims), len(right_claims))
                claim_overlap = len(shared_claims) / smaller if smaller else 0.0
                union = left_subjects | right_subjects
                subject_overlap = len(left_subjects & right_subjects) / len(union) if union else 0.0
                if claim_overlap < 0.5 and subject_overlap < 0.5:
                    continue
                findings.append({
                    "code": "redundant_bullet",
                    "severity": "error",
                    "message": (
                        f"{section}[{left_index}] and {section}[{right_index}] are materially "
                        f"redundant: they cite the same evidence "
                        f"({', '.join(sorted(shared_claims)) if shared_claims else 'none'}), share the "
                        f"figures {sorted(shared_numbers)} and restate the same subject without adding a "
                        f"distinct responsibility or result"
                    ),
                    "location": f"{section}[{left_index}]",
                })
    return findings


def claim_role_scope(claim: dict[str, Any]) -> str:
    """Deterministic role scope of a verified claim, derived only from existing
    claim data (id prefix, attribution). Returns 'career', 'current', 'earlier',
    'credential' or 'general'. Unknown claims resolve to 'general' so the
    attribution gate never flags content the evidence register does not classify.
    """
    claim_id = str(claim.get("id", ""))
    attribution = str(claim.get("attribution", "") or "").lower()
    if claim_id.startswith("credential."):
        return "credential"
    if claim_id.startswith(_EARLIER_SCOPE_PREFIXES) or "earlier" in attribution:
        return "earlier"
    if claim_id.startswith("career.") or "career" in attribution or "portfolio" in attribution:
        return "career"
    if claim_id.startswith(_CURRENT_SCOPE_PREFIXES):
        return "current"
    return "general"


def _check_role_attribution(application: dict[str, Any], bundle: dict[str, Any]) -> list[dict[str, Any]]:
    """Reject role-attribution errors in both chronology directions.

    Earlier-role bullets may not cite career-wide/current/credential evidence as if
    it belonged to an earlier employer. Current-role bullets may not cite claims
    whose verified scope is explicitly an earlier employer/role. Role-neutral
    capability claims remain allowed in either section.
    """
    claims_by_id = {claim["id"]: claim for claim in bundle.get("claims", [])}
    findings: list[dict[str, Any]] = []

    earlier_bullets = application.get("earlier_role_bullets", [])
    if isinstance(earlier_bullets, list):
        for index, item in enumerate(earlier_bullets):
            if not isinstance(item, dict):
                continue
            for claim_id in item.get("claim_ids", []):
                claim = claims_by_id.get(claim_id)
                if claim is None:
                    continue
                scope = claim_role_scope(claim)
                if scope in ("career", "current", "credential"):
                    findings.append({
                        "code": "role_attribution",
                        "severity": "error",
                        "message": (
                            f"earlier_role_bullets[{index}] cites claim '{claim_id}' "
                            f"({str(claim.get('label', '')).strip() or 'no label'}) whose scope is "
                            f"{scope} ({str(claim.get('attribution', '')).strip() or 'no attribution'}), "
                            "but earlier-role bullets may cite only Cube Architects or earlier-role evidence"
                        ),
                        "location": f"earlier_role_bullets[{index}]",
                    })

    current_bullets = application.get("current_role_bullets", [])
    if isinstance(current_bullets, list):
        for index, item in enumerate(current_bullets):
            if not isinstance(item, dict):
                continue
            for claim_id in item.get("claim_ids", []):
                claim = claims_by_id.get(claim_id)
                if claim is None:
                    continue
                if claim_role_scope(claim) == "earlier":
                    findings.append({
                        "code": "role_attribution",
                        "severity": "error",
                        "message": (
                            f"current_role_bullets[{index}] cites claim '{claim_id}' "
                            f"({str(claim.get('label', '')).strip() or 'no label'}) whose scope is earlier; "
                            "current-role bullets may not attribute earlier-employer evidence to the current role"
                        ),
                        "location": f"current_role_bullets[{index}]",
                    })
    return findings


SYSTEM_INSTRUCTION = """You are writing a senior, credible and commercially aware job application for Abdelhamid Farah.
Write original, coherent and persuasive prose tailored to the vacancy. Do not use canned fill-in-the-blank wording.
Use only the evidence claims supplied in this packet. Every factual profile statement, achievement bullet and cover-email proof point must cite the supporting claim IDs in the structured output.
Do not invent employers, clients, dates, qualifications, metrics, tools or achievements. A genuine gap must remain an acknowledged gap.
Avoid repetition: never restate the same evidence, figure, subject or outcome in more than one bullet; each bullet must add a distinct responsibility or result.
Respect attribution: career-wide and current-role claims (for example 112+ projects, portfolio values, KSA team or office figures, and TTW programme and governance metrics) belong in the profile, metric boxes or current-role bullets only; earlier-role bullets must cite only Cube Architects or earlier-role evidence.
Return only JSON matching the supplied schema. Use ASCII hyphens only. Do not mention availability.
"""

# Concise one-pass generation guidance mirrored into the exported packet so every
# adapter sees the anti-repetition and anti-attribution-leakage rules explicitly.
GENERATION_GUIDANCE = [
    "Never restate the same evidence, figure, subject or outcome in more than one bullet; each bullet must add a distinct responsibility or result.",
    "Career-wide and current-role claims (112+ projects, portfolio values, KSA team or office figures, TTW programme and governance metrics) belong in the profile, metric boxes or current-role bullets only.",
    "Earlier-role bullets must cite only Cube Architects or earlier-role evidence (cube.*, earlier.* claims); never place career totals or current-role metrics under an earlier role.",
    "Current-role bullets must never cite cube.* or earlier.* claims; use only verified career-wide, current-role or role-neutral evidence for the current employer.",
    "Produce exactly eleven earlier-role bullets: seven distinct Cube Design Manager/Senior Architect bullets, then one each for Cube Project Architect, Creative Urban Designs, Al-Mehanya and Sigma. Cite the role-scoped claim that determines placement.",
    "Use the exact deterministic cover-email subject supplied in email_draft_policy.expected_subject; do not rewrite or decorate it.",
]


def expected_email_subject(normalized_job: dict[str, Any], bundle: dict[str, Any]) -> tuple[str, str]:
    required = str(normalized_job.get("required_email_subject", "") or "").strip()
    if required:
        return required, "job_description"
    identity = bundle.get("identity", {})
    policy = bundle["config"]["policy"]
    pattern = str(policy.get("email_subject_fallback", "{name} - {post_name}"))
    role = str(normalized_job.get("role", "Position")).replace("—", "-").replace("–", "-")
    subject = pattern.replace("{name}", str(identity.get("name", "Abdelhamid Farah"))).replace(
        "{post_name}", role
    )
    return subject.strip(), "fallback"

# Role-scope prefixes for the verified claim register. "career" and "current"
# scopes must never be cited by an earlier-role bullet; "earlier" scopes are the
# only role-specific evidence allowed there; "general" capabilities and
# "credential" claims are governed by the other gates.
_EARLIER_SCOPE_PREFIXES = ("cube.", "earlier.")
_CURRENT_SCOPE_PREFIXES = (
    "governance.", "cost.", "stations.", "leadership.", "contract.", "bim.", "proposal.",
)


def create_generation_packet(
    *,
    job_id: str,
    normalized_job: dict[str, Any],
    matches: list[dict[str, Any]],
    score: dict[str, Any],
    route: dict[str, Any],
    bundle: dict[str, Any],
) -> dict[str, Any]:
    selected_metrics = select_metric_claims(
        matches, bundle.get("claims", []), bundle["config"]["policy"]["metric_box_count"]
    )
    selected_claim_ids: list[str] = []
    for match in matches:
        if match["status"] in {"matched", "adjacent"}:
            selected_claim_ids.extend(match.get("claim_ids", []))
    selected_claim_ids.extend(selected_metrics)
    # The approved two-page template contains a fully evidence-controlled career
    # chronology on page two. Include every verified earlier-role claim so the
    # generator can replace all fixed chronology prose rather than silently
    # inheriting uncited template wording.
    selected_claim_ids.extend(
        claim["id"] for claim in bundle.get("claims", []) if claim_role_scope(claim) == "earlier"
    )
    # The fixed current-role section also needs enough verified current/career
    # evidence to fill all seven bullets without borrowing facts from earlier
    # employers. Include those role-scoped claims even when the rule matcher did
    # not select them for a specific JD requirement; the prose generator still
    # decides which are relevant and every use remains claim-cited/validated.
    selected_claim_ids.extend(
        claim["id"]
        for claim in bundle.get("claims", [])
        if claim_role_scope(claim) in {"current", "career"}
    )
    selected_claim_ids = list(dict.fromkeys(selected_claim_ids))
    claims_by_id = {claim["id"]: claim for claim in bundle.get("claims", [])}
    selected_claims = [claims_by_id[item] for item in selected_claim_ids if item in claims_by_id]
    role = normalized_job["role"]
    filename = outward_filename(role, bundle["config"]["policy"]["external_filename_pattern"])
    expected_subject, subject_source = expected_email_subject(normalized_job, bundle)
    generation_config = bundle["config"]["generation"]
    policy = bundle["config"]["policy"]
    route_name = str(route.get("route", "unresolved"))
    default_variant = (
        generation_config.get("email_default_resume_variant", "modern-executive-sidebar")
        if route_name == "email"
        else generation_config.get("portal_default_resume_variant", "ats-linear")
    )
    return {
        "schema_version": 1,
        "job_id": job_id,
        "bundle_hash": bundle["bundle_hash"],
        "system_instruction": SYSTEM_INSTRUCTION,
        "vacancy": normalized_job,
        "fit_evaluation": score,
        "requirement_matrix": matches,
        "selected_claims": selected_claims,
        "selected_metric_claim_ids": selected_metrics,
        "career_chronology": bundle.get("career_chronology", []),
        "identity": bundle.get("identity", {}),
        "writing_rules": bundle.get("writing_rules", []),
        "policy": bundle["config"]["policy"],
        "application_route": route,
        "email_draft_policy": {
            "account": policy.get("email_draft_account", "hameedo@gmail.com"),
            "sender": bundle.get("identity", {}).get("outward_email", ""),
            "recipient": route.get("recipient", ""),
            "recipient_source": route.get("recipient_source", ""),
            "expected_subject": expected_subject,
            "subject_source": subject_source,
            "attachment_count": int(policy.get("email_attachment_count", 1)),
            "default_resume_variant": default_variant,
            "preview_override_allowed": bool(generation_config.get("preview_template_override_allowed", True)),
            "attach_only_selected_resume_variant": bool(generation_config.get("attach_only_selected_resume_variant", True)),
        },
        "output_schema_path": "projects/job-automation/config/generated_application.schema.json",
        "output_contract": {
            "free_prose": True,
            "claim_citations_required": True,
            "routine_llm_calls": 1,
            "second_review_only_when": bundle["config"]["generation"]["second_review_conditions"],
        },
        "generation_guidance": GENERATION_GUIDANCE,
        "outward_filename": filename,
        "owner_questions": _owner_questions(route, normalized_job),
    }


def _owner_questions(route: dict[str, Any], job: dict[str, Any]) -> list[str]:
    questions: list[str] = []
    if route.get("route") == "unresolved":
        questions.append(route.get("blocker", "Confirm the application route"))
    low = job.get("full_job_description", "").lower()
    for phrase, question in (
        ("expected salary", "Expected salary"),
        ("current salary", "Current salary"),
        ("date of birth", "Date of birth"),
        ("conflict of interest", "Conflict-of-interest declaration"),
        ("criminal", "Criminal-history declaration"),
        ("notice period", "Notice period or start-date answer for the form"),
    ):
        if phrase in low:
            questions.append(question)
    return questions


def export_packet(packet: dict[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(packet, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def validate_generated_application(application: dict[str, Any], packet: dict[str, Any], bundle: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    required = {
        "schema_version", "job_id", "bundle_hash", "headline", "leadership_profile",
        "metric_claim_ids", "current_role_bullets", "earlier_role_bullets",
        "credential_claim_ids", "cover_email", "tailoring_rationale", "acknowledged_gaps",
    }
    missing = sorted(required - set(application))
    if missing:
        findings.append({"code": "schema_missing", "severity": "error", "message": f"Missing fields: {', '.join(missing)}"})
        return findings
    if application.get("schema_version") != 1:
        findings.append({"code": "schema_version", "severity": "error", "message": "Generated application schema_version must be 1"})
    if application.get("job_id") != packet.get("job_id"):
        findings.append({"code": "job_id", "severity": "error", "message": "Generated application job_id does not match packet"})
    if application.get("bundle_hash") != packet.get("bundle_hash"):
        findings.append({"code": "bundle_hash", "severity": "error", "message": "Generated content used a stale or different Career Engine bundle"})
    allowed = {claim["id"] for claim in packet.get("selected_claims", [])}
    all_known = {claim["id"] for claim in bundle.get("claims", [])}

    def check_claims(ids: Any, location: str, *, selected_only: bool = True) -> None:
        if not isinstance(ids, list) or not ids:
            findings.append({"code": "missing_claim_citation", "severity": "error", "message": f"No claim citation at {location}"})
            return
        permitted = allowed if selected_only else all_known
        unknown = [item for item in ids if item not in permitted]
        if unknown:
            findings.append({"code": "unsupported_claim", "severity": "error", "message": f"Unsupported claim IDs at {location}: {unknown}"})

    text_fields: list[tuple[str, str, list[str]]] = []
    profile = application.get("leadership_profile", {})
    if isinstance(profile, dict):
        check_claims(profile.get("claim_ids"), "leadership_profile")
        text_fields.append((str(profile.get("text", "")), "leadership_profile", profile.get("claim_ids", [])))
    else:
        findings.append({"code": "schema_type", "severity": "error", "message": "leadership_profile must be an object"})
    metrics = application.get("metric_claim_ids", [])
    if not isinstance(metrics, list) or len(metrics) != bundle["config"]["policy"]["metric_box_count"] or len(set(metrics)) != len(metrics):
        findings.append({"code": "metric_count", "severity": "error", "message": "Exactly six unique metric claim IDs are required"})
    else:
        check_claims(metrics, "metric_claim_ids")
    for section in ("current_role_bullets", "earlier_role_bullets"):
        items = application.get(section, [])
        if not isinstance(items, list):
            findings.append({"code": "schema_type", "severity": "error", "message": f"{section} must be an array"})
            continue
        expected_count = 7 if section == "current_role_bullets" else 11
        if len(items) != expected_count:
            findings.append({
                "code": "role_bullet_count",
                "severity": "error",
                "message": f"{section} must contain exactly {expected_count} evidence-controlled bullets; got {len(items)}",
            })
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                findings.append({"code": "schema_type", "severity": "error", "message": f"{section}[{index}] must be an object"})
                continue
            check_claims(item.get("claim_ids"), f"{section}[{index}]")
            text = str(item.get("text", ""))
            text_fields.append((text, f"{section}[{index}]", item.get("claim_ids", [])))
            findings.extend(validate_bullet_numbers(text, bundle["config"]["policy"]["max_numbers_per_bullet"]))
    earlier_items = application.get("earlier_role_bullets", [])
    if isinstance(earlier_items, list):
        buckets = {"cube": 0, "cube_project_architect": 0, "cud": 0, "almehanya": 0, "sigma": 0}
        for item in earlier_items:
            ids = set(item.get("claim_ids", [])) if isinstance(item, dict) else set()
            if any(claim_id.startswith("cube.") for claim_id in ids):
                buckets["cube"] += 1
            if any(claim_id.startswith("earlier.cube_project_architect.") for claim_id in ids):
                buckets["cube_project_architect"] += 1
            if any(claim_id.startswith("earlier.cud.") for claim_id in ids):
                buckets["cud"] += 1
            if "earlier.procurement.20plus" in ids:
                buckets["almehanya"] += 1
            if any(claim_id.startswith("earlier.sigma.") for claim_id in ids):
                buckets["sigma"] += 1
        expected = {"cube": 7, "cube_project_architect": 1, "cud": 1, "almehanya": 1, "sigma": 1}
        if buckets != expected:
            findings.append({
                "code": "earlier_role_coverage",
                "severity": "error",
                "message": f"Earlier-role evidence placement must be {expected}; got {buckets}",
            })
    credentials = application.get("credential_claim_ids", [])
    if credentials:
        check_claims(credentials, "credential_claim_ids", selected_only=False)
    email = application.get("cover_email", {})
    if isinstance(email, dict):
        check_claims(email.get("claim_ids"), "cover_email")
        subject = str(email.get("subject", "")).strip()
        expected_subject = str(packet.get("email_draft_policy", {}).get("expected_subject", "")).strip()
        if expected_subject and subject != expected_subject:
            findings.append({
                "code": "email_subject_mismatch",
                "severity": "error",
                "message": f"cover_email.subject must exactly match the required subject: {expected_subject}",
            })
        text_fields.append((subject, "cover_email.subject", []))
        text_fields.append((str(email.get("body", "")), "cover_email.body", email.get("claim_ids", [])))
    else:
        findings.append({"code": "schema_type", "severity": "error", "message": "cover_email must be an object"})
    text_fields.append((str(application.get("headline", "")), "headline", []))
    for text, location, _ in text_fields:
        findings.extend(validate_text(text, bundle, location=location))
        findings.extend(_check_chronology(text, bundle))
        if "{{" in text or "}}" in text or "<insert" in text.lower() or "[company]" in text.lower():
            findings.append({"code": "placeholder", "severity": "error", "message": f"Placeholder text at {location}"})
    findings.extend(_check_required_coverage(application, packet))
    findings.extend(_check_current_title_placement(application, bundle))
    findings.extend(_check_bullet_redundancy(application))
    findings.extend(_check_role_attribution(application, bundle))
    return findings


CENTRAL_DISPATCHER = Path("/home/hameedo/vps-infra-dev/scripts/operations/model-route-dispatch.py")


def _dispatcher_runtime_paths() -> tuple[Path, Path]:
    """Return the clean source cwd and persistent verified routing cache.

    Generation packets live under the shared mutable tracker, so their path must
    never be used to infer the executable checkout. The Career config/runtime
    authority already resolves both identities correctly: source executes from
    the clean runtime checkout while the routing cache persists under the one
    canonical tracker runtime and survives nested-worker DNS failures.
    """
    from .config import load_config

    _config, paths = load_config()
    return paths.repo_root, paths.tracker_base / "runtime" / "model-routing-authority-cache"


def _run_central_dispatcher(packet_path: Path, output_path: Path) -> dict[str, Any]:
    """Generate through the central dispatcher and promote a sandbox-local stage.

    Routed workspace-write workers are intentionally confined to ``repo_root``.
    The canonical tracker may live outside that clean runtime checkout, so the
    model writes to a unique stage inside ``repo_root``. Only this trusted host
    process validates the staged JSON and atomically promotes it to the tracker.
    """
    evidence_path = output_path.parent / "model-routing-evidence.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    repo_root, cache_dir = _dispatcher_runtime_paths()
    cache_dir.parent.mkdir(parents=True, exist_ok=True)

    stage_handle = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=".json", prefix=".career-generation-", dir=repo_root, delete=False
    )
    stage_path = Path(stage_handle.name)
    stage_handle.close()
    stage_path.unlink(missing_ok=True)
    prompt = (
        "Read the Career Engine generation packet at " + str(packet_path) +
        ". Write exactly one valid generated-application JSON object to " + str(stage_path) +
        ". Follow the packet schema and citations. Do not modify any other files. "
        "This is the single generation pass; do not perform external actions or submit/contact anyone."
    )
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".txt", delete=False) as handle:
        handle.write(prompt)
        prompt_path = Path(handle.name)
    try:
        try:
            completed = subprocess.run(
                [
                    "/usr/bin/python3", str(CENTRAL_DISPATCHER),
                    "--prompt-file", str(prompt_path),
                    "--cwd", str(repo_root),
                    "--evidence-file", str(evidence_path),
                    "--cache-dir", str(cache_dir),
                    "--mode", "workspace-write",
                    "--timeout-seconds", "900",
                ],
                check=False, text=True, capture_output=True, timeout=930,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {
                "returncode": 1,
                "error": type(exc).__name__,
                "output_exists": False,
                "evidence_exists": evidence_path.is_file(),
            }

        if completed.returncode == 0:
            if not stage_path.is_file():
                raise RuntimeError("Generation route succeeded but staged output was not created")
            try:
                staged = json.loads(stage_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise RuntimeError("Generation route succeeded but staged output is invalid JSON") from exc
            if not isinstance(staged, dict):
                raise RuntimeError("Generation route succeeded but staged output is not a JSON object")

            promoted_handle = tempfile.NamedTemporaryFile(
                mode="wb", suffix=".json", prefix=".career-promotion-", dir=output_path.parent, delete=False
            )
            promoted_path = Path(promoted_handle.name)
            promoted_handle.close()
            try:
                shutil.copyfile(stage_path, promoted_path)
                os.replace(promoted_path, output_path)
            finally:
                promoted_path.unlink(missing_ok=True)

        return {
            "returncode": completed.returncode,
            "stdout": completed.stdout[-4000:],
            "stderr": completed.stderr[-4000:],
            "output_exists": output_path.is_file(),
            "evidence_exists": evidence_path.is_file(),
            "evidence": str(evidence_path),
        }
    finally:
        prompt_path.unlink(missing_ok=True)
        stage_path.unlink(missing_ok=True)


def run_adapter(adapter: str, packet_path: Path, output_path: Path, *, provider: str = "", model: str = "") -> dict[str, Any]:
    if adapter == "manual":
        return {"adapter": "manual", "packet": str(packet_path), "output": str(output_path), "executed": False}
    if adapter not in {"opencode", "hermes"}:
        raise ValueError(f"Unknown generation adapter: {adapter}")
    routed = _run_central_dispatcher(packet_path, output_path)
    return {
        "adapter": adapter,
        "executed": True,
        **routed,
    }
