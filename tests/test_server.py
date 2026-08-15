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

import contextlib
import json
import re
from dataclasses import fields
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from houseaccount.normalize import situs_display
from houseaccount.publish import DOORS_GEOJSON_NAME, RunManifest, publish
from houseaccount.resolve import DoorFacts, ResolveReport
from houseaccount.route import Stop, decode_share
from houseaccount.scoring.engine import score_door
from houseaccount.server.app import MCP_PATH, UI_ORIGINS, DataUnavailable, create_app
from houseaccount.server.mcp_tools import create_mcp_server
from houseaccount.server.published import Door, door_payload

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

#: The five groups R6 scores; T013 puts them on the door endpoint so the
#: evidence panel can draw the prototype's "Score breakdown" section (R8.1).
GROUP_NAMES = {"mover", "hires_out", "capacity", "need", "modifier"}

#: What the door endpoint carries beyond the published allowlist after T013.
DOOR_DETAIL_FIELDS = {"groups", "raw_total", "talk_track", "talk_track_branches"}


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


def test_the_door_endpoint_carries_the_group_math_the_breakdown_draws(client):
    """T013: the panel's "Score breakdown" needs `groups` and `raw_total` (R8.1).

    The published GeoJSON deliberately does not carry them — they are server-side
    (`doors.groups`, `doors.raw_total` in SQLite) — so the panel gets them from
    the door endpoint, which is the only place a browser can ask for one door.
    """
    result = engine_result(OAK)

    body = client.get(f"/api/door/{OAK.pams_pin}").json()

    assert DOOR_DETAIL_FIELDS <= set(body)
    assert set(body["groups"]) == GROUP_NAMES
    assert body["groups"] == dict(result.groups)
    assert body["raw_total"] == result.raw_total


def test_the_door_endpoints_group_math_reconciles_with_its_evidence(client):
    """The breakdown and the evidence list are two views of one sum (R8.1/R7.1)."""
    body = client.get(f"/api/door/{OAK.pams_pin}").json()

    assert sum(body["groups"].values()) == body["raw_total"]
    assert sum(item["points"] for item in body["evidence"]) == body["raw_total"]


def test_the_door_endpoint_carries_the_same_talk_track_the_route_does(client):
    """R7.2: one opener per door, whichever surface the rep reached it through.

    Asserted against `POST /api/route` rather than against a re-derivation, so the
    panel and the route list cannot drift into two different sentences.
    """
    body = client.get(f"/api/door/{OAK.pams_pin}").json()
    stops = client.post(
        "/api/route", json={"hours": 2, "start_point": list(START)}
    ).json()["stops"]

    planned = next(stop for stop in stops if stop["pams_pin"] == OAK.pams_pin)
    assert isinstance(body["talk_track"], str) and body["talk_track"].strip()
    assert body["talk_track"] == planned["talk_track"]


#: The engine's own permit sentence (`scoring.engine._score_hires_out`), verbatim
#: — 124 characters, and the sentence QA saw the panel cut at "…gets contracted
#: out rather than. Is now a bad time?" (ticket 021). The route side of the same
#: defect is pinned in `tests/test_route.py`; this is the panel's own path.
PERMIT_EVIDENCE = (
    "1 permit filed here in the last 24 months (Alteration) — work at this address "
    "gets contracted out rather than done in-house."
)

#: Endings that leave the homeowner waiting for the rest of the sentence.
DANGLING_ENDINGS = {"and", "gets", "out", "rather", "than", "the", "to"}


def permit_led_door():
    """One scored door whose top evidence is the long permit sentence."""
    return Door(
        pams_pin=OAK.pams_pin,
        properties={
            "PAMS_PIN": OAK.pams_pin,
            "situs": OAK.situs,
            "score": 56,
            "confidence": "medium",
            "evidence": [
                {"type": "permit_history", "points": 20, "sentence": PERMIT_EVIDENCE},
                {"type": "age", "points": 8, "sentence": "Built in 1962."},
            ],
            "exclusion_reason": None,
        },
        centroid=START,
        groups={"hires_out": 20, "need": 8},
        raw_total=28,
    )


