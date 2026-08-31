import json
import unittest
from urllib.parse import parse_qs, urlsplit

from career_engine.rega_enrichment.outscraper_validation import validate_emails
from career_engine.rega_enrichment.provider_clients import OutscraperClient, ProviderBudget


class FakeResponse:
    def __init__(self, payload, status=200):
        self.status = status
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def opener_with(payload, captured=None, status=200):
    def _open(request, timeout=15):
        if captured is not None:
            captured.append(request)
        return FakeResponse(payload, status=status)

    return _open


class OutscraperValidationTests(unittest.TestCase):
    def test_maps_only_receiving_to_safe_send(self):
        payload = {
            "status": "Success",
            "data": [
                {"query": "good@example.com", "status": "RECEIVING", "status_details": "SMTP validated"},
                {"query": "bad@example.com", "status": "INVALID", "status_details": "Invalid SMTP"},
                {"query": "blocked@example.com", "status": "BLACKLISTED", "status_details": "Blacklisted"},
                {"query": "maybe@example.com", "status": "UNKNOWN", "status_details": "Cannot validate"},
            ],
        }
        captured = []
        client = OutscraperClient("secret-key", opener=opener_with(payload, captured))
        result = validate_emails(
            client,
            ["GOOD@example.com", "bad@example.com", "blocked@example.com", "maybe@example.com"],
            ProviderBudget(allow_existing_credit=True, max_calls=1, max_credits=4),
        )

        self.assertEqual(
            [item["status"] for item in result],
            ["RECEIVING", "INVALID", "BLACKLISTED", "UNKNOWN"],
        )
        self.assertEqual(
            [item["metadata"]["safe_to_send"] for item in result],
            [True, False, False, False],
        )
        self.assertEqual(result[0]["metadata"]["status_details"], "SMTP validated")
        self.assertEqual(captured[0].get_header("X-api-key"), "secret-key")
        self.assertNotIn("secret-key", json.dumps(result))
        self.assertEqual(captured[0].full_url.count("query="), 4)
        self.assertIn("async=false", captured[0].full_url)

    def test_dedupes_and_preserves_input_order(self):
        payload = {
            "status": "Success",
            "data": [
                {"query": "a@example.com", "status": "RECEIVING"},
                {"query": "b@example.com", "status": "INVALID"},
            ],
        }
        client = OutscraperClient("key", opener=opener_with(payload))
        result = validate_emails(
            client,
            ["A@example.com", "a@example.com", " b@example.com ", ""],
            ProviderBudget(allow_existing_credit=True, max_calls=1, max_credits=2),
        )
        self.assertEqual(
            [item["metadata"]["email"] for item in result],
            ["a@example.com", "b@example.com"],
        )

    def test_missing_result_becomes_unknown(self):
        client = OutscraperClient(
            "key",
            opener=opener_with({"status": "Success", "data": []}),
        )
        result = validate_emails(
            client,
            ["missing@example.com"],
            ProviderBudget(allow_existing_credit=True, max_calls=1, max_credits=1),
        )
        self.assertEqual(result[0]["status"], "UNKNOWN")
        self.assertFalse(result[0]["metadata"]["safe_to_send"])
        self.assertEqual(result[0]["metadata"]["status_details"], "missing_result")

    def test_requires_existing_credit_permission(self):
        called = []
        client = OutscraperClient("key", opener=opener_with({}, called))
        result = validate_emails(
            client,
            ["x@example.com"],
            ProviderBudget(max_calls=1, max_credits=1),
        )
        self.assertEqual(result[0]["status"], "budget_exhausted")
        self.assertFalse(result[0]["metadata"]["safe_to_send"])
        self.assertEqual(called, [])

    def test_missing_key_never_calls_provider(self):
        called = []
        client = OutscraperClient("", opener=opener_with({}, called))
        result = validate_emails(
            client,
            ["x@example.com"],
            ProviderBudget(allow_existing_credit=True, max_calls=1, max_credits=1),
        )
        self.assertEqual(result[0]["status"], "missing_credential")
        self.assertFalse(result[0]["metadata"]["safe_to_send"])
        self.assertEqual(called, [])

    def test_splits_batches_at_1000(self):
        emails = [f"person{i}@example.com" for i in range(1001)]
        calls = []

        def _open(request, timeout=15):
            calls.append(request)
            queries = parse_qs(urlsplit(request.full_url).query).get("query", [])
            payload = {
                "status": "Success",
                "data": [{"query": email, "status": "RECEIVING"} for email in queries],
            }
            return FakeResponse(payload)

        client = OutscraperClient("key", opener=_open)
        result = validate_emails(
            client,
            emails,
            ProviderBudget(allow_existing_credit=True, max_calls=2, max_credits=1001),
        )
        self.assertEqual(len(result), 1001)
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(item["status"] == "RECEIVING" for item in result))


if __name__ == "__main__":
    unittest.main()
