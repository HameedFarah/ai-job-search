"""Value-free, no-charge provider health probes.

The probe never prints credentials or response bodies. A provider is only
called when its configured secret is present, and every request is bounded.
"""
from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request

NAMES = ("DATAFORSEO_API_KEY", "TOMBA_API_KEY", "APIFY_API_KEY", "APIFY_USER_ID",
         "OUTSCRAPER_API_KEY", "ANYMAILFINDER_API_KEY", "ZEROBOUNCE_API_KEY")

def probe(name: str, url: str, headers: dict[str, str] | None = None) -> dict[str, object]:
    req = urllib.request.Request(url, headers=headers or {}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=12) as response:
            return {"provider": name, "status": "success", "http_status": response.status}
    except urllib.error.HTTPError as exc:
        status = {401: "auth_failed", 403: "auth_failed", 402: "quota_required", 429: "quota_required"}.get(exc.code, "http_failed")
        return {"provider": name, "status": status, "http_status": exc.code}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {"provider": name, "status": "network_failed", "reason_type": type(getattr(exc, "reason", exc)).__name__}

def _dataforseo_headers(value: str) -> dict[str, str]:
    # Accept either a pre-composed Basic token or login:password material.
    token = value.strip()
    if ":" in token and not token.lower().startswith("basic "):
        token = base64.b64encode(token.encode()).decode()
    elif token.lower().startswith("basic "):
        token = token.split(" ", 1)[1].strip()
    return {"Authorization": "Basic " + token}

def main() -> int:
    present = {name: bool(os.environ.get(name, "").strip()) for name in NAMES}
    results: list[dict[str, object]] = []
    if present["DATAFORSEO_API_KEY"]:
        results.append(probe("dataforseo", "https://api.dataforseo.com/v3/serp/google/locations", _dataforseo_headers(os.environ["DATAFORSEO_API_KEY"])))
    if present["TOMBA_API_KEY"]:
        results.append(probe("tomba", "https://api.tomba.io/v1/account", {"X-Api-Key": os.environ["TOMBA_API_KEY"]}))
    if present["APIFY_API_KEY"]:
        results.append(probe("apify", "https://api.apify.com/v2/users/me", {"Authorization": "Bearer " + os.environ["APIFY_API_KEY"]}))
    if present["OUTSCRAPER_API_KEY"]:
        results.append(probe("outscraper", "https://api.app.outscraper.com/api/v1/account", {"X-API-KEY": os.environ["OUTSCRAPER_API_KEY"]}))
    if present["ANYMAILFINDER_API_KEY"]:
        results.append(probe("anymailfinder", "https://api.anymailfinder.com/v5/account", {"X-Api-Key": os.environ["ANYMAILFINDER_API_KEY"]}))
    if present["ZEROBOUNCE_API_KEY"]:
        url = "https://api.zerobounce.net/v2/getcredits?api_key=" + urllib.parse.quote(os.environ["ZEROBOUNCE_API_KEY"], safe="")
        results.append(probe("zerobounce", url))
    print(json.dumps({"credential_presence": present, "probes": results}, sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