def test_the_panel_opener_for_a_permit_led_door_never_reads_out_the_permit():
    """R7.2: the panel shows the evidence sentence in full, and the opener under
    it says none of it.

    The panel is the rep's screen; the opener is the homeowner's ears. Reciting
    "1 permit filed here in the last 24 months" at a stranger's door tells them
    we hold a file on the house, so the permit picks the *angle* — who do you
    call when something needs doing — and never the words. Ticket 021's cut
    sentence cannot recur because nothing is quoted to cut.

    Driven through `door_payload` — the function `GET /api/door/{pin}` serves —
    because the published territory this module builds has no permits in it.
    """
    payload = door_payload(permit_led_door())
    track = payload["talk_track"]
    sentences = [part.strip() for part in re.split(r"[.?!]", track) if part.strip()]

    assert PERMIT_EVIDENCE in [item["sentence"] for item in payload["evidence"]], (
        "the panel still shows the evidence in full"
    )
    for word in ["permit", "Alteration", "24 months", "contracted out"]:
        assert word not in track, f"{word!r} was read out at the door"
    for sentence in sentences:
        assert sentence.split()[-1].lower() not in DANGLING_ENDINGS, sentence


def test_the_panel_carries_the_branches_that_follow_the_opener():
    """The opener stops on one open question; what the rep says next depends on
    the answer, so the panel gets the alternatives rather than a paragraph."""
    payload = door_payload(permit_led_door())

    assert payload["talk_track"].strip().endswith("?")
    assert payload["talk_track_branches"]
    for branch in payload["talk_track_branches"]:
        assert branch["trigger"].strip() and branch["line"].strip()


def test_an_unscored_door_has_no_group_math_and_no_talk_track(client):
    """No score, no breakdown, no opener — the panel shows its exclusion instead (R9.4)."""
    body = client.get(f"/api/door/{BIRCH.pams_pin}").json()

    assert DOOR_DETAIL_FIELDS <= set(body)
    assert body["groups"] is None
    assert body["raw_total"] is None
    assert body["talk_track"] is None
    assert body["talk_track_branches"] is None


def test_the_published_geojson_is_not_widened_by_the_door_detail(client):
    """R11.1: the browser-facing artifact keeps its allowlist exactly.

    Widening the door endpoint is a server-side lookup, not a change to what the
    pipeline publishes — the map still downloads only the allowlist for 540 doors.
    """
    for feature in client.get("/api/doors.geojson").json()["features"]:
        assert set(feature["properties"]) == PUBLISHED_PROPERTIES


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


def test_every_stop_carries_the_line_the_rep_walks(client):
    """The map draws the planner's walk rather than joining centroids itself.

    Whether these legs are streets or straight lines depends on whether the
    published parcels describe a street grid — this fixture's handful of squares
    do not, and that is the fallback working. What the endpoint always owes the
    map is a line per leg, starting where the last one ended.
    """
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


def test_route_endpoint_with_no_time_returns_an_empty_route(client):
    response = client.post("/api/route", json={"hours": 0, "start_point": list(START)})

    assert response.status_code == 200
    assert response.json()["stops"] == []


def test_the_route_endpoint_replans_without_an_excluded_door(client):
    """T013 / frame 4c: "that house is vacant" — ✕ on a row re-plans without it.

    Exclusion has to reach the planner: dropping the stop in the browser would
    leave the rest of the walk detouring around a house nobody is visiting, and
    would put route ordering in JavaScript, which R10.3 forbids.
    """
    response = client.post(
        "/api/route",
        json={"hours": 2, "start_point": list(START), "exclude": [OAK.pams_pin]},
    )

    assert response.status_code == 200
    pins = [stop["pams_pin"] for stop in response.json()["stops"]]
    assert OAK.pams_pin not in pins
    assert MAPLE.pams_pin in pins


def test_excluding_re_plans_the_walk_rather_than_filtering_it(client):
    """The legs are re-measured from the new predecessor, not left as they were."""
    full = client.post("/api/route", json={"hours": 2, "start_point": list(START)}).json()
    without = client.post(
        "/api/route",
        json={"hours": 2, "start_point": list(START), "exclude": [OAK.pams_pin]},
    ).json()

    kept = next(stop for stop in full["stops"] if stop["pams_pin"] == MAPLE.pams_pin)
    replanned = next(stop for stop in without["stops"] if stop["pams_pin"] == MAPLE.pams_pin)
    assert replanned["cumulative_minutes"] != kept["cumulative_minutes"]
    assert without["total_minutes"] == without["stops"][-1]["cumulative_minutes"]


def test_the_same_exclusions_plan_the_same_walk_every_time(client):
    """Deterministic: the rep who excludes the same door twice sees one answer."""
    body = {"hours": 2, "start_point": list(START), "exclude": [OAK.pams_pin]}

    first = client.post("/api/route", json=body).json()
    second = client.post("/api/route", json=body).json()

    assert first == second


