"""The FastAPI app on the V2 score contract (ticket 105, plan R27/R30, R8/R9/R10.3/R12).

One app, two surfaces. The MCP tools are `tests/test_mcp_tools.py`; this module
is the HTTP side — the endpoints the Map UI calls, the CORS that lets a browser
on the UI's origin call them, and the boot behaviour a reviewer sees when the
artifacts are not there.

**What changed under V2 (R27/R30).**

* `GET /api/door/{pin}` serves the published V2 record — `score_contract_version`,
  `categories{project,capacity,fit}`, `base`, `mover`, `mover_lift`,
  `rental_modifier`, `adjustment`, `data_gaps` — plus exactly three detail
  fields: `talk_track`, `talk_track_branches`, `reason_chip`. The V1 detail
  (`groups`, `raw_total`) is gone from every payload; a V1 key anywhere is a
  failure, not a leftover.
* Every served evidence list reconciles to the displayed score (R7 at the API
  boundary): sum(points) == base + mover_lift + rental_modifier, and adding
  `adjustment` lands on the integer the map colours — including through the
  rental demotion and the 0-clamp.
* `POST /api/route` responses carry `score_contract_version` and per-stop
  `reason_chip`s; a request that declares a stale version gets a refresh signal
  instead of a mixed-version route (R27/R30). Route aggregates are computed
  from the displayed V2 scores.
* A published run carrying a record from another contract version does not
  boot: mixed-version outputs are rejected, with the version in the message.

**Fixtures are synthetic published records.** Ticket 103 owns `publish()`; this
module writes hand-consistent V2 artifacts under `tmp_path` (see
`tests/test_mcp_tools.py` for the arithmetic), so the HTTP surface is tested
against the published contract rather than against a particular writer.
"""

import contextlib
import json
import re
import sqlite3
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from houseaccount.server.app import MCP_PATH, UI_ORIGINS, DataUnavailable, create_app
from houseaccount.server.mcp_tools import create_mcp_server

START = (-74.1560, 41.0447)

#: The published per-door property allowlist, V2-shaped (R11.1/R27/R30).
PUBLISHED_PROPERTIES = {
    "PAMS_PIN",
    "score",
    "confidence",
    "evidence",
    "situs",
    "exclusion_reason",
    "score_contract_version",
    "categories",
    "base",
    "mover",
    "mover_lift",
    "rental_modifier",
    "adjustment",
    "data_gaps",
}

#: What the door endpoint carries beyond the published allowlist under V2:
#: presentation, and only presentation.
DOOR_DETAIL_FIELDS = {"talk_track", "talk_track_branches", "reason_chip"}

#: V1 vocabulary that may appear as a key in no served payload (R27/R28).
FORBIDDEN_KEYS = {"groups", "raw_total"}

#: The V2 category names — the only subtotal vocabulary any surface may use.
CATEGORY_NAMES = {"project", "capacity", "fit"}

#: Unspeakable at the door (PRD R7.2.1): never a chip, never in a talk track.
UNSPEAKABLE_TYPES = {"capacity_territory_percentile", "capacity_local_relative_value"}

EXCLUSION_REASON = "parcel record incomplete in county data"


# --- synthetic V2 published records (arithmetic mirrors tests/test_mcp_tools.py) ---


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


def evidence(etype, points, reason):
    return {"type": etype, "points": points, "reason": reason}


OAK_PIN = "0248_01101_00012"
MAPLE_PIN = "0248_01101_00020"
CEDAR_PIN = "0248_01101_00031"
BIRCH_PIN = "0248_01101_00099"


def scored_properties(pin, situs, *, score, categories, base, mover, mover_lift,
                      rental_modifier, adjustment, confidence, data_gaps, trail):
    return {
        "PAMS_PIN": pin,
        "situs": situs,
        "score": score,
        "confidence": confidence,
        "evidence": trail,
        "exclusion_reason": None,
        "score_contract_version": "v2",
        "categories": categories,
        "base": base,
        "mover": mover,
        "mover_lift": mover_lift,
        "rental_modifier": rental_modifier,
        "adjustment": adjustment,
        "data_gaps": data_gaps,
    }


