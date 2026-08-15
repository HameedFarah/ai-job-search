"""Source-level regressions for Career Engine bulk/table dashboard controls."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "dashboard/career-review/site"


def test_dashboard_loads_bulk_table_assets() -> None:
    html = (SITE / "index.html").read_text(encoding="utf-8")
    assert 'assets/bulk-table.css' in html
    assert 'assets/bulk-table.js' in html
    assert html.index('assets/app.js') < html.index('assets/bulk-table.js')


def test_bulk_module_keeps_applied_confirmation_individual() -> None:
    source = (SITE / "assets/bulk-table.js").read_text(encoding="utf-8")
    assert "Applied / sent requires individual submission confirmation" in source
    assert "option.disabled = stage.id === 'applied'" in source


def test_stage_mutation_does_not_full_rerender_board() -> None:
    source = (SITE / "assets/bulk-table.js").read_text(encoding="utf-8")
    mutation = source.split("moveRole = async function moveRoleInBackground", 1)[1].split("function buildStageSelect", 1)[0]
    assert "renderBoard()" not in mutation
    assert "refreshRoleSurfaces(role)" in mutation
    assert "stageWriteQueues" in mutation


def test_table_and_card_multiselect_are_present() -> None:
    source = (SITE / "assets/bulk-table.js").read_text(encoding="utf-8")
    assert "role-bulk-checkbox" in source
    assert "table-role-checkbox" in source
    assert "Select visible" in source
    assert "Apply to selected" in source
    assert "data-career-view" in source


def test_detail_stage_uses_visible_select_and_hides_old_stage_menu() -> None:
    source = (SITE / "assets/bulk-table.js").read_text(encoding="utf-8")
    assert "detail-stage-inline" in source
    assert "detail-stage-select" in source
    assert "bulk-hidden-stage-menu" in source
