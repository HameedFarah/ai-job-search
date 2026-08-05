from __future__ import annotations

import hashlib
import re
from collections import Counter
from typing import Any

from .models import EvidenceMatch, FitScore, Requirement, RouteDecision, ValidationFinding, to_data


CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
BULLET_RE = re.compile(r"^\s*(?:[-*•▪◦]|\d+[.)])\s+")
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
NUMBER_RE = re.compile(r"(?<![A-Za-z])(?:SAR\s*)?\d[\d,.]*(?:\s*(?:%|M|B|K|\+|million|billion))?", re.IGNORECASE)

LIVE_STATUS_VALUES = ("live", "closed", "unverified")


def normalize_text(text: str) -> str:
    text = CONTROL_RE.sub("", text or "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    output: list[str] = []
    blank = False
    for line in lines:
        if not line:
            if output and not blank:
                output.append("")
            blank = True
            continue
        output.append(line)
        blank = False
    return "\n".join(output).strip()


def slug(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9]+", "_", value.strip())
    return re.sub(r"_+", "_", value).strip("_") or "Target_Role"


def normalize_live_status(payload: dict[str, Any]) -> tuple[str, str, str]:
    """Normalize the live-vacancy contract fields.

    Accepts ``live_status`` with values ``live``, ``closed`` or ``unverified``,
    plus optional ``live_verified_at`` and ``live_verification_source``.
    Missing status defaults to ``unverified``. Invalid status values fail
    deterministically so connectors cannot silently guess that a vacancy is live.
    """
    raw = str(payload.get("live_status", "unverified") or "unverified").strip().lower()
    if raw not in LIVE_STATUS_VALUES:
        raise ValueError(f"Invalid live_status: {raw!r}; expected one of {', '.join(LIVE_STATUS_VALUES)}")
    verified_at = str(payload.get("live_verified_at", "") or "").strip()
    verification_source = str(payload.get("live_verification_source", "") or "").strip()
    return raw, verified_at, verification_source


def validate_live_status(normalized_job: dict[str, Any]) -> list[dict[str, Any]]:
    """Deterministic validation of the live-vacancy gate.

    A ``live`` job requires a non-empty verification source and timestamp.
    Invalid combinations return an error finding; ``closed`` and ``unverified``
    jobs carry no verification requirement and are gated at the route/pipeline level.
    """
    status = str(normalized_job.get("live_status", "unverified") or "unverified").strip().lower()
    if status != "live":
        return []
    missing: list[str] = []
    if not str(normalized_job.get("live_verification_source", "") or "").strip():
        missing.append("live_verification_source")
    if not str(normalized_job.get("live_verified_at", "") or "").strip():
        missing.append("live_verified_at")
    if not missing:
        return []
    return [to_data(ValidationFinding(
        code="invalid_live_metadata",
        severity="error",
        message=f"A live vacancy requires verification metadata; missing: {', '.join(sorted(missing))}",
        location="live_status",
    ))]


def outward_filename(role: str, pattern: str) -> str:
    concise_role = re.sub(r"\s*\([^)]*\)\s*", " ", role).strip()
    return pattern.format(target_role=slug(concise_role))


def _heading_kind(line: str, headings: dict[str, list[str]]) -> str | None:
    cleaned = line.lower().strip(" :")
    for kind, values in headings.items():
        if cleaned in values:
            return kind
    return None


def _terms(text: str, aliases: dict[str, list[str]]) -> list[str]:
    low = text.lower()
    found: list[str] = []
    for canonical, variants in aliases.items():
        if canonical in low or any(item in low for item in variants):
            found.append(canonical)
    words = re.findall(r"[a-z][a-z0-9+.-]{2,}", low)
    stop = {"and", "the", "with", "for", "from", "that", "this", "will", "have", "your", "you", "our", "are", "job", "role"}
    found.extend(word for word in words if word not in stop)
    return list(dict.fromkeys(found))[:20]


def domain_requirement_gate(text: str, taxonomy: dict[str, Any]) -> str | None:
    """Return the mandatory domain a requirement demands, or None.

    A requirement is a mandatory domain/sector requirement only when its
    wording carries a mandatory signal (essential, required, must have,
    proven experience, track record, minimum years with a focus on, or
    equivalent) AND an explicit domain term (stadium/sports venue, shopping
    mall/retail, hospitality/leisure, high-rise/tower, defense/military,
    aviation, healthcare or similar). Such requirements must be satisfied by
    approved claim evidence that directly supports the domain; generic
    architecture, leadership, coordination or project-delivery evidence must
    not satisfy them. Returns the first matching domain key in taxonomy order.
    """
    low = text.lower()
    signals = taxonomy.get("mandatory_signals", [])
    if not any(signal in low for signal in signals):
        return None
    for domain, variants in taxonomy.get("mandatory_domain_terms", {}).items():
        if any(variant in low for variant in variants):
            return domain
    return None


def claim_supports_domain(
    claim: dict[str, Any],
    domain: str,
    taxonomy: dict[str, Any],
    requirement_text: str = "",
) -> bool:
    """True when an approved claim directly supports a mandatory domain.

    Support comes only from the claim's own label, tags, aliases or safe
    wording. Generic architecture, leadership or delivery evidence cannot
    satisfy a sector gate. Scale-qualified shopping-mall requirements are
    intentionally stricter: broad ``retail`` or ``mixed use`` aliases do not
    prove experience with a ``major`` or ``large-scale`` mall unless the claim
    itself contains the same scale qualification.
    """
    variants = taxonomy.get("mandatory_domain_terms", {}).get(domain, [])
    if not variants:
        return False
    parts = [
        str(claim.get("label", "")),
        str(claim.get("safe_wording", "")),
    ]
    parts.extend(str(item) for item in claim.get("tags", []))
    parts.extend(str(item) for item in claim.get("aliases", []))
    corpus = " ".join(parts).lower()
    if not any(variant in corpus for variant in variants):
        return False

    requirement_low = requirement_text.lower()
    if domain == "shopping_mall_retail" and any(
        qualifier in requirement_low
        for qualifier in ("major shopping mall", "major mall", "large-scale shopping mall", "large scale shopping mall")
    ):
        return any(
            qualifier in corpus
            for qualifier in ("major shopping mall", "major mall", "large-scale shopping mall", "large scale shopping mall")
        )
    return True


def normalize_job(payload: dict[str, Any], taxonomy: dict[str, Any]) -> dict[str, Any]:
    text = normalize_text(str(payload.get("full_job_description") or payload.get("job_description") or ""))
    if len(text) < 80:
        raise ValueError("Job description is too short to evaluate")
    headings = taxonomy.get("headings", {})
    aliases = taxonomy.get("aliases", {})
    section = "responsibilities"
    buckets: dict[str, list[str]] = {"responsibilities": [], "mandatory": [], "preferred": []}
    ambiguous: list[str] = []
    for raw in text.splitlines():
        kind = _heading_kind(raw, headings)
        if kind:
            section = kind
            continue
        line = BULLET_RE.sub("", raw).strip()
        if not line or len(line) < 8:
            continue
        if raw.endswith(":") and len(raw.split()) <= 8:
            ambiguous.append(raw)
            continue
        buckets.setdefault(section, []).append(line)
    requirements: list[Requirement] = []
    index = 1
    for kind in ("mandatory", "preferred", "responsibilities"):
        priority = "mandatory" if kind == "mandatory" else ("preferred" if kind == "preferred" else "context")
        for line in buckets.get(kind, []):
            requirements.append(Requirement(
                id=f"req-{index:03d}",
                text=line,
                priority=priority,
                category=kind,
                terms=_terms(line, aliases),
            ))
            index += 1
    if not requirements:
        for sentence in re.split(r"(?<=[.!?])\s+", text):
            sentence = sentence.strip()
            if len(sentence) >= 25:
                requirements.append(Requirement(
                    id=f"req-{index:03d}", text=sentence, priority="context",
                    category="responsibilities", terms=_terms(sentence, aliases)
                ))
                index += 1
    company = str(payload.get("company", "")).strip()
    role = str(payload.get("role", "")).strip()
    if not company or not role:
        nonempty = [line for line in text.splitlines() if line.strip()]
        role = role or (nonempty[0][:120] if nonempty else "Unknown role")
        company = company or "Unknown company"
    live_status, live_verified_at, live_verification_source = normalize_live_status(payload)
    return {
        "company": company,
        "role": role,
        "location": str(payload.get("location", "")).strip(),
        "reference": str(payload.get("reference", payload.get("external_job_id", ""))).strip(),
        "source": str(payload.get("source", "manual")),
        "source_url": str(payload.get("source_url", "")),
        "application_url": str(payload.get("application_url", "")),
        "recipient": str(payload.get("recipient", "")),
        "recipient_source": str(payload.get("recipient_source", "")),
        "live_status": live_status,
        "live_verified_at": live_verified_at,
        "live_verification_source": live_verification_source,
        "full_job_description": text,
        "requirements": [to_data(item) for item in requirements],
        "ambiguous_clauses": ambiguous,
        "jd_hash": hashlib.sha256(re.sub(r"\s+", " ", text).lower().encode()).hexdigest(),
    }


def match_evidence(normalized_job: dict[str, Any], bundle: dict[str, Any]) -> list[dict[str, Any]]:
    claims = bundle.get("claims", [])
    aliases = bundle.get("taxonomy", {}).get("aliases", {})
    taxonomy = bundle.get("taxonomy", {})
    results: list[dict[str, Any]] = []
    for requirement in normalized_job.get("requirements", []):
        req_terms = set(requirement.get("terms", []))
        req_low = requirement["text"].lower()
        gate_domain = domain_requirement_gate(requirement["text"], taxonomy)
        if gate_domain:
            domain_claims = [
                claim
                for claim in claims
                if claim_supports_domain(claim, gate_domain, taxonomy, requirement["text"])
            ]
            if not domain_claims:
                results.append(to_data(EvidenceMatch(
                    requirement_id=requirement["id"], status="gap", claim_ids=[],
                    score=0.0,
                    note=f"Mandatory domain gap: no approved claim directly supports {gate_domain.replace('_', ' ')}",
                )))
                continue
            eligible = domain_claims
        else:
            eligible = claims
        scored: list[tuple[float, str]] = []
        for claim in eligible:
            claim_terms = set(str(term).lower() for term in claim.get("tags", []) + claim.get("aliases", []))
            for canonical, variants in aliases.items():
                if canonical in req_terms and (canonical in claim_terms or any(v in claim_terms for v in variants)):
                    claim_terms.add(canonical)
            overlap = req_terms & claim_terms
            phrase_hits = sum(1 for term in claim_terms if len(term) > 3 and term in req_low)
            score = len(overlap) * 2.0 + phrase_hits * 1.5
            if score > 0:
                scored.append((score, claim["id"]))
        scored.sort(reverse=True)
        selected = [claim_id for _, claim_id in scored[:4]]
        top = scored[0][0] if scored else 0.0
        if top >= 4:
            status = "matched"
        elif top > 0:
            status = "adjacent"
        elif requirement.get("priority") == "context" and not gate_domain:
            status = "unmapped_context"
        else:
            status = "gap"
        note = (
            f"Mandatory domain ({gate_domain}) matched only against approved domain-supporting claims"
            if gate_domain
            else "Rule-based alias and tag match"
        )
        results.append(to_data(EvidenceMatch(
            requirement_id=requirement["id"], status=status, claim_ids=selected,
            score=top, note=note,
        )))
    return results


def _role_title_signals(role: str, taxonomy: dict[str, Any]) -> dict[str, Any]:
    """Classify a role title against the owner's specialization/seniority lane.

    The owner is positioned as a senior architecture / design-management /
    delivery-management professional, not as a production architect or an
    engineer/planner. The classification is used to suppress *materially
    mismatched* specialization/seniority roles while never suppressing
    adjacent senior design-management roles (Design Manager, Design Director,
    Design Governance, Technical Design Director, and similar).

    Returns boolean signals plus the applied factor pair:
      - ``adjacent_design_management``: management title + design-lane word
        (always multiplier 1.0 - never suppressed);
      - ``out_of_lane``: materially different function (planner, engineering,
        strategy, theming, ...);
      - ``production``: individual-contributor function (architect, designer,
        planner, engineer, ...) without management authority;
      - ``junior``: explicitly junior title.
    """
    title = role.lower()
    spec = taxonomy.get("specialization", {})
    design_lane = spec.get("design_lane_terms", [])
    out_of_lane = spec.get("out_of_lane_terms", [])
    production = spec.get("production_terms", [])
    junior = spec.get("junior_terms", [])
    seniority_terms = taxonomy.get("seniority_terms", [])

    has_management = any(term in title for term in seniority_terms)
    has_design_lane = any(term in title for term in design_lane)
    has_out_of_lane = any(term in title for term in out_of_lane)
    has_production = any(term in title for term in production)
    has_junior = any(term in title for term in junior)

    if has_junior:
        specialization_factor, seniority_factor, multiplier = 0.3, 0.2, 0.4
    elif has_out_of_lane and has_management:
        specialization_factor, seniority_factor, multiplier = 0.3, 1.0, 0.35
    elif has_out_of_lane:
        specialization_factor, seniority_factor, multiplier = 0.2, 0.5, 0.35
    elif has_management and has_design_lane:
        # Adjacent senior design-management roles are never suppressed.
        specialization_factor, seniority_factor, multiplier = 1.0, 1.0, 1.0
    elif has_production and not has_management:
        specialization_factor, seniority_factor, multiplier = 0.4, 0.35, 0.6
    elif has_management:
        specialization_factor, seniority_factor, multiplier = 1.0, 1.0, 1.0
    else:
        specialization_factor, seniority_factor, multiplier = 1.0, 0.55, 1.0

    return {
        "role_title": role,
        "adjacent_design_management": bool(has_management and has_design_lane),
        "out_of_lane": has_out_of_lane,
        "production": has_production and not has_management,
        "junior": has_junior,
        "has_management": has_management,
        "specialization_factor": specialization_factor,
        "seniority_factor": seniority_factor,
        "mismatch_multiplier": multiplier,
    }


def score_fit(normalized_job: dict[str, Any], matches: list[dict[str, Any]], bundle: dict[str, Any]) -> dict[str, Any]:
    config = bundle["config"]
    weights = config["scoring"]["weights"]
    thresholds = config["scoring"]["thresholds"]
    req_by_id = {item["id"]: item for item in normalized_job.get("requirements", [])}
    taxonomy = bundle.get("taxonomy", {})

    def _is_mandatory(match: dict[str, Any]) -> bool:
        requirement = req_by_id.get(match["requirement_id"], {})
        if requirement.get("priority") == "mandatory":
            return True
        # Explicit mandatory domain requirements stay material even when the
        # JD headings were not recognised, so an unsupported domain gap cannot
        # be hidden by bucket default credit.
        return domain_requirement_gate(str(requirement.get("text", "")), taxonomy) is not None

    def coverage(priority: str) -> float:
        points = {"matched": 1.0, "adjacent": 0.55, "unmapped_context": 0.25, "gap": 0.0}
        if priority == "mandatory":
            relevant = [m for m in matches if _is_mandatory(m)]
            if not relevant:
                # No explicit mandatory bucket: use overall requirement match
                # quality as the mandatory proxy instead of a fixed default, so
                # a JD whose responsibilities genuinely match the verified
                # evidence can still reach credible generation, while an
                # unmatched or generic JD cannot inflate itself to 80+.
                relevant = [
                    m for m in matches
                    if req_by_id.get(m["requirement_id"], {}).get("priority") != "preferred"
                ]
                if not relevant:
                    return 0.4
        else:
            relevant = [m for m in matches if req_by_id.get(m["requirement_id"], {}).get("priority") == priority]
            if not relevant:
                # An absent preferred section is weak evidence, not a perfect
                # score.
                return 0.5
        return sum(points.get(m["status"], 0.0) for m in relevant) / len(relevant)

    text = normalized_job["full_job_description"].lower()
    taxonomy = bundle.get("taxonomy", {})
    signals = _role_title_signals(normalized_job["role"], taxonomy)
    leadership = 1.0 if any(term in text for term in taxonomy.get("leadership_terms", [])) else 0.55
    seniority = signals["seniority_factor"]
    geography = 1.0 if any(term in (normalized_job.get("location", "") + " " + text).lower() for term in taxonomy.get("gcc_locations", [])) else 0.45
    sector_terms = {term for claim in bundle.get("claims", []) for term in claim.get("tags", []) if term in text}
    sector = min(1.0, 0.35 + 0.1 * len(sector_terms))
    credential = 1.0 if any(word in text for word in ("degree", "professional", "certification", "sce", "architect")) else 0.6
    factors = {
        "mandatory_coverage": coverage("mandatory"),
        "preferred_coverage": coverage("preferred"),
        "leadership": leadership,
        "seniority": seniority,
        "sector": sector,
        "geography": geography,
        "credentials": credential,
    }
    subscores = {name: round(weights[name] * factors[name]) for name in weights}
    raw_total = min(100, sum(subscores.values()))
    mandatory_domain_gaps = [
        match
        for match in matches
        if match.get("status") == "gap"
        and domain_requirement_gate(
            str(req_by_id.get(match.get("requirement_id", ""), {}).get("text", "")),
            taxonomy,
        )
        is not None
    ]
    # A candidate cannot be high priority while an explicit mandatory sector
    # requirement remains unsupported. Preserve the underlying subscores for
    # transparency, but cap the total immediately below the high-priority
    # threshold so strong general leadership evidence cannot hide the gap.
    if mandatory_domain_gaps:
        raw_total = min(raw_total, thresholds["high_priority"] - 1)
    # Specialization/seniority mismatch calibration. The multiplier suppresses
    # materially mismatched roles (planner, project engineering, production
    # architect, junior titles) after all subscore evidence is computed; raw
    # subscores stay preserved for transparency. Adjacent senior
    # design-management roles keep multiplier 1.0.
    total = min(100, round(raw_total * signals["mismatch_multiplier"]))
    if total >= thresholds["high_priority"]:
        recommendation = "high_priority"
    elif total >= thresholds["credible"]:
        recommendation = "credible"
    elif total >= thresholds["selective"]:
        recommendation = "selective"
    else:
        recommendation = "weak"
    strengths = [req_by_id[m["requirement_id"]]["text"] for m in matches if m["status"] == "matched"][:8]
    gaps = [req_by_id[m["requirement_id"]]["text"] for m in matches if m["status"] == "gap"][:8]
    return to_data(FitScore(
        total=total, recommendation=recommendation, subscores=subscores,
        strengths=strengths, gaps=gaps,
        adjustment_ceiling=config["scoring"]["llm_adjustment_ceiling"],
        raw_total=raw_total,
        calibration=signals,
    ))


def decide_route(normalized_job: dict[str, Any], bundle: dict[str, Any]) -> dict[str, Any]:
    live_status = str(normalized_job.get("live_status", "unverified") or "unverified").strip().lower()
    if live_status != "live":
        return to_data(RouteDecision(
            route="unresolved",
            blocker=f"Vacancy is not verified as live (live_status={live_status})",
        ))
    live_findings = validate_live_status(normalized_job)
    if live_findings:
        return to_data(RouteDecision(route="unresolved", blocker=live_findings[0]["message"]))
    recipient = normalized_job.get("recipient", "").strip()
    recipient_source = normalized_job.get("recipient_source", "").strip()
    application_url = normalized_job.get("application_url", "").strip()
    self_addresses = {item.lower() for item in bundle["config"]["policy"]["self_review_addresses"]}
    if recipient:
        if not EMAIL_RE.match(recipient):
            return to_data(RouteDecision(route="unresolved", blocker="Recipient email is invalid"))
        if recipient.lower() in self_addresses:
            return to_data(RouteDecision(route="unresolved", blocker="Self-addressed review drafts are prohibited"))
        if not recipient_source:
            return to_data(RouteDecision(route="unresolved", blocker="Recipient requires a verification source"))
        return to_data(RouteDecision(route="email", recipient=recipient, recipient_source=recipient_source))
    if application_url.startswith("https://"):
        return to_data(RouteDecision(route="portal", application_url=application_url))
    return to_data(RouteDecision(route="unresolved", blocker="No verified recipient or official application URL"))


def validate_text(text: str, bundle: dict[str, Any], *, location: str = "") -> list[dict[str, Any]]:
    policy = bundle["config"]["policy"]
    findings: list[ValidationFinding] = []
    low = text.lower()
    for name in policy.get("prohibited_experience_names", []):
        if name.lower() in low:
            findings.append(ValidationFinding("prohibited_name", "error", f"Prohibited experience name: {name}", location))
    for term in policy.get("prohibited_terms", []):
        if term.lower() in low:
            findings.append(ValidationFinding("prohibited_term", "error", f"Prohibited term: {term}", location))
    for pattern in policy.get("availability_patterns", []):
        if pattern.lower() in low:
            findings.append(ValidationFinding("availability", "error", f"External availability wording is prohibited: {pattern}", location))
    for char in policy.get("forbidden_characters", []):
        if char in text:
            findings.append(ValidationFinding("forbidden_character", "error", f"Forbidden dash character: U+{ord(char):04X}", location))
    if "50 hours" in low and ("cmp" in low or "contracts management professional" in low):
        findings.append(ValidationFinding("cmp_hours", "error", "CMP must be listed without course hours", location))
    return [to_data(item) for item in findings]


def validate_bullet_numbers(text: str, maximum: int) -> list[dict[str, Any]]:
    count = len(NUMBER_RE.findall(text))
    if count <= maximum:
        return []
    return [to_data(ValidationFinding(
        "too_many_numbers", "error", f"Achievement bullet contains {count} numeric figures; maximum is {maximum}", "bullet"
    ))]


def select_metric_claims(matches: list[dict[str, Any]], claims: list[dict[str, Any]], count: int = 6) -> list[str]:
    frequencies: Counter[str] = Counter()
    for match in matches:
        weight = 3 if match["status"] == "matched" else 1
        for claim_id in match.get("claim_ids", []):
            frequencies[claim_id] += weight
    metric_ids = [claim["id"] for claim in claims if claim.get("value")]
    ordered = [item for item, _ in frequencies.most_common() if item in metric_ids]
    for claim_id in metric_ids:
        if claim_id not in ordered:
            ordered.append(claim_id)
    return ordered[:count]
