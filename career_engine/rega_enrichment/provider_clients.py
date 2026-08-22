"""Small, fail-closed HTTP clients for career enrichment providers.

The clients use an injectable opener so tests never need network access.  They
return dictionaries containing only safe metadata and never include response
bodies in errors.
"""
from __future__ import annotations

import base64, json, re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

NOW = lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

@dataclass
class ProviderBudget:
    allow_existing_credit: bool = False
    max_calls: int = 0
    max_credits: float = 0
    max_domains: int = 0
    calls: int = 0
    credits: float = 0
    domains: int = 0
    def permit(self, *, billable=False, credits=0, domains=0) -> bool:
        if billable and not self.allow_existing_credit: return False
        if self.calls >= self.max_calls or self.credits + credits > self.max_credits or self.domains + domains > self.max_domains: return False
        self.calls += 1; self.credits += credits; self.domains += domains
        return True

def classify_local_part(email: str) -> str:
    local = email.split("@", 1)[0].lower() if "@" in email else ""
    if local in {"hr", "careers", "jobs", "recruitment", "talent"}: return "role_candidate"
    if local in {"info", "sales", "support", "admin", "hello", "contact"}: return "generic"
    return "person" if local else "unknown"

def _record(provider, source, evidence, *, status="candidate", cost_status="unknown", **meta):
    return {"provider": provider, "retrieved_at": NOW(), "source_url": source,
            "evidence": evidence, "status": status, "cost_status": cost_status,
            "metadata": {k: v for k, v in meta.items() if k not in {"secret", "api_key", "password"}}}

class ProviderClient:
    provider = "provider"
    def __init__(self, opener=urlopen): self.opener = opener
    def _request(self, method, url, headers=None, payload=None):
        data = json.dumps(payload).encode() if payload is not None else None
        req = Request(url, data=data, headers=headers or {}, method=method)
        try:
            with self.opener(req, timeout=15) as r: return r.status, json.loads(r.read().decode() or "{}")
        except HTTPError as e: return e.code, None
        except (URLError, OSError, TimeoutError): return 0, None
    def _failure(self, status, source): return _record(self.provider, source, "", status=status, cost_status="not_charged")

class DataForSEOClient(ProviderClient):
    provider = "dataforseo"; root = "https://api.dataforseo.com/v3"
    def __init__(self, credential, opener=urlopen):
        super().__init__(opener); self.credential = credential.strip()
    def _headers(self):
        token = self.credential[6:].strip() if self.credential.lower().startswith("basic ") else self.credential
        if ":" in token: token = base64.b64encode(token.encode()).decode()
        if not token: raise ValueError("credential_format_invalid")
        return {"Authorization": "Basic " + token}
    def account(self):
        try: s, _ = self._request("GET", self.root + "/appendix/user_data", self._headers())
        except ValueError: return self._failure("credential_format_invalid", self.root + "/appendix/user_data")
        return _record(self.provider, self.root + "/appendix/user_data", "account probe", status="success" if s == 200 else "auth_failed" if s in (401,403) else "failed", cost_status="free")
    def search(self, query, budget: ProviderBudget):
        source = self.root + "/serp/google/organic/live/advanced"
        try: headers = self._headers()
        except ValueError: return [self._failure("credential_format_invalid", source)]
        if not budget.permit(billable=True): return [self._failure("budget_exhausted", source)]
        s, body = self._request("POST", source, {**headers, "Content-Type":"application/json"}, [{"keyword":query,"location_name":"Saudi Arabia","language_code":"en","depth":10,"device":"desktop"}])
        if s != 200 or not isinstance(body, dict): return [self._failure("failed", source)]
        out=[]
        for task in body.get("tasks",[]):
            cost=task.get("cost");
            for item in (task.get("result") or [{}])[0].get("items",[]):
                if item.get("type") == "organic" and item.get("url"):
                    out.append(_record(self.provider, source, "organic SERP result", cost_status="charged" if cost else "unknown", url=item.get("url"), title=item.get("title",""), description=item.get("description",""), task_cost=cost))
        return out or [_record(self.provider, source, "no organic results", status="not_found", cost_status="charged" if body.get("cost") else "unknown", task_cost=body.get("cost"))]

