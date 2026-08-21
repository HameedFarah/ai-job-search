import shutil
import subprocess
import unittest
from pathlib import Path


class DashboardCvGenerationUiContractTest(unittest.TestCase):
    def test_generate_selected_cv_uses_rebuild_backend_that_republishes(self):
        path = Path("dashboard/career-review/site/assets/overlay-layout.js")
        text = path.read_text(encoding="utf-8")
        self.assertIn("ownerQueuePackageGeneration = async function", text)
        self.assertIn("request_type: 'rebuild_documents'", text)
        self.assertIn("Republish the private dashboard", text)
        self.assertIn("['rebuild_documents', 'edit_cv']", text)
        self.assertNotIn("template_id: templateId", text)

    def test_mobile_layout_forces_resume_viewer_before_utilities(self):
        path = Path("dashboard/career-review/site/assets/overlay-layout.js")
        text = path.read_text(encoding="utf-8")
        self.assertIn("@media (max-width: 900px)", text)
        self.assertIn("flex-direction: column !important", text)
        self.assertIn(".resume-workspace", text)
        self.assertIn("order: 1 !important", text)
        self.assertIn(".detail-utility", text)
        self.assertIn("order: 2 !important", text)
        self.assertIn("#ov-resume-frame:not([hidden])", text)

    def test_overlay_layout_javascript_syntax(self):
        path = Path("dashboard/career-review/site/assets/overlay-layout.js")
        if shutil.which("node"):
            result = subprocess.run(["node", "--check", str(path)], text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
