"""Reusable ATS-safe visual design options for the ATS Linear renderer.

Five one-column, ATS-safe style variants are defined here as reusable
configuration. Every style renders the *identical* claim-grounded content
from ``generated_application.json`` and ``generation_packet.json``; styles
differ only in typography, color, spacing, rules and section order. Nothing
is invented or omitted, chronology is never reordered within a role, and no
tables/images/text-boxes/floating objects/multi-column layouts are ever used.

``ats-classic`` is the existing ATS Linear visual (the portal default). Its
fonts and margins resolve from the non-bundle template spec
``projects/job-automation/config/ats-linear-template.v1.json`` so the default
render stays content-identical (every DOCX XML part and the extracted PDF text
match the current production output). The other four variants carry explicit,
validated presentation values.
"""

from __future__ import annotations

from typing import Any

DEFAULT_STYLE_ID = "ats-classic"

_SECTION_ORDER_DEFAULT = ["summary", "experience", "education", "credentials", "languages"]
_VALID_SECTIONS = set(_SECTION_ORDER_DEFAULT)
_KNOWN_COLORS = {
    "000000": "black",
    "404040": "dark gray (classic rule)",
    "1F3864": "navy (restrained executive accent)",
    "595959": "mid gray",
    "BFBFBF": "light gray (hairline)",
}


def _presentation(**overrides: Any) -> dict[str, Any]:
    """Classic presentation defaults overlaid with per-style overrides."""
    base: dict[str, Any] = {
        "name_alignment": "center",
        "name_after": 1,
        "contact_after": 4,
        "headline_after": 2,
        "profile_after": 2,
        "section_before": 8,
        "section_after": 3,
        "section_rule": {"enabled": True, "size": 6, "space": 2},
        "role_before": 5,
        "role_after": 0,
        "role_meta_after": 2,
        "bullet_after": 2,
        "bullet_left_indent": 0.5,
        "bullet_hanging_indent": -0.5,
        "role_rule": False,
        "role_rule_size": 4,
        "role_rule_space": 2,
        "experience_heading_size_delta_pt": 0.0,
        "education_degree_after": 0,
        "education_institution_after": 2,
        "languages_after": 1,
        "nationality_after": 2,
    }
    base.update(overrides)
    return base


