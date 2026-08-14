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


# --- a transport that raises instead of answering ---------------------------
#
# A read timeout mid-run is the ordinary case for ~1000 NJ ortho tile fetches,
# not an exotic one. It has to arrive at the caller as `SourceError`, because
# that is the only thing `pipeline._fetch_tiles` catches to record a missing
# frame; anything else aborts a run that was otherwise complete.


class RaisingTransport:
    """Raises `exc` for the first `failures` calls, then answers normally."""

    def __init__(self, exc, failures=1, then=None):
        self.exc = exc
        self.failures = failures
        self.then = then if then is not None else ok()
        self.calls = []

    def __call__(self, method, url, params, headers):
        self.calls.append(url)
        if len(self.calls) <= self.failures:
            raise self.exc
        return self.then


def timed_out():
    """What `requests` raises on a read timeout, without importing requests."""
    return OSError("HTTPSConnectionPool(host='maps.nj.gov', port=443): Read timed out.")


def test_transport_exception_is_retried_then_succeeds(cache):
    transport = RaisingTransport(timed_out(), failures=1)
    assert fetch_json(URL, params=PARAMS, cache=cache, transport=transport) == PAYLOAD
    assert len(transport.calls) == 2


def test_persistent_transport_exception_becomes_a_source_error(cache):
    transport = RaisingTransport(timed_out(), failures=99)
    with pytest.raises(SourceError) as excinfo:
        fetch_bytes(URL, params=PARAMS, cache=cache, transport=transport)
    assert excinfo.value.url == URL
    assert excinfo.value.status == 0
    assert isinstance(excinfo.value.__cause__, OSError)


def test_transport_exception_retries_are_bounded(cache):
    transport = RaisingTransport(timed_out(), failures=99)
    with pytest.raises(SourceError):
        fetch_json(URL, params=PARAMS, cache=cache, transport=transport)
    assert 1 < len(transport.calls) <= 10


def test_a_raised_response_is_not_cached(cache):
    """The failure must not poison the address for the retry that works."""
    transport = RaisingTransport(timed_out(), failures=1)
    fetch_json(URL, params=PARAMS, cache=cache, transport=transport)
    warm = ScriptedTransport(ok([{"different": "payload"}]))
    assert fetch_json(URL, params=PARAMS, cache=cache, transport=warm) == PAYLOAD
    assert warm.calls == []


# --- a 200 that is not JSON -------------------------------------------------
#
# The Census API answers an unactivated key with an HTML "Invalid Key" page
# under a 200 status. Cached, that page outlives its own cause: the key gets
# activated and every later run is still served the error off disk.


HTML_ERROR = b"<html><head><title>Invalid Key</title></head><body>go away</body></html>"


def html_200():
    return Response(status=200, body=HTML_ERROR, headers={"content-type": "text/html"})


def test_a_200_that_is_not_json_raises_source_error(cache):
    with pytest.raises(SourceError) as excinfo:
        fetch_json(URL, params=PARAMS, cache=cache, transport=ScriptedTransport(html_200()))
    assert excinfo.value.url == URL


def test_a_200_that_is_not_json_is_evicted_so_the_next_run_refetches(cache):
    """The whole point: fixing the upstream cause must be enough."""
    with pytest.raises(SourceError):
        fetch_json(URL, params=PARAMS, cache=cache, transport=ScriptedTransport(html_200()))
    # The key now works, so the very next call must reach the network again.
    recovered = ScriptedTransport(ok())
    assert fetch_json(URL, params=PARAMS, cache=cache, transport=recovered) == PAYLOAD
    assert len(recovered.calls) == 1


def test_valid_json_is_still_cached(cache):
    """Eviction must not cost the warm-cache guarantee."""
    fetch_json(URL, params=PARAMS, cache=cache, transport=ScriptedTransport(ok()))
    assert fetch_json(URL, params=PARAMS, cache=cache, transport=ExplodingTransport()) == PAYLOAD


def test_fetch_bytes_still_accepts_a_non_json_body(cache):
    """Only `fetch_json` demands JSON — tile bytes are not JSON and never were."""
    transport = ScriptedTransport(Response(status=200, body=IMAGE, headers={}))
    assert fetch_bytes(URL, cache=cache, transport=transport) == IMAGE


def test_a_non_oserror_from_the_transport_still_escapes(cache):
    """Only network faults are retried. A bug in a transport must surface."""
    with pytest.raises(AssertionError):
        fetch_json(URL, params=PARAMS, cache=cache, transport=ExplodingTransport())
