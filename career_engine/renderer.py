from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.shared import Pt
from docx.text.paragraph import Paragraph

from .bundle import load_bundle
from .config import load_config
from .template import status as approved_template_status


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _libreoffice_binary(home: Path | None = None) -> str:
    """Resolve LibreOffice without requiring a system-wide installation or PATH change.

    Resolution order: explicit ``CAREER_ENGINE_LIBREOFFICE`` env override, then any
    system ``libreoffice``/``soffice`` on PATH, then the newest user-local
    ``~/.local/opt/libreoffice-*/opt/libreoffice*/program/soffice``. The user-local
    glob avoids mutating the global PATH and works for non-root installs such as
    ``~/.local/opt/libreoffice-26.2.5``.
    """
    candidates: list[str] = []
    override = os.environ.get("CAREER_ENGINE_LIBREOFFICE", "").strip()
    if override:
        candidates.append(override)
    for name in ("libreoffice", "soffice"):
        resolved = shutil.which(name)
        if resolved:
            candidates.append(resolved)
            continue
        # ``shutil.which`` relies on ``os.access(..., X_OK)`` and therefore may
        # reject an executable file located on a ``noexec`` mount. Preserve PATH
        # precedence by checking permission bits directly before falling back to
        # the user-local LibreOffice installation.
        for directory in os.environ.get("PATH", "").split(os.pathsep):
            if not directory:
                continue
            path_candidate = Path(directory) / name
            if path_candidate.is_file() and path_candidate.stat().st_mode & 0o111:
                candidates.append(str(path_candidate))
                break
    user_local = sorted(
        (home or Path.home()).glob(".local/opt/libreoffice-*/opt/libreoffice*/program/soffice"),
        reverse=True,
    )
    candidates.extend(str(path) for path in user_local)
    for candidate in candidates:
        path = Path(candidate).expanduser()
        # Check the executable permission bits directly. ``os.access(..., X_OK)``
        # also reflects mount-level ``noexec`` policy, which caused valid bundled
        # binaries and isolated test fixtures to be rejected even though the file
        # itself was correctly marked executable.
        if path.is_file() and path.stat().st_mode & 0o111:
            return str(path.resolve())
    return ""


def _libreoffice_profile_dir(home: Path | None = None) -> Path:
    """Return a writable directory for isolated headless LibreOffice user profiles.

    A fresh profile per conversion avoids lock collisions between concurrent headless
    instances. The profile and LibreOffice's own scratch space must not depend on a
    small or full system temp filesystem (e.g. a 100% full ``/tmp`` tmpfs), which
    otherwise aborts conversion with a write error; the user cache directory lives on
    the main disk and is writable for non-root installs.
    """
    base = (home or Path.home()) / ".cache" / "career-engine" / "libreoffice-profiles"
    base.mkdir(parents=True, exist_ok=True)
    return base


def render_tooling() -> dict[str, Any]:
    """Report the external tooling required for DOCX -> PDF rendering and PDF verification."""
    libreoffice = _libreoffice_binary()
    pdfinfo = shutil.which("pdfinfo")
    pdftotext = shutil.which("pdftotext")
    return {
        "libreoffice": bool(libreoffice),
        "libreoffice_path": libreoffice,
        "pdfinfo": bool(pdfinfo),
        "pdftotext": bool(pdftotext),
        "pdf_conversion_available": bool(libreoffice),
        "pdf_verification_available": bool(pdfinfo and pdftotext),
    }


def template_status(*, root: Path | None = None) -> dict[str, Any]:
    return approved_template_status(root=root)


def _all_paragraphs(document: Document) -> Iterable[Paragraph]:
    # Deduplicate on the lxml element itself, never on id() values: lxml proxy
    # objects are garbage-collected eagerly and Python reuses id() slots, which
    # caused placeholder paragraphs (metric boxes, evidence cards, achievements,
    # headline) to be skipped and the renderer to fail on the approved template.
    seen: set[Any] = set()

    def walk_table(table: Any) -> Iterable[Paragraph]:
        for row in table.rows:
            for cell in row.cells:
                for paragraph in cell.paragraphs:
                    if paragraph._p not in seen:
                        seen.add(paragraph._p)
                        yield paragraph
                for nested in cell.tables:
                    yield from walk_table(nested)

    for paragraph in document.paragraphs:
        if paragraph._p not in seen:
            seen.add(paragraph._p)
            yield paragraph
    for table in document.tables:
        yield from walk_table(table)


