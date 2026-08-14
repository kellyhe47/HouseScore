"""The FastAPI app: the REST surface the Map UI eats, plus the MCP mount (T011, R8/R9/R10.3/R12).

One app, two surfaces. The MCP tools are `tests/test_mcp_tools.py`; this module
is the HTTP side — the four endpoints the Map UI calls, the CORS that lets a
browser on the UI's origin call them, and the boot behaviour a reviewer sees when
the artifacts are not there.

**The seam.** `create_app(data_dir=...)` builds the app from published artifacts
and returns a plain FastAPI instance, so uvicorn can serve it and `TestClient`
can drive it without a live process. `data_dir` defaults to the one
`Config.from_env()` points at, which is what makes `uvicorn ...:create_app
--factory` work in deployment; every test here passes its own `tmp_path`
territory instead, so the suite never depends on whether `make pipeline` has run.

**The four endpoints.**

* `GET /health` — the Fly.io/uvicorn liveness probe, and the one thing that must
  answer before anything else works (R12).
* `GET /api/doors.geojson` — the published collection, served verbatim. The map
  renders exactly the artifact the pipeline wrote; anything reshaped here is a
  place the map and the artifact can disagree.
* `GET /api/door/{pin}` — one door's published properties, for the evidence
  panel. An unknown PIN is a 404 carrying a structured body, not a stack trace.
* `POST /api/route` — the UI's route request, delegated to
  `houseaccount.route` exactly as the MCP tool is (R10.3). A malformed body is a
  422 from the request model, never a 500 from inside the planner.

**Errors are shapes, not prose.** Every failure body carries `error` and
`message`, so the UI's error banner (R9.3) renders one thing whatever failed.
The tests assert those keys and the status code and never the wording.

**A missing `data/` is a startup failure with an address on it.** A reviewer who
clones and runs the server before running the pipeline should be told which
directory was empty — `DataUnavailable`, naming the path — rather than reading a
`FileNotFoundError` out of a JSON parser.

No network, no live uvicorn process, no fixtures on disk: every door is built
in-module and every artifact is written under `tmp_path`.
"""

import json
from dataclasses import fields
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from houseaccount.normalize import situs_display
from houseaccount.publish import DOORS_GEOJSON_NAME, RunManifest, publish
from houseaccount.resolve import DoorFacts, ResolveReport
from houseaccount.route import Stop
from houseaccount.scoring.engine import score_door
from houseaccount.server.app import UI_ORIGINS, DataUnavailable, create_app
from houseaccount.server.mcp_tools import create_mcp_server

AS_OF = date(2026, 8, 14)
RUN_AT = datetime(2026, 8, 14, 6, 30, 0, tzinfo=timezone.utc)

TERRITORY_MEDIAN_VALUE = 700000.0
ACS_DUAL_INCOME_THRESHOLD = 0.35

START = (-74.1560, 41.0447)

#: The R11.1 allowlist `publish` writes; the door endpoint serves it, unwidened.
PUBLISHED_PROPERTIES = {
    "PAMS_PIN",
    "score",
    "confidence",
    "evidence",
    "situs",
    "exclusion_reason",
}

STOP_FIELDS = {field.name for field in fields(Stop)}


# --- builders -----------------------------------------------------------------


def polygon(lon, lat):
    step = 0.0002
    return {
        "type": "Polygon",
        "coordinates": [
            [[lon, lat], [lon + step, lat], [lon + step, lat + step], [lon, lat + step], [lon, lat]]
        ],
    }


def north_of(origin, metres):
    lon, lat = origin
    return (lon, lat + metres / 111194.9266)


def facts(pin, prop_loc, *, deed, yr_constr, net_value, point):
    lot = pin.rsplit("_", 1)[-1].lstrip("0") or "0"
    return DoorFacts(
        pams_pin=pin,
        prop_class="2",
        prop_loc=prop_loc,
        zip5="07446",
        pclblock="1101",
        pcllot=lot,
        parcel_key=f"0248/1101/{lot}",
        address_key=prop_loc,
        situs=situs_display(prop_loc, "07446"),
        centroid=point,
        geometry=polygon(*point) if point else None,
        deed_date=deed,
        sale_price=1150000.0 if deed else 0.0,
        sales_code="",
        yr_constr=yr_constr,
        net_value=net_value,
        calc_acre=0.61,
    )


OAK = facts(
    "0248_01101_00012",
    "12 OAK ST",
    deed=date(2026, 7, 20),
    yr_constr=1962,
    net_value=980000.0,
    point=START,
)
MAPLE = facts(
    "0248_01101_00020",
    "20 MAPLE AVE",
    deed=date(2001, 3, 4),
    yr_constr=1998,
    net_value=520000.0,
    point=north_of(START, 300.0),
)
BIRCH = facts(
    "0248_01101_00099",
    "99 BIRCH LN",
    deed=None,
    yr_constr=0,
    net_value=0.0,
    point=None,
)

DOORS = (OAK, MAPLE, BIRCH)


def engine_result(door):
    return score_door(
        door.to_score_input(
            as_of=AS_OF,
            territory_median_value=TERRITORY_MEDIAN_VALUE,
            acs_dual_income_threshold=ACS_DUAL_INCOME_THRESHOLD,
        )
    )


@pytest.fixture
def data_dir(tmp_path):
    target = tmp_path / "data"
    publish(
        [(door, engine_result(door) if door is not BIRCH else None) for door in DOORS],
        report=ResolveReport(as_of=AS_OF, doors_total=len(DOORS), doors_with_signal=2),
        manifest=RunManifest(
            run_at=RUN_AT,
            as_of=AS_OF,
            code_version="a1b2c3d",
            territory_median_value=TERRITORY_MEDIAN_VALUE,
            acs_dual_income_threshold=ACS_DUAL_INCOME_THRESHOLD,
            retrieved={"parcel": date(2026, 8, 13)},
            cost_usd=0.0,
        ),
        data_dir=target,
    )
    return target


