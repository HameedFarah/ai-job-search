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

    def test_package_progress_uses_existing_request_polling_and_same_job_refresh(self):
        path = Path("dashboard/career-review/site/assets/overlay-layout.js")
        text = path.read_text(encoding="utf-8")
        self.assertIn("kind === 'package_progress'", text)
        self.assertIn("Estimating ETA…", text)
        self.assertIn("Generating ${escapeHtml(templateLabel(selected))}…", text)
        self.assertIn("package-progress-retry", text)
        self.assertIn("package-progress-track", text)
        self.assertIn("role', 'progressbar'", text)
        self.assertIn("ownerProgressTiming", text)
        self.assertIn("#ov-package-progress-empty", text)
        self.assertIn("url.searchParams.set('job', role.key)", text)
        self.assertIn("window.location.replace", text)
        self.assertIn("loadCollection('ai_requests', 300, true, true)", text)
        self.assertIn("const anyPending = aiRequestsForRole(role.key)", text)

    def test_generation_starts_polling_when_history_write_fails(self):
        path = Path("dashboard/career-review/site/assets/overlay-layout.js")
        text = path.read_text(encoding="utf-8")
        start = text.index("ownerQueuePackageGeneration = async function")
        end = text.index("const ownerBaseSetupAiPollingWithProgress", start)
        block = text[start:end]
        history_start = block.index("try {\n      await createRecord('history'")
        history_end = block.index("renderOverlayAi(role);", history_start)
        history_block = block[history_start:history_end]
        self.assertIn("catch (error)", history_block)
        self.assertIn("console.warn('Package generation history unavailable', error)", history_block)
        self.assertLess(history_end, block.index("setupAiPolling(role);", history_end))

    def test_ai_request_polling_distinguishes_read_errors_from_empty_results(self):
        shared = Path("dashboard/career-review/site/assets/shared.js").read_text(encoding="utf-8")
        self.assertIn("throwOnError = false", shared)
        self.assertIn("if (throwOnError) throw error", shared)
        for relative in (
            "add-job.js",
            "overlay-layout.js",
            "external-links.js",
            "bulk-table.js",
            "app.js",
            "detail.js",
        ):
            source = Path("dashboard/career-review/site/assets", relative).read_text(encoding="utf-8")
            if relative == "app.js":
                self.assertIn("loadCollection('ai_requests', 300, true, true)", source)
            else:
                self.assertIn("loadCollection('ai_requests', 300, true, true)", source)

    def test_overlay_layout_javascript_syntax(self):
        path = Path("dashboard/career-review/site/assets/overlay-layout.js")
        if shutil.which("node"):
            result = subprocess.run(["node", "--check", str(path)], text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
