#!/usr/bin/env python3
"""Prepare the validated Outscraper campaign queue without sending email.

This script reconciles current Send Queue rows against recent Gmail Sent/Drafts,
prioritizes source-associated RECEIVING routes, writes deterministic queue
artifacts, and optionally updates only Send_State (column O) in the authoritative
Google Sheet. It never creates/sends Gmail messages and never submits portals.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import time
import urllib.error
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from email.utils import getaddresses
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from career_engine.gmail import _gmail_access_token
from runtime.outscraper_sheet_runner import SPREADSHEET_ID, read_queue, rclone_access_token, write_state_updates
from runtime.run_outscraper_monitored import mailbox_route_kind

REPO_ROOT = Path(__file__).resolve().parents[1]
MONITOR_ROOT = REPO_ROOT / 'runtime/acceptance/outscraper-monitor-20260901'
CV_PATH = Path('/home/hameedo/projects/ai-job-search/projects/job-automation/artifacts/general-consultancy-outreach-controller-20260828/Abdelhamid_Farah_CV_Senior_Design_Project_Leadership.pdf')
PORTFOLIO_PATH = MONITOR_ROOT / 'materials/Abdelhamid Farah-Portfolio-2026.pdf'
TARGETS_PATH = Path('/home/hameedo/obsidian/HermesOpsVault/projects/job-automation/research/rega-real-estate-developer-targets.md')
SUBJECT = 'Abdelhamid Farah | Senior Design & Project Leadership'
BODY = '''Dear Hiring Team,\nI am reaching out to express interest in senior design, project delivery, or consultancy-management opportunities with your organization. Please find my CV and portfolio attached for your consideration.\nI would welcome the opportunity to discuss where my background may be relevant to your current or upcoming requirements.\nKind regards,\nAbdelhamid Farah\nhameedfarah@gmail.com\n'''
ENGINEERING_SOURCE = 'Engineering offices.xlsx / Sheet1'
REGA_MASTER = 'REGA master analysis / All_Companies'
REGA_DEDUPED = 'REGA deduped email queue / All_New_Emails'
RECOVERY_SOURCE = 'career_engine_portfolio_bounce_recovery_tracker_2026-08-31.csv'


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def gmail_json(token: str, url: str) -> dict:
    last_exc: BaseException | None = None
    for attempt in range(4):
        req = Request(url, headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/json'})
        try:
            with urlopen(req, timeout=30) as response:
                return json.loads(response.read().decode('utf-8') or '{}')
        except urllib.error.HTTPError as exc:
            last_exc = exc
            if exc.code not in {429, 500, 502, 503, 504} or attempt == 3:
                raise
        except (urllib.error.URLError, ConnectionResetError, TimeoutError, OSError) as exc:
            last_exc = exc
            if attempt == 3:
                raise
        time.sleep(0.5 * (attempt + 1))
    raise RuntimeError('Gmail request failed after bounded retries') from last_exc


def gmail_list_all(token: str, kind: str, query: str) -> list[dict]:
    key = 'messages' if kind == 'messages' else 'drafts'
    out: list[dict] = []
    page = ''
    while True:
        params = {'q': query, 'maxResults': 500}
        if page:
            params['pageToken'] = page
        data = gmail_json(token, f'https://gmail.googleapis.com/gmail/v1/users/me/{kind}?{urlencode(params)}')
        out.extend(data.get(key, []) or [])
        page = str(data.get('nextPageToken') or '')
        if not page:
            return out


def parse_address(value: str) -> str:
    found = re.search(r'<([^>]+@[^>]+)>', value or '') or re.search(r'([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})', value or '')
    return found.group(1).strip().lower() if found else ''


def _header_recipients(headers: list[dict]) -> set[str]:
    values = [
        str(header.get('value') or '')
        for header in headers
        if str(header.get('name') or '').strip().lower() in {'to', 'cc', 'bcc'}
    ]
    return {
        address.strip().lower()
        for _display, address in getaddresses(values)
        if address and '@' in address
    }


def message_recipients(token: str, message_id: str) -> tuple[set[str], str]:
    params = urlencode([
        ('format', 'metadata'),
        ('metadataHeaders', 'To'),
        ('metadataHeaders', 'Cc'),
        ('metadataHeaders', 'Bcc'),
    ])
    data = gmail_json(token, f'https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}?{params}')
    headers = ((data.get('payload') or {}).get('headers') or [])
    return _header_recipients(headers), message_id


def draft_recipients(token: str, draft_id: str) -> tuple[set[str], str]:
    params = urlencode([
        ('format', 'metadata'),
        ('metadataHeaders', 'To'),
        ('metadataHeaders', 'Cc'),
        ('metadataHeaders', 'Bcc'),
    ])
    data = gmail_json(token, f'https://gmail.googleapis.com/gmail/v1/users/me/drafts/{draft_id}?{params}')
    headers = (((data.get('message') or {}).get('payload') or {}).get('headers') or [])
    return _header_recipients(headers), draft_id


def gmail_dedupe() -> tuple[dict[str, str], dict[str, str]]:
    token = _gmail_access_token()
    sent = gmail_list_all(token, 'messages', 'in:sent after:2026/08/01')
    drafts = gmail_list_all(token, 'drafts', '')
    sent_ids = [str(x.get('id') or '') for x in sent if x.get('id')]
    draft_ids = [str(x.get('id') or '') for x in drafts if x.get('id')]
    with ThreadPoolExecutor(max_workers=8) as pool:
        sent_pairs = list(pool.map(lambda mid: message_recipients(token, mid), sent_ids))
    with ThreadPoolExecutor(max_workers=8) as pool:
        draft_pairs = list(pool.map(lambda did: draft_recipients(token, did), draft_ids))
    # Gmail returns newest first; preserve first message/draft observed per recipient.
    sent_by_email: dict[str, str] = {}
    draft_by_email: dict[str, str] = {}
    for recipients, mid in sent_pairs:
        for email in recipients:
            if email not in sent_by_email:
                sent_by_email[email] = mid
    for recipients, did in draft_pairs:
        for email in recipients:
            if email not in draft_by_email:
                draft_by_email[email] = did
    return sent_by_email, draft_by_email


def curated_routes() -> tuple[set[str], list[dict]]:
    text = TARGETS_PATH.read_text(encoding='utf-8')
    emails = {x.lower() for x in re.findall(r'[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}', text)}
    portals: list[dict] = []
    for line in text.splitlines():
        if not line.startswith('| ') or line.startswith('| Priority') or line.startswith('|---'):
            continue
        cells = [cell.strip() for cell in line.strip().strip('|').split('|')]
        if len(cells) != 8:
            continue
        priority, company, location, website, careers, hr_email, route, status = cells
        if not careers.startswith('http'):
            continue
        route_l = route.lower()
        url_l = careers.lower()
        actionable = any(x in url_l for x in ('career', '/job', 'jobs', 'recruit', 'employment', 'join-us', 'employedmnt', 'job_requests'))
        # A verified contact form may still be an application route when the
        # canonical research explicitly says it is used for CV intake.
        if '/contact' in url_l and 'cv intake' in route_l:
            actionable = True
        if 'career route not found' in route_l or 'careers route unresolved' in route_l:
            actionable = False
        portals.append({
            'priority': priority,
            'company': company,
            'location': location,
            'url': careers,
            'route': route,
            'research_status': status,
            'actionable_application_route': bool(actionable),
            'source': 'canonical_rega_target_research',
            'submission_authorized': False,
        })
    return emails, portals


def current_rega_portals() -> list[dict]:
    path = MONITOR_ROOT / 'rega-enrichment-consolidated.csv'
    if not path.is_file():
        return []
    with path.open(encoding='utf-8', newline='') as handle:
        rows = list(csv.DictReader(handle))
    out: list[dict] = []
    for row in rows:
        for field in ('careers_page', 'ats_url'):
            url = str(row.get(field) or '').strip()
            if not url.startswith('http'):
                continue
            out.append({
                'priority': str(row.get('Career Priority') or ''),
                'company': str(row.get('English Name') or ''),
                'location': str(row.get('English Location(s)') or ''),
                'url': url,
                'route': field,
                'research_status': f"current_{str(row.get('assignment') or '').strip().lower()}",
                'actionable_application_route': True,
                'source': 'current_rega_enrichment_2026-09-01',
                'submission_authorized': False,
            })
    return out


def priority_for(row: dict[str, str]) -> tuple[int, str]:
    source = str(row.get('Source_Dataset') or '').strip()
    route = mailbox_route_kind(str(row.get('Email') or '').strip().lower())
    if source in {RECOVERY_SOURCE, REGA_MASTER} and route == 'recruitment':
        return 1, 'verified_direct_recruitment'
    if source in {RECOVERY_SOURCE, REGA_MASTER}:
        return 2, 'verified_official_general'
    if source == ENGINEERING_SOURCE and route == 'general':
        return 3, 'engineering_source_general'
    if source == ENGINEERING_SOURCE:
        return 4, 'engineering_source_listed'
    return 9, 'held_or_other'


def identity_eligible(row: dict[str, str], curated_emails: set[str]) -> tuple[bool, str]:
    email = str(row.get('Email') or '').strip().lower()
    source = str(row.get('Source_Dataset') or '').strip()
    verification = str(row.get('Source_Verification') or '').strip()
    if mailbox_route_kind(email) == 'excluded':
        return False, 'excluded_mailbox_localpart'
    if source == ENGINEERING_SOURCE and verification == 'SOURCE_LISTED_ONLY' and str(row.get('Source_Record_ID') or '').strip():
        return True, 'engineering_source_record_plus_current_receiving'
    if source == RECOVERY_SOURCE and verification.lower().startswith('verified'):
        return True, 'current_verified_recovery_route'
    if source == REGA_MASTER and verification.lower().startswith('verified - official'):
        return True, 'verified_official_rega_master'
    if email in curated_emails:
        return True, 'canonical_verified_rega_recruitment_route'
    return False, 'identity_not_confirmed'


def update_sheet_states(token: str, updates: list[tuple[str, str, str]]) -> int:
    written = 0
    for offset in range(0, len(updates), 25):
        written += write_state_updates(token, updates[offset:offset+25], SPREADSHEET_ID)
    return written


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply-sheet', action='store_true')
    args = parser.parse_args()
    for path in (CV_PATH, PORTFOLIO_PATH, TARGETS_PATH):
        if not path.is_file():
            raise SystemExit(f'missing required material/source: {path}')
    if not CV_PATH.read_bytes().startswith(b'%PDF') or not PORTFOLIO_PATH.read_bytes().startswith(b'%PDF'):
        raise SystemExit('career attachment is not a valid PDF header')

    token = rclone_access_token()
    rows = read_queue(token, SPREADSHEET_ID)
    if len(rows) != 1236:
        raise SystemExit(f'expected 1236 queue rows, got {len(rows)}')
    sent_by_email, draft_by_email = gmail_dedupe()
    curated_emails, portals = curated_routes()
    portals.extend(current_rega_portals())
    portal_by_url: dict[str, dict] = {}
    for item in portals:
        url = item['url'].rstrip('/')
        current = portal_by_url.get(url)
        if current is None or item['source'].startswith('current_'):
            portal_by_url[url] = item
    portals = sorted(portal_by_url.values(), key=lambda x: (not x['actionable_application_route'], x['priority'], x['company'], x['url']))

    prepared: list[dict] = []
    held: list[dict] = []
    sheet_updates: list[tuple[str, str, str]] = []
    already_sent = 0
    already_drafted = 0
    for row in rows:
        queue_id = str(row.get('Queue_ID') or '').strip()
        email = str(row.get('Email') or '').strip().lower()
        provider_status = str(row.get('Outscraper_Status') or '').strip().upper()
        if email in sent_by_email:
            sheet_updates.append((queue_id, email, 'ALREADY_SENT_DEDUPED'))
            already_sent += 1
            continue
        if email in draft_by_email:
            sheet_updates.append((queue_id, email, 'ALREADY_DRAFTED_DEDUPED'))
            already_drafted += 1
            continue
        if provider_status != 'RECEIVING':
            continue
        eligible, basis = identity_eligible(row, curated_emails)
        if not eligible:
            held.append({
                'queue_id': str(row.get('Queue_ID') or ''),
                'email': email,
                'company': str(row.get('Company_or_Office') or ''),
                'source_dataset': str(row.get('Source_Dataset') or ''),
                'source_verification': str(row.get('Source_Verification') or ''),
                'reason': basis,
                'provider_status': provider_status,
            })
            continue
        priority, tier = priority_for(row)
        prepared.append({
            'queue_id': str(row.get('Queue_ID') or ''),
            'priority': priority,
            'priority_tier': tier,
            'email': email,
            'company': str(row.get('Company_or_Office') or ''),
            'source_dataset': str(row.get('Source_Dataset') or ''),
            'source_record_id': str(row.get('Source_Record_ID') or ''),
            'source_verification': str(row.get('Source_Verification') or ''),
            'identity_basis': basis,
            'provider_status': provider_status,
            'provider_verification': str(row.get('Outscraper_Verification') or ''),
            'subject': SUBJECT,
            'body': BODY,
            'sender': 'hameedfarah@gmail.com',
            'attachments': [
                {'filename': CV_PATH.name, 'path': str(CV_PATH), 'sha256': sha256(CV_PATH), 'size_bytes': CV_PATH.stat().st_size},
                {'filename': PORTFOLIO_PATH.name, 'path': str(PORTFOLIO_PATH), 'sha256': sha256(PORTFOLIO_PATH), 'size_bytes': PORTFOLIO_PATH.stat().st_size},
            ],
            'queue_state': 'queued_prepared_no_send',
            'gmail_draft_materialized': False,
            'send_authorized': False,
        })
        sheet_updates.append((queue_id, email, 'QUEUED_OUTSCRAPER_PREPARED'))

    prepared.sort(key=lambda x: (x['priority'], x['company'].lower(), x['email']))
    direct_prepared_hiring = {
        x['email'] for x in prepared if x['priority_tier'] == 'verified_direct_recruitment'
    }
    hiring_emails = curated_emails | direct_prepared_hiring
    hr_routes = []
    for email in sorted(hiring_emails):
        matching = [r for r in rows if str(r.get('Email') or '').strip().lower() == email]
        hr_routes.append({
            'email': email,
            'current_sheet_match': bool(matching),
            'current_provider_status': str(matching[0].get('Outscraper_Status') or '') if matching else 'NOT_IN_CURRENT_SHEET',
            'already_sent_since_2026_08_01': email in sent_by_email,
            'already_drafted': email in draft_by_email,
            'source': 'canonical_rega_target_research' if email in curated_emails else 'verified_current_recovery_route',
            'send_authorized': False,
        })

    if args.apply_sheet:
        update_sheet_states(token, sheet_updates)
        after = read_queue(token, SPREADSHEET_ID)
        state_counts = Counter(str(r.get('Send_State') or '').strip() or 'EMPTY' for r in after)
    else:
        state_counts = Counter(str(r.get('Send_State') or '').strip() or 'EMPTY' for r in rows)

    MONITOR_ROOT.mkdir(parents=True, exist_ok=True)
    (MONITOR_ROOT/'email-preparation-queue.json').write_text(json.dumps({'queue': prepared}, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    (MONITOR_ROOT/'email-identity-holds.json').write_text(json.dumps({'holds': held}, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    (MONITOR_ROOT/'portal-review-queue.json').write_text(json.dumps({'portals': portals}, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    (MONITOR_ROOT/'verified-hr-routes.json').write_text(json.dumps({'routes': hr_routes}, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    summary = {
        'ok': True,
        'prepared_queue': len(prepared),
        'held_receiving_identity': len(held),
        'already_sent_deduped': already_sent,
        'already_drafted_deduped': already_drafted,
        'priority_counts': dict(Counter(x['priority_tier'] for x in prepared)),
        'source_counts': dict(Counter(x['source_dataset'] for x in prepared)),
        'verified_hiring_routes': len(hr_routes),
        'verified_hiring_current_receiving': sum(x['current_provider_status'].upper() == 'RECEIVING' for x in hr_routes),
        'verified_hiring_current_receiving_unsent': sum(
            x['current_provider_status'].upper() == 'RECEIVING' and not x['already_sent_since_2026_08_01'] and not x['already_drafted']
            for x in hr_routes
        ),
        'portal_routes_total': len(portals),
        'portal_routes_actionable': sum(bool(x['actionable_application_route']) for x in portals),
        'sheet_applied': bool(args.apply_sheet),
        'sheet_state_counts': dict(state_counts),
        'gmail_drafts_materialized': 0,
        'gmail_storage_avoided_bytes_approx': sum(sum(a['size_bytes'] for a in x['attachments']) for x in prepared),
        'sends': 0,
        'portal_submissions': 0,
    }
    (MONITOR_ROOT/'preparation-summary.json').write_text(json.dumps(summary, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
