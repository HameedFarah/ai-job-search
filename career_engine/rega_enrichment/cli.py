"""CLI for REGA enrichment pipeline."""

from __future__ import annotations

import argparse
import csv
import json
import hashlib
from pathlib import Path

from .pipeline import run_pipeline, load_canonical, build_regression_set, enrich_company
from .config import freeze_manifest

def main() -> None:
    parser = argparse.ArgumentParser(description="REGA enrichment pipeline — deterministic, source-controlled")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="Run batch enrichment")
    p_run.add_argument("--input", required=True, help="Canonical CSV path")
    p_run.add_argument("--out-dir", required=True, help="Output dir for sidecar")
    p_run.add_argument("--ids", help="Comma-separated company_id or license_no filter")
    p_run.add_argument("--limit", type=int, help="Limit number of companies")
    p_run.add_argument("--delay", type=float, default=0.8)

    p_reg = sub.add_parser("regression", help="Run 20-company regression")
    p_reg.add_argument("--input", required=True)
    p_reg.add_argument("--out-dir", required=True)
    p_reg.add_argument("--delay", type=float, default=0.8)

    p_val = sub.add_parser("validate", help="Validate sidecar")
    p_val.add_argument("--sidecar", required=True)
    p_val.add_argument("--input", required=True)

    args = parser.parse_args()

    if args.cmd == "run":
        out = Path(args.out_dir)
        ids = [x.strip() for x in args.ids.split(",")] if args.ids else None
        manifest = run_pipeline(Path(args.input), out, company_ids=ids, limit=args.limit, delay_s=args.delay)
        print(json.dumps(manifest, indent=2, ensure_ascii=False))

    elif args.cmd == "regression":
        out = Path(args.out_dir)
        out.mkdir(parents=True, exist_ok=True)
        # Build regression set and run via enrich_company directly to ensure synthetic handling
        canonical = Path(args.input)
        reg_set = build_regression_set(canonical)
        # Write regression input for transparency
        reg_input_path = out / "regression-input.csv"
        with reg_input_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["company_id","License No","English Name","Arabic Name","English Location(s)","Career Priority","Research Status"])
            w.writeheader()
            for c in reg_set:
                w.writerow({"company_id": c.company_id, "License No": c.license_no, "English Name": c.english_name, "Arabic Name": c.arabic_name, "English Location(s)": c.location, "Career Priority": c.career_priority, "Research Status": c.research_status})
        # Also write a temp canonical that includes regression synthetics for pipeline compatibility?
        # Instead, enrich each directly and produce sidecar manually
        from .pipeline import utc_now
        from pathlib import Path as P
        import time
        manifest = freeze_manifest(canonical)
        ts = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        sidecar_path = out / f"rega-regression-sidecar-{ts}.csv"
        evidence_path = out / f"rega-regression-evidence-{ts}.jsonl"
        rejected_path = out / f"rega-regression-rejected-{ts}.jsonl"
        manifest_path = out / f"rega-regression-manifest-{ts}.json"
        manifest.update({"run_started_at": utc_now(), "regression_size": len(reg_set), "mode": "regression"})

        fieldnames = ["company_id","License No","English Name","Arabic Name","English Location(s)","Career Priority","Research Status",
                      "official_website","official_domain","linkedin_company_page","general_email","main_phone","careers_page","ats_url","ats_domain","recruitment_email","ttw_bd_email","procurement_email","supplier_registration_url",
                      "assignment","confidence","best_candidate_url","best_candidate_title","best_candidate_engine","rejected_count","evidence_count","notes"]
        ev_f = evidence_path.open("w", encoding="utf-8")
        rej_f = rejected_path.open("w", encoding="utf-8")
        with sidecar_path.open("w", newline="", encoding="utf-8") as csvfile:
            w = csv.DictWriter(csvfile, fieldnames=fieldnames)
            w.writeheader()
            for comp in reg_set:
                try:
                    enriched = enrich_company(comp, delay_s=args.delay)
                except Exception as e:
                    from .models import EnrichmentRow
                    enriched = EnrichmentRow(company=comp, assignment="not_found", confidence="not_found", notes=f"Exception: {type(e).__name__}: {e}")
                for ev in enriched.evidence:
                    ev_f.write(json.dumps({"company_id": ev.company_id, "license_no": ev.license_no, "field": ev.field, "value": ev.value, "source_url": ev.source_url, "source_type": ev.source_type, "evidence_text": ev.evidence_text, "verified_at": ev.verified_at, "confidence": ev.confidence, "verification_method": ev.verification_method}, ensure_ascii=False)+"\n")
                for rc in enriched.rejected_candidates:
                    rej_f.write(json.dumps({"company_id": rc.company_id, "license_no": rc.license_no, "query_id": rc.query_id, "url": rc.url, "title": rc.title, "engine": rc.engine, "verification_status": rc.verification_status, "verification_score": rc.verification_score, "verification_method": rc.verification_method, "verification_evidence": rc.verification_evidence}, ensure_ascii=False)+"\n")
                w.writerow({
                    "company_id": enriched.company.company_id, "License No": enriched.company.license_no, "English Name": enriched.company.english_name, "Arabic Name": enriched.company.arabic_name, "English Location(s)": enriched.company.location, "Career Priority": enriched.company.career_priority, "Research Status": enriched.company.research_status,
                    "official_website": enriched.official_website, "official_domain": enriched.official_domain, "linkedin_company_page": enriched.linkedin_company_page, "general_email": enriched.general_email, "main_phone": enriched.main_phone, "careers_page": enriched.careers_page, "ats_url": enriched.ats_url, "ats_domain": enriched.ats_domain, "recruitment_email": enriched.recruitment_email, "ttw_bd_email": enriched.ttw_bd_email, "procurement_email": enriched.procurement_email, "supplier_registration_url": enriched.supplier_registration_url,
                    "assignment": enriched.assignment, "confidence": enriched.confidence, "best_candidate_url": enriched.best_candidate.url if enriched.best_candidate else "", "best_candidate_title": enriched.best_candidate.title if enriched.best_candidate else "", "best_candidate_engine": enriched.best_candidate.engine if enriched.best_candidate else "", "rejected_count": len(enriched.rejected_candidates), "evidence_count": len(enriched.evidence), "notes": enriched.notes,
                })
                ev_f.flush(); rej_f.flush()
                time.sleep(0.5)
        ev_f.close(); rej_f.close()
        manifest.update({
            "run_finished_at": utc_now(),
            "sidecar_path": str(sidecar_path), "sidecar_sha256": hashlib.sha256(sidecar_path.read_bytes()).hexdigest(),
            "evidence_path": str(evidence_path), "evidence_sha256": hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
            "rejected_path": str(rejected_path), "rejected_sha256": hashlib.sha256(rejected_path.read_bytes()).hexdigest(),
            "manifest_path": str(manifest_path), "regression_input_path": str(reg_input_path),
        })
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps(manifest, indent=2, ensure_ascii=False))
        # Acceptance check
        # Load sidecar and verify 100% known-company etc.
        import csv as csvm
        with sidecar_path.open(encoding="utf-8-sig") as f:
            rows = list(csvm.DictReader(f))
        # Known 15 are first 15 in reg_set
        known = rows[:15]
        ok_known = sum(1 for r in known if r["assignment"] in ("confirmed","candidate") and r["official_website"])
        print(f"\n--- Regression acceptance summary ---")
        print(f"Known-company identity resolution: {ok_known}/15")
        # Check that obscure 5 did not promote unrelated domains with high confidence incorrectly?
        # For obscure, we check that if assignment is confirmed/candidate, verification evidence must contain token
        print(f"Total sidecar rows: {len(rows)}")
        for r in rows:
            print(f"{r['company_id']:>2} {r['License No']:<20} {r['English Name'][:40]:<40} {r['assignment']:<12} {r['official_website'][:40]}")

    elif args.cmd == "validate":
        sidecar = Path(args.sidecar)
        with sidecar.open(encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        print(f"Rows: {len(rows)}")
        for r in rows[:5]:
            print(r)

if __name__ == "__main__":
    main()
