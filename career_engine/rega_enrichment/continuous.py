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
import html as html_tools
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
from bs4 import BeautifulSoup


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


DISCOVERY_VERSION = 9

# Domains proven to be third-party directories/platforms or different legal
# entities during the Sep-12 live recovery audit. They can contain a target
# company's profile/name, but can never establish employer ownership.
THIRD_PARTY_IDENTITY_DOMAINS = {
    "propertyfinder.sa",
    "argaam.com",
    "wzufa.com",
    "muktamel.com",
    "sanadak.sa",
    "sida.com.sa",
    "alrajhi-capital.sa",
    "watheer-est.com",
    "tiktok.com",
    "x.com",
    "linkedin.com",
    "facebook.com",
    "instagram.com",
    "youtube.com",
}


def can_preserve_current_domain(record):
    """Only preserve an unavailable retry when identity was proven by this version."""
    return (
        bool(record.get("domain"))
        and record.get("domain") not in THIRD_PARTY_IDENTITY_DOMAINS
        and int(record.get("discovery_version") or 0) >= DISCOVERY_VERSION
    )


def _maps_query(row):
    name = str(row.get("Company_or_Office") or "").split("(", 1)[0].strip()
    location = str(row.get("Region") or "").strip()
    return ", ".join(x for x in (name, location, "Saudi Arabia") if x)


def _batch_maps_prefetch(root, selected, records, outscraper):
    from .provider_clients import ProviderBudget
    path = Path(root) / "outscraper-maps-v9.json"
    cache = {"discovery_version": DISCOVERY_VERSION, "maps_batch_version": 2, "records": {}}
    if path.exists():
        try:
            loaded = json.loads(path.read_text())
            if int(loaded.get("discovery_version") or 0) == DISCOVERY_VERSION and int(loaded.get("maps_batch_version") or 0) == 2:
                cache = loaded
        except Exception:
            pass
    cached = cache.setdefault("records", {})
    targets = []
    for row in selected:
        mid = str(row.get("Master_ID") or "").strip()
        prior = records.get(mid) or {}
        if mid and not prior.get("excluded") and not prior.get("outscraper_attempted") and mid not in cached and not str(row.get("Address_or_Website") or "").strip():
            targets.append(row)
    high = [r for r in targets if priority_band(career_value_score(r)) == "A"]
    lower = [r for r in targets if priority_band(career_value_score(r)) != "A"]
    for rows, limit in ((high, 3), (lower, 1)):
        for start in range(0, len(rows), 25):
            batch = rows[start:start + 25]
            if not batch:
                continue
            queries = [_maps_query(r) for r in batch]
            budget = ProviderBudget(allow_existing_credit=True, max_calls=1, max_domains=len(batch) * limit)
            groups = outscraper.maps_businesses_batch(queries, budget, limit=limit)
            if len(groups) != len(batch):
                raise RuntimeError("outscraper_maps_batch_alignment_failed")
            for row, query, group in zip(batch, queries, groups):
                cached[row["Master_ID"]] = {"query": query, "limit": limit, "records": group, "at": now()}
            save(path, cache)
    return {mid: item.get("records") or [] for mid, item in cached.items()}


def _resolve_prefetched_maps(row, research, provider_records):
    retryable = False
    evidence = []
    checked = 0
    for item in provider_records or []:
        status = str(item.get("status") or "")
        if status in {"failed", "auth_failed", "quota_required", "budget_exhausted", "missing_credential"}:
            retryable = True
            continue
        if status != "candidate":
            continue
        meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        site = str(meta.get("site") or "").strip()
        host = domain(site)
        if not host or is_blocked(host) or host in THIRD_PARTY_IDENTITY_DOMAINS:
            continue
        checked += 1
        evidence.append({"url": site, "basis": "outscraper_maps_batch_clue"})
        page = research.fetch("https://" + host + "/")
        if not page and site:
            page = research.fetch(site)
        if not page:
            page = research.render_public("https://" + host + "/")
        if page and official_home_matches(row, page, host):
            page["identity_confirmed"] = True
            detail = {"basis": "outscraper_maps_batch_current_first_party_root_identity_match", "candidate_count": len(provider_records or []), "websites_evaluated": checked, "retryable": False}
            return host, [page], evidence, detail
    detail = {"basis": "outscraper_maps_batch_provider_failure" if retryable else "outscraper_maps_batch_no_confirmed_domain", "candidate_count": len(provider_records or []), "websites_evaluated": checked, "retryable": retryable}
    return "", [], evidence, detail