#: Fresh mover: base 39, full blend 55.875 -> 94.875 -> 95 (adjustment 0.125).
OAK_PROPERTIES = scored_properties(
    OAK_PIN,
    "12 OAK ST, Ramsey NJ 07446",
    score=95,
    categories={"project": 15, "capacity": 12, "fit": 12},
    base=39,
    mover={"eligible": True, "days_since_move": 30, "strength": 90.0},
    mover_lift=55.875,
    rental_modifier=0,
    adjustment=0.125,
    confidence="normal",
    data_gaps=[],
    trail=[
        evidence("project_active", 15, "active qualifying project with recent lifecycle activity"),
        evidence(
            "capacity_acs_dual_income_prior",
            5,
            "neighborhood-level ACS dual-income prior at or above 35% (block-group prior, not a household claim)",
        ),
        evidence(
            "capacity_local_relative_value",
            7,
            "assessed value above the median of the nearest comparables",
        ),
        evidence(
            "fit_roof_age",
            12,
            "latest explicit completed roof installation is old enough to need service",
        ),
        evidence(
            "mover_recency",
            55.875,
            "home changed hands 30 days ago in a market sale — new owners are "
            "the likeliest to start projects, so the score gets a large lift "
            "that fades out over the first year",
        ),
    ],
)

#: Unspeakable-led: percentile (10) and local ratio (7) top the trail, so the
#: chip must be `fit_lot` and the talk track the angle that says nothing.
MAPLE_PROPERTIES = scored_properties(
    MAPLE_PIN,
    "20 MAPLE AVE, Ramsey NJ 07446",
    score=22,
    categories={"project": 0, "capacity": 17, "fit": 5},
    base=22,
    mover={"eligible": False, "days_since_move": None, "strength": 0.0},
    mover_lift=0.0,
    rental_modifier=0,
    adjustment=0.0,
    confidence="normal",
    data_gaps=[{"type": "acs_missing"}],
    trail=[
        evidence(
            "capacity_territory_percentile",
            10,
            "assessed value ranks high among territory single-family properties",
        ),
        evidence(
            "capacity_local_relative_value",
            7,
            "assessed value above the median of the nearest comparables",
        ),
        evidence("fit_lot", 5, "lot of at least half an acre"),
    ],
)

#: Demoted rental with the 0-clamp: 13 - 25 = -12 -> 0 (adjustment 12.0).
CEDAR_PROPERTIES = scored_properties(
    CEDAR_PIN,
    "31 CEDAR CT, Ramsey NJ 07446",
    score=0,
    categories={"project": 8, "capacity": 0, "fit": 5},
    base=13,
    mover={"eligible": False, "days_since_move": None, "strength": 0.0},
    mover_lift=0.0,
    rental_modifier=-25,
    adjustment=12.0,
    confidence="low",
    data_gaps=[{"type": "acs_missing"}, {"type": "assessed_value_missing"}],
    trail=[
        evidence("project_completed", 8, "qualifying project completed within 24 months"),
        evidence("fit_lot", 5, "lot of at least half an acre"),
        evidence(
            "rental_registration", -25, "current verified rental registration demotes the door"
        ),
        evidence(
            "mover_invalid_sale",
            0,
            "a transfer was disqualified from mover influence (nominal price, disqualifying code, or invalid/future date)",
        ),
    ],
)

BIRCH_PROPERTIES = {
    "PAMS_PIN": BIRCH_PIN,
    "situs": "99 BIRCH LN, Ramsey NJ 07446",
    "score": None,
    "confidence": None,
    "evidence": [],
    "exclusion_reason": EXCLUSION_REASON,
    "score_contract_version": "v2",
    "categories": None,
    "base": None,
    "mover": None,
    "mover_lift": None,
    "rental_modifier": None,
    "adjustment": None,
    "data_gaps": None,
}

