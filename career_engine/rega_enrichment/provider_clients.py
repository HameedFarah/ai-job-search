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
    def _headers(self):
        if not self.key or not self.secret:
            return None
        return {"X-Tomba-Key":self.key,"X-Tomba-Secret":self.secret}
    def rate_limits(self):
        source=self.root+"/rate-limits"
        headers=self._headers()
        if not headers:
            return _record(self.provider,source,"rate-limit probe",status="missing_required_secret",cost_status="free")
        s,_=self._request("GET",source,headers)
        return _record(self.provider,source,"rate-limit probe",status="success" if s==200 else "auth_failed" if s in (401,403) else "failed",cost_status="free")
    def domain_search(self, domain, budget):
        source=self.root+"/domain-search?"+urlencode({"domain":domain})
        headers=self._headers()
        if not headers: return [_record(self.provider,source,"",status="missing_required_secret",cost_status="not_charged")]
        if not budget.permit(billable=True, domains=1): return [_record(self.provider,source,"",status="budget_exhausted",cost_status="not_charged")]
        s,b=self._request("GET",source,headers)
        if s!=200 or not isinstance(b,dict): return [self._failure("auth_failed" if s in (401,403) else "quota_required" if s in (402,429) else "failed",source)]
        emails=(b.get("data",{}).get("emails") or b.get("emails") or [])
        if not isinstance(emails,list):
            emails=[]
        out=[]
        for x in emails:
            if not isinstance(x,dict) or not x.get("email"):
                continue
            raw_sources=x.get("sources") or []
            source_urls=[]
            if isinstance(raw_sources,list):
                for item in raw_sources:
                    if isinstance(item,str): source_urls.append(item)
                    elif isinstance(item,dict) and item.get("uri"): source_urls.append(str(item["uri"]))
                    elif isinstance(item,dict) and item.get("url"): source_urls.append(str(item["url"]))
            out.append(_record(
                self.provider,
                self.root+"/domain-search",
                "Tomba domain-search candidate with provider source references",
                email=str(x.get("email")),
                full_name=x.get("full_name","") or " ".join(str(x.get(k) or "") for k in ("first_name","last_name")).strip(),
                position=x.get("position","") or x.get("job_title","") or "",
                department=x.get("department",""),
                seniority=x.get("seniority",""),
                verification=x.get("verification"),
                source_urls=source_urls,
                mailbox_class=classify_local_part(str(x.get("email"))),
            ))
        return out or [_record(self.provider,self.root+"/domain-search","domain search returned no contacts",status="not_found",cost_status="free_or_existing_credit")]

class ApifyClient(ProviderClient):
    provider="apify"
    def __init__(self,key="",opener=urlopen): super().__init__(opener); self.key=key
    def account(self):
        source="https://api.apify.com/v2/users/me"
        if not self.key: return _record(self.provider,source,"account probe",status="missing_credential",cost_status="free")
        s,_=self._request("GET",source,{"Authorization":"Bearer "+self.key})
        return _record(self.provider,source,"account probe",status="success" if s==200 else "auth_failed" if s in (401,403) else "quota_required" if s in (402,429) else "network_failed" if s == 0 else "failed",cost_status="free")
    def actor(self, actor_id, input_payload, budget: ProviderBudget):
        source="https://api.apify.com/v2/acts/"+str(actor_id)+"/runs"
        if not self.key: return [_record(self.provider,source,"actor run gated",status="missing_credential",cost_status="not_charged")]
        if not actor_id or not budget.permit(billable=True): return [_record(self.provider,source,"actor run gated",status="budget_exhausted",cost_status="not_charged")]
        s,b=self._request("POST",source+"?waitForFinish=30",{"Authorization":"Bearer "+self.key,"Content-Type":"application/json"},input_payload)
        return [_record(self.provider,source,"bounded actor adapter response",status="success" if s in (200,201) else "auth_failed" if s in (401,403) else "quota_required" if s in (402,429) else "network_failed" if s == 0 else "failed",cost_status="charged" if s in (200,201) else "not_charged",actor_id=str(actor_id))]
    def actor_status(self): return _record(self.provider,"","actor execution not enabled",status="not_configured_actor",cost_status="not_charged")

