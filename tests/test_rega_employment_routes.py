"""Deterministic tests for first-party REGA employment-route discovery."""
from __future__ import annotations

from career_engine.rega_enrichment.employment_routes import (
    _extract_emails,
    _is_ats_url,
    _looks_like_soft_404,
    discover_employment_route,
)


class FakeResponse:
    def __init__(self, url: str, text: str, status_code: int = 200):
        self.url = url
        self.text = text
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeClient:
    def __init__(self, pages: dict[str, FakeResponse]):
        self.pages = pages
        self.calls: list[str] = []

    def get(self, url: str) -> FakeResponse:
        self.calls.append(url)
        return self.pages.get(url, FakeResponse(url, "<html><title>404 Page Not Found</title>page not found</html>", 404))


def test_official_homepage_linked_ats_wins_without_guessing_paths():
    root = "https://example.com/"
    client = FakeClient({
        root: FakeResponse(root, '<html><body><a href="https://example.wd5.myworkdayjobs.com/jobs">Careers</a></body></html>'),
    })
    route = discover_employment_route(root, client=client)
    assert route is not None
    assert route.kind == "ats"
    assert "myworkdayjobs.com" in route.value
    assert client.calls == [root]


def test_soft_404_careers_path_is_not_promoted():
    root = "https://example.com/"
    client = FakeClient({
        root: FakeResponse(root, '<html><body>Contact <a href="mailto:info@example.com">info@example.com</a></body></html>'),
        "https://example.com/careers": FakeResponse("https://example.com/careers", "<html><title>404 Page Not Found</title><body>page not found careers jobs</body></html>"),
    })
    route = discover_employment_route(root, client=client)
    assert route is not None
    assert route.kind == "email"
    assert route.email_kind == "general"
    assert route.value == "info@example.com"


def test_first_party_application_page_is_a_successful_portal_route():
    root = "https://example.com/"
    careers = "https://example.com/careers"
    client = FakeClient({
        root: FakeResponse(root, '<html><body><a href="/careers">Careers</a></body></html>'),
        careers: FakeResponse(careers, "<html><title>Careers</title><body>Explore jobs and vacancies. Apply now.<form></form></body></html>"),
    })
    route = discover_employment_route(root, client=client)
    assert route is not None
    assert route.kind == "careers_page"
    assert route.value == careers


def test_recruitment_mailbox_is_candidate_not_deliverability_claim():
    root = "https://example.com/"
    careers = "https://example.com/careers"
    client = FakeClient({
        root: FakeResponse(root, '<html><body><a href="/careers">Careers</a></body></html>'),
        careers: FakeResponse(careers, '<html><title>Careers</title><body>Join our team. Contact <a href="mailto:careers@example.com">our recruitment team</a>.</body></html>'),
    })
    route = discover_employment_route(root, client=client)
    assert route is not None
    assert route.kind == "email"
    assert route.email_kind == "recruitment"
    assert route.value == "careers@example.com"


def test_email_extraction_rejects_third_party_domain():
    emails = _extract_emails("hr@example.com info@other.com", [], "example.com")
    assert emails == [("hr@example.com", "recruitment")]


def test_known_ats_and_soft_404_helpers():
    assert _is_ats_url("https://tenant.successfactors.com/careers")
    assert _looks_like_soft_404("404 - Page Not Found", "careers jobs", 200)
