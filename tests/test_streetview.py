"""The Street View pass-through: context for the panel, never evidence (T-SV).

The contract under test:

* the metadata endpoint answers one of three shapes — `available` (with the
  capture date and a Maps link), `unavailable` (no imagery, or no key), or a
  502 with the standard `error`/`message` body when Google fails us;
* the image endpoint streams the bytes through with `Cache-Control: no-store`
  and never exposes the key to the browser;
* nothing is written anywhere — the transport is a scripted callable, and no
  test here can touch the network or the cache.

The territory is a stub: these endpoints only ever ask it for a door's situs
and centroid, so the whole published-run machinery would be setup noise.
"""

import json
from dataclasses import dataclass

from fastapi import FastAPI
from fastapi.testclient import TestClient

from houseaccount.server.streetview import IMAGE_URL, METADATA_URL, build_streetview_router

PIN = "0248_1534_13"
KEY = "test-google-key"


@dataclass
class StubDoor:
    situs: str
    centroid: tuple[float, float] | None


class StubTerritory:
    def __init__(self, door):
        self._door = door

    def door(self, pin):
        return self._door if pin == PIN else None


DOOR = StubDoor(situs="227 LAKESIDE DR, Ramsey NJ 07446", centroid=(-74.156, 41.0447))


def client(*, key=KEY, fetch=None, door=DOOR):
    app = FastAPI()
    app.routes.extend(
        build_streetview_router(StubTerritory(door), key, fetch=fetch or _fail).routes
    )
    return TestClient(app)


def _fail(url, params):  # a transport no test should reach
    raise AssertionError(f"unexpected fetch of {url}")


def metadata_transport(status="OK", date="2019-07"):
    """A scripted Google metadata answer, recording what was asked."""
    calls = []

    def fetch(url, params):
        calls.append((url, dict(params)))
        body = {"status": status}
        if date is not None:
            body["date"] = date
        return 200, json.dumps(body).encode(), "application/json"

    fetch.calls = calls
    return fetch


# --- GET /api/streetview/{pin} ------------------------------------------------


def test_available_imagery_answers_with_date_image_url_and_maps_link():
    fetch = metadata_transport(status="OK", date="2019-07")
    body = client(fetch=fetch).get(f"/api/streetview/{PIN}").json()
    assert body["status"] == "available"
    assert body["capture_date"] == "2019-07"
    # Relative to the API base the map already holds — no `/api` prefix here.
    assert body["image_url"] == f"/streetview/{PIN}/image"
    assert "google.com/maps" in body["maps_url"]


def test_the_metadata_call_asks_google_for_the_situs_address_outdoors():
    fetch = metadata_transport()
    client(fetch=fetch).get(f"/api/streetview/{PIN}")
    url, params = fetch.calls[0]
    assert url == METADATA_URL
    assert params["location"] == DOOR.situs
    assert params["source"] == "outdoor"
    assert params["key"] == KEY


def test_zero_results_is_the_designed_unavailable_state_not_an_error():
    fetch = metadata_transport(status="ZERO_RESULTS", date=None)
    response = client(fetch=fetch).get(f"/api/streetview/{PIN}")
    assert response.status_code == 200
    assert response.json()["status"] == "unavailable"
    assert response.json()["reason"] == "no_imagery"


def test_no_key_is_unavailable_and_never_calls_google():
    response = client(key=None).get(f"/api/streetview/{PIN}")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "unavailable"
    assert body["reason"] == "not_configured"
    # The Maps link still works keyless — it is Google's own site, not the API.
    assert "google.com/maps" in body["maps_url"]


def test_a_google_failure_is_a_502_with_the_standard_error_shape():
    def denied(url, params):
        return 200, json.dumps({"status": "REQUEST_DENIED"}).encode(), "application/json"

    response = client(fetch=denied).get(f"/api/streetview/{PIN}")
    assert response.status_code == 502
    assert set(response.json()) >= {"error", "message"}


def test_a_transport_that_raises_is_the_same_502():
    def broken(url, params):
        raise ConnectionError("dns blip")

    assert client(fetch=broken).get(f"/api/streetview/{PIN}").status_code == 502


def test_an_unknown_pin_is_the_standard_door_404():
    response = client(fetch=metadata_transport()).get("/api/streetview/nope")
    assert response.status_code == 404
    assert response.json()["error"] == "door_not_found"


# --- GET /api/streetview/{pin}/image ------------------------------------------


def image_transport():
    calls = []

    def fetch(url, params):
        calls.append((url, dict(params)))
        return 200, b"\xff\xd8jpeg-bytes", "image/jpeg"

    fetch.calls = calls
    return fetch


def test_the_image_streams_through_with_no_store_and_no_key_in_sight():
    fetch = image_transport()
    response = client(fetch=fetch).get(f"/api/streetview/{PIN}/image")
    assert response.status_code == 200
    assert response.content == b"\xff\xd8jpeg-bytes"
    assert response.headers["cache-control"] == "no-store"
    url, params = fetch.calls[0]
    assert url == IMAGE_URL
    assert params["size"] == "640x400"


def test_the_full_screen_view_asks_for_the_larger_size():
    fetch = image_transport()
    client(fetch=fetch).get(f"/api/streetview/{PIN}/image?view=full")
    assert fetch.calls[0][1]["size"] == "640x640"


def test_an_unknown_view_falls_back_to_the_panel_size_not_a_passthrough():
    fetch = image_transport()
    client(fetch=fetch).get(f"/api/streetview/{PIN}/image?view=2048x2048")
    assert fetch.calls[0][1]["size"] == "640x400"


def test_an_upstream_image_failure_is_a_502():
    def down(url, params):
        return 500, b"", "text/plain"

    assert client(fetch=down).get(f"/api/streetview/{PIN}/image").status_code == 502