FEATURES = [
    {"type": "Feature", "geometry": polygon(*START), "properties": OAK_PROPERTIES},
    {"type": "Feature", "geometry": polygon(*north_of(START, 300.0)), "properties": MAPLE_PROPERTIES},
    {"type": "Feature", "geometry": None, "properties": CEDAR_PROPERTIES},
    {"type": "Feature", "geometry": None, "properties": BIRCH_PROPERTIES},
]

ALL_PINS = {OAK_PIN, MAPLE_PIN, CEDAR_PIN, BIRCH_PIN}
SCORED_PINS = [OAK_PIN, MAPLE_PIN, CEDAR_PIN]

MANIFEST = {
    "run_at": "2026-08-14T06:30:00+00:00",
    "as_of": "2026-08-14",
    "code_version": "a1b2c3d",
    "score_contract_version": "v2",
    "acs_dual_income_threshold": 0.35,
    "retrieved": {"parcel": "2026-08-13"},
    "cost_usd": 0.0,
    "doors_total": 4,
    "doors_with_signal": 3,
    "coverage": 0.75,
}


def write_data_dir(target, features=FEATURES):
    target.mkdir(parents=True, exist_ok=True)
    (target / "doors.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8"
    )
    (target / "run_manifest.json").write_text(json.dumps(MANIFEST), encoding="utf-8")

    connection = sqlite3.connect(target / "houseaccount.sqlite")
    try:
        connection.execute(
            "CREATE TABLE doors ("
            "pams_pin TEXT PRIMARY KEY, situs TEXT, score INTEGER, confidence TEXT,"
            "score_contract_version TEXT, project INTEGER, capacity INTEGER, fit INTEGER,"
            "base INTEGER, mover_eligible INTEGER, days_since_move INTEGER,"
            "mover_strength REAL, mover_lift REAL, rental_modifier INTEGER,"
            "adjustment REAL, exclusion_reason TEXT)"
        )
        connection.execute(
            "CREATE TABLE evidence (pams_pin TEXT, type TEXT, points REAL, reason TEXT)"
        )
        for feature in features:
            p = feature["properties"]
            categories = p["categories"] or {}
            mover = p["mover"] or {}
            connection.execute(
                "INSERT INTO doors VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    p["PAMS_PIN"], p["situs"], p["score"], p["confidence"],
                    p["score_contract_version"], categories.get("project"),
                    categories.get("capacity"), categories.get("fit"), p["base"],
                    mover.get("eligible"), mover.get("days_since_move"),
                    mover.get("strength"), p["mover_lift"], p["rental_modifier"],
                    p["adjustment"], p["exclusion_reason"],
                ),
            )
            for item in p["evidence"]:
                connection.execute(
                    "INSERT INTO evidence VALUES (?,?,?,?)",
                    (p["PAMS_PIN"], item["type"], item["points"], item["reason"]),
                )
        connection.commit()
    finally:
        connection.close()
    return target


def every_key(payload):
    if isinstance(payload, dict):
        for key, value in payload.items():
            yield key
            yield from every_key(value)
    elif isinstance(payload, list):
        for item in payload:
            yield from every_key(item)


@pytest.fixture
def data_dir(tmp_path):
    return write_data_dir(tmp_path / "data")


@pytest.fixture
def client(data_dir):
    return TestClient(create_app(data_dir=data_dir))


# --- boot ---------------------------------------------------------------------


def test_create_app_returns_an_asgi_app_whose_health_answers_200(data_dir):
    app = create_app(data_dir=data_dir)
    assert isinstance(app, FastAPI)

    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.parametrize("factory", [create_app, create_mcp_server], ids=["app", "mcp"])
def test_a_missing_data_directory_fails_with_the_path_it_looked_in(tmp_path, factory):
    missing = tmp_path / "no-such-data"

    with pytest.raises(DataUnavailable) as raised:
        factory(data_dir=missing)

    assert issubclass(DataUnavailable, RuntimeError)
    assert str(missing) in str(raised.value)


