import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import career_dashboard_add_job as add_job


JOB_HTML = """
<html><head><script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "JobPosting",
  "title": "Senior Design Manager",
  "description": "<p>Lead multidisciplinary design delivery across complex projects.</p><p>Manage consultants, clients and design assurance through construction.</p>",
  "identifier": {"@type": "PropertyValue", "value": "REQ-123"},
  "hiringOrganization": {"@type": "Organization", "name": "Example Development"},
  "jobLocation": {"@type": "Place", "address": {"addressLocality": "Riyadh", "addressCountry": "Saudi Arabia"}}
}
</script></head><body></body></html>
"""


class CareerDashboardAddJobTests(unittest.TestCase):
    def test_extract_structured_jobposting(self):
        parsed = add_job.extract_structured_job(JOB_HTML, "https://example.com/jobs/123")
        self.assertEqual(parsed["role"], "Senior Design Manager")
        self.assertEqual(parsed["company"], "Example Development")
        self.assertEqual(parsed["external_job_id"], "REQ-123")
        self.assertIn("Riyadh", parsed["location"])
        self.assertIn("multidisciplinary design delivery", parsed["job_description"])

    def test_rejects_non_public_or_invalid_urls(self):
        with self.assertRaises(add_job.AddJobError):
            add_job._valid_public_url("not-a-url")
        with self.assertRaises(add_job.AddJobError):
            add_job._valid_public_url("http://localhost/job/1")

    def test_pasted_job_is_prepared_and_generated(self):
        prepared = {
            "job_id": "abcdef1234567890",
            "fit_score": {"total": 84},
            "blockers": [],
        }
        generated = {}
        refreshed = []

        def fake_generate(**kwargs):
            generated.update(kwargs)
            return "generated_and_rendered"

        with tempfile.TemporaryDirectory() as temp_dir, patch.object(add_job, "_run_prepare", return_value=prepared):
            root = Path(temp_dir)
            job_id, message = add_job.run_add_job(
                repo=root,
                dispatcher=root / "dispatcher.py",
                website_root=root / "dashboard",
                data={
                    "job_description": (
                        "Lead multidisciplinary design delivery across complex projects and manage consultant coordination, "
                        "technical reviews, client interfaces, programme requirements and construction-stage design issues."
                    ),
                    "company": "Example Development",
                    "role": "Senior Design Manager",
                    "location": "Riyadh, Saudi Arabia",
                },
                generate_package=fake_generate,
                refresh_dashboard=lambda repo, site: refreshed.append((repo, site)),
            )
        self.assertEqual(job_id, "abcdef1234567890")
        self.assertEqual(generated["job_id"], job_id)
        self.assertTrue(generated["force_regenerate"])
        self.assertTrue(refreshed)
        self.assertIn("84/100", message)
        self.assertIn("Nothing was sent or submitted", message)

    def test_blocked_job_is_kept_without_forced_generation(self):
        prepared = {
            "job_id": "abcdef1234567890",
            "fit_score": {"total": 42},
            "blockers": ["below_generation_threshold:42"],
        }
        generated = []
        refreshed = []
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(add_job, "_run_prepare", return_value=prepared):
            root = Path(temp_dir)
            job_id, message = add_job.run_add_job(
                repo=root,
                dispatcher=root / "dispatcher.py",
                website_root=root / "dashboard",
                data={
                    "job_description": "Fraud investigation specialist role requiring financial-crime investigations, AML controls and case management expertise.",
                    "company": "Example Company",
                    "role": "Fraud Investigator",
                },
                generate_package=lambda **kwargs: generated.append(kwargs),
                refresh_dashboard=lambda repo, site: refreshed.append((repo, site)),
            )
        self.assertTrue(job_id)
        self.assertFalse(generated)
        self.assertTrue(refreshed)
        self.assertIn("was not forced", message)
        self.assertIn("below_generation_threshold:42", message)

    def test_url_metadata_fills_missing_fields(self):
        structured = {
            "job_description": "Lead design management, consultant coordination, technical assurance and construction-stage design delivery across a major programme.",
            "company": "Example Development",
            "role": "Design Manager",
            "location": "Riyadh, Saudi Arabia",
            "external_job_id": "REQ-999",
        }
        captured = {}

        def fake_prepare(repo, args):
            captured["args"] = args
            return {"job_id": "12345678abcdef00", "fit_score": {"total": 80}, "blockers": []}

        with tempfile.TemporaryDirectory() as temp_dir, \
                patch.object(add_job, "_fetch_structured_job", return_value=structured), \
                patch.object(add_job, "_run_prepare", side_effect=fake_prepare):
            root = Path(temp_dir)
            job_id, _ = add_job.run_add_job(
                repo=root,
                dispatcher=root / "dispatcher.py",
                website_root=root / "dashboard",
                data={"job_url": "https://example.com/jobs/999"},
                generate_package=lambda **kwargs: "generated_and_rendered",
                refresh_dashboard=lambda repo, site: None,
            )
        self.assertEqual(job_id, "12345678abcdef00")
        self.assertIn("REQ-999", captured["args"])
        self.assertIn("https://example.com/jobs/999", captured["args"])


if __name__ == "__main__":
    unittest.main()