def test_an_exclusion_that_names_no_door_changes_nothing(client):
    """A stale share link or a re-excluded door must not empty the route."""
    plain = client.post("/api/route", json={"hours": 2, "start_point": list(START)}).json()
    ignored = client.post(
        "/api/route",
        json={"hours": 2, "start_point": list(START), "exclude": ["0248_99999_99999"]},
    ).json()

    assert ignored == plain


def test_excluding_every_door_returns_an_empty_route(client):
    response = client.post(
        "/api/route",
        json={
            "hours": 2,
            "start_point": list(START),
            "exclude": [door.pams_pin for door in DOORS],
        },
    )

    assert response.status_code == 200
    assert response.json()["stops"] == []


def test_a_share_token_from_the_browser_decodes_on_the_server(client):
    """R10.4's share link: the map encodes the fragment, the server has to read it.

    `zlib.compress(payload, 9)` and the browser's `CompressionStream('deflate')`
    write the same zlib format at different compression levels, so the bytes
    differ while the stream stays readable. This is the browser's spelling of the
    OAK/MAPLE route, produced by `CompressionStream`, and `decode_share` must
    take it — otherwise a link shared from a phone opens an empty route.
    """
    from_browser = "r1eJwzMDKxiDcwNDQwjDcwMDA00jFAETAygAgYmxoYxVvoGRgCAPz0Clk"

    assert decode_share(from_browser) == (
        "0248_01101_00012",
        "0248_01101_00020",
        "0248_3502_8.01",
    )


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
    """`exclude` is a list of PINs; a bare string is a 422, not a per-character filter."""
    response = client.post(
        "/api/route",
        json={"hours": 2, "start_point": list(START), "exclude": OAK.pams_pin},
    )

    assert response.status_code == 422


# --- the published artifacts the Data & Ethics page reads (T016, R12) ---------
#
# `web/ethics.html` fetches `${HOUSEACCOUNT_ARTIFACT_BASE}/eval/report.json` and
# `${...}/data/run_manifest.json`. A static host serving `web/` as the site root
# resolves neither, so the deployed page would show its honest-but-empty "no
# published run" fallback instead of the real numbers. The API serves both, at
# exactly the paths the page already asks for, so the deploy sets one base URL
# and nothing in `web/` changes.
#
# `eval/report.json` lives outside `data/`, so it is a second argument to
# `create_app` — defaulting to the repository's own `eval/report.json`, which is
# what `uvicorn ... --factory` gets. It is deliberately *not* part of the boot
# contract: `make pipeline` can have run without `make eval`, and a missing eval
# report is an ordinary state the ethics page already degrades through. Only a
# missing `data/` fails the boot.


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
    """What `uvicorn ... --factory` serves: no second path to configure."""
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


def test_the_artifact_routes_are_the_paths_the_ethics_page_fetches(data_dir):
    """Anti-drift: the page's fetches and the server's routes are one decision.

    The deploy points `HOUSEACCOUNT_ARTIFACT_BASE` at the API's `/api`, so
    `artifact('eval/report.json')` has to land on a route that exists.
    """
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
    """The ethics page is on Vercel and these bytes are on Fly (R12)."""
    origin = UI_ORIGINS[0]

    for route in (REPORT_ROUTE, MANIFEST_ROUTE):
        response = client.get(route, headers={"Origin": origin})
        assert response.headers["access-control-allow-origin"] in {origin, "*"}, route


def test_a_missing_eval_report_does_not_fail_the_boot(data_dir, tmp_path):
    """`make pipeline` without `make eval` is an ordinary state, not a crash."""
    app = create_app(data_dir=data_dir, eval_report=tmp_path / "never-ran" / "report.json")

    client = TestClient(app)
    assert client.get("/health").status_code == 200
    assert client.get("/api/doors.geojson").status_code == 200


def test_a_missing_eval_report_is_a_structured_404(data_dir, tmp_path):
    """The page's `fetch` treats a non-ok response as "no run" and says so."""
    client = TestClient(create_app(data_dir=data_dir, eval_report=tmp_path / "gone.json"))

    response = client.get(REPORT_ROUTE)

    assert response.status_code == 404
    body = response.json()
    assert isinstance(body.get("error"), str) and body["error"]
    assert isinstance(body.get("message"), str) and body["message"]


def test_a_malformed_eval_report_degrades_rather_than_500ing(data_dir, tmp_path):
    """Half a JSON file is the shape an interrupted `make eval` leaves behind."""
    broken = tmp_path / "report.json"
    broken.write_text('{"fixtures_total": 12, ', encoding="utf-8")

    client = TestClient(
        create_app(data_dir=data_dir, eval_report=broken), raise_server_exceptions=False
    )

    response = client.get(REPORT_ROUTE)
    assert response.status_code == 404
    assert isinstance(response.json().get("error"), str)