class TombaClient(ProviderClient):
    provider="tomba"; root="https://api.tomba.io/v1"
    def __init__(self, key, secret="", opener=urlopen): super().__init__(opener); self.key=key; self.secret=secret
    def domain_search(self, domain, budget):
        source=self.root+"/domain-search?"+urlencode({"domain":domain})
        if not self.key or not self.secret: return [_record(self.provider,source,"",status="missing_required_secret",cost_status="not_charged")]
        if not budget.permit(billable=True, domains=1): return [_record(self.provider,source,"",status="budget_exhausted",cost_status="not_charged")]
        s,b=self._request("GET",source,{"X-Tomba-Key":self.key,"X-Tomba-Secret":self.secret})
        if s!=200 or not isinstance(b,dict): return [self._failure("failed",source)]
        return [_record(self.provider, source, "Tomba domain search", email=x.get("email",""), full_name=x.get("full_name",""), position=x.get("position",""), department=x.get("department",""), seniority=x.get("seniority",""), verification=x.get("verification"), source_urls=x.get("sources",[])) for x in (b.get("data",{}).get("emails") or b.get("emails") or [])]

class ApifyClient(ProviderClient):
    provider="apify"
    def account(self, key):
        source="https://api.apify.com/v2/users/me"; s,_=self._request("GET",source,{"Authorization":"Bearer "+key})
        return _record(self.provider,source,"account probe",status="success" if s==200 else "failed",cost_status="free")
    def actor_status(self): return _record(self.provider,"","actor execution not enabled",status="not_configured_actor",cost_status="not_charged")

class OutscraperClient(ProviderClient):
    provider="outscraper"
    def domain_contacts(self, domain, budget):
        return [_record(self.provider, "", "official endpoint not verified", status="disabled_unverified_endpoint", cost_status="not_charged", domain=domain)]

class AnymailFinderClient(ProviderClient):
    provider="anymailfinder"; root="https://api.anymailfinder.com/v5.1"
    def __init__(self,key,opener=urlopen): super().__init__(opener); self.key=key
    def account(self):
        source=self.root+"/account"; s,_=self._request("GET",source,{"Authorization":self.key}); return _record(self.provider,source,"account probe",status="success" if s==200 else "failed",cost_status="free")
    def company(self,domain,budget):
        source=self.root+"/find-email/company"
        if not budget.permit(billable=True,credits=1,domains=1): return [_record(self.provider,source,"",status="budget_exhausted",cost_status="not_charged")]
        s,b=self._request("POST",source,{"Authorization":self.key,"Content-Type":"application/json"},{"domain":domain})
        if s!=200 or not isinstance(b,dict): return [self._failure("failed",source)]
        emails=b.get("emails") or ([] if not b.get("email") else [b["email"]]); charged=b.get("credits_charged", 1 if emails else 0); return [_record(self.provider,source,"company email result",email=e,cost_status="charged" if charged else "free",credits_charged=charged,mailbox_class=classify_local_part(e)) for e in emails[:20]]

class ZeroBounceClient(ProviderClient):
    provider="zerobounce"; root="https://api.zerobounce.net/v2"
    def validate(self,email,budget,*,allow_existing_credit=False,intended_outreach=False):
        source=self.root+"/validate"; status="not_permitted" if not (allow_existing_credit and intended_outreach) else ""
        if status or not budget.permit(billable=True,credits=1): return [_record(self.provider,source,"validation gated",status=status or "budget_exhausted",cost_status="not_charged")]
        s,b=self._request("GET",source+"?"+urlencode({"api_key":"redacted","email":email,"ip_address":""}))
        st=(b or {}).get("status","unknown").lower(); return [_record(self.provider,source,"email validation",status=st if st in {"valid","invalid","catch-all","unknown","spamtrap","abuse","do_not_mail"} else "unknown",cost_status="charged" if st!="unknown" else "free")]