def test_a_run_from_another_score_contract_version_is_rejected_at_boot(tmp_path):
    """R27: mixed-version outputs must be rejected. A published record still
    claiming `v1` is a stale run, not a servable territory — the boot failure
    names the offending version so the operator knows to re-run the pipeline."""
    stale = {**OAK_PROPERTIES, "score_contract_version": "v1"}
    features = [
        {"type": "Feature", "geometry": polygon(*START), "properties": stale},
        FEATURES[1],
    ]
    target = write_data_dir(tmp_path / "stale", features=features)

    with pytest.raises(DataUnavailable) as raised:
        create_app(data_dir=target)

    assert "v1" in str(raised.value)


def test_the_app_mounts_the_mcp_surface(data_dir):
    paths = {getattr(route, "path", "") for route in create_app(data_dir=data_dir).routes}
    assert any(path.startswith("/mcp") for path in paths), sorted(paths)


# --- GET /api/doors.geojson -----------------------------------------------------


def test_doors_geojson_is_served_verbatim(client, data_dir):
    response = client.get("/api/doors.geojson")

    assert response.status_code == 200
    assert "json" in response.headers["content-type"]
    assert response.json() == json.loads(
        (Path(data_dir) / "doors.geojson").read_text(encoding="utf-8")
    )


def test_doors_geojson_still_carries_the_unscored_door(client):
    features = client.get("/api/doors.geojson").json()["features"]

    by_pin = {feature["properties"]["PAMS_PIN"]: feature for feature in features}
    assert set(by_pin) == ALL_PINS
    assert by_pin[BIRCH_PIN]["properties"]["score"] is None
    assert by_pin[BIRCH_PIN]["geometry"] is None


def test_every_served_feature_keeps_the_v2_allowlist_exactly(client):
    """R11.1/R27: the exact V2 property set — nothing widened, nothing V1."""
    for feature in client.get("/api/doors.geojson").json()["features"]:
        assert set(feature["properties"]) == PUBLISHED_PROPERTIES


# --- GET /api/door/{pin} ---------------------------------------------------------


def test_door_endpoint_serves_the_published_v2_record_plus_presentation(client):
    body = client.get(f"/api/door/{OAK_PIN}").json()

    assert set(body) == PUBLISHED_PROPERTIES | DOOR_DETAIL_FIELDS
    assert body["PAMS_PIN"] == OAK_PIN
    assert body["score"] == 95
    assert body["confidence"] == "normal"
    assert body["score_contract_version"] == "v2"
    assert body["categories"] == {"project": 15, "capacity": 12, "fit": 12}
    assert set(body["categories"]) == CATEGORY_NAMES
    assert body["base"] == 39
    assert body["mover"] == {"eligible": True, "days_since_move": 30, "strength": 90.0}
    assert body["mover_lift"] == pytest.approx(55.875)
    assert body["rental_modifier"] == 0
    assert body["adjustment"] == pytest.approx(0.125)
    assert body["data_gaps"] == []


def test_no_v1_vocabulary_survives_in_any_door_payload(client):
    """R27/R28: `groups` and `raw_total` are keys of nothing this app serves."""
    for pin in [*SCORED_PINS, BIRCH_PIN]:
        body = client.get(f"/api/door/{pin}").json()
        assert set(every_key(body)) & FORBIDDEN_KEYS == set(), pin


def test_door_evidence_items_are_type_points_reason(client):
    body = client.get(f"/api/door/{OAK_PIN}").json()

    assert body["evidence"]
    for item in body["evidence"]:
        assert {"type", "points", "reason"} <= set(item) <= {"type", "points", "reason", "imagery"}
        assert "sentence" not in item and "source" not in item


