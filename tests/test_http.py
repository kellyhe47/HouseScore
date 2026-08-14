"""Retrying HTTP client, cache-first (T001).

The seam: `fetch_json` / `fetch_bytes` never touch a socket themselves. They call
an injected

    transport(method, url, params, headers) -> Response(status, body, headers)

so the whole suite runs offline. The cache sits *in front* of the transport: on a
warm cache the transport is not called at all.
"""

import json
import time

import pytest

from houseaccount.cache import Cache
from houseaccount.http import Response, SourceError, fetch_bytes, fetch_json

URL = "https://data.nj.gov/resource/w9se-dmra.json"
PARAMS = {"comu": "0248"}
PAYLOAD = [{"block": "1101", "lot": "3", "permittypedesc": "ROOFING"}]
IMAGE = b"\x89PNG\r\n\x1a\n ortho tile bytes"


@pytest.fixture(autouse=True)
def no_real_sleeping(monkeypatch):
    """Backoff must not make the suite slow."""
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)


@pytest.fixture
def cache(tmp_path):
    return Cache(tmp_path / "cache")


def ok(payload=PAYLOAD):
    return Response(status=200, body=json.dumps(payload).encode(), headers={})


def error(status):
    return Response(status=status, body=b"upstream said no", headers={})


class ScriptedTransport:
    """Returns each queued response in turn; records every call it received."""

    def __init__(self, *responses):
        self.queued = list(responses)
        self.calls = []

    def __call__(self, method, url, params, headers):
        self.calls.append({"method": method, "url": url, "params": params, "headers": headers})
        return self.queued.pop(0) if len(self.queued) > 1 else self.queued[0]


class ExplodingTransport:
    """Any call at all is a failure of the cache-first contract."""

    def __call__(self, method, url, params, headers):
        raise AssertionError(f"transport was called for {url} — cache was not consulted")


# --- happy path -------------------------------------------------------------


def test_fetch_json_returns_the_decoded_payload(cache):
    transport = ScriptedTransport(ok())
    assert fetch_json(URL, params=PARAMS, cache=cache, transport=transport) == PAYLOAD


def test_fetch_bytes_returns_the_raw_body(cache):
    transport = ScriptedTransport(Response(status=200, body=IMAGE, headers={}))
    assert fetch_bytes(URL, params=PARAMS, cache=cache, transport=transport) == IMAGE


def test_transport_receives_the_requested_url_and_params(cache):
    transport = ScriptedTransport(ok())
    fetch_json(URL, params=PARAMS, cache=cache, transport=transport)
    assert len(transport.calls) == 1
    call = transport.calls[0]
    assert call["url"] == URL
    assert dict(call["params"] or {}) == PARAMS


# --- cache-first ------------------------------------------------------------


def test_warm_cache_performs_zero_transport_calls(cache):
    cold = ScriptedTransport(ok())
    first = fetch_json(URL, params=PARAMS, cache=cache, transport=cold)
    assert len(cold.calls) == 1

    second = fetch_json(URL, params=PARAMS, cache=cache, transport=ExplodingTransport())
    assert second == first


def test_warm_cache_survives_a_fresh_cache_instance(tmp_path):
    root = tmp_path / "cache"
    fetch_json(URL, params=PARAMS, cache=Cache(root), transport=ScriptedTransport(ok()))
    assert (
        fetch_json(URL, params=PARAMS, cache=Cache(root), transport=ExplodingTransport()) == PAYLOAD
    )


def test_fetch_bytes_warm_cache_performs_zero_transport_calls(cache):
    transport = ScriptedTransport(Response(status=200, body=IMAGE, headers={}))
    fetch_bytes(URL, params=PARAMS, cache=cache, transport=transport)
    assert fetch_bytes(URL, params=PARAMS, cache=cache, transport=ExplodingTransport()) == IMAGE


def test_different_params_are_a_cache_miss(cache):
    transport = ScriptedTransport(ok())
    fetch_json(URL, params=PARAMS, cache=cache, transport=transport)
    fetch_json(URL, params={"comu": "0299"}, cache=cache, transport=transport)
    assert len(transport.calls) == 2


def test_failed_responses_are_not_cached(cache):
    """A 404 must not poison the cache for the eventual successful fetch."""
    with pytest.raises(SourceError):
        fetch_json(URL, params=PARAMS, cache=cache, transport=ScriptedTransport(error(404)))
    good = ScriptedTransport(ok())
    assert fetch_json(URL, params=PARAMS, cache=cache, transport=good) == PAYLOAD


# --- retries ----------------------------------------------------------------


@pytest.mark.parametrize("transient_status", [500, 502, 503])
def test_transient_failure_then_success(cache, transient_status):
    transport = ScriptedTransport(error(transient_status), ok())
    assert fetch_json(URL, params=PARAMS, cache=cache, transport=transport) == PAYLOAD
    assert len(transport.calls) == 2


def test_retries_are_bounded(cache):
    """Permanent 500 must give up, not spin forever."""
    transport = ScriptedTransport(error(500))
    with pytest.raises(SourceError):
        fetch_json(URL, params=PARAMS, cache=cache, transport=transport)
    assert 1 < len(transport.calls) <= 10


def test_retry_exhaustion_reports_the_url_and_status(cache):
    with pytest.raises(SourceError) as excinfo:
        fetch_json(URL, params=PARAMS, cache=cache, transport=ScriptedTransport(error(500)))
    assert excinfo.value.url == URL
    assert excinfo.value.status == 500


@pytest.mark.parametrize("status", [400, 403, 404])
def test_client_error_raises_source_error_with_url_and_status(cache, status):
    with pytest.raises(SourceError) as excinfo:
        fetch_json(URL, params=PARAMS, cache=cache, transport=ScriptedTransport(error(status)))
    assert excinfo.value.url == URL
    assert excinfo.value.status == status


def test_fetch_bytes_raises_source_error_too(cache):
    with pytest.raises(SourceError) as excinfo:
        fetch_bytes(URL, params=PARAMS, cache=cache, transport=ScriptedTransport(error(404)))
    assert excinfo.value.status == 404


def test_source_error_is_an_exception():
    assert issubclass(SourceError, Exception)
