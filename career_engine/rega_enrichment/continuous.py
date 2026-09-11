"""Resumable REGA research using the existing Master Tracker and API adapters.

Execution artifacts are a checkpoint/projection, never a second operational
tracker. This runner has no Gmail, queue admission, purchasing or send path.
"""
from __future__ import annotations

import argparse
import collections
import csv
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import threading
import time
from urllib.parse import urljoin, urlsplit

import requests


class CompanyDeadline(BaseException):
    """Must not be swallowed by HTTP adapters' ordinary exception handlers."""

from .config import GENERIC_TOKENS
from .employment_routes import (
    _parse_html, _extract_emails, _employment_score, _is_ats_url,
    _homepage_ats_link_is_employment, _looks_like_soft_404,
)
from .resolution import _arabic_tokens, career_value_score, priority_band
from .verify import is_blocked


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def save(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    tmp.replace(path)


def domain(url):
    value = url if "://" in str(url) else "https://" + str(url)
    return (urlsplit(value).hostname or "").lower().removeprefix("www.")


def public_url(url):
    p = urlsplit(url)
    if p.scheme not in {"https", "http"} or not p.hostname or p.username or p.password:
        return False
    if p.port not in {None, 80, 443}:
        return False
    try:
        addresses = socket.getaddrinfo(p.hostname, p.port or 443)
        return bool(addresses) and all(ipaddress.ip_address(x[4][0]).is_global for x in addresses)
    except (OSError, ValueError):
        return False


def norm(text):
    text = re.sub(r"[إأآ]", "ا", str(text).lower()).replace("ى", "ي").replace("ة", "ه")
    return re.sub(r"[^a-z0-9\u0621-\u064a]+", " ", text)


def identity_matches(row, text, host):
    """Require distinctive name evidence on the fetched site, never snippets alone."""
    words = set(norm(text).split())
    english = [w for w in norm(row.get("Company_or_Office", "")).split()
               if len(w) > 2 and w not in GENERIC_TOKENS]
    arabic = [norm(w).strip() for w in _arabic_tokens(row.get("Arabic_Name", ""))]
    for tokens in (english, arabic):
        tokens = list(dict.fromkeys(tokens))
        hits = sum(w in words for w in tokens)
        if len(tokens) >= 2 and hits >= 2:
            return True
        # Single-word brands need the same brand in the domain plus Saudi context.
        if len(tokens) == 1 and hits and tokens[0] in norm(host).replace(" ", ""):
            if host.endswith(".sa") or any(w in words for w in ("saudi", "riyadh", "السعوديه", "الرياض")):
                return True
    return False


def parse_exa(payload):
    if payload.get("isError"):
        raise RuntimeError("exa_provider_error")
    chunks = [x.get("text", "") for x in payload.get("content", []) if x.get("type") == "text"]
    text = "\n".join(chunks)
    if any(x in text.lower() for x in ("rate limit", "usage limit", "quota exceeded", "unauthorized")):
        raise RuntimeError("exa_capacity_unavailable")
    out = []
    for block in re.split(r"(?m)^Title: ", text)[1:]:
        title = block.splitlines()[0]
        match = re.search(r"(?m)^URL: (https?://\S+)", block)
        if match:
            out.append({"url": match.group(1), "title": title, "text": block[:12000]})
    return out


class Research:
    def __init__(self, root, search_limit=1200):
        self.root = Path(root)
        self.search_limit = search_limit
        self.search_stats_path = self.root / "search-usage.json"
        self.stats = json.loads(self.search_stats_path.read_text()) if self.search_stats_path.exists() else {"calls": 0, "errors": 0, "empty": 0}
        self.consecutive_errors = 0
        self.session = requests.Session()

    def search(self, query):
        key = hashlib.sha256(query.encode()).hexdigest()
        path = self.root / "search" / (key + ".json")
        if path.exists():
            return json.loads(path.read_text())
        if self.stats["calls"] >= self.search_limit or self.consecutive_errors >= 5:
            return {"status": "search_capacity_held", "results": []}
        self.stats["calls"] += 1
        save(self.search_stats_path, self.stats)
        try:
            # The installed Google engine is healthy even when Qwant is CAPTCHA
            # blocked. Treat per-engine errors explicitly, never as no results.
            local = self.session.get("http://127.0.0.1:8888/search",
                                     params={"q": query, "format": "json", "engines": "google"}, timeout=18)
            local.raise_for_status()
            data = local.json()
            items = data.get("results", [])
            if not data.get("unresponsive_engines") and items:
                output = {"status": "ok", "provider": "searxng-google", "at": now(),
                          "results": [{"url": x["url"], "title": x.get("title", ""),
                                       "text": x.get("title", "") + "\n" + x.get("content", "")}
                                      for x in items[:5] if x.get("url", "").startswith(("https://", "http://"))]}
                self.consecutive_errors = 0
                save(path, output)
                return output
        except Exception:
            pass
        try:
            proc = subprocess.run(
                ["rtk", "proxy", "mcporter", "call", "exa.web_search_exa", "--args",
                 json.dumps({"query": query, "numResults": 5}), "--output", "json", "--timeout", "20000"],
                capture_output=True, text=True, timeout=25,
            )
            if proc.returncode:
                raise RuntimeError("search_process_failed")
            results = parse_exa(json.loads(proc.stdout))
            self.consecutive_errors = 0
            if not results:
                self.stats["empty"] += 1
            output = {"status": "ok", "provider": "exa", "results": results, "at": now()}
            save(path, output)
        except Exception as exc:
            self.stats["errors"] += 1
            self.consecutive_errors += 1
            output = {"status": "search_error", "error_type": type(exc).__name__, "results": []}
        save(self.search_stats_path, self.stats)
        return output

    def fetch(self, url):
        key = hashlib.sha256(url.encode()).hexdigest()
        path = self.root / "pages" / (key + ".json")
        if path.exists():
            return json.loads(path.read_text())
        current = url
        try:
            for _ in range(5):
                if not public_url(current):
                    return None
                response = self.session.get(current, timeout=8, allow_redirects=False,
                                            headers={"User-Agent": "Mozilla/5.0 (compatible; CareerResearch/1.0)"}, stream=True)
                if response.is_redirect:
                    current = urljoin(current, response.headers.get("Location", ""))
                    response.close()
                    continue
                if response.status_code >= 400:
                    response.close()
                    return None
                # Bound downloads; PDF/social/browser work is left as explicit research candidates.
                if "html" not in response.headers.get("Content-Type", "").lower():
                    response.close()
                    return None
                content = bytearray()
                for chunk in response.iter_content(16384):
                    content.extend(chunk)
                    if len(content) > 1_000_000:
                        break
                response.close()
                html = bytes(content).decode(response.encoding or "utf-8", errors="replace")
                parsed = _parse_html(html)
                text = " ".join(parsed.text_parts)
                if _looks_like_soft_404("", text, response.status_code):
                    return None
                output = {"url": current, "text": text[:90000], "html": html,
                          "links": parsed.links, "mailtos": parsed.mailtos, "at": now()}
                save(path, output)
                return output
        except Exception:
            return None
        return None

    def resolve(self, row):
        existing = row.get("Address_or_Website", "").strip()
        pages = []
        evidence = []
        if existing.startswith(("http://", "https://")) and not is_blocked(domain(existing)):
            page = self.fetch(existing)
            verified = bool(re.search(r"\bverified\b", row.get("Source_Verification", "").lower()))
            if page and domain(page["url"]) == domain(existing) and (verified or identity_matches(row, page["text"], domain(page["url"]))):
                return domain(page["url"]), [page], [{"url": page["url"], "basis": "tracker_and_current_site"}], "confirmed"
        names = [row.get("Arabic_Name", ""), row.get("Company_or_Office", "")]
        seen = set()
        errors = 0
        for name in names:
            if not name.strip():
                continue
            result = self.search(name.strip() + " " + row.get("Region", "") + " Saudi Arabia official website")
            if result["status"] != "ok":
                errors += 1
                continue
            for item in result["results"]:
                host = domain(item["url"])
                if not host or host in seen or is_blocked(host):
                    continue
                seen.add(host)
                evidence.append({"url": item["url"], "title": item["title"], "basis": "search_candidate"})
                if len(seen) > 5:
                    break
                page = self.fetch(item["url"])
                if page and domain(page["url"]) == host and identity_matches(row, page["text"], host):
                    # A site's own title/brand must identify the business; a news article
                    # merely mentioning the legal name is not its official domain.
                    title = re.search(r"<title[^>]*>(.*?)</title>", page["html"], re.I | re.S)
                    branding = (title.group(1) if title else "") + " " + host.replace("-", " ")
                    tokens = [x for x in norm(row.get("Company_or_Office", "")).split() if len(x)>2 and x not in GENERIC_TOKENS]
                    ar = [norm(x).strip() for x in _arabic_tokens(row.get("Arabic_Name", ""))]
                    if not any(x in norm(branding) for x in tokens + ar):
                        continue
                    page["identity_confirmed"] = True
                    return host, [page], evidence + [{"url": page["url"], "basis": "current_first_party_identity_match"}], "confirmed"
        return "", pages, evidence, "search_unavailable" if errors else "identity_unconfirmed"

    def routes(self, host, pages):
        contacts = {}
        portals = {}
        visited = {x["url"] for x in pages}
        urls = []
        def collect(page):
            for email, kind in _extract_emails(page["text"], page["mailtos"], host):
                contacts[email] = {"email": email, "provider": "first_party", "title": "", "name": "",
                                   "source_urls": [page["url"]], "domain": host, "kind": kind, "relevance_confirmed": True}
            for href, label in page["links"]:
                target = urljoin(page["url"], href)
                if _is_ats_url(target) and _homepage_ats_link_is_employment(target, label):
                    portals[target] = {"url": target, "source_url": page["url"], "kind": "ats"}
                elif domain(target) == host and not target.startswith("mailto:"):
                    if _employment_score(label, "", target) or any(t in (target + " " + label).lower() for t in ("contact", "about", "اتصل", "تواصل", "من نحن")):
                        urls.append(target.split("#")[0])
            text = page["text"].lower()
            if _employment_score("", text, page["url"]) >= 2 and any(x in text for x in ("apply", "submit cv", "vacanc", "وظائف", "السيرة الذاتية")):
                if urlsplit(page["url"]).path.strip("/") and any(x in page["url"].lower() for x in ("career", "job", "recruit", "توظيف")):
                    portals[page["url"]] = {"url": page["url"], "source_url": page["url"], "kind": "careers_page"}
        for page in list(pages):
            collect(page)
        urls.extend([f"https://{host}/contact", f"https://{host}/careers"])
        for url in list(dict.fromkeys(urls))[:5]:
            if url in visited:
                continue
            visited.add(url)
            page = self.fetch(url)
            if page and domain(page["url"]) == host:
                pages.append(page)
                collect(page)
        return list(contacts.values()), list(portals.values())


def contact_rank(c):
    local = c.get("email", "").split("@")[0].lower()
    if re.fullmatch(r"(?:hr|careers?|jobs?|recruitment|talent|hiring|humanresources)", local):
        return 0
    if re.search(r"human resource|recruit|talent acquisition|\bhr\b", c.get("title", "").lower()):
        return 1
    if local in {"info", "contact", "hello", "office", "admin", "contactus", "inquiry", "enquiry"}:
        return 2
    return 9


def is_current_source(c, host, pages):
    email = c.get("email", "").lower()
    return any(email in (p["text"] + " " + " ".join(p["mailtos"])).lower()
               for p in pages if domain(p["url"]) == host)


def hiring_evidence(c, row, research):
    if contact_rank(c) != 1 or len(c.get("name", "").split()) < 2:
        return []
    result = research.search('"' + c["name"] + '" ' + row.get("Company_or_Office", "") + " recruitment human resources")
    matches = []
    name_tokens = norm(c["name"]).split()
    company_tokens = [x for x in norm(row.get("Company_or_Office", "")).split() if len(x)>2 and x not in GENERIC_TOKENS]
    for item in result["results"]:
        text = norm(item["text"])
        if domain(item["url"]) not in {"linkedin.com", c["domain"]}:
            continue
        # Candidate names in another person's liked/shared posts cannot establish
        # current employment. Require the profile's own title/name to match.
        own_title = norm(item["title"]).split()
        if not all(x in own_title for x in name_tokens[:2]) or not any(x in text.split() for x in company_tokens):
            continue
        if not re.search(r"human resource|recruit|talent acquisition|\bhr\b", text):
            continue
        matches.append({"url": item["url"], "observed_at": now(), "basis": "indexed_public_hiring_profile", "excerpt": item["text"][:3000]})
    return matches


def select_rows(rows, dedupe):
    selected, excluded = [], {}
    from runtime import rega_priority_scan as legacy
    for row in rows:
        mid = row["Master_ID"]
        company = legacy.normalized_company(row.get("Company_or_Office", ""))
        status = row.get("Source_Status", "").lower()
        if company in dedupe["contacted_companies"] or "already successfully contacted" in status:
            excluded[mid] = "already_contacted"
        elif company in dedupe["queued_companies"] or "already covered" in status:
            excluded[mid] = "already_covered"
        elif "verified receiving email route" in status:
            excluded[mid] = "existing_receiving_route"
        else:
            selected.append(row)
    selected.sort(key=lambda r: (not bool(r.get("Address_or_Website")), -career_value_score(r), r["Master_ID"]))
    return selected, excluded


def process(row, research, apis, dedupe):
    result = {"master_id": row["Master_ID"], "company": row.get("Company_or_Office"),
              "priority": priority_band(career_value_score(row)), "at": now(), "contacts": [], "portals": []}
    host, pages, evidence, identity = research.resolve(row)
    result.update(domain=host, identity_status=identity, evidence=evidence)
    if not host:
        result["outcome"] = identity
        return result
    candidates, portals = research.routes(host, pages)
    result["portals"] = portals
    # Discover HR even when an official general inbox or a portal is available.
    candidates.extend(apis.hunter_contacts(host))
    if not any(contact_rank(c) < 2 for c in candidates):
        candidates.extend(apis.prospeo_contacts(host))
    if not any(contact_rank(c) <= 2 for c in candidates):
        candidates.extend(apis.snov_contacts(host))
    unique = {}
    for c in sorted(candidates, key=contact_rank):
        email = str(c.get("email", "")).lower().strip()
        if not re.fullmatch(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}", email):
            continue
        if email.rsplit("@", 1)[1] != host or email in unique or contact_rank(c) >= 9:
            continue
        c["email"] = email
        c["rank"] = contact_rank(c)
        c["relevance_confirmed"] = is_current_source(c, host, pages)
        if not c["relevance_confirmed"] and contact_rank(c) == 1:
            c["hiring_evidence"] = hiring_evidence(c, row, research)
            c["relevance_confirmed"] = bool(c["hiring_evidence"])
        if email in dedupe["permanent_bounces"]:
            c["held_reason"] = "permanent_bounce"
        elif email in dedupe["known_emails"]:
            c["held_reason"] = "already_known_or_contacted"
        elif not c["relevance_confirmed"]:
            c["held_reason"] = "needs_current_first_party_or_hiring_evidence"
        unique[email] = c
    result["contacts"] = list(unique.values())
    result["provider_stats"] = apis.stats
    result["selected"] = None
    for c in result["contacts"]:
        if c.get("held_reason"):
            continue
        validation = apis.verify(c["email"])
        c["validation"] = validation
        if validation.get("safe_to_send") and validation.get("status") == "RECEIVING":
            result["selected"] = c
            break
    result["outcome"] = ("validated_hr" if result["selected"] and result["selected"]["rank"] < 2 else
                         "validated_general" if result["selected"] else
                         "email_candidates_held" if result["contacts"] else
                         "portal_only" if portals else "domain_confirmed_no_route")
    if result["outcome"] == "domain_confirmed_no_route" and apis.stats.get("halted"):
        result["outcome"] = "provider_research_incomplete"
    return result


def tracker_updates(row, result):
    """Append evidence; avoid live sender admission fields and preserve portal history."""
    marker = "REGA_API_20260911"
    evidence = {"outcome": result["outcome"], "domain": result.get("domain", ""),
                "contacts": [{"email": c["email"], "provider": c.get("provider"),
                              "held": c.get("held_reason"), "sources": c.get("source_urls"),
                              "validation": c.get("validation", {}).get("status")}
                             for c in result.get("contacts", [])],
                "portals": result.get("portals", []), "at": result["at"],
                "supersedes_result_at": result.get("supersedes_result_at")}
    note = marker + " " + json.dumps(evidence, ensure_ascii=False, separators=(",", ":"))
    old = row.get("Notes", "")
    updates = {"Notes": old + (" | " if old else "") + note}
    if result.get("domain") and not row.get("Address_or_Website", "").strip():
        updates["Address_or_Website"] = "https://" + result["domain"] + "/"
    # Keep old lifecycle/send decisions; notes are append-only research evidence.
    return updates


def write_result(legacy, result, apply):
    if not apply:
        return "not_requested"
    token = legacy.rclone_access_token("gdrive")
    headers = legacy.read_named_sheet(token, legacy.MASTER_SHEET, "A1:AF1")[0]
    id_column = legacy._column_letter(headers.index("Master_ID") + 1)
    identities = legacy.read_named_sheet(token, legacy.MASTER_SHEET, f"{id_column}2:{id_column}")
    found = [n for n, cells in enumerate(identities, 2) if cells and cells[0] == result["master_id"]]
    if len(found) != 1:
        raise RuntimeError("master_identity_changed")
    row_number = found[0]
    raw = legacy.read_named_sheet(token, legacy.MASTER_SHEET, f"A{row_number}:AF{row_number}")[0]
    row = dict(zip(headers, raw + [""] * (len(headers)-len(raw))))
    row["__row_number"] = str(row_number)
    if row.get("Master_ID") != result["master_id"]:
        raise RuntimeError("master_row_moved")
    marker = "REGA_API_20260911 "
    signature = '"at":"' + result["at"] + '"'
    if marker in row.get("Notes", "") and signature in row.get("Notes", ""):
        return "already_written"
    updates = tracker_updates(row, result)
    # Use RAW, never interpret website text as a Sheet formula. Retrying a fixed
    # range/value batch is idempotent; no append/queue mutation occurs here.
    data = [{"range": f"'{legacy.MASTER_SHEET}'!{legacy._column_letter(headers.index(k)+1)}{row['__row_number']}",
             "values": [[str(v)]]} for k, v in updates.items()]
    legacy.sheets_request(token, "POST", f"https://sheets.googleapis.com/v4/spreadsheets/{legacy.SPREADSHEET_ID}/values:batchUpdate",
                          {"valueInputOption": "RAW", "data": data})
    # Read back exact written fields before checkpointing completion.
    after = legacy.read_named_sheet(token, legacy.MASTER_SHEET, f"A{row_number}:AF{row_number}")[0]
    check = dict(zip(headers, after + [""] * (len(headers)-len(after))))
    if check.get("Master_ID") != result["master_id"] or any(check.get(k, "") != v for k, v in updates.items()):
        raise RuntimeError("tracker_readback_mismatch")
    return "verified"


def build_args(parser):
    parser.add_argument("--root", default="runtime/acceptance/rega-api-enrichment-20260911")
    parser.add_argument("--apply", action="store_true", help="Append research evidence to existing Master Tracker, no queue/sends")
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--search-limit", type=int, default=1200)
    parser.add_argument("--hunter-cap", type=float, default=15)
    parser.add_argument("--prospeo-cap", type=float, default=20)
    parser.add_argument("--snov-cap", type=float, default=50)
    parser.add_argument("--max-runtime", type=int, default=21600)
    parser.add_argument("--company-timeout", type=int, default=150)
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--pilot", action="store_true", help="Start with six known-domain and six unresolved-identity records")


def run(args):
    root = Path(args.root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    if args.status:
        print((root / "summary.json").read_text() if (root / "summary.json").exists() else '{"status":"not_started"}')
        return 0
    # One runner across all roots and restarts on this VPS.
    (Path.home() / ".cache").mkdir(exist_ok=True)
    lock = open(Path.home() / ".cache" / "career-rega-enrichment.lock", "a+")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise RuntimeError("another_rega_enrichment_is_running")
    from career_engine.cli import doctor
    if not doctor()["valid"]:
        raise RuntimeError("career_engine_doctor_failed")
    from runtime import rega_priority_scan as legacy
    from .contact_apis import ContactAPIs
    apis = ContactAPIs(root / "api-cache", {"hunter": args.hunter_cap, "prospeo": args.prospeo_cap, "snov": args.snov_cap})
    balances = apis.account_balances()
    for provider, cap in list(apis._budgets_cfg.items()):
        info = balances.get(provider, {})
        try:
            remaining = float(info["remaining"])
            if info.get("status") != "success":
                raise ValueError()
            used = apis._budget_tracker.reserved(provider)
            # Reserve ten percent of available capacity and obey the operator cap.
            apis._budgets_cfg[provider] = min(cap, used + max(0, remaining * .9))
        except (KeyError, TypeError, ValueError):
            apis._budgets_cfg[provider] = 0
    research = Research(root, args.search_limit)
    token = legacy.rclone_access_token("gdrive")
    headers, master = legacy.read_master(token)
    rows = [r for r in master if r.get("Record_Type", "").strip() == "REGA_COMPANY_NO_EMAIL"]
    ids = [r.get("Master_ID", "").strip() for r in rows]
    if not all(ids) or len(ids) != len(set(ids)):
        raise RuntimeError("duplicate_or_missing_master_ids")
    queue = legacy.read_queue(token, legacy.SPREADSHEET_ID)
    dedupe = legacy.read_dedupe_state(token, master, queue)
    selected, excluded = select_rows(rows, dedupe)
    if args.pilot:
        known = [r for r in selected if r.get("Address_or_Website")][:6]
        unknown = [r for r in selected if not r.get("Address_or_Website")][:6]
        pilot_ids = {r["Master_ID"] for r in known + unknown}
        interleaved = [r for pair in zip(known, unknown) for r in pair]
        interleaved_ids = {r["Master_ID"] for r in interleaved}
        selected = interleaved + [r for r in known + unknown if r["Master_ID"] not in interleaved_ids] + [r for r in selected if r["Master_ID"] not in pilot_ids]
    save(root / "scope.json", {"at": now(), "universe": len(rows), "selected_ids": [r["Master_ID"] for r in selected], "excluded": excluded})
    checkpoint = root / "records.json"
    records = json.loads(checkpoint.read_text()) if checkpoint.exists() else {}
    for mid, reason in excluded.items():
        records.setdefault(mid, {"master_id": mid, "outcome": reason, "at": now(), "excluded": True})
    summary = {"status": "running", "started_at": now(), "universe": len(rows), "selected": len(selected),
               "excluded": len(excluded), "balances_before": balances, "sends": 0, "queue_writes": 0,
               "purchases": 0, "apply": args.apply, "run_processed": 0, "task_id": "t_96c554af"}
    stopped = threading.Event()
    def heartbeat():
        while not stopped.wait(15):
            save(root / "heartbeat.json", {"at": now(), "pid": os.getpid()})
    threading.Thread(target=heartbeat, daemon=True).start()
    def report():
        summary.update(updated_at=now(), outcomes=dict(collections.Counter(r["outcome"] for r in records.values())),
                       accounted=len(records), research_remaining=sum(r["Master_ID"] not in records for r in selected),
                       provider_stats=apis.stats, search_stats=research.stats)
        save(checkpoint, records)
        save(root / "summary.json", summary)
        # CSV is a read-only export of evidence keyed to canonical Master IDs.
        with (root / "index.csv").open("w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["Master_ID", "Company", "Outcome", "Domain", "Selected_Email", "Candidate_Emails", "Portals", "Tracker_Write"])
            for mid, r in records.items():
                writer.writerow([mid, r.get("company", ""), r["outcome"], r.get("domain", ""),
                                 (r.get("selected") or {}).get("email", ""),
                                 ";".join(c["email"] for c in r.get("contacts", [])),
                                 ";".join(p["url"] for p in r.get("portals", [])), r.get("tracker_write", "")])
    started = time.monotonic()
    def timeout_handler(signum, frame):
        raise CompanyDeadline("company_time_limit")
    signal.signal(signal.SIGALRM, timeout_handler)
    report()
    try:
        for row in selected:
            mid = row["Master_ID"]
            if mid in records:
                if args.apply and records[mid].get("tracker_write") not in {"verified", "already_written"} and not records[mid].get("excluded"):
                    records[mid]["tracker_write"] = write_result(legacy, records[mid], True)
                if records[mid].get("outcome") not in {"research_error", "search_unavailable", "provider_research_incomplete"}:
                    continue
            if (args.limit and summary["run_processed"] >= args.limit) or time.monotonic()-started >= args.max_runtime:
                break
            summary["current_company"] = mid
            report()
            signal.alarm(args.company_timeout)
            try:
                result = process(row, research, apis, dedupe)
            except (Exception, CompanyDeadline) as exc:
                result = {"master_id": mid, "company": row.get("Company_or_Office"), "outcome": "research_error",
                          "error_type": type(exc).__name__, "at": now()}
            finally:
                signal.alarm(0)
            records[mid] = result
            summary["run_processed"] += 1
            report()  # Durable research before any external write.
            result["tracker_write"] = write_result(legacy, result, args.apply)
            report()
            print(json.dumps({"master_id": mid, "outcome": result["outcome"], "processed": summary["run_processed"]}), flush=True)
            if research.consecutive_errors >= 5:
                summary["status"] = "paused_search_unavailable"
                break
            time.sleep(1)
        if summary["status"] == "running":
            summary["status"] = "batch_complete" if any(r["Master_ID"] not in records for r in selected) else "scope_processed"
        summary["balances_after"] = apis.account_balances()
    except Exception as exc:
        summary.update(status="failed", error_type=type(exc).__name__)
        raise
    finally:
        summary["finished_at"] = now()
        report()
        stopped.set()
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 0
