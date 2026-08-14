"""Cache-first, retrying HTTP client. Every data source fetches through here.

The seam that matters is the *transport*: a plain callable

    transport(method, url, params, headers) -> Response

injected at the call site. Nothing in this module opens a socket, so the whole
test suite runs offline against scripted transports, and a source module can be
exercised against recorded responses without a network stub framework.

Order of operations is cache -> transport, never the reverse: on a warm cache
the transport is not called at all, which is what makes a re-run free. Only 2xx
bodies are cached — a 404 or a 500 must not poison the address for the eventual
successful fetch.

Retries cover the transient statuses (an upstream ArcGIS/Socrata hiccup) *and*
the transport failing to produce a response at all — a read timeout, a reset
connection, a DNS blip. A 4xx is the caller's mistake and fails immediately.
Backoff goes through `time.sleep` so it can be neutralised in tests.

A transport that raises is the same event as a 503 for everyone upstream, so it
is retried and then reported as `SourceError` rather than escaping as whatever
the HTTP library happened to throw. That is what lets a caller like the
pipeline's tile fetch treat one unreachable tile as a missing frame instead of
a failed run; a bare `requests.ConnectionError` would sail past its `except
SourceError` and abort ~1000 good fetches over one flaky one.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional

from houseaccount.cache import Cache

#: Statuses worth trying again: upstream congestion or a transient outage.
RETRY_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})

#: Total attempts, including the first. Bounded so a dead upstream fails a run
#: in seconds rather than spinning.
MAX_ATTEMPTS = 4

#: Backoff is BACKOFF_BASE_SECONDS * 2**(attempt - 1).
BACKOFF_BASE_SECONDS = 0.5

DEFAULT_TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class Response:
    """What a transport hands back: status, raw body, headers."""

    status: int
    body: bytes
    headers: dict[str, str] = field(default_factory=dict)


Transport = Callable[[str, str, Optional[Mapping[str, Any]], Mapping[str, str]], Response]


class SourceError(Exception):
    """An upstream source refused or failed. Carries the url and status so a
    partial run can report exactly which source degraded."""

    def __init__(self, url: str, status: int, message: str | None = None) -> None:
        self.url = url
        self.status = status
        super().__init__(message or f"{url} failed with HTTP {status}")


def requests_transport(
    method: str,
    url: str,
    params: Mapping[str, Any] | None,
    headers: Mapping[str, str] | None,
) -> Response:
    """The only code here that touches the network. Never exercised by tests.

    `requests` is imported lazily so importing this module stays cheap and the
    offline test suite never depends on it.
    """
    import requests

    response = requests.request(
        method,
        url,
        params=dict(params or {}),
        headers=dict(headers or {}),
        timeout=DEFAULT_TIMEOUT_SECONDS,
    )
    return Response(
        status=response.status_code,
        body=response.content,
        headers=dict(response.headers),
    )


def fetch_bytes(
    url: str,
    *,
    params: Mapping[str, Any] | None = None,
    cache: Cache | None = None,
    transport: Transport = requests_transport,
    headers: Mapping[str, str] | None = None,
    method: str = "GET",
    max_attempts: int = MAX_ATTEMPTS,
) -> bytes:
    """Return the raw response body, from cache when warm."""
    key = cache.key(url, params) if cache is not None else None
    if key is not None:
        cached = cache.get(key)
        if cached is not None:
            return cached if isinstance(cached, bytes) else json.dumps(cached).encode("utf-8")

    body = _request(
        method=method,
        url=url,
        params=params,
        headers=dict(headers or {}),
        transport=transport,
        max_attempts=max_attempts,
    )
    if key is not None:
        cache.put(key, body)
    return body


def fetch_json(
    url: str,
    *,
    params: Mapping[str, Any] | None = None,
    cache: Cache | None = None,
    transport: Transport = requests_transport,
    headers: Mapping[str, str] | None = None,
    method: str = "GET",
    max_attempts: int = MAX_ATTEMPTS,
) -> Any:
    """Return the decoded JSON payload, from cache when warm.

    The cache stores the raw body, so a warm read decodes the exact bytes the
    upstream sent — a cached run and a live run cannot diverge.
    """
    body = fetch_bytes(
        url,
        params=params,
        cache=cache,
        transport=transport,
        headers=headers,
        method=method,
        max_attempts=max_attempts,
    )
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        # A 200 whose body is not JSON is an upstream lying about success — the
        # Census API answers an unactivated key with an HTML "Invalid Key" page
        # under a 200, and `fetch_bytes` has already cached it by now. Left
        # there it outlives the cause: activating the key changes nothing,
        # because the next run is served the error page off disk and never
        # calls anyone. So the entry is evicted on the way out, and the failure
        # is reported as the source failure it is.
        if cache is not None:
            cache.drop(cache.key(url, params))
        raise SourceError(url, 0, f"{url} returned a 200 that is not JSON: {exc}") from exc


def _request(
    *,
    method: str,
    url: str,
    params: Mapping[str, Any] | None,
    headers: Mapping[str, str],
    transport: Transport,
    max_attempts: int,
) -> bytes:
    """Call the transport until it succeeds or the attempt budget runs out."""
    for attempt in range(1, max(1, max_attempts) + 1):
        exhausted = attempt >= max_attempts
        try:
            response = transport(method, url, params, headers)
        except OSError as exc:
            # Every `requests` network failure subclasses OSError, so this
            # catches timeouts and resets without importing requests here and
            # without swallowing a test transport's AssertionError.
            if exhausted:
                raise SourceError(url, 0, f"{url} could not be reached: {exc}") from exc
            time.sleep(BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)))
            continue
        if 200 <= response.status < 300:
            return response.body
        if response.status not in RETRY_STATUSES or exhausted:
            raise SourceError(url, response.status)
        time.sleep(BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)))
    raise SourceError(url, 0, f"{url} produced no response")
