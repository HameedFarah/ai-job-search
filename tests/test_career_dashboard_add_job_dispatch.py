import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import career_dashboard_add_job as add_job
from tools import career_dashboard_assistant as assistant


class CareerDashboardAddJobDispatchTests(unittest.TestCase):
    def test_add_job_request_dispatches_to_intake_worker(self):
        captured = {}

        def fake_run_add_job(**kwargs):
            captured.update(kwargs)
            return "abcdef1234567890", "Added and packaged."

        with tempfile.TemporaryDirectory() as temp_dir, patch.object(add_job, "run_add_job", side_effect=fake_run_add_job):
            root = Path(temp_dir)
            role_key, answer, metadata = assistant.answer_request(
                repo=root,
                dispatcher=root / "dispatcher.py",
                website_root=root / "dashboard",
                record={
                    "data": {
                        "role_key": assistant.ADD_JOB_ROLE_KEY,
                        "request_type": "add_job",
                        "job_description": "A sufficiently detailed owner supplied vacancy description.",
                        "company": "Example",
                        "role": "Design Manager",
                    }
                },
                progress_callback=lambda progress: None,
            )

        self.assertEqual(role_key, "tracker-abcdef1234567890")
        self.assertEqual(answer, "Added and packaged.")
        self.assertEqual(metadata, {"validation_status": "success", "owner_input_needed": False})
        self.assertEqual(captured["repo"], root)
        self.assertIs(captured["generate_package"], assistant._generate_application_package)
        self.assertIs(captured["refresh_dashboard"], assistant._refresh_dashboard_site)


if __name__ == "__main__":
    unittest.main()
