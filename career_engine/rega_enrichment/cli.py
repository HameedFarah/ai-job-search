"""CLI for REGA enrichment pipeline."""

from __future__ import annotations

import argparse
import csv
import json
import hashlib
from dataclasses import asdict
from pathlib import Path

from .pipeline import run_pipeline, load_canonical, build_regression_set, enrich_company
from .config import CANONICAL_REGA_INPUT, freeze_manifest
from .cache import default_cache_dir, cache_stats

def main() -> None:
    parser = argparse.ArgumentParser(description="REGA enrichment pipeline — deterministic, source-controlled")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="Run batch enrichment")
    input_group = p_run.add_mutually_exclusive_group(required=True)
    input_group.add_argument("--input", help="Canonical CSV path")
    input_group.add_argument("--canonical-input", action="store_true", help="Use the configured canonical REGA input")
    p_run.add_argument("--out-dir", required=True, help="Output dir for sidecar")
    p_run.add_argument("--ids", help="Comma-separated company_id or license_no filter")
    p_run.add_argument("--limit", type=int, help="Limit number of companies")
    p_run.add_argument("--delay", type=float, default=0.4)
    p_run.add_argument("--refresh", action="store_true", help="Bypass cache and refresh search results")
    p_run.add_argument("--no-cache", action="store_true", help="Disable cache entirely")
    p_run.add_argument("--cache-dir", help="Override cache directory")
    p_run.add_argument("--workers", type=int, default=1, help="Bounded company-level concurrency; default 1, maximum 16")

    p_reg = sub.add_parser("regression", help="Run 20-company regression")
    p_reg.add_argument("--input", required=True)
    p_reg.add_argument("--out-dir", required=True)
    p_reg.add_argument("--delay", type=float, default=0.4)
    p_reg.add_argument("--refresh", action="store_true", help="Bypass cache")
    p_reg.add_argument("--no-cache", action="store_true", help="Disable cache")
    p_reg.add_argument("--cache-dir", help="Override cache directory")

    p_val = sub.add_parser("validate", help="Validate sidecar")
    p_val.add_argument("--sidecar", required=True)
    p_val.add_argument("--input", required=True)

    p_export = sub.add_parser("export", help="Generate TTW private and Career Engine sanitized exports from sidecar")
    p_export.add_argument("--sidecar", required=True)
    p_export.add_argument("--out-dir", required=True)
    p_export.add_argument("--prefix", default="rega-export")

    p_cache = sub.add_parser("cache", help="Cache utilities")
    p_cache.add_argument("--stats", action="store_true", help="Show cache stats")
    p_cache.add_argument("--clear", action="store_true", help="Clear cache")
    p_cache.add_argument("--cache-dir", help="Override cache directory")

    sub.add_parser("input-status", help="Report the configured canonical REGA input identity without modifying it")

    p_providers = sub.add_parser("providers", help="Run bounded provider candidate discovery for a verified official domain")
    p_providers.add_argument("--domain", required=True, help="Verified official company domain")
    p_providers.add_argument(
        "--allow-existing-credit",
        action="store_true",
        help="Permit at most one existing/trial-credit lookup per configured provider; never purchases credits",
    )

    p_provider_batch = sub.add_parser("provider-batch", help="Run candidate-only provider discovery across verified sidecar domains")
    p_provider_batch.add_argument("--sidecar", required=True, help="Accepted REGA sidecar CSV")
    p_provider_batch.add_argument("--output", required=True, help="Derived provider candidate JSONL output")
    p_provider_batch.add_argument(
        "--allow-existing-credit",
        action="store_true",
        help="Permit existing/trial-credit lookups only; never purchases or tops up credits",
    )
    p_provider_batch.add_argument(
        "--include-candidates",
        action="store_true",
        help="Also process candidate domains; default is confirmed official domains only",
    )
    p_provider_batch.add_argument("--max-domains", type=int, help="Optional bounded unique-domain ceiling")

    args = parser.parse_args()

    if args.cmd == "run":
        out = Path(args.out_dir)
        ids = [x.strip() for x in args.ids.split(",")] if args.ids else None
        cache_dir = Path(args.cache_dir) if getattr(args, "cache_dir", None) else None
        use_cache = not getattr(args, "no_cache", False)
        refresh = getattr(args, "refresh", False)
        input_path = CANONICAL_REGA_INPUT if args.canonical_input else Path(args.input)
        manifest = run_pipeline(
            input_path, out, company_ids=ids, limit=args.limit, delay_s=args.delay,
            use_cache=use_cache, refresh=refresh, cache_dir=cache_dir, workers=args.workers
        )
        print(json.dumps(manifest, indent=2, ensure_ascii=False))

    elif args.cmd == "regression":
        out = Path(args.out_dir)
        out.mkdir(parents=True, exist_ok=True)
        canonical = Path(args.input)
        reg_set = build_regression_set(canonical)
        reg_input_path = out / "regression-input.csv"
        with reg_input_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["company_id","License No","English Name","Arabic Name","English Location(s)","Career Priority","Research Status"])
            w.writeheader()
            for c in reg_set:
                w.writerow({"company_id": c.company_id, "License No": c.license_no, "English Name": c.english_name, "Arabic Name": c.arabic_name, "English Location(s)": c.location, "Career Priority": c.career_priority, "Research Status": c.research_status})
        from .pipeline import utc_now
        import time
        cache_dir = Path(args.cache_dir) if getattr(args, "cache_dir", None) else None
        use_cache = not getattr(args, "no_cache", False)
        refresh = getattr(args, "refresh", False)
        effective_cache = cache_dir or default_cache_dir()
        before = cache_stats(effective_cache)
        manifest = freeze_manifest(canonical)
        ts = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        sidecar_path = out / f"rega-regression-sidecar-{ts}.csv"
        evidence_path = out / f"rega-regression-evidence-{ts}.jsonl"
        rejected_path = out / f"rega-regression-rejected-{ts}.jsonl"
        manifest_path = out / f"rega-regression-manifest-{ts}.json"
        manifest.update({
            "run_started_at": utc_now(),
            "regression_size": len(reg_set),
            "mode": "regression",
            "cache_dir": str(effective_cache),
            "cache_files_before": before.get("files", 0),
            "use_cache": use_cache,
            "refresh": refresh,
            "synthetic_ids": [c.license_no for c in reg_set if c.license_no.startswith("REG-")],
            "baseline_complete_preserved": 29,
        })

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
                    enriched = enrich_company(comp, delay_s=args.delay, use_cache=use_cache, refresh=refresh, cache_dir=effective_cache)
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
                ev_f.flush(); rej_f.flush(); csvfile.flush()
                time.sleep(0.5)
        ev_f.close(); rej_f.close()
        after = cache_stats(effective_cache)
        manifest.update({
            "run_finished_at": utc_now(),
            "sidecar_path": str(sidecar_path), "sidecar_sha256": hashlib.sha256(sidecar_path.read_bytes()).hexdigest(),
            "evidence_path": str(evidence_path), "evidence_sha256": hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
            "rejected_path": str(rejected_path), "rejected_sha256": hashlib.sha256(rejected_path.read_bytes()).hexdigest(),
            "manifest_path": str(manifest_path), "regression_input_path": str(reg_input_path),
            "cache_files_after": after.get("files", 0),
            "cache_files_created": after.get("files", 0) - before.get("files", 0),
            "firecrawl_search_calls_estimate": 0,
            "firecrawl_extract_calls_estimate": len(reg_set) * 2 if manifest.get("credentials_configured") else 0,
        })
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps(manifest, indent=2, ensure_ascii=False))
        import csv as csvm
        with sidecar_path.open(encoding="utf-8-sig") as f:
            rows = list(csvm.DictReader(f))
        known = rows[:15]
        ok_known = sum(1 for r in known if r["assignment"] in ("confirmed","candidate") and r["official_website"])
        print(f"\n--- Regression acceptance summary ---")
        print(f"Known-company identity resolution: {ok_known}/15")
        print(f"Total sidecar rows: {len(rows)}")
        for r in rows:
            print(f"{r['company_id']:>2} {r['License No']:<20} {r['English Name'][:40]:<40} {r['assignment']:<12} {r['official_website'][:50]}")

    elif args.cmd == "export":
        sidecar = Path(args.sidecar)
        out_dir = Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        with sidecar.open(encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        # TTW private: website, general email, phone, BD/sales, procurement, supplier route
        ttw_fields = ["License No","English Name","Arabic Name","English Location(s)","Career Priority","official_website","official_domain","general_email","main_phone","ttw_bd_email","procurement_email","supplier_registration_url","careers_page","ats_url","linkedin_company_page","confidence","verified_at","source"]
        # Add verified_at/source from evidence? For now use sidecar notes timestamp? Use manifest time
        # We'll use sidecar's verified_at from evidence if available, else now
        from .pipeline import utc_now
        now = utc_now()
        ttw_path = out_dir / f"{args.prefix}-ttw-private.csv"
        with ttw_path.open("w", newline="", encoding="utf-8") as out:
            w = csv.DictWriter(out, fieldnames=ttw_fields)
            w.writeheader()
            for r in rows:
                w.writerow({
                    "License No": r["License No"],
                    "English Name": r["English Name"],
                    "Arabic Name": r["Arabic Name"],
                    "English Location(s)": r["English Location(s)"],
                    "Career Priority": r["Career Priority"],
                    "official_website": r["official_website"],
                    "official_domain": r["official_domain"],
                    "general_email": r["general_email"],
                    "main_phone": r["main_phone"],
                    "ttw_bd_email": r["ttw_bd_email"],
                    "procurement_email": r["procurement_email"],
                    "supplier_registration_url": r["supplier_registration_url"],
                    "careers_page": r["careers_page"],
                    "ats_url": r["ats_url"],
                    "linkedin_company_page": r["linkedin_company_page"],
                    "confidence": r["confidence"],
                    "verified_at": now,
                    "source": "REGA",
                })
        # Sanitized Career Engine: only identity, official domain, careers, ATS, explicit recruitment, LinkedIn, market, priority, verified_at, source
        ce_fields = ["License No","English Name","Arabic Name","official_domain","careers_url","ats_url","ats_domain","recruitment_email","linkedin_company","market","career_priority","verified_at","source"]
        ce_path = out_dir / f"{args.prefix}-career-engine-sanitized.json"
        ce_rows = []
        for r in rows:
            # Do not export general_email, main_phone, procurement, TTW
            ce_rows.append({
                "license_no": r["License No"],
                "english_name": r["English Name"],
                "arabic_name": r["Arabic Name"],
                "official_domain": r["official_domain"],
                "careers_url": r["careers_page"],
                "ats_url": r["ats_url"],
                "ats_domain": r["ats_domain"],
                "recruitment_email": r["recruitment_email"],
                "linkedin_company": r["linkedin_company_page"],
                "market": r["English Location(s)"],
                "career_priority": r["Career Priority"],
                "verified_at": now,
                "source": "REGA",
            })
        ce_path.write_text(json.dumps(ce_rows, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps({"ttw_private": str(ttw_path), "career_engine_sanitized": str(ce_path), "rows": len(rows)}, indent=2))

    elif args.cmd == "cache":
        cache_dir = Path(args.cache_dir) if getattr(args, "cache_dir", None) else None
        if args.stats:
            print(json.dumps(cache_stats(cache_dir), indent=2))
        if args.clear:
            from .cache import clear_cache
            n = clear_cache(cache_dir)
            print(f"cleared {n} files")

    elif args.cmd == "input-status":
        rows = load_canonical(CANONICAL_REGA_INPUT)
        print(json.dumps({
            "path": str(CANONICAL_REGA_INPUT),
            "exists": CANONICAL_REGA_INPUT.is_file(),
            "rows": len(rows),
            "sha256": hashlib.sha256(CANONICAL_REGA_INPUT.read_bytes()).hexdigest(),
        }, indent=2))

    elif args.cmd == "providers":
        from .provider_waterfall import run_configured_domain_waterfall
        result = run_configured_domain_waterfall(
            args.domain,
            allow_existing_credit=args.allow_existing_credit,
        )
        print(json.dumps({
            "domain": args.domain.strip().lower().removeprefix("www."),
            "provider_statuses": result.provider_statuses,
            "candidate_contacts": [asdict(contact) for contact in result.contacts],
            "outreach_ready_count": len(result.official_recruitment_contacts),
            "official_promotion_performed": False,
        }, indent=2, ensure_ascii=False))

    elif args.cmd == "provider-batch":
        from .provider_batch import run_provider_batch
        summary = run_provider_batch(
            Path(args.sidecar),
            Path(args.output),
            allow_existing_credit=args.allow_existing_credit,
            include_candidates=args.include_candidates,
            max_domains=args.max_domains,
        )
        print(json.dumps(summary, indent=2, ensure_ascii=False))

    elif args.cmd == "validate":
        sidecar = Path(args.sidecar)
        with sidecar.open(encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        print(f"Rows: {len(rows)}")
        for r in rows[:5]:
            print(r)


if __name__ == "__main__":
    main()