@pytest.mark.parametrize("pin", SCORED_PINS, ids=["mover", "flat", "rental-clamped"])
def test_every_served_evidence_list_reconciles_to_the_displayed_score(client, pin):
    """R7 at the API boundary, for all three arithmetic shapes: plain base,
    mover blend, and the rental demotion through the 0-clamp."""
    body = client.get(f"/api/door/{pin}").json()

    assert sum(body["categories"].values()) == body["base"]
    trail_sum = sum(item["points"] for item in body["evidence"])
    assert trail_sum == pytest.approx(
        body["base"] + body["mover_lift"] + body["rental_modifier"]
    )
    assert body["score"] == pytest.approx(trail_sum + body["adjustment"])


def test_door_endpoint_serves_the_unscored_door_with_its_exclusion(client):
    body = client.get(f"/api/door/{BIRCH_PIN}").json()

    assert body["score"] is None
    assert body["evidence"] == []
    assert body["exclusion_reason"] == EXCLUSION_REASON
    assert body["score_contract_version"] == "v2"


def test_an_unscored_door_has_no_breakdown_and_no_presentation(client):
    body = client.get(f"/api/door/{BIRCH_PIN}").json()

    assert DOOR_DETAIL_FIELDS <= set(body)
    assert body["categories"] is None
    assert body["talk_track"] is None
    assert body["talk_track_branches"] is None
    assert body["reason_chip"] is None


def test_the_door_reason_chip_follows_the_selection_rule(client):
    """Highest points wins (OAK: `mover_recency` at 55.875); unspeakable
    leaders are skipped (MAPLE: percentile 10 and ratio 7 -> `fit_lot`)."""
    oak = client.get(f"/api/door/{OAK_PIN}").json()
    maple = client.get(f"/api/door/{MAPLE_PIN}").json()

    assert oak["reason_chip"] == "mover_recency"
    assert maple["reason_chip"] == "fit_lot"


def test_no_served_chip_is_ever_an_unspeakable_type(client):
    for pin in SCORED_PINS:
        chip = client.get(f"/api/door/{pin}").json()["reason_chip"]
        assert chip not in UNSPEAKABLE_TYPES, pin


def test_the_door_endpoint_carries_the_same_talk_track_the_route_does(client):
    body = client.get(f"/api/door/{OAK_PIN}").json()
    stops = client.post(
        "/api/route", json={"hours": 2, "start_point": list(START)}
    ).json()["stops"]

    planned = next(stop for stop in stops if stop["pams_pin"] == OAK_PIN)
    assert isinstance(body["talk_track"], str) and body["talk_track"].strip()
    assert body["talk_track"] == planned["talk_track"]


def test_the_panel_shows_the_reason_and_the_opener_speaks_none_of_it(client):
    """R7.2/PRD R7.2.1: the evidence reasons stay on the panel; the opener under
    them never recites the file — checked on the unspeakable-led door."""
    body = client.get(f"/api/door/{MAPLE_PIN}").json()

    reasons = [item["reason"] for item in body["evidence"]]
    assert "assessed value ranks high among territory single-family properties" in reasons

    track = body["talk_track"].lower()
    for word in ["assessed", "percentile", "median", "comparable", "territory", "score"]:
        assert word not in track, f"{word!r} was read out at the door"
    assert body["talk_track"].strip().endswith("?")
    for branch in body["talk_track_branches"]:
        assert branch["trigger"].strip() and branch["line"].strip()


def test_the_acs_prior_is_served_as_a_neighborhood_prior_never_a_household_claim(client):
    """R15 at the API boundary: the served reason names the block-group prior;
    nothing served claims the household itself is dual-income."""
    body = client.get(f"/api/door/{OAK_PIN}").json()

    (acs_reason,) = [
        item["reason"]
        for item in body["evidence"]
        if item["type"] == "capacity_acs_dual_income_prior"
    ]
    lowered = acs_reason.lower()
    assert "neighborhood-level" in lowered or "block-group" in lowered
    assert "household is dual-income" not in lowered
    assert "this household" not in lowered
    assert "dual" not in body["talk_track"].lower()


def test_unknown_pin_is_a_structured_404(client):
    response = client.get("/api/door/0248_99999_99999")

    assert response.status_code == 404
    body = response.json()
    assert isinstance(body.get("error"), str) and body["error"]
    assert isinstance(body.get("message"), str) and body["message"]