def brand_tokens(row):
    # Legal forms are not brand identity; strip Arabic conjunctions only when
    # they prefix a known legal/sector word, never from the actual brand.
    generic = set(norm("شركة الشركة شركات مؤسسة المؤسسة للتطوير التطوير تطوير والاستثمار الاستثمار للاستثمار استثمار العقاري العقارية للعقارات عقارات مساهمة عامة مقفلة محدودة المحدودة ذات مسؤولية شخص واحد فرع سعودية").split())
    arabic = [w for w in norm(row.get("Arabic_Name", "")).split() if w not in generic]
    english = [w for w in norm(row.get("Company_or_Office", "")).split()
               if len(w) > 2 and w not in GENERIC_TOKENS
               and w not in {"listed", "joint", "closed", "person", "branch", "limited"}]
    return list(dict.fromkeys(english)), list(dict.fromkeys(arabic))


def official_home_matches(row, page, host):
    """Require the company's own branding, not a body mention or portfolio."""
    if domain(page["url"]) != host:
        return False
    title = re.search(r"<title[^>]*>(.*?)</title>", page["html"], re.I | re.S)
    heading = re.search(r"<h[12][^>]*>(.*?)</h[12]>", page["html"], re.I | re.S)
    branding = norm(re.sub(r"<[^>]+>", " ", (title.group(1) if title else "") + " " + (heading.group(1) if heading else "")))
    if any(w in branding.split() for w in ("news", "stock", "stocks", "سهم", "اخبار", "توقعات")):
        return False
    english, arabic = brand_tokens(row)
    text = norm(page["text"])
    sector = any(w in text.split() for w in ("estate", "development", "investment", "العقاري", "العقاريه", "للتطوير", "للاستثمار"))
    # Exact distinctive Arabic branding handles legitimate transliteration
    # differences (Waken/Wakan, Zamiliya/Alzamiliah) without fuzzy matching.
    saudi = host.endswith(".sa") or any(w in text.split() for w in ("saudi", "ksa", "riyadh", "jeddah", "khobar", "السعوديه", "الرياض", "جده", "الخبر", "الدمام"))
    if arabic and all(w in branding.split() for w in arabic) and sector and saudi:
        return True
    label = host.split(".")[0].replace("-", "")
    # A registry-supplied acronym can be the public brand (e.g. NHC).
    if len(label) >= 3 and label in english and label in branding.split() and sector and saudi:
        return True
    english_brand = (all(w in branding.split() for w in english) or
                     (len(english) > 1 and "".join(english) in branding.split()))
    if english and english_brand:
        return sector and saudi and any(w in label for w in english)
    # Retain the prior conservative multi-token ownership path.
    return identity_matches(row, page["text"], host) and any(
        len(w) > 3 and w in label and w in text.split() for w in english)


def parse_exa(payload):
    if payload.get("isError") or payload.get("error") or payload.get("issue"):
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


def parse_page(html, url):
    soup = BeautifulSoup(html, "html.parser")
    for node in soup.select("script, style, noscript, template"):
        node.decompose()
    parsed = _parse_html(str(soup))
    return {"url": url, "text": " ".join(parsed.text_parts)[:90000], "html": html,
            "links": parsed.links, "mailtos": parsed.mailtos, "at": now()}