class OutscraperClient(ProviderClient):
    provider="outscraper"; root="https://api.outscraper.com"
    def __init__(self,key,opener=urlopen): super().__init__(opener); self.key=key
    def balance(self):
        source=self.root+"/profile/balance"
        if not self.key:
            return _record(self.provider,source,"balance probe",status="missing_credential",cost_status="free")
        s,b=self._request("GET",source,{"X-API-KEY":self.key})
        metadata={}
        if s==200 and isinstance(b,dict):
            if isinstance(b.get("balance"),(int,float)): metadata["balance"]=b["balance"]
            if isinstance(b.get("account_status"),str): metadata["account_status"]=b["account_status"]
        return _record(self.provider,source,"balance probe",status="success" if s==200 else "auth_failed" if s in (401,403) else "quota_required" if s==402 else "failed",cost_status="free",**metadata)
    def domain_contacts(self, domain, budget):
        source=self.root+"/emails-and-contacts"
        if not self.key:
            return [_record(self.provider,source,"",status="missing_credential",cost_status="not_charged")]
        if not budget.permit(billable=True,domains=1):
            return [_record(self.provider,source,"",status="budget_exhausted",cost_status="not_charged")]
        request_url=source+"?"+urlencode({"query":domain,"async":"false"})
        s,b=self._request("GET",request_url,{"X-API-KEY":self.key})
        if s!=200 or not isinstance(b,dict):
            return [self._failure("auth_failed" if s in (401,403) else "quota_required" if s in (402,429) else "failed",source)]
        rows=b.get("data") or []
        if not isinstance(rows,list): rows=[]
        out=[]
        for row in rows:
            if not isinstance(row,dict): continue
            for email_item in row.get("emails") or []:
                if not isinstance(email_item,dict) or not email_item.get("value"): continue
                email=str(email_item["value"])
                refs=[]
                for evidence_source in email_item.get("sources") or []:
                    if isinstance(evidence_source,dict) and evidence_source.get("ref"):
                        refs.append(str(evidence_source["ref"]))
                out.append(_record(
                    self.provider,
                    source,
                    "Outscraper domain email candidate with public-source references",
                    email=email,
                    domain=str(row.get("domain") or domain),
                    source_urls=refs,
                    mailbox_class=classify_local_part(email),
                    cost_status="free_tier_or_existing_metered_credit",
                ))
        return out or [_record(self.provider,source,"domain contacts returned no emails",status="not_found",cost_status="free_tier_or_existing_metered_credit",domain=domain)]

class AnymailFinderClient(ProviderClient):
    provider="anymailfinder"; root="https://api.anymailfinder.com/v5.1"
    def __init__(self,key,opener=urlopen): super().__init__(opener); self.key=key
    def account(self):
        source=self.root+"/account"; s,b=self._request("GET",source,{"Authorization":self.key})
        metadata = {}
        if s == 200 and isinstance(b, dict) and isinstance(b.get("credits_left"), (int, float)):
            metadata["credits_left"] = b["credits_left"]
        return _record(self.provider,source,"account probe",status="success" if s==200 else "auth_failed" if s in (401,403) else "failed",cost_status="free",**metadata)
    def company(self,domain,budget):
        source=self.root+"/find-email/company"
        if not self.key:
            return [_record(self.provider,source,"",status="missing_credential",cost_status="not_charged")]
        if not budget.permit(billable=True,credits=1,domains=1): return [_record(self.provider,source,"",status="budget_exhausted",cost_status="not_charged")]
        s,b=self._request("POST",source,{"Authorization":self.key,"Content-Type":"application/json"},{"domain":domain})
        if s!=200 or not isinstance(b,dict): return [self._failure("failed",source)]
        charged = b.get("credits_charged", 0)
        if not isinstance(charged, (int, float)):
            charged = 0
        email_status = str(b.get("email_status") or "").lower()
        emails = b.get("valid_emails") if email_status == "valid" else []
        if not isinstance(emails, list):
            emails = []
        if not emails:
            return [_record(
                self.provider,
                source,
                "company email lookup",
                status="not_found" if email_status in {"not_found", "not-found", ""} else email_status,
                cost_status="charged" if charged else "free",
                credits_charged=charged,
                email_status=email_status,
            )]
        return [
            _record(
                self.provider,
                source,
                "company email result",
                email=str(e),
                status="candidate",
                cost_status="charged" if charged else "free",
                credits_charged=charged,
                email_status=email_status,
                mailbox_class=classify_local_part(str(e)),
            )
            for e in emails[:20]
            if e
        ]

class ZeroBounceClient(ProviderClient):
    provider="zerobounce"; root="https://api.zerobounce.net/v2"
    def __init__(self,key,opener=urlopen): super().__init__(opener); self.key=key
    def credits(self):
        source=self.root+"/getcredits"
        if not self.key:
            return _record(self.provider,source,"credits probe",status="missing_credential",cost_status="free")
        request_url=source+"?"+urlencode({"api_key":self.key})
        s,b=self._request("GET",request_url)
        credits=(b or {}).get("Credits") if isinstance(b,dict) else None
        if s!=200:
            status="auth_failed" if s in (401,403) else "failed"
        elif str(credits) == "-1":
            status="auth_failed"
        else:
            status="success"
        metadata={}
        if status == "success" and credits is not None:
            metadata["credits"] = credits
        return _record(self.provider,source,"credits probe",status=status,cost_status="free",**metadata)
    def validate(self,email,budget,*,allow_existing_credit=False,intended_outreach=False):
        source=self.root+"/validate"; status="not_permitted" if not (allow_existing_credit and intended_outreach) else ""
        if not self.key:
            return [_record(self.provider,source,"validation gated",status="missing_credential",cost_status="not_charged")]
        if status or not budget.permit(billable=True,credits=1): return [_record(self.provider,source,"validation gated",status=status or "budget_exhausted",cost_status="not_charged")]
        request_url=source+"?"+urlencode({"api_key":self.key,"email":email,"ip_address":""})
        s,b=self._request("GET",request_url)
        if s != 200 or not isinstance(b, dict):
            return [self._failure("auth_failed" if s in (401,403) else "failed", source)]
        st=str(b.get("status") or "unknown").lower()
        allowed={"valid","invalid","catch-all","unknown","spamtrap","abuse","do_not_mail"}
        safe_status=st if st in allowed else "unknown"
        return [_record(
            self.provider,
            source,
            "email validation",
            status=safe_status,
            cost_status="charged" if safe_status!="unknown" else "free",
            safe_to_send=safe_status == "valid",
        )]