# --- POST /api/route ---------------------------------------------------------------


def test_route_endpoint_returns_ordered_stops_with_talk_tracks_and_chips(client):
    response = client.post(
        "/api/route", json={"hours": 2, "start_point": list(START), "max_doors": 20}
    )

    assert response.status_code == 200
    body = response.json()
    assert [stop["pams_pin"] for stop in body["stops"]] == [OAK_PIN, MAPLE_PIN]
    assert body["total_minutes"] == body["stops"][-1]["cumulative_minutes"]
    assert isinstance(body["estimate_disclosure"], str) and body["estimate_disclosure"]
    for stop in body["stops"]:
        assert isinstance(stop["talk_track"], str) and stop["talk_track"].strip()
        assert "reason_chip" in stop
        assert stop["reason_chip"] not in UNSPEAKABLE_TYPES


def test_the_route_response_carries_the_score_contract_version(client):
    """R30: the shared route payload names the contract its scores came from."""
    body = client.post("/api/route", json={"hours": 2, "start_point": list(START)}).json()

    assert body["score_contract_version"] == "v2"


def test_a_route_request_declaring_a_stale_version_gets_a_refresh_signal(client):
    """R27/R30: a share link minted under V1 must not replay as a V2 route —
    the response says refresh, and plans nothing."""
    response = client.post(
        "/api/route",
        json={"hours": 2, "start_point": list(START), "score_contract_version": "v1"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["refresh_required"] is True
    assert body["score_contract_version"] == "v2"
    assert body.get("stops", []) == []


def test_a_route_request_declaring_the_current_version_plans_normally(client):
    body = client.post(
        "/api/route",
        json={"hours": 2, "start_point": list(START), "score_contract_version": "v2"},
    ).json()

    assert body.get("refresh_required") is not True
    assert [stop["pams_pin"] for stop in body["stops"]] == [OAK_PIN, MAPLE_PIN]


def test_route_aggregates_come_from_the_displayed_v2_scores(client):
    """R30: the average the UI shows is arithmetic over the published scores."""
    body = client.post("/api/route", json={"hours": 2, "start_point": list(START)}).json()

    assert [stop["score"] for stop in body["stops"]] == [95, 22]
    assert body["average_score"] == pytest.approx((95 + 22) / 2)


def test_an_empty_route_has_no_average(client):
    body = client.post("/api/route", json={"hours": 0, "start_point": list(START)}).json()

    assert body["stops"] == []
    assert body["average_score"] is None


def test_every_stop_carries_the_line_the_rep_walks(client):
    response = client.post(
        "/api/route", json={"hours": 2, "start_point": list(START), "max_doors": 20}
    )

    body = response.json()
    position = list(START)
    for stop in body["stops"]:
        path = stop["path"]
        assert len(path) >= 2
        assert path[0] == position
        assert all(len(point) == 2 for point in path)
        position = path[-1]


def test_the_route_endpoint_replans_without_an_excluded_door(client):
    response = client.post(
        "/api/route",
        json={"hours": 2, "start_point": list(START), "exclude": [OAK_PIN]},
    )

    assert response.status_code == 200
    pins = [stop["pams_pin"] for stop in response.json()["stops"]]
    assert OAK_PIN not in pins
    assert MAPLE_PIN in pins


def test_excluding_re_plans_the_walk_rather_than_filtering_it(client):
    full = client.post("/api/route", json={"hours": 2, "start_point": list(START)}).json()
    without = client.post(
        "/api/route",
        json={"hours": 2, "start_point": list(START), "exclude": [OAK_PIN]},
    ).json()

    kept = next(stop for stop in full["stops"] if stop["pams_pin"] == MAPLE_PIN)
    replanned = next(stop for stop in without["stops"] if stop["pams_pin"] == MAPLE_PIN)
    assert replanned["cumulative_minutes"] != kept["cumulative_minutes"]
    assert without["total_minutes"] == without["stops"][-1]["cumulative_minutes"]


def test_the_same_exclusions_plan_the_same_walk_every_time(client):
    body = {"hours": 2, "start_point": list(START), "exclude": [OAK_PIN]}

    first = client.post("/api/route", json=body).json()
    second = client.post("/api/route", json=body).json()

    assert first == second


def test_an_exclusion_that_names_no_door_changes_nothing(client):
    plain = client.post("/api/route", json={"hours": 2, "start_point": list(START)}).json()
    ignored = client.post(
        "/api/route",
        json={"hours": 2, "start_point": list(START), "exclude": ["0248_99999_99999"]},
    ).json()

    assert ignored == plain


def test_excluding_every_door_returns_an_empty_route(client):
    response = client.post(
        "/api/route",
        json={"hours": 2, "start_point": list(START), "exclude": sorted(ALL_PINS)},
    )

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


def test_a_malformed_exclusion_list_is_rejected_too(client):
    response = client.post(
        "/api/route",
        json={"hours": 2, "start_point": list(START), "exclude": OAK_PIN},
    )

    assert response.status_code == 422


# --- the published artifacts the Data & Ethics page reads (T016, R12) ----------------

REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_EVAL_REPORT = REPO_ROOT / "eval" / "report.json"

REPORT_ROUTE = "/api/eval/report.json"
MANIFEST_ROUTE = "/api/data/run_manifest.json"

REPORT = {"fixtures_total": 12, "precision": 0.8181818181818182, "ok": True}


@pytest.fixture
def eval_report(tmp_path):
    path = tmp_path / "eval" / "report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(REPORT), encoding="utf-8")
    return path


def test_the_report_endpoint_serves_the_eval_report(data_dir, eval_report):
    client = TestClient(create_app(data_dir=data_dir, eval_report=eval_report))

    response = client.get(REPORT_ROUTE)

    assert response.status_code == 200
    assert "json" in response.headers["content-type"]
    assert response.json() == REPORT


def test_the_report_endpoint_defaults_to_the_repositorys_eval_report(client):
    assert REPO_EVAL_REPORT.is_file(), "the repo ships a published eval report"

    response = client.get(REPORT_ROUTE)

    assert response.status_code == 200
    assert response.json() == json.loads(REPO_EVAL_REPORT.read_text(encoding="utf-8"))


def test_the_manifest_endpoint_serves_the_published_run_manifest(client, data_dir):
    response = client.get(MANIFEST_ROUTE)

    assert response.status_code == 200
    assert "json" in response.headers["content-type"]
    assert response.json() == json.loads(
        (Path(data_dir) / "run_manifest.json").read_text(encoding="utf-8")
    )


def test_the_served_manifest_names_the_score_contract_version(client):
    """R27/R28: the run manifest identifies the contract that produced the run."""
    assert client.get(MANIFEST_ROUTE).json()["score_contract_version"] == "v2"


def test_the_artifact_routes_are_the_paths_the_ethics_page_fetches(data_dir):
    fetched = set(
        re.findall(
            r"artifact\(\s*['\"]([^'\"]+)['\"]",
            (REPO_ROOT / "web" / "ethics.html").read_text(encoding="utf-8"),
        )
    )
    assert fetched == {"eval/report.json", "data/run_manifest.json"}, sorted(fetched)

    paths = {getattr(route, "path", "") for route in create_app(data_dir=data_dir).routes}
    assert {f"/api/{name}" for name in fetched} <= paths, sorted(paths)


def test_the_artifact_endpoints_are_readable_from_the_ui_origin(client):
    origin = UI_ORIGINS[0]

    for route in (REPORT_ROUTE, MANIFEST_ROUTE):
        response = client.get(route, headers={"Origin": origin})
        assert response.headers["access-control-allow-origin"] in {origin, "*"}, route


def test_a_missing_eval_report_does_not_fail_the_boot(data_dir, tmp_path):
    app = create_app(data_dir=data_dir, eval_report=tmp_path / "never-ran" / "report.json")

    client = TestClient(app)
    assert client.get("/health").status_code == 200
    assert client.get("/api/doors.geojson").status_code == 200


def test_a_missing_eval_report_is_a_structured_404(data_dir, tmp_path):
    client = TestClient(create_app(data_dir=data_dir, eval_report=tmp_path / "gone.json"))

    response = client.get(REPORT_ROUTE)

    assert response.status_code == 404
    body = response.json()
    assert isinstance(body.get("error"), str) and body["error"]
    assert isinstance(body.get("message"), str) and body["message"]


def test_a_malformed_eval_report_degrades_rather_than_500ing(data_dir, tmp_path):
    broken = tmp_path / "report.json"
    broken.write_text('{"fixtures_total": 12, ', encoding="utf-8")

    client = TestClient(
        create_app(data_dir=data_dir, eval_report=broken), raise_server_exceptions=False
    )

    response = client.get(REPORT_ROUTE)
    assert response.status_code == 404
    assert isinstance(response.json().get("error"), str)


def test_a_missing_run_manifest_is_a_structured_404(data_dir):
    (Path(data_dir) / "run_manifest.json").unlink()

    client = TestClient(create_app(data_dir=data_dir))

    assert client.get("/health").status_code == 200
    response = client.get(MANIFEST_ROUTE)
    assert response.status_code == 404
    assert isinstance(response.json().get("error"), str)


# --- CORS ---------------------------------------------------------------------


def test_cors_allows_the_ui_origin(client):
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


# --- the MCP transport answers to the host the platform hands out ----------------

MCP_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}

INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "tests", "version": "1.0"},
    },
}