@pytest.fixture
def client(data_dir):
    return TestClient(create_app(data_dir=data_dir))


# --- boot ---------------------------------------------------------------------


def test_create_app_returns_an_asgi_app_whose_health_answers_200(data_dir):
    """R12's liveness probe: uvicorn serves this object and Fly.io calls this path."""
    app = create_app(data_dir=data_dir)
    assert isinstance(app, FastAPI)

    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.parametrize("factory", [create_app, create_mcp_server], ids=["app", "mcp"])
def test_a_missing_data_directory_fails_with_the_path_it_looked_in(tmp_path, factory):
    """A reviewer who has not run the pipeline gets told what is missing (R13)."""
    missing = tmp_path / "no-such-data"

    with pytest.raises(DataUnavailable) as raised:
        factory(data_dir=missing)

    assert issubclass(DataUnavailable, RuntimeError)
    assert str(missing) in str(raised.value)


def test_the_app_mounts_the_mcp_surface(data_dir):
    """One deployment serves both surfaces (R8 + R9)."""
    paths = {getattr(route, "path", "") for route in create_app(data_dir=data_dir).routes}
    assert any(path.startswith("/mcp") for path in paths), sorted(paths)


# --- GET /api/doors.geojson ---------------------------------------------------


def test_doors_geojson_is_served_verbatim(client, data_dir):
    """What the map draws is the artifact the pipeline published, unreshaped."""
    response = client.get("/api/doors.geojson")

    assert response.status_code == 200
    assert "json" in response.headers["content-type"]
    assert response.json() == json.loads(
        (Path(data_dir) / DOORS_GEOJSON_NAME).read_text(encoding="utf-8")
    )


def test_doors_geojson_still_carries_the_unscored_door(client):
    """R9.4: the gap in the coverage readout is clickable, so it has to be there."""
    features = client.get("/api/doors.geojson").json()["features"]

    by_pin = {feature["properties"]["PAMS_PIN"]: feature for feature in features}
    assert set(by_pin) == {door.pams_pin for door in DOORS}
    assert by_pin[BIRCH.pams_pin]["properties"]["score"] is None
    assert by_pin[BIRCH.pams_pin]["geometry"] is None


# --- GET /api/door/{pin} ------------------------------------------------------


def test_door_endpoint_returns_the_published_properties(client):
    result = engine_result(OAK)

    response = client.get(f"/api/door/{OAK.pams_pin}")

    assert response.status_code == 200
    body = response.json()
    assert PUBLISHED_PROPERTIES <= set(body)
    assert body["PAMS_PIN"] == OAK.pams_pin
    assert body["score"] == result.score
    assert body["confidence"] == result.confidence
    assert body["situs"] == OAK.situs
    assert [item["sentence"] for item in body["evidence"]] == [
        item.sentence for item in result.evidence
    ]


def test_door_endpoint_serves_the_unscored_door_with_its_exclusion(client):
    body = client.get(f"/api/door/{BIRCH.pams_pin}").json()

    assert body["score"] is None
    assert body["evidence"] == []
    assert isinstance(body["exclusion_reason"], str) and body["exclusion_reason"]


def test_unknown_pin_is_a_structured_404(client):
    response = client.get("/api/door/0248_99999_99999")

    assert response.status_code == 404
    body = response.json()
    assert isinstance(body.get("error"), str) and body["error"]
    assert isinstance(body.get("message"), str) and body["message"]


# --- POST /api/route ----------------------------------------------------------


def test_route_endpoint_returns_ordered_stops_with_talk_tracks(client):
    response = client.post(
        "/api/route", json={"hours": 2, "start_point": list(START), "max_doors": 20}
    )

    assert response.status_code == 200
    body = response.json()
    assert [stop["pams_pin"] for stop in body["stops"]] == [OAK.pams_pin, MAPLE.pams_pin]
    assert body["total_minutes"] == body["stops"][-1]["cumulative_minutes"]
    assert isinstance(body["estimate_disclosure"], str) and body["estimate_disclosure"]
    for stop in body["stops"]:
        assert set(stop) == STOP_FIELDS
        assert isinstance(stop["talk_track"], str) and stop["talk_track"].strip()


def test_route_endpoint_with_no_time_returns_an_empty_route(client):
    response = client.post("/api/route", json={"hours": 0, "start_point": list(START)})

    assert response.status_code == 200
    assert response.json()["stops"] == []


@pytest.mark.parametrize(
    "body",
    [
        {"start_point": list(START)},
        {"hours": "two", "start_point": list(START)},
        {"hours": 2, "start_point": "outside my house"},
        {"hours": 2, "start_point": [-74.156]},
    ],
    ids=["no-hours", "hours-not-a-number", "start-not-a-point", "start-half-a-point"],
)
def test_a_malformed_route_request_is_rejected_before_the_planner_sees_it(client, body):
    assert client.post("/api/route", json=body).status_code == 422


# --- CORS ---------------------------------------------------------------------


def test_cors_allows_the_ui_origin(client):
    """The Map UI is deployed on its own origin and calls this app from a browser."""
    assert UI_ORIGINS, "the UI has to be allowed from somewhere"
    origin = UI_ORIGINS[0]

    preflight = client.options(
        "/api/route",
        headers={"Origin": origin, "Access-Control-Request-Method": "POST"},
    )
    assert preflight.status_code < 400
    assert preflight.headers["access-control-allow-origin"] in {origin, "*"}

    simple = client.get("/api/doors.geojson", headers={"Origin": origin})
    assert simple.headers["access-control-allow-origin"] in {origin, "*"}
