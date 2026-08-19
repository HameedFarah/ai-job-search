from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_dashboard_loads_irrelevant_feedback_extension_after_app_before_bulk() -> None:
    html = (ROOT / "dashboard/career-review/site/index.html").read_text(encoding="utf-8")
    app = html.index("assets/app.js")
    irrelevant = html.index("assets/irrelevant-feedback.js")
    bulk = html.index("assets/bulk-table.js")
    assert app < irrelevant < bulk


def test_irrelevant_extension_preserves_applied_evidence_and_records_owner_feedback() -> None:
    js = (ROOT / "dashboard/career-review/site/assets/irrelevant-feedback.js").read_text(encoding="utf-8")
    assert "roleHasActiveSubmissionEvidence(role)" in js
    assert "return 'applied'" in js
    assert "role_marked_irrelevant" in js
    assert "role_irrelevant_retracted" in js
    assert "explicit_owner_relevance_feedback" in js
    assert "DOMContentLoaded" in js
    assert "__ownerRelevanceWrapped" in js


def test_irrelevant_is_real_status_option() -> None:
    js = (ROOT / "dashboard/career-review/site/assets/irrelevant-feedback.js").read_text(encoding="utf-8")
    assert "id: 'irrelevant'" in js
    assert "label: 'Irrelevant'" in js
