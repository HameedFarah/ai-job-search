"""REGA Enrichment Pipeline — deterministic, source-controlled.

Identity authority (immutable):
  - License No
  - Arabic legal name (Arabic Name)
  - English name (English Name)
  - location (English Location(s))

No enrichment step may modify these fields.

Pipeline stages:
  discovery (Firecrawl/Hermes web backend) -> verification (fetch + identity checks)
  -> extraction (field-level) -> sidecar + evidence -> exports

Follows Career Engine governance: sidecar first, no SharePoint/master mutation
until acceptance. All requests carry immutable company_id, license_no, query_id.
"""

__version__ = "1.0.0"
PIPELINE_VERSION = "1.0.0"
# Freeze on 2026-08-22 for regression run
