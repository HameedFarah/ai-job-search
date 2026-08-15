"""Source-level regressions for Career Engine bulk/table dashboard controls."""

import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "dashboard/career-review/site"


class DashboardBulkTableSourceTests(unittest.TestCase):
    def test_dashboard_loads_bulk_table_assets(self) -> None:
        html = (SITE / "index.html").read_text(encoding="utf-8")
        self.assertIn('assets/bulk-table.css', html)
        self.assertIn('assets/bulk-table.js', html)
        self.assertLess(html.index('assets/app.js'), html.index('assets/bulk-table.js'))

    def test_bulk_module_javascript_syntax(self) -> None:
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is unavailable on this test runner")
        result = subprocess.run(
            [node, "--check", str(SITE / "assets/bulk-table.js")],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_bulk_module_keeps_applied_confirmation_individual(self) -> None:
        source = (SITE / "assets/bulk-table.js").read_text(encoding="utf-8")
        self.assertIn("Applied / sent requires individual submission confirmation", source)
        self.assertIn("option.disabled = stage.id === 'applied'", source)

    def test_stage_mutation_does_not_full_rerender_board(self) -> None:
        source = (SITE / "assets/bulk-table.js").read_text(encoding="utf-8")
        mutation = source.split("moveRole = async function moveRoleInBackground", 1)[1].split("function buildStageSelect", 1)[0]
        self.assertNotIn("renderBoard()", mutation)
        self.assertIn("refreshRoleSurfaces(role)", mutation)
        self.assertIn("stageWriteQueues", mutation)

    def test_table_and_card_multiselect_are_present(self) -> None:
        source = (SITE / "assets/bulk-table.js").read_text(encoding="utf-8")
        self.assertIn("role-bulk-checkbox", source)
        self.assertIn("table-role-checkbox", source)
        self.assertIn("Select visible", source)
        self.assertIn("Apply to selected", source)
        self.assertIn("data-career-view", source)

    def test_detail_stage_uses_visible_select_and_hides_old_stage_menu(self) -> None:
        source = (SITE / "assets/bulk-table.js").read_text(encoding="utf-8")
        self.assertIn("detail-stage-inline", source)
        self.assertIn("detail-stage-select", source)
        self.assertIn("bulk-hidden-stage-menu", source)


if __name__ == "__main__":
    unittest.main()
