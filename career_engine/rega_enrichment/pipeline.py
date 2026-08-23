"""Orchestrator — deterministic REGA enrichment pipeline."""

from __future__ import annotations

import csv
import hashlib
import json
import pathlib
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

from .config import freeze_manifest, IDENTITY_FIELDS
from .models import CompanyRecord, EvidenceRecord, EnrichmentRow
from .discovery import discover_company
from .verify import verify_candidate
from .extract import extract_fields
from .cache import default_cache_dir, cache_stats

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

def load_canonical(path: Path) -> list[CompanyRecord]:
    rows: list[CompanyRecord] = []
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        # Verify identity fields present
        missing = [fn for fn in IDENTITY_FIELDS if fn not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"Canonical input missing identity fields: {missing}")
        for idx, r in enumerate(reader, 1):
            lic = (r.get("License No") or "").strip()
            # company_id is stable 1-indexed; license_no is immutable join key
            rows.append(CompanyRecord(
                company_id=str(idx),
                license_no=lic,
                english_name=(r.get("English Name") or "").strip(),
                arabic_name=(r.get("Arabic Name") or "").strip(),
                location=(r.get("English Location(s)") or "").strip(),
                career_priority=(r.get("Career Priority") or "").strip(),
                research_status=(r.get("Research Status") or "").strip(),
            ))
    return rows

