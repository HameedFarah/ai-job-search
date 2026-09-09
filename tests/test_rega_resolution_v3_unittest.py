"""unittest-discover acceptance coverage for REGA resolution v3."""
from __future__ import annotations

import unittest

from career_engine.rega_enrichment.models import CompanyRecord
from career_engine.rega_enrichment.resolution import (
    MuqawilProfile,
    career_value_score,
    is_terminal_resolution,
    parse_muqawil_profile,
    priority_band,
)
from runtime import rega_resolution_v3 as resolver


class RegaResolutionV3Tests(unittest.TestCase):
    def test_priority_a_for_current_riyadh_operating_project(self):
        row = {
            "Master_ID": "CE-A",
            "Company_or_Office": "Example Development",
            "Arabic_Name": "شركة المثال للتطوير العقاري",
            "Region": "Riyadh",
            "Address_or_Website": "https://example.sa/",
            "Source_Verification": "Verified - official first-party company domain",
            "Source_Status": "Active developer; official domain verified",
            "Notes": "Current project is under construction in Riyadh.",
        }
        score = career_value_score(row)
        self.assertGreaterEqual(score, 55)
        self.assertEqual(priority_band(score), "A")

    def test_muqawil_profile_increases_priority_without_exposing_email(self):
        row = {
            "Region": "Eastern Prov.",
            "Company_or_Office": "Example Contractor",
            "Arabic_Name": "شركة المثال للمقاولات",
            "Source_Verification": "Not researched",
            "Source_Status": "No verified official domain",
            "Notes": "",
        }
        profile = MuqawilProfile(
            name="Example Contractor",
            url="https://muqawil.org/en/contractors/100/200",
            company_size="Large",
            membership_number="123456",
            status="Account Verified",
        )
        self.assertGreater(career_value_score(row, muqawil=profile), career_value_score(row))
        self.assertFalse(hasattr(profile, "email"))

    def test_muqawil_parser_requires_identity_match(self):
        company = CompanyRecord(
            company_id="CE-MUQ",
            license_no="1234",
            english_name="Example Contracting Company",
            arabic_name="شركة المثال للمقاولات",
            location="Riyadh",
        )
        good = """
        <html><body><h1>Example Contracting Company</h1>
        <div>Membership Number | 7654321</div>
        <div>Status | Account Verified</div>
        <div>Company Size Based on Number of Employees | Medium Company Size</div>
        </body></html>
        """
        bad = "<html><body><h1>Completely Different Services Company</h1><div>Company Size | Large</div></body></html>"
        parsed = parse_muqawil_profile(company, "https://muqawil.org/en/contractors/100/200", good)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.company_size, "Medium")
        self.assertEqual(parsed.membership_number, "7654321")
        self.assertIsNone(parse_muqawil_profile(company, "https://muqawil.org/en/contractors/100/200", bad))

    def test_v3_terminal_states_are_idempotent(self):
        statuses = [
            "Verified receiving email route",
            "Verified careers/ATS application route",
            "Resolved - verified company; no usable employment route",
            "Resolved - official mailbox not receiving (INVALID)",
            "Resolved - official mailbox found; validation unavailable",
            "Resolved - company already successfully contacted",
            "Resolved - company already covered by active/sent outreach",
            "Identity unconfirmed after automated REGA resolution",
        ]
        for status in statuses:
            with self.subTest(status=status):
                self.assertTrue(is_terminal_resolution({"Source_Status": status}))
        self.assertFalse(is_terminal_resolution({"Source_Status": "No verified official domain"}))

    def test_no_client_means_no_paid_provider_call(self):
        self.assertFalse(resolver._paid_available(None, 1.0, 0.003))

    def test_selected_skips_terminal_and_keeps_unresolved(self):
        master = [
            {
                "Master_ID": "CE-1",
                "Record_Type": "REGA_COMPANY_NO_EMAIL",
                "Company_or_Office": "Current Co",
                "Region": "Riyadh",
                "Source_Status": "No verified official domain",
                "Source_Verification": "Not researched",
            },
            {
                "Master_ID": "CE-2",
                "Record_Type": "REGA_COMPANY_NO_EMAIL",
                "Company_or_Office": "Covered Co",
                "Region": "Riyadh",
                "Source_Status": "Resolved - company already covered by active/sent outreach",
            },
        ]
        selected = resolver._selected(master, reopen_unconfirmed=False)
        self.assertEqual([row["Master_ID"] for row in selected], ["CE-1"])


if __name__ == "__main__":
    unittest.main()
