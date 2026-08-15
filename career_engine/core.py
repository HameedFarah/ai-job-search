from __future__ import annotations

import hashlib
import re
from collections import Counter
from typing import Any
from urllib.parse import urlparse

from .models import EvidenceMatch, FitScore, Requirement, RouteDecision, ValidationFinding, to_data


CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
BULLET_RE = re.compile(r"^\s*(?:[-*•▪◦]|\d+[.)])\s+")
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
NUMBER_RE = re.compile(r"(?<![A-Za-z])(?:SAR\s*)?\d[\d,.]*(?:\s*(?:%|M|B|K|\+|million|billion))?", re.IGNORECASE)

LIVE_STATUS_VALUES = ("live", "closed", "unverified")
THIRD_PARTY_APPLICATION_HOSTS = (
    "4dayweek.io",
    "adzuna.com",
    "echojobs.io",
    "freehire.me",
    "whatjobs.com",
    "djinni.co",
    "t.me",
)


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


_INLINE_HEADING_ALIASES: dict[str, tuple[str, ...]] = {
    "responsibilities": ("your key duties", "key duties", "what you will be doing"),
    "mandatory": (
        "your skills and experience",
        "skills and experience",
        "experience and qualifications",
        "about you",
        "what required skills you'll bring",
    ),
    "preferred": (
        "preferred qualifications",
        "nice-to-have",
        "what desired skills you'll bring",
    ),
}
_MARKETING_HEADINGS = (
    "what we offer",
    "what we offer you",
    "why join us",
    "benefits",
    "about the company",
    "about us",
    "equal opportunity",
    "a place for everyone",
    "apply today",
)
_BOILERPLATE_PREFIXES = (
    "we are an equal opportunity employer",
    "equal opportunity employer",
    "all qualified applicants",
    "parsons equally employs representation",
    "parsons is aware of fraudulent recruitment practices",
    "to learn more about recruitment fraud",
    "we truly invest and care about our employee",
)
_CLAUSE_STARTERS = (
    "lead", "manage", "oversee", "provide", "review", "support", "develop",
    "define", "evaluate", "conduct", "create", "write", "liaise", "prepare",
    "maintain", "ensure", "coordinate", "facilitate", "identify", "establish",
    "promote", "direct", "monitor", "deliver", "drive", "mentor", "advise",
    "leading", "management of", "overseeing",
)


def _heading_kind(line: str, headings: dict[str, list[str]]) -> str | None:
    cleaned = line.lower().strip()
    # Job boards commonly decorate headings with Markdown markers, HTML-ish
    # heading prefixes, emoji or other symbols (for example
    # "**Requirements**", "### Desired Skills" or "🌟 Requirements:").
    # Remove decoration only at the heading boundary; do not alter JD prose.
    cleaned = re.sub(r"^#{1,6}\s*", "", cleaned)
    cleaned = re.sub(r"^(?:\*\*|__)+", "", cleaned)
    cleaned = re.sub(r"(?:\*\*|__)+$", "", cleaned)
    cleaned = re.sub(r"^[^\w]+", "", cleaned, flags=re.UNICODE).strip(" :;*#_")
    for kind, values in headings.items():
        if cleaned in values:
            return kind
    for kind, values in _INLINE_HEADING_ALIASES.items():
        if cleaned in values:
            return kind
    return None


def _requirement_lines(text: str, headings: dict[str, list[str]]) -> list[str]:
    """Restore structural boundaries for flattened JDs without changing JD text."""
    original = text.splitlines()
    nonempty = [line for line in original if line.strip()]
    has_flattened_line = any(len(line) >= 400 for line in nonempty)
    if not has_flattened_line:
        return original

    heading_map: dict[str, str] = {}
    for kind, values in headings.items():
        for value in values:
            heading_map[value.lower().strip(" :;")] = kind
    for kind, values in _INLINE_HEADING_ALIASES.items():
        for value in values:
            heading_map[value] = kind

    markers = sorted(set(heading_map) | set(_MARKETING_HEADINGS), key=len, reverse=True)
    marker_pattern = re.compile(
        r"(?<![A-Za-z0-9])(" + "|".join(re.escape(item) for item in markers) + r")(?:\s*[:;])?(?=\s|$)",
        re.IGNORECASE,
    )
    expanded: list[str] = []
    for raw in original:
        if len(raw) < 400:
            expanded.append(raw)
            continue
        cursor = 0
        for match in marker_pattern.finditer(raw):
            before = raw[cursor:match.start()].strip()
            if before:
                expanded.append(before)
            expanded.append(match.group(1).strip())
            cursor = match.end()
        tail = raw[cursor:].strip()
        if tail:
            expanded.append(tail)

    output: list[str] = []
    stop = False
    sentence_boundary = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")
    starter_boundary = re.compile(
        r"\s+(?=(?:" + "|".join(re.escape(item) for item in _CLAUSE_STARTERS) + r")\b)",
        re.IGNORECASE,
    )
    for raw in expanded:
        cleaned = raw.strip()
        low = cleaned.lower().strip(" :")
        if low in _MARKETING_HEADINGS:
            stop = True
            continue
        if stop:
            continue
        if low in heading_map:
            output.append(cleaned)
            continue
        for part in sentence_boundary.split(cleaned):
            part = part.strip()
            subparts = starter_boundary.split(part) if len(part) >= 320 else [part]
            output.extend(item.strip(" ;") for item in subparts if len(item.strip(" ;")) >= 8)
    return output


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