def enrich_company(
    company: CompanyRecord,
    delay_s: float = 0.4,
    use_cache: bool = True,
    refresh: bool = False,
    cache_dir: pathlib.Path | None = None,
) -> EnrichmentRow:
    row = EnrichmentRow(company=company)
    # Discovery — REGA uses SearXNG Qwant cache, Firecrawl reserved for fetch
    candidates = discover_company(
        company, limit_per_query=5, delay_s=delay_s, use_cache=use_cache, refresh=refresh, cache_dir=cache_dir
    )
    if not candidates:
        row.assignment = "not_found"
        row.confidence = "not_found"
        row.notes = "No search candidates returned; discovery failed or blocked."
        return row
    # Deduplicate by URL (keep earliest query_id)
    dedup: dict[str, Any] = {}
    for c in candidates:
        if c.url not in dedup:
            dedup[c.url] = c
    unique_cands = list(dedup.values())
    # Pre-score candidates by discovery signals to prioritize verification and save quota
    # Deterministic pre-sort: host contains distinctive token -> higher priority
    from .config import GENERIC_TOKENS as _GT
    from .models import distinctive_tokens as _dt, hostname_tokens as _ht
    from urllib.parse import urlsplit as _us
    def _pre_score(c):
        host = (_us(c.url).hostname or "").lower()
        host_norm = _ht(host)
        toks = _dt(company.english_name, _GT)
        s = 0
        for tok in toks:
            if tok in host_norm:
                s += 10
            if tok in c.title.lower():
                s += 5
        # Prefer earlier discovery position (lower) and Firecrawl over SearXNG Bing
        if "firecrawl" in c.engine:
            s += 2
        if ".sa" in host:
            s += 3
        s -= (c.position or 5)
        return -s  # negative for sort ascending
    unique_cands.sort(key=lambda x: (_pre_score(x), x.url))
    # Limit verification to top 8 to preserve Firecrawl quota and determinism (still carry all rejected as not_found)
    to_verify = unique_cands[:8]
    remaining = unique_cands[8:]
    verified: list[Any] = []
    rejected: list[Any] = []
    for cand in to_verify:
        v = verify_candidate(cand, company)
        if v.verification_status in ("confirmed", "candidate"):
            verified.append(v)
        elif v.verification_status == "unconfirmed":
            rejected.append(v)
        else:
            rejected.append(v)
        time.sleep(0.4)
    # Remaining beyond top 8 are treated as rejected without fetch (to avoid quota)
    for cand in remaining:
        cand.verification_status = "rejected"
        cand.verification_method = "rejected_unrelated"
        cand.verification_score = -5
        cand.verification_evidence = "Not verified — beyond top-8 pre-score cutoff; discovery only"
        rejected.append(cand)

    # Sort verified by score desc, then confirmed first
    verified.sort(key=lambda x: (0 if x.verification_status=="confirmed" else 1, -x.verification_score, x.url))
    rejected.sort(key=lambda x: (-x.verification_score))

    row.rejected_candidates = rejected
    if verified:
        # Prefer confirmed with highest score
        best = verified[0]
        # Only accept if confirmed or candidate with score >=8 and host token
        if best.verification_status == "confirmed" and best.verification_score >= 10:
            row.best_candidate = best
            row.assignment = "confirmed"
            row.confidence = "confirmed"
        elif best.verification_status in ("confirmed","candidate") and best.verification_score >= 8:
            row.best_candidate = best
            row.assignment = "candidate"
            row.confidence = "candidate"
        else:
            row.best_candidate = None
            row.assignment = "not_found"
            row.confidence = "not_found"
            row.rejected_candidates = verified + rejected
            row.notes = "Candidates found but none passed identity verification threshold."
            return row
        # Extract fields from official domain — normalize to homepage root, not subpath
        raw_candidate_url = best.url.rstrip("/")
        # Derive homepage as https://host/ for official_website field
        from urllib.parse import urlsplit as _urlsplit
        host = (_urlsplit(raw_candidate_url).hostname or "").lower()
        if host:
            homepage = f"https://{host}/"
        else:
            homepage = raw_candidate_url
            host = ""
        official_url = homepage
        # Ensure https
        if not official_url.startswith("http"):
            official_url = "https://" + official_url
        try:
            values, evidence, debug = extract_fields(company, official_url)
            # Preserve best candidate original path as careers candidate hint (not yet, extract handles discovery)
            for k, v in values.items():
                setattr(row, k, v)
            row.evidence = evidence
            # Add verification evidence for official website
            row.notes = f"Verified via {best.verification_method} score {best.verification_score:.1f}; {best.verification_evidence[:300]}"
            if debug:
                row.notes += f" | debug: {debug}"
        except Exception as e:
            row.notes = f"Verification succeeded but extraction failed: {type(e).__name__}: {e}"
            # still keep official website
            row.official_website = official_url
            row.official_domain = (official_url.split("//")[-1].split("/")[0].replace("www.",""))
            # evidence for website still
            row.evidence.append(EvidenceRecord(
                company_id=company.company_id, license_no=company.license_no, field="official_website", value=official_url, source_url=official_url, source_type="official_page",
                evidence_text=best.verification_evidence[:600], verified_at=utc_now(), confidence="confirmed", verification_method=best.verification_method
            ))
    else:
        row.assignment = "not_found"
        row.confidence = "not_found"
        row.notes = "No candidate passed identity verification; all were rejected or unconfirmed."
        # keep top rejected for reporting
        if rejected:
            row.best_candidate = rejected[0]

    return row

