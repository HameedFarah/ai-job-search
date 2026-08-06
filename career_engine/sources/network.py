"""Bounded HTTP requests for discovery adapters (stdlib only).

All network access goes through this module so that every request has:

- an explicit timeout (default 12s, never infinite);
- a size cap on the response body (default 2 MiB);
- a neutral browser-like User-Agent with a clear self-identification string;
- a single exception surface (``SourceError``) the adapters translate;
- explicit request methods and bounded JSON request bodies where an approved API requires POST.

The module never follows blind redirects to login walls and never stores
cookies or session state. A 404 is surfaced as ``SourceNotFound`` so adapters
can distinguish "this board identifier does not exist" from other failures.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from .base import SourceError

DEFAULT_TIMEOUT = 12
DEFAULT_MAX_BYTES = 2 * 1024 * 1024
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126 Safari/537.36 "
    "CareerEngineDiscovery/1.0 (+discovery-only; no-send)"
)


class SourceNotFound(SourceError):
    """The requested board identifier, posting or page does not exist."""


class HttpBlocked(SourceError):
    """The source returned a challenge or explicitly blocked automated access."""


def request(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
    json_body: Any = None,
    timeout: int = DEFAULT_TIMEOUT,
    max_bytes: int = DEFAULT_MAX_BYTES,
    user_agent: str = USER_AGENT,
) -> bytes:
    """Make one bounded stateless HTTP request and return raw bytes."""
    if body is not None and json_body is not None:
        raise ValueError("body and json_body are mutually exclusive")
    request_headers = {"User-Agent": user_agent, **(headers or {})}
    data = body
    if json_body is not None:
        data = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(
        url,
        data=data,
        headers=request_headers,
        method=method.upper(),
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            payload = response.read(max_bytes + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise SourceNotFound(f"Not found (HTTP 404): {url}") from exc
        if exc.code in (403, 429):
            raise HttpBlocked(f"Blocked or rate-limited (HTTP {exc.code}): {url}") from exc
        raise SourceError(f"HTTP {exc.code} fetching {url}") from exc
    except urllib.error.URLError as exc:
        raise SourceError(f"Network error fetching {url}: {exc.reason}") from exc
    except TimeoutError as exc:
        raise SourceError(f"Timeout after {timeout}s fetching {url}") from exc
    if len(payload) > max_bytes:
        raise SourceError(f"Response too large (> {max_bytes} bytes) for {url}")
    return payload


def fetch(
    url: str,
    *,
    timeout: int = DEFAULT_TIMEOUT,
    max_bytes: int = DEFAULT_MAX_BYTES,
    user_agent: str = USER_AGENT,
) -> bytes:
    return request(
        url,
        timeout=timeout,
        max_bytes=max_bytes,
        user_agent=user_agent,
    )


def fetch_text(
    url: str,
    *,
    timeout: int = DEFAULT_TIMEOUT,
    max_bytes: int = DEFAULT_MAX_BYTES,
    user_agent: str = USER_AGENT,
) -> str:
    return fetch(url, timeout=timeout, max_bytes=max_bytes, user_agent=user_agent).decode(
        "utf-8", errors="replace"
    )


def fetch_json(
    url: str,
    *,
    timeout: int = DEFAULT_TIMEOUT,
    max_bytes: int = DEFAULT_MAX_BYTES,
    user_agent: str = USER_AGENT,
) -> Any:
    return request_json(
        url,
        timeout=timeout,
        max_bytes=max_bytes,
        user_agent=user_agent,
    )


def request_json(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
    json_body: Any = None,
    timeout: int = DEFAULT_TIMEOUT,
    max_bytes: int = DEFAULT_MAX_BYTES,
    user_agent: str = USER_AGENT,
) -> Any:
    payload = request(
        url,
        method=method,
        headers=headers,
        body=body,
        json_body=json_body,
        timeout=timeout,
        max_bytes=max_bytes,
        user_agent=user_agent,
    )
    try:
        return json.loads(payload.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        raise SourceError(f"Invalid JSON from {url}: {exc}") from exc