def _replace_text(paragraph: Paragraph, text: str) -> None:
    if paragraph.runs:
        paragraph.runs[0].text = text
        for run in paragraph.runs[1:]:
            run.text = ""
    else:
        paragraph.add_run(text)


def _remove_paragraph(paragraph: Paragraph) -> None:
    parent = paragraph._p.getparent()
    if parent is not None:
        parent.remove(paragraph._p)


def _insert_paragraph_after(source: Paragraph, text: str) -> Paragraph:
    new_p = deepcopy(source._p)
    source._p.addnext(new_p)
    paragraph = Paragraph(new_p, source._parent)
    _replace_text(paragraph, text)
    return paragraph


def _claim_map(packet: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {claim["id"]: claim for claim in packet.get("selected_claims", [])}


def _metric_label(claim: dict[str, Any]) -> str:
    value = str(claim.get("value", "")).strip()
    label = str(claim.get("label", "")).strip()
    if value and label.lower().startswith(value.lower()):
        label = label[len(value):].strip(" -|:")
    return label or str(claim.get("safe_wording", ""))


def _recognized_context(packet: dict[str, Any]) -> str:
    text = " ".join(str(claim.get("safe_wording", "")) for claim in packet.get("selected_claims", []))
    names = [name for name in ("Saudi Aramco", "TotalEnergies", "Amazon") if name.lower() in text.lower()]
    sectors: list[str] = []
    low = text.lower()
    for token, label in (
        ("healthcare", "healthcare"),
        ("entertainment", "entertainment"),
        ("sports", "sports infrastructure"),
        ("government", "confidential government programmes"),
        ("logistics", "logistics and fulfilment"),
    ):
        if token in low and label not in sectors:
            sectors.append(label)
    values = names + sectors
    if not values:
        values = ["major Saudi design, supervision and project-delivery assignments"]
    if len(values) == 1:
        return values[0]
    return ", ".join(values[:-1]) + " and " + values[-1]


def build_render_input(job_id: str, generated_application: dict[str, Any], packet: dict[str, Any], *, root: Path | None = None) -> dict[str, Any]:
    config, paths = load_config(root)
    status = template_status(root=root)
    if not status["valid"]:
        raise FileNotFoundError("Approved Career Engine DOCX template is missing or invalid: " + status["path"])
    artifact_dir = paths.tracker_base / "artifacts" / job_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    render_input = {
        "schema_version": 1,
        "job_id": job_id,
        "bundle_hash": packet["bundle_hash"],
        "template": status,
        "layout": {
            "page_limit": config["template"]["page_limit"],
            "headshot_required": config["template"]["headshot_required"],
            "body_alignment": config["template"]["body_alignment"],
        },
        "outward_filename": packet["outward_filename"],
        "content": generated_application,
        "application_route": packet["application_route"],
    }
    path = artifact_dir / "render_input.json"
    path.write_text(json.dumps(render_input, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"render_input": render_input, "path": str(path)}


def render_docx(job_id: str, generated_application: dict[str, Any], packet: dict[str, Any], *, root: Path | None = None) -> dict[str, Any]:
    config, paths = load_config(root)
    template = paths.repo_root / config["template"]["repository_path"]
    status = template_status(root=root)
    if not status["valid"]:
        raise FileNotFoundError("Approved Career Engine DOCX template is missing or invalid")
    artifact_dir = paths.tracker_base / "artifacts" / job_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    outward_pdf = packet["outward_filename"]
    outward_docx = Path(outward_pdf).with_suffix(".docx").name
    destination = artifact_dir / outward_docx
    document = Document(template)
    paragraphs = list(_all_paragraphs(document))

    headline = str(generated_application["headline"]).strip()
    profile = str(generated_application["leadership_profile"]["text"]).strip()
    current_bullets = [str(item["text"]).strip() for item in generated_application["current_role_bullets"]]
    if len(current_bullets) != 7:
        raise ValueError(f"The approved template requires exactly seven generated current-role bullets, got {len(current_bullets)}")
    claim_map = _claim_map(packet)
    metric_ids = generated_application["metric_claim_ids"]
    metrics = [claim_map[item] for item in metric_ids]

    achievement_paragraphs = [p for p in paragraphs if p.text.startswith("• [ACHIEVEMENT")]
    if len(achievement_paragraphs) != 7:
        raise ValueError(f"Template achievement placeholders changed: expected 7, got {len(achievement_paragraphs)}")
    for paragraph, text in zip(achievement_paragraphs, current_bullets):
        _replace_text(paragraph, "• " + text.lstrip("• ").strip())
        paragraph.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        paragraph.paragraph_format.space_after = Pt(10)
        paragraph.paragraph_format.line_spacing = 1.08

    # The approved template originally carried two fixed current-role statements.
    # Remove them so every role bullet in the outward CV comes from the validated,
    # claim-cited generated application.
    for paragraph in list(paragraphs):
        if paragraph.text.startswith("• Directed end-to-end design and project delivery") or paragraph.text.startswith(
            "• Led multidisciplinary teams, consultants, contractors and stakeholders"
        ):
            _remove_paragraph(paragraph)

    earlier_items = generated_application.get("earlier_role_bullets", [])
    if len(earlier_items) != 11:
        raise ValueError(f"The approved template requires exactly eleven generated earlier-role bullets, got {len(earlier_items)}")

    def bullet_for_claim(claim_id: str) -> str:
        matches = [
            str(item["text"]).strip()
            for item in earlier_items
            if claim_id in item.get("claim_ids", [])
        ]
        if len(matches) != 1:
            raise ValueError(f"Expected exactly one earlier-role bullet citing {claim_id}, got {len(matches)}")
        return matches[0]

    cube_claim_order = [
        "cube.projects.25",
        "cube.team.10plus",
        "cube.assets.office_scale",
        "cube.agreements.30plus",
        "cube.bim.workflow.50",
        "cube.value_engineering.15plus",
        "cube.procurement.tender",
    ]
    cube_placeholder_starts = [
        "• Led multidisciplinary design delivery across 25 developments",
        "• Directed a core team of 10+ designers and engineers",
        "• Delivered major commercial assets",
        "• Negotiated and formalised 30+",
        "• Led BIM/Revit adoption",
        "• Delivered construction-cost savings above 15%",
        "• Managed procurement and tender strategy",
    ]
    cube_placeholders = [
        next((p for p in paragraphs if p.text.startswith(start)), None)
        for start in cube_placeholder_starts
    ]
    if any(paragraph is None for paragraph in cube_placeholders):
        raise ValueError("Template Cube Architects chronology placeholders changed")
    for paragraph, claim_id in zip(cube_placeholders, cube_claim_order):
        assert paragraph is not None
        _replace_text(paragraph, "• " + bullet_for_claim(claim_id).lstrip("• ").strip())
        paragraph.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        paragraph.paragraph_format.space_after = Pt(14)
        paragraph.paragraph_format.line_spacing = 1.08

    earlier_role_map = [
        ("Coordinated architectural design, construction documentation", "earlier.cube_project_architect.delivery"),
        ("Managed a 750-sqm retail branch", "earlier.cud.smartbuy.750"),
        ("Prepared feasibility studies and budgets", "earlier.procurement.20plus"),
        ("Developed coordinated design, tender and construction packages", "earlier.sigma.design_packages"),
    ]
    for start, claim_id in earlier_role_map:
        paragraph = next((p for p in paragraphs if p.text.startswith(start)), None)
        if paragraph is None:
            raise ValueError(f"Template earlier-career placeholder changed: {start}")
        _replace_text(paragraph, bullet_for_claim(claim_id))
        paragraph.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        paragraph.paragraph_format.space_after = Pt(18)
        paragraph.paragraph_format.line_spacing = 1.08

    replacements = {
        "[TARGET ROLE HEADLINE]": headline,
        "[TAILORED PROFILE: 55 TO 75 WORDS. POSITION THE CANDIDATE FOR THE VACANCY, USE THE OFFICIAL TITLE ONLY IN CHRONOLOGY, AND LEAD WITH THE STRONGEST EVIDENCE-SUPPORTED VALUE PROPOSITION.]": profile,
    }
    for paragraph in paragraphs:
        if paragraph.text in replacements:
            _replace_text(paragraph, replacements[paragraph.text])
            if paragraph.text == profile:
                paragraph.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
                paragraph.paragraph_format.space_after = Pt(8)
                paragraph.paragraph_format.line_spacing = 1.08

    metric_value_paragraphs = [p for p in paragraphs if re.fullmatch(r"\[M[1-6]\]", p.text)]
    metric_label_paragraphs = [p for p in paragraphs if p.text.startswith("[VACANCY-RELEVANT")]
    if len(metric_value_paragraphs) != 6 or len(metric_label_paragraphs) != 6:
        raise ValueError("Template metric placeholders changed")
    for paragraph, claim in zip(metric_value_paragraphs, metrics):
        _replace_text(paragraph, str(claim.get("value", "")))
    for paragraph, claim in zip(metric_label_paragraphs, metrics):
        _replace_text(paragraph, _metric_label(claim))

    metric_set = set(metric_ids)
    evidence_claims = [
        claim for claim in packet.get("selected_claims", [])
        if claim.get("id") not in metric_set
        and not str(claim.get("id", "")).startswith(("credential.", "education.", "cube.", "earlier."))
    ]
    if len(evidence_claims) < 4:
        evidence_claims.extend(claim for claim in metrics if claim not in evidence_claims)
    evidence_claims = evidence_claims[:4]
    evidence_titles = [p for p in paragraphs if p.text.startswith("[EVIDENCE CARD")]
    evidence_texts = [p for p in paragraphs if p.text.startswith("[One concise evidence statement")]
    if len(evidence_titles) != 4 or len(evidence_texts) != 4:
        raise ValueError("Template evidence-card placeholders changed")
    for index, (title_p, text_p, claim) in enumerate(zip(evidence_titles, evidence_texts, evidence_claims), start=1):
        _replace_text(title_p, str(claim.get("label", f"Selected evidence {index}")))
        title_p.paragraph_format.space_after = Pt(3)
        _replace_text(text_p, str(claim.get("safe_wording", "")))
        text_p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        text_p.paragraph_format.space_after = Pt(8)
        text_p.paragraph_format.line_spacing = 1.08

    for paragraph in paragraphs:
        if paragraph.text.startswith("Representative KSA context:"):
            _replace_text(paragraph, "Representative KSA context: " + _recognized_context(packet) + ".")
            paragraph.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
            paragraph.paragraph_format.space_before = Pt(6)
            paragraph.paragraph_format.space_after = Pt(6)
            break

    page_two_headings = {
        "EARLIER CAREER",
        "EDUCATION",
        "PROFESSIONAL CREDENTIALS & AFFILIATIONS",
        "LANGUAGES & NATIONALITY",
    }
    page_two_institutions = {
        "Zigurat Institute / Barcelona University, Spain",
        "New York Institute of Technology, USA | GPA 3.93/4.00, with honors",
        "University of Jordan, Jordan | GPA 3.14/4.00",
    }
    for paragraph in paragraphs:
        if paragraph.text in page_two_headings:
            paragraph.paragraph_format.space_before = Pt(6)
            paragraph.paragraph_format.space_after = Pt(6)
        elif paragraph.text in page_two_institutions:
            paragraph.paragraph_format.space_after = Pt(10)

    document.core_properties.title = f"Abdelhamid Farah - {headline}"
    document.core_properties.subject = "Vacancy-tailored curriculum vitae"
    document.core_properties.author = "Abdelhamid Farah"
    document.core_properties.keywords = "architecture, design management, project delivery, Saudi Arabia"
    document.save(destination)
    return {
        "docx": str(destination),
        "sha256": file_sha256(destination),
        "template_sha256": status["sha256"],
        "outward_filename": outward_docx,
    }


def copy_template_to_workspace(job_id: str, *, root: Path | None = None) -> dict[str, Any]:
    config, paths = load_config(root)
    template = paths.repo_root / config["template"]["repository_path"]
    if not template.is_file():
        raise FileNotFoundError(template)
    destination = paths.tracker_base / "artifacts" / job_id / template.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(template, destination)
    return {"path": str(destination), "sha256": file_sha256(destination)}


def convert_docx_to_pdf(docx_path: Path, output_dir: Path) -> dict[str, Any]:
    libreoffice = _libreoffice_binary()
    if not libreoffice:
        return {"converted": False, "blocker": "libreoffice_missing"}
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="career-engine-lo-", dir=_libreoffice_profile_dir()) as profile_dir:
        profile = Path(profile_dir).resolve()
        # Keep LibreOffice's own scratch space on the main disk next to the fresh
        # user profile: a full system /tmp would otherwise abort the export with a
        # write error even though the output directory itself has space.
        env = dict(os.environ)
        env["TMPDIR"] = str(profile)
        completed = subprocess.run(
            [
                libreoffice,
                "--headless",
                "--norestore",
                f"-env:UserInstallation={profile.as_uri()}",
                "--convert-to",
                "pdf",
                "--outdir",
                str(output_dir),
                str(docx_path),
            ],
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
            env=env,
        )
    pdf = output_dir / (docx_path.stem + ".pdf")
    return {
        "converted": completed.returncode == 0 and pdf.is_file(),
        "returncode": completed.returncode,
        "libreoffice": libreoffice,
        "pdf": str(pdf) if pdf.is_file() else "",
        "stdout": completed.stdout[-2000:],
        "stderr": completed.stderr[-2000:],
        "sha256": file_sha256(pdf) if pdf.is_file() else "",
    }


def _command_output(command: list[str], *, timeout: int = 120) -> tuple[int, str, str]:
    completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    return completed.returncode, completed.stdout, completed.stderr


def verify_pdf(pdf_path: Path, *, root: Path | None = None) -> dict[str, Any]:
    config, _ = load_config(root)
    bundle = load_bundle(root)
    findings: list[dict[str, str]] = []
    page_count = 0
    if shutil.which("pdfinfo"):
        code, out, err = _command_output(["pdfinfo", str(pdf_path)])
        if code == 0:
            match = re.search(r"^Pages:\s+(\d+)", out, re.MULTILINE)
            if match:
                page_count = int(match.group(1))
        else:
            findings.append({"severity": "error", "code": "pdfinfo_failed", "message": err[-500:]})
    if page_count != int(config["template"]["page_limit"]):
        findings.append({"severity": "error", "code": "page_count", "message": f"Expected 2 pages, got {page_count}"})
    text = ""
    if shutil.which("pdftotext"):
        code, out, err = _command_output(["pdftotext", "-layout", str(pdf_path), "-"])
        if code == 0:
            text = out
        else:
            findings.append({"severity": "error", "code": "pdftotext_failed", "message": err[-500:]})
    if not text.strip():
        findings.append({"severity": "error", "code": "missing_text_layer", "message": "PDF text layer is empty"})
    for value in bundle["config"]["policy"].get("prohibited_experience_names", []):
        if value.lower() in text.lower():
            findings.append({"severity": "error", "code": "prohibited_name", "message": value})
    for value in bundle["config"]["policy"].get("prohibited_terms", []):
        if value.lower() in text.lower():
            findings.append({"severity": "error", "code": "prohibited_term", "message": value})
    for value in bundle["config"]["policy"].get("availability_patterns", []):
        if value.lower() in text.lower():
            findings.append({"severity": "error", "code": "availability", "message": value})
    for value in bundle["config"]["policy"].get("forbidden_characters", []):
        if value in text:
            findings.append({"severity": "error", "code": "forbidden_character", "message": value})
    required = ["Abdelhamid Farah", "hameedfarah@gmail.com", "+966 53 079 6449", "Consultant"]
    for value in required:
        if value.lower() not in text.lower():
            findings.append({"severity": "error", "code": "required_text_missing", "message": value})
    return {
        "valid": not any(item["severity"] == "error" for item in findings),
        "pdf": str(pdf_path),
        "sha256": file_sha256(pdf_path),
        "page_count": page_count,
        "text_characters": len(text),
        "findings": findings,
    }


def render_and_verify(job_id: str, generated_application: dict[str, Any], packet: dict[str, Any], *, root: Path | None = None) -> dict[str, Any]:
    docx = render_docx(job_id, generated_application, packet, root=root)
    docx_path = Path(docx["docx"])
    converted = convert_docx_to_pdf(docx_path, docx_path.parent)
    if not converted.get("converted"):
        return {"valid": False, "docx": docx, "conversion": converted, "verification": {}}
    verification = verify_pdf(Path(converted["pdf"]), root=root)
    return {"valid": verification["valid"], "docx": docx, "conversion": converted, "verification": verification}