def run_pipeline(
    canonical_path: Path,
    out_dir: Path,
    company_ids: list[str] | None = None,
    license_nos: list[str] | None = None,
    delay_s: float = 0.4,
    limit: int | None = None,
    use_cache: bool = True,
    refresh: bool = False,
    cache_dir: Path | None = None,
    workers: int = 1,
) -> dict[str, Any]:
    workers = max(1, min(int(workers), 16))
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = freeze_manifest(canonical_path)
    all_companies = load_canonical(canonical_path)
    if company_ids is not None or license_nos is not None:
        id_sel = set(company_ids or [])
        license_sel = set(license_nos or [])
        companies = [
            c for c in all_companies
            if c.company_id in id_sel or c.license_no in license_sel
        ]
    else:
        companies = all_companies
    if limit:
        companies = companies[:limit]

    # Deterministic ordering
    companies.sort(key=lambda x: int(x.company_id) if x.company_id.isdigit() else x.company_id)

    # Prepare output paths with version + timestamp
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    sidecar_path = out_dir / f"rega-enrichment-sidecar-{ts}.csv"
    evidence_path = out_dir / f"rega-enrichment-evidence-{ts}.jsonl"
    manifest_path = out_dir / f"rega-enrichment-manifest-{ts}.json"
    rejected_path = out_dir / f"rega-enrichment-rejected-{ts}.jsonl"

    # Cache dir resolved
    effective_cache_dir = cache_dir or default_cache_dir()
    cache_before = cache_stats(effective_cache_dir)
    # Write manifest upfront (freeze)
    manifest.update({
        "run_started_at": utc_now(),
        "out_dir": str(out_dir),
        "canonical_row_count": len(all_companies),
        "selected_count": len(companies),
        "company_ids": [c.company_id for c in companies],
        "requested_company_ids": list(company_ids or []),
        "requested_license_nos": list(license_nos or []),
        "cache_dir": str(effective_cache_dir),
        "cache_files_before": cache_before.get("files", 0),
        "use_cache": use_cache,
        "refresh": refresh,
        "workers": workers,
    })
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    fieldnames = ["company_id","License No","English Name","Arabic Name","English Location(s)","Career Priority","Research Status",
                  "official_website","official_domain","linkedin_company_page","general_email","main_phone","careers_page","ats_url","ats_domain","recruitment_email","ttw_bd_email","procurement_email","supplier_registration_url",
                  "assignment","confidence","best_candidate_url","best_candidate_title","best_candidate_engine","rejected_count","evidence_count","notes"]

    evidence_f = evidence_path.open("w", encoding="utf-8")
    rejected_f = rejected_path.open("w", encoding="utf-8")

    def _safe_enrich(comp: CompanyRecord) -> EnrichmentRow:
        try:
            return enrich_company(
                comp, delay_s=delay_s, use_cache=use_cache, refresh=refresh, cache_dir=effective_cache_dir
            )
        except Exception as exc:
            # Do not fabricate; preserve blank with error note and immutable identity.
            return EnrichmentRow(
                company=comp,
                assignment="not_found",
                confidence="not_found",
                notes=f"Pipeline exception: {type(exc).__name__}: {exc}",
            )

    rows_written = 0
    try:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="rega-company") as executor:
            enriched_rows = executor.map(_safe_enrich, companies)
            with sidecar_path.open("w", newline="", encoding="utf-8") as csvfile:
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                writer.writeheader()
                for enriched in enriched_rows:
                    # Write evidence
                    for ev in enriched.evidence:
                        evidence_f.write(json.dumps({
                            "company_id": ev.company_id,
                            "license_no": ev.license_no,
                            "field": ev.field,
                            "value": ev.value,
                            "source_url": ev.source_url,
                            "source_type": ev.source_type,
                            "evidence_text": ev.evidence_text,
                            "verified_at": ev.verified_at,
                            "confidence": ev.confidence,
                            "verification_method": ev.verification_method,
                        }, ensure_ascii=False) + "\n")
                    # Write rejected candidates
                    for rc in enriched.rejected_candidates:
                        rejected_f.write(json.dumps({
                            "company_id": rc.company_id,
                            "license_no": rc.license_no,
                            "query_id": rc.query_id,
                            "url": rc.url,
                            "title": rc.title,
                            "engine": rc.engine,
                            "verification_status": rc.verification_status,
                            "verification_score": rc.verification_score,
                            "verification_method": rc.verification_method,
                            "verification_evidence": rc.verification_evidence,
                        }, ensure_ascii=False) + "\n")

                    writer.writerow({
                        "company_id": enriched.company.company_id,
                        "License No": enriched.company.license_no,
                        "English Name": enriched.company.english_name,
                        "Arabic Name": enriched.company.arabic_name,
                        "English Location(s)": enriched.company.location,
                        "Career Priority": enriched.company.career_priority,
                        "Research Status": enriched.company.research_status,
                        "official_website": enriched.official_website,
                        "official_domain": enriched.official_domain,
                        "linkedin_company_page": enriched.linkedin_company_page,
                        "general_email": enriched.general_email,
                        "main_phone": enriched.main_phone,
                        "careers_page": enriched.careers_page,
                        "ats_url": enriched.ats_url,
                        "ats_domain": enriched.ats_domain,
                        "recruitment_email": enriched.recruitment_email,
                        "ttw_bd_email": enriched.ttw_bd_email,
                        "procurement_email": enriched.procurement_email,
                        "supplier_registration_url": enriched.supplier_registration_url,
                        "assignment": enriched.assignment,
                        "confidence": enriched.confidence,
                        "best_candidate_url": enriched.best_candidate.url if enriched.best_candidate else "",
                        "best_candidate_title": enriched.best_candidate.title if enriched.best_candidate else "",
                        "best_candidate_engine": enriched.best_candidate.engine if enriched.best_candidate else "",
                        "rejected_count": len(enriched.rejected_candidates),
                        "evidence_count": len(enriched.evidence),
                        "notes": enriched.notes,
                    })
                    rows_written += 1
                    evidence_f.flush()
                    rejected_f.flush()
                    csvfile.flush()
                    if workers == 1:
                        time.sleep(0.5)
    finally:
        evidence_f.close()
        rejected_f.close()

    # Finalize manifest — cache stats after, firecrawl usage (estimated from rejected evidence)
    cache_after = cache_stats(effective_cache_dir)
    # Count Firecrawl calls from evidence/rejected (search vs extract are tracked via source_type/engine)
    # For now, count cache files created during run
    output_sha = hashlib.sha256(sidecar_path.read_bytes()).hexdigest()
    evidence_sha = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
    rejected_sha = hashlib.sha256(rejected_path.read_bytes()).hexdigest()
    manifest.update({
        "run_finished_at": utc_now(),
        "sidecar_path": str(sidecar_path),
        "sidecar_sha256": output_sha,
        "sidecar_rows": rows_written,
        "evidence_path": str(evidence_path),
        "evidence_sha256": evidence_sha,
        "rejected_path": str(rejected_path),
        "rejected_sha256": rejected_sha,
        "manifest_path": str(manifest_path),
        "cache_files_after": cache_after.get("files", 0),
        "cache_files_created": cache_after.get("files", 0) - cache_before.get("files", 0),
        "firecrawl_search_calls_estimate": 0,
        "firecrawl_extract_calls_estimate": rows_written * 2 if manifest.get("credentials_configured") else 0,
    })
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    return manifest