def test_a_missing_run_manifest_is_a_structured_404(data_dir):
    """`doors.geojson` and the database boot the server; the manifest does not."""
    (Path(data_dir) / "run_manifest.json").unlink()

    client = TestClient(create_app(data_dir=data_dir))

    assert client.get("/health").status_code == 200
    response = client.get(MANIFEST_ROUTE)
    assert response.status_code == 404
    assert isinstance(response.json().get("error"), str)


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


# --- the MCP transport answers to the host the platform hands out -------------
#
# The regression these guard is a deployment that looks entirely healthy and is
# half dark. `streamable_http_app`'s `host` argument defaults to `127.0.0.1`, and
# the SDK reads a loopback bind as "this is a local server" and switches DNS
# rebinding protection on with a localhost-only allowlist. Deployed, `/health` is
# green, the map draws, every REST endpoint answers — and `initialize` is a 421
# for every MCP client, because the platform's hostname is not on that list.
#
# It survived a full suite because nothing here had ever POSTed to `/mcp`; the
# mount was asserted by route introspection alone. So these drive the transport
# over HTTP with the `Host` header a real client sends.

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

#: The status the transport returns for a `Host` it will not answer to.
MISDIRECTED = 421


def mcp_initialize(client, host):
    """Open an MCP session as a real client does, from `host`."""
    return client.post(MCP_PATH, headers={**MCP_HEADERS, "Host": host}, json=INITIALIZE)


@contextlib.contextmanager
def mcp_client(data_dir):
    """A client whose lifespan has run.

    `TestClient` only fires startup when it is used as a context manager, and the
    streamable-HTTP transport refuses every request until its session manager is
    running — so a bare `TestClient(app)` fails these with a task-group error
    that has nothing to do with the header being tested.
    """
    with TestClient(create_app(data_dir=data_dir)) as client:
        yield client


def test_the_mcp_transport_answers_on_the_railway_domain(data_dir, monkeypatch):
    """Railway injects `RAILWAY_PUBLIC_DOMAIN`; the allowlist is built from it."""
    domain = "houseaccount-production.up.railway.app"
    monkeypatch.setenv("RAILWAY_PUBLIC_DOMAIN", domain)

    with mcp_client(data_dir) as client:
        response = mcp_initialize(client, domain)

    assert response.status_code != MISDIRECTED, response.text
    assert response.status_code == 200, response.text
    assert response.headers.get("mcp-session-id"), "initialize opened no session"


def test_the_mcp_transport_answers_on_the_fly_domain(data_dir, monkeypatch):
    """Fly injects `FLY_APP_NAME`, and the hostname is that plus `.fly.dev`."""
    monkeypatch.setenv("FLY_APP_NAME", "houseaccount")

    with mcp_client(data_dir) as client:
        response = mcp_initialize(client, "houseaccount.fly.dev")

    assert response.status_code == 200, response.text


def test_the_mcp_transport_answers_on_an_explicitly_configured_host(data_dir, monkeypatch):
    """The escape hatch, for a custom domain or a platform with no variable."""
    monkeypatch.setenv("HOUSEACCOUNT_PUBLIC_HOST", "score.example.com, alt.example.com")

    with mcp_client(data_dir) as client:
        for host in ("score.example.com", "alt.example.com"):
            assert mcp_initialize(client, host).status_code == 200, host


def test_the_mcp_transport_still_answers_on_loopback(data_dir, monkeypatch):
    """`make serve` is the case the protection is actually for; it keeps working."""
    monkeypatch.delenv("RAILWAY_PUBLIC_DOMAIN", raising=False)
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    monkeypatch.delenv("HOUSEACCOUNT_PUBLIC_HOST", raising=False)

    with mcp_client(data_dir) as client:
        for host in ("localhost:8000", "127.0.0.1:8000"):
            assert mcp_initialize(client, host).status_code == 200, host


def test_the_mcp_transport_rejects_a_host_it_was_never_given(data_dir, monkeypatch):
    """The protection is kept, not traded away: an unlisted host is still 421."""
    monkeypatch.setenv("RAILWAY_PUBLIC_DOMAIN", "houseaccount-production.up.railway.app")

    with mcp_client(data_dir) as client:
        response = mcp_initialize(client, "evil.example.com")

    assert response.status_code == MISDIRECTED, response.text