# Style ids are stable slugs used by the renderer, CLI, gallery manifest and
# test suite. ``fonts``/``margins_cm`` are ``None`` only for ``ats-classic``,
# which resolves them from the template spec at render time.
_RAW_STYLES: dict[str, dict[str, Any]] = {
    "ats-classic": {
        "label": "ATS Classic",
        "description": (
            "Current conservative ATS Linear design: one column, centered name block, "
            "gray section rules and standard Calibri hierarchy. Recommended default "
            "for portal uploads because it is the exact visual already shipped and "
            "verified for every portal application."
        ),
        "recommended_for_portal": True,
        "fonts": None,  # resolved from template spec
        "margins_cm": None,  # resolved from template spec
        "colors": {
            "name": "000000",
            "headline": "000000",
            "section_heading": "000000",
            "section_rule": "404040",
            "role_title": "000000",
            "role_meta": "000000",
        },
        "line_spacing": 1.0,
        "section_order": list(_SECTION_ORDER_DEFAULT),
        "presentation": _presentation(),
    },
    "ats-executive-line": {
        "label": "Executive Line",
        "description": (
            "One-column executive resume: restrained navy section headings, thin rules "
            "and a left-aligned name block. Same content and chronology as ATS Classic "
            "with a quieter, more senior typography."
        ),
        "recommended_for_portal": False,
        "fonts": {
            "name": "Calibri",
            "name_size_pt": 16,
            "contact_size_pt": 9.5,
            "headline_size_pt": 11,
            "section_heading_size_pt": 11,
            "role_title_size_pt": 10.5,
            "role_meta_size_pt": 9.5,
            "body_size_pt": 10,
            "bullet_size_pt": 10,
        },
        "margins_cm": {"top": 1.4, "bottom": 1.4, "left": 1.8, "right": 1.8},
        "colors": {
            "name": "1F3864",
            "headline": "1F3864",
            "section_heading": "1F3864",
            "section_rule": "1F3864",
            "role_title": "1F3864",
            "role_meta": "404040",
        },
        "line_spacing": 1.0,
        "section_order": list(_SECTION_ORDER_DEFAULT),
        "presentation": _presentation(
            name_alignment="left",
            contact_after=3,
            section_before=7,
            section_after=2,
            section_rule={"enabled": True, "size": 4, "space": 2},
            role_before=4,
        ),
    },
    "ats-compact-technical": {
        "label": "Compact Technical",
        "description": (
            "Denser single-column layout for screen-first technical review: tighter "
            "spacing, smaller body with a clear role/credential hierarchy and navy "
            "section headings. Every factual sentence is retained."
        ),
        "recommended_for_portal": False,
        "fonts": {
            "name": "Calibri",
            "name_size_pt": 15.5,
            "contact_size_pt": 9.5,
            "headline_size_pt": 11,
            "section_heading_size_pt": 11,
            "role_title_size_pt": 10.5,
            "role_meta_size_pt": 9.5,
            "body_size_pt": 10,
            "bullet_size_pt": 10,
        },
        "margins_cm": {"top": 1.2, "bottom": 1.2, "left": 1.5, "right": 1.5},
        "colors": {
            "name": "000000",
            "headline": "000000",
            "section_heading": "1F3864",
            "section_rule": "1F3864",
            "role_title": "000000",
            "role_meta": "404040",
        },
        "line_spacing": 1.0,
        "section_order": list(_SECTION_ORDER_DEFAULT),
        "presentation": _presentation(
            name_alignment="left",
            name_after=1,
            contact_after=3,
            section_before=6,
            section_after=2,
            section_rule={"enabled": True, "size": 4, "space": 2},
            role_before=4,
            role_meta_after=1,
            bullet_after=1,
            bullet_left_indent=0.4,
            bullet_hanging_indent=-0.4,
            education_institution_after=1,
        ),
    },
    "ats-minimal-modern": {
        "label": "Minimal Modern",
        "description": (
            "Monochrome, whitespace-led layout with clean typography and a hairline "
            "section rule. Near-monochrome ink (black text, light gray hairline only) "
            "with generous margins for a modern, minimal impression."
        ),
        "recommended_for_portal": False,
        "fonts": {
            "name": "Calibri",
            "name_size_pt": 18,
            "contact_size_pt": 10,
            "headline_size_pt": 11.5,
            "section_heading_size_pt": 11.5,
            "role_title_size_pt": 11,
            "role_meta_size_pt": 10,
            "body_size_pt": 10.5,
            "bullet_size_pt": 10.5,
        },
        "margins_cm": {"top": 1.6, "bottom": 1.6, "left": 2.0, "right": 2.0},
        "colors": {
            "name": "000000",
            "headline": "000000",
            "section_heading": "000000",
            "section_rule": "BFBFBF",
            "role_title": "000000",
            "role_meta": "595959",
        },
        "line_spacing": 1.12,
        "section_order": list(_SECTION_ORDER_DEFAULT),
        "presentation": _presentation(
            name_alignment="left",
            name_after=2,
            contact_after=5,
            headline_after=3,
            section_before=10,
            section_after=4,
            section_rule={"enabled": True, "size": 2, "space": 3},
            role_before=6,
            bullet_after=3,
            education_institution_after=3,
            languages_after=2,
            nationality_after=2,
        ),
    },
    "ats-project-led": {
        "label": "Project Led",
        "description": (
            "Delivery-evidence-first layout: the PROFESSIONAL EXPERIENCE section leads "
            "the document with navy role headings and thin rules separating each "
            "verified delivery block. Full chronology and every factual sentence are "
            "preserved - only section order and emphasis change."
        ),
        "recommended_for_portal": False,
        "fonts": {
            "name": "Calibri",
            "name_size_pt": 17,
            "contact_size_pt": 10,
            "headline_size_pt": 11.5,
            "section_heading_size_pt": 11.5,
            "role_title_size_pt": 12,
            "role_meta_size_pt": 10,
            "body_size_pt": 10.5,
            "bullet_size_pt": 10.5,
        },
        "margins_cm": {"top": 1.3, "bottom": 1.3, "left": 1.6, "right": 1.6},
        "colors": {
            "name": "000000",
            "headline": "000000",
            "section_heading": "1F3864",
            "section_rule": "1F3864",
            "role_title": "1F3864",
            "role_meta": "404040",
        },
        "line_spacing": 1.0,
        "section_order": ["experience", "summary", "education", "credentials", "languages"],
        "presentation": _presentation(
            name_alignment="center",
            section_before=7,
            section_after=2,
            section_rule={"enabled": True, "size": 5, "space": 2},
            role_before=6,
            role_after=0,
            role_rule=True,
            role_rule_size=4,
            role_rule_space=2,
            experience_heading_size_delta_pt=1.0,
        ),
    },
}