def _contains_domain_variant(text: str, variant: str) -> bool:
    """Match a domain phrase without allowing acronym substrings inside words.

    This keeps short taxonomy terms such as RAMS, RBD and FTA from matching
    unrelated words such as ``programs`` while preserving multi-word and
    punctuation-bearing phrases such as ``high-rise`` or ``security/defense``.
    """
    phrase = str(variant or "").strip().lower()
    if not phrase:
        return False
    prefix = r"(?<![a-z0-9])" if phrase[0].isalnum() else ""
    suffix = r"(?![a-z0-9])" if phrase[-1].isalnum() else ""
    return re.search(prefix + re.escape(phrase) + suffix, text.lower()) is not None


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
        if any(_contains_domain_variant(low, variant) for variant in variants):
            return domain
    return None


def _infer_headingless_kind(text: str, taxonomy: dict[str, Any]) -> str | None:
    """Infer qualification buckets only for unmistakable heading-less clauses."""
    low = text.lower()
    mandatory = taxonomy.get("mandatory_signals", [])
    if any(signal in low for signal in mandatory):
        if domain_requirement_gate(text, taxonomy) is not None:
            return "mandatory"
        if any(term in low for term in ("degree", "qualification", "certification", "years of experience", "must ", "required")):
            return "mandatory"
    if any(signal in low for signal in ("preferred", "desirable", "nice to have", "advantageous")):
        return "preferred"
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
    has_recognized_heading = False
    buckets: dict[str, list[str]] = {"responsibilities": [], "mandatory": [], "preferred": []}
    ambiguous: list[str] = []
    for raw in _requirement_lines(text, headings):
        kind = _heading_kind(raw, headings)
        if kind:
            section = kind
            has_recognized_heading = True
            continue
        line = BULLET_RE.sub("", raw).strip()
        if not line or len(line) < 8:
            continue
        low_line = line.lower().strip()
        if any(low_line.startswith(prefix) for prefix in _BOILERPLATE_PREFIXES):
            break
        if low_line.startswith("http://") or low_line.startswith("https://"):
            continue
        if raw.endswith(":") and len(raw.split()) <= 8:
            ambiguous.append(raw)
            continue
        inferred = (
            _infer_headingless_kind(line, taxonomy)
            if section == "responsibilities" and not has_recognized_heading
            else None
        )
        buckets.setdefault(inferred or section, []).append(line)
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
        "posting_date": str(payload.get("posting_date", "")).strip(),
        "posting_date_precision": str(payload.get("posting_date_precision", "")).strip(),
        "posting_date_source": str(payload.get("posting_date_source", "")).strip(),
        "reference": str(payload.get("reference", payload.get("external_job_id", ""))).strip(),
        "source": str(payload.get("source", "manual")),
        "source_url": str(payload.get("source_url", "")),
        "application_url": str(payload.get("application_url", "")),
        "recipient": str(payload.get("recipient", "")).strip(),
        "recipient_source": str(payload.get("recipient_source", "")).strip(),
        "required_email_subject": str(
            payload.get("required_email_subject", payload.get("email_subject", ""))
        ).strip(),
        "application_instructions": str(payload.get("application_instructions", "")).strip(),
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
            # Match against the approved claim's actual safe wording and label,
            # not only its manually assigned tags.  The previous tag-only path
            # systematically under-scored broad senior delivery/design roles
            # whose JDs describe the same work in natural prose.
            claim_corpus = " ".join([
                str(claim.get("label", "")),
                str(claim.get("safe_wording", "")),
                *[str(term) for term in claim.get("tags", [])],
                *[str(term) for term in claim.get("aliases", [])],
            ])
            claim_terms = set(_terms(claim_corpus, aliases))
            claim_terms.update(str(term).lower() for term in claim.get("tags", []) + claim.get("aliases", []))
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
    # Normalize punctuation so equivalent titles such as "Supervisor-Warehouse"
    # and "Supervisor / Warehouse" are classified the same way.
    title = re.sub(r"[^a-z0-9]+", " ", role.lower()).strip()
    spec = taxonomy.get("specialization", {})
    design_lane = spec.get("design_lane_terms", [])
    target_management = spec.get("target_management_terms", [])
    out_of_lane = spec.get("out_of_lane_terms", [])
    production = spec.get("production_terms", [])
    junior = spec.get("junior_terms", [])
    functional_out_of_lane = spec.get("functional_out_of_lane_terms", [])
    mep_specialist_terms = spec.get("mep_specialist_terms", [])
    seniority_terms = taxonomy.get("seniority_terms", [])

    def phrase(term: str) -> bool:
        return bool(re.search(r"(?<![a-z0-9])" + re.escape(term.lower()) + r"(?![a-z0-9])", title))
    has_management = any(phrase(term) for term in seniority_terms)
    has_design_lane = any(phrase(term) for term in design_lane)
    has_target_management = any(phrase(term) for term in target_management)
    has_out_of_lane = any(phrase(term) for term in out_of_lane) or phrase("account manager")
    has_production = any(phrase(term) for term in production)
    has_junior = any(phrase(term) for term in junior)
    specialist_out = any(phrase(term) for term in functional_out_of_lane)
    mep_specialist = any(phrase(term) for term in mep_specialist_terms)
    has_out_of_lane = has_out_of_lane or specialist_out or mep_specialist
    if mep_specialist and has_management:
        has_target_management = False

    if has_junior:
        specialization_factor, seniority_factor, multiplier = 0.3, 0.2, 0.4
    elif has_out_of_lane and has_management:
        specialization_factor, seniority_factor, multiplier = 0.3, 1.0, 0.35
    elif has_out_of_lane:
        specialization_factor, seniority_factor, multiplier = 0.2, 0.5, 0.35
    elif has_management and (has_design_lane or has_target_management):
        # Explicit design/project/program/delivery/construction/technical
        # management roles are central target lanes and are never suppressed.
        specialization_factor, seniority_factor, multiplier = 1.0, 1.0, 1.0
    elif has_production and not has_management:
        specialization_factor, seniority_factor, multiplier = 0.4, 0.35, 0.6
    elif has_management:
        # A generic Manager/Director title proves seniority, not functional fit.
        # Keep it potentially selective, but require an explicit target-lane
        # title before generic management can receive full fit credit.
        specialization_factor, seniority_factor, multiplier = 0.6, 1.0, 0.65
    else:
        specialization_factor, seniority_factor, multiplier = 1.0, 0.55, 1.0

    return {
        "role_title": role,
        "adjacent_design_management": bool(has_management and has_design_lane),
        "target_management": bool(has_management and has_target_management),
        "generic_management": bool(has_management and not has_design_lane and not has_target_management and not has_out_of_lane),
        "out_of_lane": has_out_of_lane,
        "functional_domain": "mep" if mep_specialist else "specialist" if specialist_out else "general",
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
                # unmatched or generic JD cannot inflate itself to 70+.
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
    # Some generic built-environment-sounding titles are actually technology
    # roles (for example "Asset Infrastructure Design"). When the JD carries
    # multiple unmistakable IT-domain signals, apply the same specialization
    # suppression used for an explicitly out-of-lane title.
    technology_signals = (
        "it infrastructure", "information technology", "computer science",
        "information systems", "network engineering", "network architecture",
        "enterprise architecture", "technology assets", "cybersecurity",
    )
    technology_hits = sum(1 for term in technology_signals if term in text)
    if technology_hits >= 2:
        signals = dict(signals)
        signals["out_of_lane"] = True
        signals["jd_out_of_lane"] = "technology_infrastructure"
        signals["mismatch_multiplier"] = min(float(signals["mismatch_multiplier"]), 0.35)
        signals["specialization_factor"] = min(float(signals["specialization_factor"]), 0.3)
    leadership_hits = sum(
        1 for term in taxonomy.get("leadership_terms", [])
        if re.search(r"(?<![a-z0-9])" + re.escape(term.lower()) + r"(?![a-z0-9])", text)
    )
    # Marketing boilerplate ("we lead", "industry-leading") is not role
    # leadership evidence. Require multiple signals or explicit responsibility context.
    responsibility_context = bool(re.search(
        r"\b(manage|supervise|direct|mentor|team of|reports to|reports directly|lead a team|project delivery)\b",
        text,
    ))
    leadership = 1.0 if leadership_hits >= 2 and responsibility_context else 0.55
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
    """Resolve the preparation route independently from vacancy verification.

    Verification is retained as provenance and confidence metadata, but an
    unverified role may still be scored and prepared. A role explicitly known
    to be closed remains blocked. External submission is governed separately
    by owner approval and current-state checks.
    """
    live_status = str(normalized_job.get("live_status", "unverified") or "unverified").strip().lower()
    if live_status == "closed":
        return to_data(RouteDecision(
            route="unresolved",
            blocker="Vacancy is explicitly marked closed",
        ))
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
        if bundle["config"]["policy"].get("portal_requires_official_url", False):
            host = (urlparse(application_url).hostname or "").lower().strip(".")
            if any(host == item or host.endswith("." + item) for item in THIRD_PARTY_APPLICATION_HOSTS):
                return to_data(RouteDecision(
                    route="unresolved",
                    blocker="Third-party aggregator URL is not an official application portal",
                ))
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
    draft_account = str(bundle.get("identity", {}).get("draft_account", "")).strip().lower()
    outward_email = str(bundle.get("identity", {}).get("outward_email", "")).strip().lower()
    if draft_account and draft_account != outward_email and draft_account in low:
        findings.append(ValidationFinding(
            "internal_email_exposed",
            "error",
            f"Internal draft mailbox must not appear in employer-facing material: {draft_account}",
            location,
        ))
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