MISDIRECTED = 421


def mcp_initialize(client, host):
    return client.post(MCP_PATH, headers={**MCP_HEADERS, "Host": host}, json=INITIALIZE)


@contextlib.contextmanager
def mcp_client(data_dir):
    with TestClient(create_app(data_dir=data_dir)) as client:
        yield client


def test_the_mcp_transport_answers_on_the_railway_domain(data_dir, monkeypatch):
    domain = "houseaccount-production.up.railway.app"
    monkeypatch.setenv("RAILWAY_PUBLIC_DOMAIN", domain)

    with mcp_client(data_dir) as client:
        response = mcp_initialize(client, domain)

    assert response.status_code != MISDIRECTED, response.text
    assert response.status_code == 200, response.text
    assert response.headers.get("mcp-session-id"), "initialize opened no session"


def test_the_mcp_transport_answers_on_the_fly_domain(data_dir, monkeypatch):
    monkeypatch.setenv("FLY_APP_NAME", "houseaccount")

    with mcp_client(data_dir) as client:
        response = mcp_initialize(client, "houseaccount.fly.dev")

    assert response.status_code == 200, response.text


def test_the_mcp_transport_answers_on_an_explicitly_configured_host(data_dir, monkeypatch):
    monkeypatch.setenv("HOUSEACCOUNT_PUBLIC_HOST", "score.example.com, alt.example.com")

    with mcp_client(data_dir) as client:
        for host in ("score.example.com", "alt.example.com"):
            assert mcp_initialize(client, host).status_code == 200, host


def test_the_mcp_transport_still_answers_on_loopback(data_dir, monkeypatch):
    monkeypatch.delenv("RAILWAY_PUBLIC_DOMAIN", raising=False)
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    monkeypatch.delenv("HOUSEACCOUNT_PUBLIC_HOST", raising=False)

    with mcp_client(data_dir) as client:
        for host in ("localhost:8000", "127.0.0.1:8000"):
            assert mcp_initialize(client, host).status_code == 200, host


def test_the_mcp_transport_rejects_a_host_it_was_never_given(data_dir, monkeypatch):
    monkeypatch.setenv("RAILWAY_PUBLIC_DOMAIN", "houseaccount-production.up.railway.app")

    with mcp_client(data_dir) as client:
        response = mcp_initialize(client, "evil.example.com")

    assert response.status_code == MISDIRECTED, response.text