_VALID_STYLE_IDS = set(_RAW_STYLES)


def available_style_ids() -> list[str]:
    return list(_RAW_STYLES)


def style_list() -> list[dict[str, Any]]:
    """Return style metadata (without resolved fonts) for registry/CLI use."""
    entries: list[dict[str, Any]] = []
    for style_id in _VALID_STYLE_IDS:
        raw = _RAW_STYLES[style_id]
        entries.append({
            "id": style_id,
            "label": raw["label"],
            "description": raw["description"],
            "recommended_for_portal": raw["recommended_for_portal"],
            "default": style_id == DEFAULT_STYLE_ID,
            "section_order": list(raw["section_order"]),
            "color_scheme": {key: {"hex": value, "name": _KNOWN_COLORS.get(value, value)} for key, value in raw["colors"].items()},
        })
    return entries


def resolve_style(style_id: str, template_spec: dict[str, Any]) -> dict[str, Any]:
    """Resolve a full style spec for rendering.

    ``ats-classic`` resolves its fonts and margins from the template spec so the
    default render matches the existing ATS Linear output (identical document
    XML parts and extracted PDF text). Unknown style ids raise ``ValueError``.
    """
    if style_id not in _RAW_STYLES:
        raise ValueError(f"Unknown ATS design style {style_id!r}; available: {sorted(_VALID_STYLE_IDS)}")
    raw = _RAW_STYLES[style_id]
    if style_id == DEFAULT_STYLE_ID:
        fonts = {key: value for key, value in (template_spec.get("fonts") or {}).items()}
        margins = {key: value for key, value in (template_spec.get("margins_cm") or {}).items()}
        if not fonts or not margins:
            raise ValueError("ATS Classic style requires the ats-linear-template.v1.json fonts and margins")
        default_family = fonts.get("name", "Calibri")
    else:
        fonts = dict(raw["fonts"])
        margins = dict(raw["margins_cm"])
        default_family = fonts.get("name", "Calibri")
    resolved = {
        "id": style_id,
        "label": raw["label"],
        "description": raw["description"],
        "recommended_for_portal": raw["recommended_for_portal"],
        "default": style_id == DEFAULT_STYLE_ID,
        "fonts": fonts,
        "default_font_family": str(default_family),
        "margins_cm": margins,
        "colors": dict(raw["colors"]),
        "line_spacing": float(raw["line_spacing"]),
        "section_order": list(raw["section_order"]),
        "presentation": {key: (dict(value) if isinstance(value, dict) else value) for key, value in raw["presentation"].items()},
    }
    validate_style(resolved)
    return resolved


def validate_style(style: dict[str, Any]) -> list[str]:
    """Structural validation of a resolved style spec. Returns problem messages."""
    problems: list[str] = []
    if not style.get("id"):
        problems.append("style id missing")
    fonts = style.get("fonts") or {}
    for key in ("name", "name_size_pt", "contact_size_pt", "headline_size_pt", "section_heading_size_pt", "role_title_size_pt", "role_meta_size_pt", "body_size_pt", "bullet_size_pt"):
        if key not in fonts:
            problems.append(f"font {key} missing")
    if any(float(fonts[key]) <= 0 for key in fonts if key != "name"):
        problems.append("non-positive font size")
    margins = style.get("margins_cm") or {}
    for key in ("top", "bottom", "left", "right"):
        if key not in margins or float(margins[key]) < 0.5:
            problems.append(f"margin {key} missing or too small")
    for key, value in (style.get("colors") or {}).items():
        if not isinstance(value, str) or len(value) != 6 or not all(char in "0123456789abcdefABCDEF" for char in value):
            problems.append(f"color {key} not a 6-digit hex: {value!r}")
    order = style.get("section_order") or []
    if set(order) != _VALID_SECTIONS:
        problems.append(f"section_order must cover exactly {sorted(_VALID_SECTIONS)}")
    presentation = style.get("presentation") or {}
    if "section_rule" in presentation and not isinstance(presentation["section_rule"], dict):
        problems.append("section_rule must be a dict")
    return problems
