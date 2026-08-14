"""Legacy SAP SuccessFactors Recruiting Management XML job-feed adapter.

Some employers still publish the documented RCM XML feed instead of a modern
Recruiting Marketing / Career Site Builder tile feed. SAP documents the public
feed shape as::

    /career?company=<company-id>&career_ns=job_listing_summary&resultType=XML

The field set is tenant-configurable, so this adapter intentionally maps a
small set of stable/common names and leaves unavailable values unknown instead
of inventing them. The feed is an official employer ATS surface and this code
never sends applications or contacts anyone.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from html import unescape
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from .. import network
from ..base import DiscoveryJob, SourceAdapter, SourceError, html_to_text
from ..dates import parse_date, unknown
from ..provenance import provenance as make_provenance

_SF_HOST_RE = re.compile(r"(?:^|\.)successfactors\.(?:eu|com)$", re.I)


class SuccessFactorsXmlAdapter(SourceAdapter):
    source_id = "successfactors_xml"
    source_name = "SAP SuccessFactors RCM public XML jobs"
    source_kind = "ats_web"
    official = True

    def search(
        self,
        *,
        company: str,
        location: str | None = None,
        limit: int = 10,
        fetch_full: bool = False,
        offline: bool = False,
    ) -> list[DiscoveryJob]:
        company_name, feed_url, company_id = self._feed_target(company)
        requested = max(1, min(int(limit), 100))
        if offline:
            with open(self._fixture_path("successfactors-xml.xml"), encoding="utf-8") as handle:
                xml_text = handle.read()
        else:
            xml_text = network.fetch_text(feed_url, max_bytes=8 * 1024 * 1024)

        jobs = self._parse_feed(xml_text, company_name=company_name, company_id=company_id, feed_url=feed_url)
        if location:
            wanted = location.strip().lower()
            jobs = [job for job in jobs if not job.location or self._location_matches(wanted, job.location.lower())]
        return jobs[:requested]

    @staticmethod
    def _location_matches(wanted: str, actual: str) -> bool:
        if wanted in actual:
            return True
        if wanted == "saudi arabia":
            return any(token in actual for token in ("saudi arabia", "riyadh", "jeddah", "dammam", "khobar", "ksa"))
        return False

    def _feed_target(self, value: str) -> tuple[str, str, str]:
        raw = str(value or "").strip()
        company_name = ""
        if "|" in raw and raw.split("|", 1)[1].lstrip().startswith(("https://", "http://")):
            company_name, raw = [part.strip() for part in raw.split("|", 1)]
        parsed = urlsplit(raw)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
            raise SourceError("SuccessFactors XML adapter requires an official HTTPS career URL")
        if not _SF_HOST_RE.search(parsed.hostname or ""):
            raise SourceError("SuccessFactors XML adapter requires a successfactors.eu/com host")
        query = parse_qs(parsed.query, keep_blank_values=True)
        company_id = str((query.get("company") or [""])[0]).strip()
        if not company_id:
            raise SourceError("SuccessFactors XML career URL must contain the employer-specific company= identifier")
        query.pop("_s.crb", None)
        query["company"] = [company_id]
        query["career_ns"] = ["job_listing_summary"]
        query["resultType"] = ["XML"]
        # urlencoding with doseq preserves any employer-supplied locale/filter.
        encoded = urlencode([(key, item) for key, values in query.items() for item in values])
        feed_url = urlunsplit((parsed.scheme, parsed.netloc, "/career", encoded, ""))
        return company_name or company_id, feed_url, company_id

    def _parse_feed(self, xml_text: str, *, company_name: str, company_id: str, feed_url: str) -> list[DiscoveryJob]:
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as exc:
            raise SourceError(f"SuccessFactors XML feed returned invalid XML: {exc}") from exc

        nodes = [node for node in root.iter() if self._tag(node.tag) in {"job", "item", "requisition", "jobposting"}]
        results: list[DiscoveryJob] = []
        seen: set[str] = set()
        for node in nodes:
            fields = self._flatten(node)
            title = self._pick(fields, "title", "jobtitle", "externaljobtitle", "exttitle")
            detail_url = self._pick(fields, "url", "joburl", "applyurl", "externaljoburl")
            raw_id = self._pick(fields, "referencenumber", "jobreqid", "jobid", "requisitionid", "id")
            if not raw_id and detail_url:
                q = parse_qs(urlsplit(detail_url).query)
                raw_id = self._first_query(q, "career_job_req_id", "jobId", "job", "jobReqId")
            if not title:
                continue
            if not raw_id:
                raw_id = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:120]
            if raw_id in seen:
                continue
            seen.add(raw_id)
            if not detail_url:
                detail_url = self._detail_url(feed_url, company_id, raw_id)
            location_text = self._pick(fields, "location", "locationmulti")
            if not location_text:
                location_text = ", ".join(
                    part for part in (
                        self._pick(fields, "city"),
                        self._pick(fields, "state"),
                        self._pick(fields, "country"),
                    ) if part
                )
            description_html = self._pick(fields, "description", "jobdescription", "externaldescription")
            date_text = self._pick(fields, "date", "postingdate", "dateposted", "referencedate")
            posted = parse_date(date_text, "SuccessFactors XML posting date") if date_text else unknown(self.source_id)
            results.append(
                DiscoveryJob(
                    adapter_id=self.source_id,
                    company=self._pick(fields, "company", "companyname") or company_name,
                    role=unescape(title).strip(),
                    location=unescape(location_text).strip(),
                    external_job_id=raw_id,
                    detail_url=detail_url,
                    application_url=detail_url,
                    posted=posted,
                    found_date=self._today(),
                    description_html=description_html,
                    description_text=html_to_text(description_html),
                    provenance=make_provenance(
                        source_id=self.source_id,
                        source_name=self.source_name,
                        source_kind=self.source_kind,
                        official=True,
                        extracted_from="official SuccessFactors RCM XML feed",
                        detail_url=detail_url,
                        raw_id=raw_id,
                    ),
                )
            )
        return results

    @staticmethod
    def _tag(tag: str) -> str:
        return tag.rsplit("}", 1)[-1].strip().lower()

    @classmethod
    def _flatten(cls, node: ET.Element) -> dict[str, list[str]]:
        fields: dict[str, list[str]] = {}
        for child in node.iter():
            if child is node:
                continue
            key = cls._tag(child.tag)
            text = " ".join(part.strip() for part in child.itertext() if part and part.strip()).strip()
            if text:
                fields.setdefault(key, []).append(text)
        return fields

    @staticmethod
    def _pick(fields: dict[str, list[str]], *names: str) -> str:
        for name in names:
            values = fields.get(name.lower()) or []
            for value in values:
                if str(value).strip():
                    return str(value).strip()
        return ""

    @staticmethod
    def _first_query(query: dict[str, list[str]], *names: str) -> str:
        for name in names:
            values = query.get(name) or []
            if values and values[0]:
                return str(values[0]).strip()
        return ""

    @staticmethod
    def _detail_url(feed_url: str, company_id: str, raw_id: str) -> str:
        parsed = urlsplit(feed_url)
        query = urlencode({
            "career_ns": "job_listing",
            "company": company_id,
            "navBarLevel": "JOB_SEARCH",
            "career_job_req_id": raw_id,
        })
        return urlunsplit((parsed.scheme, parsed.netloc, "/career", query, ""))

    @staticmethod
    def _today() -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")