def build_regression_set(canonical_path: Path) -> list[CompanyRecord]:
    """Build 20-company regression set: 15 known + 5 obscure/problematic."""
    all_companies = load_canonical(canonical_path)
    # Known 15 as per instruction — some are giga-projects not in REGA queue.
    # For those not in REGA, we will create synthetic CompanyRecord with license_no prefixed "REGRESSION-"
    # But for those that are in queue, map to existing license_no.
    # Define expected mapping for in-queue ones
    queue_by_english = {c.english_name.lower(): c for c in all_companies}
    queue_by_arabic = {c.arabic_name: c for c in all_companies}

    # Known companies list with expected details (license_no if in REGA else synthetic)
    known_definitions = [
        # in-REGA
        {"english": "Roshn Group (Closed Joint)", "arabic": "شركة مجموعة روشن شركة شخص واحد مساهمة مقفلة", "license": "382", "location": "Riyadh"},
        {"english": "Makkyoon Urban Developers", "arabic": "شركة مكيون مطورون عمرانيون مساهمة مقفلة", "license": "365", "location": "Makkah"},
        {"english": "Abdullah Al Othaim Invest.", "arabic": "شركة عبدالله العثيم للاستثمار مساهمة مقفلة", "license": "1877", "location": "Riyadh"},
        {"english": "Tilal Al Khozama for Dev & Invest.", "arabic": "شركة تلال الخزام للتطوير والاستثمار العقاري", "license": "1641", "location": "Riyadh"},
        {"english": "Sumou Real Estate (Listed)", "arabic": "شركة سمو العقارية مساهمة مدرجة", "license": "1431", "location": "Eastern Prov."},
        # not in REGA — giga projects
        {"english": "NHC", "arabic": "الشركة الوطنية للإسكان", "license": "REG-NHC", "location": "Riyadh"},
        {"english": "Diriyah Company", "arabic": "شركة الدرعية", "license": "REG-DIRIYAH", "location": "Riyadh"},
        {"english": "Saudi Downtown Company", "arabic": "شركة وسط السعودية", "license": "REG-SAUDI-DOWNTOWN", "location": "Riyadh"},
        {"english": "Retal Urban Development Company", "arabic": "شركة رتال للتطوير العمراني", "license": "REG-RETAL", "location": "Khobar"},
        {"english": "Ajdan Real Estate Development", "arabic": "شركة أجدان للتطوير العقاري", "license": "REG-AJDAN", "location": "Khobar"},
        {"english": "Dar Wa Emaar Real Estate Investment & Development", "arabic": "شركة دار وإعمار للاستثمار والتطوير العقاري", "license": "REG-DARWAEMAAR", "location": "Khobar"},
        {"english": "RAFAL Real Estate Development Company", "arabic": "شركة رافال للتطوير العقاري", "license": "REG-RAFAL", "location": "Riyadh"},
        {"english": "Al Akaria Saudi Real Estate Company", "arabic": "الشركة العقارية السعودية", "license": "REG-AKARIA", "location": "Riyadh"},
        {"english": "Mohammad Al Habib Real Estate Company", "arabic": "شركة محمد الحبيب العقارية", "license": "REG-HABIB", "location": "Riyadh"},
        {"english": "Al Rajhi United", "arabic": "شركة الراجحي المتحدة", "license": "REG-RAJHI-UNITED", "location": "Riyadh"},
    ]
    regression: list[CompanyRecord] = []
    for idx, kd in enumerate(known_definitions, 1):
        # Try to find in queue first
        existing = None
        for c in all_companies:
            if c.license_no == kd["license"]:
                existing = c
                break
        if existing:
            regression.append(existing)
        else:
            # synthetic
            regression.append(CompanyRecord(
                company_id=f"REG-{idx:02d}",
                license_no=kd["license"],
                english_name=kd["english"],
                arabic_name=kd["arabic"],
                location=kd["location"],
                career_priority="A",
                research_status="Regression - known company",
            ))
    # 5 obscure/problematic from REGA — pick generic names that previously caused false positives
    obscure_candidates = []
    for c in all_companies:
        # Pick those with very generic tokens or previously not_found
        eng = c.english_name
        # Generic short names, or names that produced wrong domains in failed run
        if eng in ["Al Ra'em International Co.", "Remar Real Estate", "Anan Real Estate", "Arwaqa for Invest & Dev.", "Osus Al-Aqar"]:
            obscure_candidates.append(c)
    # Ensure 5
    while len(obscure_candidates) < 5:
        for c in all_companies:
            if c not in regression and c not in obscure_candidates:
                # pick a short generic one
                if len(c.english_name.split()) <= 3:
                    obscure_candidates.append(c)
                    if len(obscure_candidates) >= 5:
                        break
    regression.extend(obscure_candidates[:5])
    # Assign stable company_id for regression set (1..20)
    # Keep license_no immutable; company_id is regression index
    final: list[CompanyRecord] = []
    for idx, comp in enumerate(regression[:20], 1):
        final.append(CompanyRecord(
            company_id=str(idx),
            license_no=comp.license_no,
            english_name=comp.english_name,
            arabic_name=comp.arabic_name,
            location=comp.location,
            career_priority=comp.career_priority,
            research_status=comp.research_status,
        ))
    return final