class Research:
    def __init__(self, root, search_limit=1200, reader_limit=500):
        self.root = Path(root)
        self.search_limit = search_limit
        self.reader_limit = reader_limit
        self.search_stats_path = self.root / "search-usage.json"
        self.stats = json.loads(self.search_stats_path.read_text()) if self.search_stats_path.exists() else {"calls": 0, "errors": 0, "empty": 0}
        self.consecutive_errors = 0
        self.session = requests.Session()

    def search(self, query):
        key = hashlib.sha256(query.encode()).hexdigest()
        path = self.root / "search" / (key + ".json")
        if path.exists():
            cached = json.loads(path.read_text())
            # Old Exa transport errors were incorrectly cached as empty success.
            if cached.get("results"):
                return cached
        if self.stats["calls"] >= self.search_limit or self.consecutive_errors >= 5:
            return {"status": "search_capacity_held", "results": []}
        self.stats["calls"] += 1
        save(self.search_stats_path, self.stats)
        for engine in ("google", "yandex"):
            if engine in getattr(self, "disabled_engines", set()):
                continue
            try:
                # Slow free discovery and stop hammering a CAPTCHA/limited engine.
                elapsed = time.monotonic() - getattr(self, "last_search_at", 0)
                if elapsed < 2:
                    time.sleep(2 - elapsed)
                self.last_search_at = time.monotonic()
                local = self.session.get("http://127.0.0.1:8888/search",
                                         params={"q": query, "format": "json", "engines": engine}, timeout=18)
                local.raise_for_status()
                data = local.json()
                items = data.get("results", [])
                if data.get("unresponsive_engines"):
                    self.disabled_engines = getattr(self, "disabled_engines", set()) | {engine}
                    self.stats["disabled_engines"] = sorted(self.disabled_engines)
                    save(self.search_stats_path, self.stats)
                    continue
                if items:
                    output = {"status": "ok", "provider": "searxng-" + engine, "at": now(),
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
                raise RuntimeError("search_empty_unverified")
            output = {"status": "ok", "provider": "exa", "results": results, "at": now()}
            save(path, output)
        except Exception as exc:
            self.stats["errors"] += 1
            self.consecutive_errors += 1
            output = {"status": "search_error", "error_type": type(exc).__name__, "results": []}
        save(self.search_stats_path, self.stats)
        return output

    def render_public(self, url):
        """Free public Reader fallback for JS shells; bounded and cached."""
        key = hashlib.sha256(url.encode()).hexdigest()
        path = self.root / "rendered" / (key + ".json")
        if path.exists():
            return json.loads(path.read_text())
        count = self.stats.get("reader_calls", 0)
        if count >= self.reader_limit or getattr(self, "reader_unavailable", False) or not public_url(url):
            return None
        self.stats["reader_calls"] = count + 1
        save(self.search_stats_path, self.stats)
        try:
            response = self.session.get("https://r.jina.ai/" + url, timeout=25)
            if response.status_code in {401, 402, 429}:
                self.reader_unavailable = True
            response.raise_for_status()
            content = response.text[:500000]
            source = re.search(r"(?m)^URL Source: (.+)$", content)
            if not source or domain(source.group(1).strip()) != domain(url) or "Markdown Content:" not in content:
                return None
            title = re.search(r"(?m)^Title: (.*)$", content)
            body = content.split("Markdown Content:", 1)[1]
            if any(w in body.lower() for w in ("verify you are human", "checking your browser", "captcha")):
                return None
            markup = "<title>" + html_tools.escape(title.group(1) if title else "") + "</title>"
            for line in body.splitlines():
                heading = re.match(r"^#{1,2} (.+)$", line)
                markup += ("<h1>" + html_tools.escape(heading.group(1)) + "</h1>") if heading else "<p>" + html_tools.escape(line) + "</p>"
            page = parse_page(markup, source.group(1).strip())
            page["links"] = [(u, label) for label, u in re.findall(r"\[([^]\n]+)\]\((https?://[^\s)]+)\)", body)]
            page["mailtos"] = re.findall(r"mailto:([^\s)]+)", body)
            page["retrieval"] = "public_reader"
            save(path, page)
            return page
        except Exception:
            return None

    def fetch(self, url):
        key = hashlib.sha256(url.encode()).hexdigest()
        path = self.root / "pages" / (key + ".json")
        if path.exists():
            cached = json.loads(path.read_text())
            page = parse_page(cached["html"], cached["url"])
            page["at"] = cached.get("at", page["at"])
            return (self.render_public(page["url"]) or page) if len(page["text"].strip()) < 200 else page
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
                output = parse_page(html, current)
                if _looks_like_soft_404("", output["text"], response.status_code):
                    return None
                save(path, output)
                return (self.render_public(current) or output) if len(output["text"].strip()) < 200 else output
        except Exception:
            return None
        return None

    def resolve(self, row):
        existing = row.get("Address_or_Website", "").strip()
        pages = []
        evidence = []
        if existing.startswith(("http://", "https://")) and not is_blocked(domain(existing)):
            page = self.fetch("https://" + domain(existing) + "/")
            if not page:
                page = self.render_public("https://" + domain(existing) + "/")
            if page and official_home_matches(row, page, domain(existing)):
                pages = [page]
                if existing.rstrip("/") != page["url"].rstrip("/"):
                    route_page = self.fetch(existing)
                    if route_page and domain(route_page["url"]) == domain(page["url"]):
                        pages.append(route_page)
                return domain(page["url"]), pages, [{"url": page["url"], "basis": "tracker_and_current_site"}], "confirmed"
        english, arabic = brand_tokens(row)
        english_name = str(row.get("Company_or_Office", "")).strip()
        arabic_name = str(row.get("Arabic_Name", "")).strip()
        region = str(row.get("Region", "")).strip()
        queries = []
        if english_name:
            queries.extend([
                f'"{english_name}" Saudi Arabia official website',
                f'"{english_name}" contact careers Saudi Arabia',
                f'{english_name} {region} real estate website',
            ])
        if arabic_name:
            queries.extend([
                f'"{arabic_name}" الموقع الرسمي',
                f'"{arabic_name}" تواصل معنا وظائف',
                f'{arabic_name} {region} السعودية عقارات',
            ])
        # Registry legal names often differ from the public brand. Add short,
        # distinctive brand searches rather than only repeating the legal name.
        if arabic:
            queries.append(" ".join(arabic) + " عقارات تواصل معنا")
        if english:
            queries.append(" ".join(english) + " real estate contact Saudi Arabia")
        queries = list(dict.fromkeys(q for q in queries if q.strip()))
        seen = set()
        errors = 0
        for query in queries:
            result = self.search(query)
            if result["status"] != "ok":
                errors += 1
                continue
            checked = 0
            for item in result["results"]:
                candidate_urls = [item["url"]]
                snippet = str(item.get("text") or "")
                for raw in re.findall(r'https?://[^\s"<>]+|www\.[a-z0-9.-]+\.[a-z]{2,}', snippet, re.I):
                    clue = raw.rstrip(".,);]}>")
                    if clue.startswith("www."):
                        clue = "https://" + clue
                    candidate_urls.append(clue)
                for candidate_url in candidate_urls:
                    host = domain(candidate_url)
                    if not host or host in seen or is_blocked(host) or host in THIRD_PARTY_IDENTITY_DOMAINS:
                        continue
                    if checked >= 6:
                        break
                    seen.add(host)
                    evidence.append({"url": candidate_url, "title": item["title"], "basis": "search_candidate_or_snippet_clue"})
                    checked += 1
                    page = self.fetch(candidate_url)
                    if not page:
                        page = self.fetch("https://" + host + "/")
                    if not page and any(w in norm(item.get("title", "")).split() for w in english + arabic):
                        page = self.render_public("https://" + host + "/")
                    if not (page and official_home_matches(row, page, host)):
                        continue
                    # A directory/profile page can reproduce the target company
                    # name while belonging to a different business. Require the
                    # host root to independently identify the same employer
                    # before treating the domain as first-party.
                    parsed = urlsplit(candidate_url)
                    root_url = f"{parsed.scheme or 'https'}://{host}/"
                    root_page = page if urlsplit(page["url"]).path in {"", "/"} else self.fetch(root_url)
                    if not root_page:
                        fallback = ("http" if root_url.startswith("https://") else "https") + "://" + host + "/"
                        root_page = self.fetch(fallback)
                    if not root_page or not official_home_matches(row, root_page, host):
                        continue
                    root_page["identity_confirmed"] = True
                    matched_pages = [root_page]
                    if page["url"] != root_page["url"]:
                        matched_pages.append(page)
                    return host, matched_pages, evidence + [{"url": root_page["url"], "basis": "current_first_party_root_identity_match"}], "confirmed"
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
            if _employment_score("", text, page["url"]) >= 2 and any(x in text for x in ("apply", "submit cv", "vacanc", "وظائف", "الوظائف", "السيرة الذاتية", "upload", "ارفاق", "إرفاق")):
                if urlsplit(page["url"]).path.strip("/") and any(x in page["url"].lower() for x in ("career", "job", "recruit", "employ", "join-us", "توظيف")):
                    portals[page["url"]] = {"url": page["url"], "source_url": page["url"], "kind": "careers_page"}
        for page in list(pages):
            collect(page)
        urls.extend([f"https://{host}/contact", f"https://{host}/careers"])
        # Prefer employment/contact links over about pages within the crawl cap.
        urls = sorted(dict.fromkeys(urls), key=lambda u: (not any(w in u.lower() for w in ("career", "job", "employ", "contact", "توظيف", "تواصل")), u))
        for url in urls[:8]:
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
    selected.sort(key=lambda r: (-career_value_score(r), not bool(r.get("Address_or_Website")), r["Master_ID"]))
    return selected, excluded


def process(row, research, apis, dedupe, outscraper=None, maps_cache=None):
    result = {"master_id": row["Master_ID"], "company": row.get("Company_or_Office"),
              "priority": priority_band(career_value_score(row)), "at": now(), "discovery_version": DISCOVERY_VERSION, "contacts": [], "portals": []}
    host, pages, evidence, identity = research.resolve(row)
    if not host and os.environ.get("REGA_ENABLE_COMPANY_DOMAIN_LOOKUP") == "1" and result["priority"] in {"A", "B"}:
        lookup_name = re.sub(r"\([^)]*\)", "", row.get("Company_or_Office", "")).strip()
        candidate_host = apis.snov_company_domain(lookup_name)
        if candidate_host and not is_blocked(candidate_host) and candidate_host not in THIRD_PARTY_IDENTITY_DOMAINS:
            candidate_page = research.fetch("https://" + candidate_host + "/")
            if not candidate_page:
                candidate_page = research.render_public("https://" + candidate_host + "/")
            evidence.append({"url": "https://" + candidate_host + "/", "basis": "snov_company_name_clue"})
            if candidate_page and official_home_matches(row, candidate_page, candidate_host):
                host, pages, identity = candidate_host, [candidate_page], "confirmed"
                evidence.append({"url": candidate_page["url"], "basis": "current_first_party_identity_match"})
    result["outscraper_attempted"] = bool(outscraper)
    if not host and outscraper is not None:
        from runtime import rega_priority_scan as legacy
        mid = str(row.get("Master_ID") or "")
        if maps_cache is not None and mid in maps_cache:
            candidate_host, candidate_pages, maps_evidence, maps_detail = _resolve_prefetched_maps(row, research, maps_cache[mid])
            evidence.extend(maps_evidence)
        else:
            try:
                candidate_host, maps_detail = legacy.maps_discover_domain(outscraper, row)
            except Exception as exc:
                candidate_host, maps_detail = "", {"basis": "outscraper_maps_error", "error_type": type(exc).__name__}
            candidate_pages = []
        result["outscraper_maps"] = maps_detail
        maps_basis = str(maps_detail.get("basis") or "")
        if not candidate_host:
            if maps_detail.get("retryable") or maps_basis in {"outscraper_maps_error", "outscraper_maps_provider_failure", "outscraper_maps_batch_provider_failure"}:
                identity = "provider_research_incomplete"
            else:
                identity = "identity_unconfirmed"
        elif candidate_pages:
            host, pages, identity = candidate_host, candidate_pages, "confirmed"
        elif not is_blocked(candidate_host) and candidate_host not in THIRD_PARTY_IDENTITY_DOMAINS:
            candidate_page = research.fetch("https://" + candidate_host + "/") or research.render_public("https://" + candidate_host + "/")
            evidence.append({"url": "https://" + candidate_host + "/", "basis": "outscraper_maps_clue"})
            if candidate_page and official_home_matches(row, candidate_page, candidate_host):
                candidate_page["identity_confirmed"] = True
                host, pages, identity = candidate_host, [candidate_page], "confirmed"
                evidence.append({"url": candidate_page["url"], "basis": "outscraper_maps_current_first_party_root_identity_match"})
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
    if outscraper is not None and not any(contact_rank(c) <= 2 for c in candidates):
        from runtime import rega_priority_scan as legacy
        try:
            outscraper_candidates = legacy.contact_candidates(outscraper, host)
        except Exception:
            outscraper_candidates = []
        for candidate in outscraper_candidates:
            source_pages = []
            for source_url in candidate.get("source_urls") or []:
                if domain(source_url) != host:
                    continue
                page = research.fetch(source_url)
                if page and domain(page["url"]) == host:
                    source_pages.append(page)
            pages.extend(p for p in source_pages if p["url"] not in {x["url"] for x in pages})
            candidates.append({
                "email": candidate.get("email", ""),
                "provider": "outscraper",
                "title": "",
                "name": "",
                "source_urls": candidate.get("source_urls") or [],
                "domain": host,
                "kind": candidate.get("kind", ""),
            })
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
        validation = apis.verify_snov(c["email"])
        # Retain explicit negative results; use Hunter for unavailable/pending
        # verification only, with its own persisted remaining-credit ceiling.
        if validation.get("status") == "UNKNOWN" and not validation.get("verification"):
            alternative = apis.verify(c["email"])
            if alternative.get("status") != "UNKNOWN":
                validation = alternative
        if validation.get("status") == "UNKNOWN" and outscraper is not None:
            from runtime import rega_priority_scan as legacy
            try:
                alternative = legacy.validate_one(outscraper, c["email"])
            except Exception:
                alternative = {"status": "UNKNOWN", "safe_to_send": False, "provider": "outscraper"}
            if alternative.get("status") != "UNKNOWN":
                validation = alternative
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
    parser.add_argument(
        "--use-all-available-provider-credit",
        action="store_true",
        help="Use the full currently available provider balance; never purchases/top-ups credits",
    )
    parser.add_argument(
        "--use-outscraper-fallback",
        action="store_true",
        help="Use existing Outscraper credit with zero reserve after strict v9 identity checks; never purchases/top-ups",
    )
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
    outscraper = None
    outscraper_balance_before = None
    if args.use_outscraper_fallback:
        outscraper_key = os.environ.get("OUTSCRAPER_API_KEY", "").strip()
        if not outscraper_key:
            raise RuntimeError("OUTSCRAPER_API_KEY_missing_for_requested_fallback")
        outscraper = legacy.OutscraperClient(outscraper_key)
        outscraper_balance_before = legacy.balance(outscraper)
    if args.use_all_available_provider_credit:
        apis.hunter_verification_reserve = 0.0
        apis.snov_verification_reserve = 0.0
    else:
        apis.hunter_verification_reserve = args.hunter_cap
        apis.snov_verification_reserve = 100.0
    balances = apis.account_balances()
    for provider, cap in list(apis._budgets_cfg.items()):
        info = balances.get(provider, {})
        try:
            remaining = float(info["remaining"])
            if info.get("status") != "success":
                raise ValueError()
            used = apis._budget_tracker.reserved(provider)
            if args.use_all_available_provider_credit:
                # Owner-approved full-balance mode: consume all existing credits
                # if useful, while remaining incapable of purchasing/top-ups.
                apis._budgets_cfg[provider] = used + max(0, remaining)
            else:
                # Conservative default retains ten percent and obeys the cap.
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
    _, auto_rows = legacy.read_table(token, legacy.AUTO_QUEUE_SHEET, "A:L")
    _, sent_rows = legacy.read_table(token, legacy.SENT_TRACKER_SHEET, "A:P")
    # Our staged HOLD entries are evidence awaiting verification, not a prior
    # outreach attempt. Keep all other existing records in the dedupe census.
    external_auto = [r for r in auto_rows if not (r.get("Source") == "REGA_API_RECOVERY_20260912" and r.get("Status") == "HOLD")]
    dedupe = legacy.build_dedupe_state(master, queue, external_auto, sent_rows)
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
    maps_cache = _batch_maps_prefetch(root, selected, records, outscraper) if outscraper is not None else {}
    summary = {"status": "running", "started_at": now(), "universe": len(rows), "selected": len(selected),
               "excluded": len(excluded), "balances_before": balances,
               "outscraper_fallback": bool(outscraper), "outscraper_balance_before": outscraper_balance_before,
               "sends": 0, "queue_writes": 0, "purchases": 0, "apply": args.apply,
               "run_processed": 0, "task_id": "t_73094078"}
    stopped = threading.Event()
    def heartbeat():
        while not stopped.wait(15):
            save(root / "heartbeat.json", {"at": now(), "pid": os.getpid()})
    threading.Thread(target=heartbeat, daemon=True).start()
    def report():
        summary.update(updated_at=now(), outcomes=dict(collections.Counter(r["outcome"] for r in records.values())),
                       accounted=len(records), research_remaining=sum(r["Master_ID"] not in records for r in selected),
                       provider_stats=apis.stats, search_stats=research.stats)
        active = [r for r in records.values() if not r.get("excluded")]
        summary["first_pass_remaining"] = summary["research_remaining"]
        summary["improvement_retries_remaining"] = sum(
            (
                r.get("domain") in THIRD_PARTY_IDENTITY_DOMAINS
                or (
                    r.get("discovery_version", 1) < DISCOVERY_VERSION
                    and r.get("outcome") in {"identity_unconfirmed", "domain_confirmed_no_route", "email_candidates_held", "portal_only"}
                )
                or (
                    args.use_outscraper_fallback
                    and not r.get("outscraper_attempted")
                    and r.get("outcome") in {"identity_unconfirmed", "domain_confirmed_no_route", "email_candidates_held", "provider_research_incomplete"}
                )
            )
            for r in active)
        summary["research_remaining"] += summary["improvement_retries_remaining"]
        summary["routes"] = {
            "unique_candidate_emails": len({c["email"] for r in active for c in r.get("contacts", [])}),
            "first_party_emails": len({c["email"] for r in active for c in r.get("contacts", []) if c.get("relevance_confirmed") and c.get("provider") == "first_party"}),
            "verified_emails": len({r["selected"]["email"] for r in active if r.get("selected")}),
            "unique_portals": len({p["url"] for r in active for p in r.get("portals", [])}),
            "companies_with_portals": sum(bool(r.get("portals")) for r in active),
        }
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
                retry_improved = (
                    records[mid].get("domain") in THIRD_PARTY_IDENTITY_DOMAINS
                    or (
                        records[mid].get("discovery_version", 1) < DISCOVERY_VERSION
                        and records[mid].get("outcome") in {"identity_unconfirmed", "domain_confirmed_no_route", "email_candidates_held", "portal_only"}
                    )
                    or (
                        args.use_outscraper_fallback
                        and not records[mid].get("outscraper_attempted")
                        and records[mid].get("outcome") in {"identity_unconfirmed", "domain_confirmed_no_route", "email_candidates_held", "provider_research_incomplete"}
                    )
                )
                if not retry_improved and records[mid].get("outcome") not in {"research_error", "search_unavailable", "provider_research_incomplete"}:
                    continue
            if (args.limit and summary["run_processed"] >= args.limit) or time.monotonic()-started >= args.max_runtime:
                break
            summary["current_company"] = mid
            report()
            signal.alarm(args.company_timeout)
            try:
                result = process(row, research, apis, dedupe, outscraper=outscraper, maps_cache=maps_cache)
            except (Exception, CompanyDeadline) as exc:
                result = {"master_id": mid, "company": row.get("Company_or_Office"), "outcome": "research_error",
                          "error_type": type(exc).__name__, "at": now()}
            finally:
                signal.alarm(0)
            if mid in records and not result.get("domain") and can_preserve_current_domain(records[mid]):
                # Preserve only evidence already established by the *current*
                # discovery contract. A version upgrade exists specifically to
                # revalidate older identity decisions, so a failed fresh check
                # must not silently promote a stale domain into the new version.
                save(root / "history" / (mid + "-retry-" + result["at"].replace(":", "") + ".json"), result)
                result = dict(records[mid], discovery_version=DISCOVERY_VERSION,
                              retry_outcome=result["outcome"])
            if mid in records and result.get("domain") == records[mid].get("domain") and result.get("domain"):
                contacts = {c["email"]: c for c in records[mid].get("contacts", [])}
                contacts.update({c["email"]: c for c in result.get("contacts", [])})
                result["contacts"] = list(contacts.values())
                portals = {p["url"]: p for p in records[mid].get("portals", [])}
                portals.update({p["url"]: p for p in result.get("portals", [])})
                result["portals"] = list(portals.values())
            if mid in records:
                result["supersedes_result_at"] = records[mid].get("at")
                save(root / "history" / (mid + "-" + records[mid].get("at", "unknown").replace(":", "") + ".json"), records[mid])
            records[mid] = result
            summary["run_processed"] += 1
            report()  # Durable research before any external write.
            result["tracker_write"] = write_result(legacy, result, args.apply)
            report()
            print(json.dumps({"master_id": mid, "outcome": result["outcome"], "processed": summary["run_processed"]}), flush=True)
            if research.consecutive_errors >= 5 and outscraper is None:
                summary["status"] = "paused_search_unavailable"
                break
            time.sleep(1)
        if summary["status"] == "running":
            summary["status"] = "batch_complete" if any(r["Master_ID"] not in records for r in selected) else "scope_processed"
        summary["balances_after"] = apis.account_balances()
        if outscraper is not None:
            try:
                summary["outscraper_balance_after"] = legacy.balance(outscraper)
            except Exception as exc:
                summary["outscraper_balance_after"] = None
                summary["outscraper_balance_error"] = type(exc).__name__
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
