"""The MCP tool surface on the V2 score contract (ticket 105, plan R27/R30, R8.1/R8.2/R10.3).

The rubric's motivating question is *"I have 2 hours in Ramsey — which 20 doors
do I knock, and what do I say?"*, and `plan_route` is the tool that answers it.
The other two answer the follow-up a rep asks at the door: what is this house's
score, and why.

**Exactly three tools, names unchanged** (PRD): `get_door_score`,
`explain_score`, `plan_route` — the whole sorted list is asserted off the live
server, so a missing tool and a fourth both fail.

**What changed under V2 (R27/R30).** The tools answer from V2-shaped published
records: `score_contract_version`, `categories{project,capacity,fit}`, `base`,
`mover{eligible,days_since_move,strength}`, `mover_lift`, `rental_modifier`,
`adjustment`, `score`, `confidence`, `data_gaps`, and evidence items of
`{type, points, reason}`. The V1 vocabulary — `groups`, `raw_total`, the five
group names, "hires_out", "need", "modifier" — may appear in no payload and no
tool description string: an LLM reads the descriptions to decide what a score
means, so a description still narrating V1 arithmetic is a live V1 surface.

**Fixtures are synthetic published records.** Ticket 103 owns `publish()`; this
module writes small hand-consistent V2 artifacts (doors.geojson + sqlite +
manifest) straight to `tmp_path`, so the server surface is tested against the
published *contract*, not against whichever writer produced it. Every scored
record reconciles by construction: sum(evidence points) == base + mover_lift +
rental_modifier, and score == that + adjustment (R7 at the tool boundary).

No network, no fixtures on disk beyond `tmp_path`.
"""

import asyncio
import json
import sqlite3
from datetime import date

import pytest

from houseaccount import route as route_module
from houseaccount.route import Route, RouteDoor, Stop
from houseaccount.server.mcp_tools import create_mcp_server

AS_OF = date(2026, 8, 14)

#: The territory centre, (lon, lat) — GeoJSON order, as everywhere in this codebase.
START = (-74.1560, 41.0447)

#: The V2 category names (plan R2). The only subtotal vocabulary any surface may use.
CATEGORY_NAMES = {"project", "capacity", "fit"}

#: The V2 envelope fields every scored answer must expose (R30).
V2_DOOR_FIELDS = {
    "score_contract_version",
    "categories",
    "base",
    "mover",
    "mover_lift",
    "rental_modifier",
    "adjustment",
    "score",
    "confidence",
    "data_gaps",
}

#: V1 vocabulary that may appear in no served payload (R27/R28).
FORBIDDEN_KEYS = {"groups", "raw_total"}

#: V1 vocabulary that may appear in no tool description string (R30).
FORBIDDEN_DESCRIPTION_WORDS = ["hires_out", "raw_total", "five group", "group subtotals"]

#: Unspeakable at the door (PRD R7.2.1): never a chip, never in a talk track.
UNSPEAKABLE_TYPES = {"capacity_territory_percentile", "capacity_local_relative_value"}

#: The published per-door property allowlist, V2-shaped (R11.1/R27/R30).
FEATURE_PROPERTIES = {
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

EXCLUSION_REASON = "parcel record incomplete in county data"


# --- synthetic V2 published records --------------------------------------------


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


OAK_PIN = "0248_01101_00012"
MAPLE_PIN = "0248_01101_00020"
CEDAR_PIN = "0248_01101_00031"
BIRCH_PIN = "0248_01101_00099"

OAK_SITUS = "12 OAK ST, Ramsey NJ 07446"
MAPLE_SITUS = "20 MAPLE AVE, Ramsey NJ 07446"
CEDAR_SITUS = "31 CEDAR CT, Ramsey NJ 07446"
BIRCH_SITUS = "99 BIRCH LN, Ramsey NJ 07446"

#: The fresh mover: base 39 + full mover blend. 39 + 55.875 = 94.875 -> 95.
OAK_PROPERTIES = scored_properties(
    OAK_PIN,
    OAK_SITUS,
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

#: The unspeakable-led door: its two highest-point entries are the percentile
#: and the local ratio, so the chip must fall through to `fit_lot` and the talk
#: track to the angle that says nothing (PRD R7.2.1).
MAPLE_PROPERTIES = scored_properties(
    MAPLE_PIN,
    MAPLE_SITUS,
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

#: The demoted rental with the clamp: 13 - 25 = -12, clamped to 0, adjustment 12.
#: No geometry, so it can never join a route — but it must still reconcile.
CEDAR_PROPERTIES = scored_properties(
    CEDAR_PIN,
    CEDAR_SITUS,
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
    "situs": BIRCH_SITUS,
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

SCORED_PROPERTIES = [OAK_PROPERTIES, MAPLE_PROPERTIES, CEDAR_PROPERTIES]

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
    """A published V2 run, written by hand: doors.geojson + sqlite + manifest."""
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


@pytest.fixture
def data_dir(tmp_path):
    return write_data_dir(tmp_path / "data")


@pytest.fixture
def server(data_dir):
    return create_mcp_server(data_dir=data_dir)


# --- calling a tool -----------------------------------------------------------


def call(server, name, **arguments):
    return asyncio.run(server.call_tool(name, arguments))


def payload_of(result):
    if result.structured_content is not None:
        return result.structured_content
    return json.loads(result.content[0].text)


def invoke(server, name, **arguments):
    return payload_of(call(server, name, **arguments))


def tool_names(server):
    return sorted(tool.name for tool in asyncio.run(server.list_tools()))


def descriptions(server):
    return {tool.name: tool.description or "" for tool in asyncio.run(server.list_tools())}


def every_key(payload):
    if isinstance(payload, dict):
        for key, value in payload.items():
            yield key
            yield from every_key(value)
    elif isinstance(payload, list):
        for item in payload:
            yield from every_key(item)


# --- the registry -------------------------------------------------------------


def test_the_registry_holds_exactly_the_three_tools_with_their_unchanged_names(server):
    """PRD: the tool *names* survive the V2 cutover unchanged — the whole
    sorted list, so a missing tool and a fourth one both fail."""
    assert tool_names(server) == ["explain_score", "get_door_score", "plan_route"]


# --- descriptions are product (R30) ---------------------------------------------


def test_no_tool_description_narrates_the_dead_v1_contract(server):
    """An LLM reads these strings; a description still saying `raw_total` or
    `hires_out` is a V1 surface alive in production."""
    for name, description in descriptions(server).items():
        lowered = description.lower()
        for forbidden in FORBIDDEN_DESCRIPTION_WORDS:
            assert forbidden not in lowered, f"{forbidden!r} in {name} description"


def test_explain_score_description_names_the_v2_categories(server):
    lowered = descriptions(server)["explain_score"].lower()

    for word in ["project", "capacity", "fit"]:
        assert word in lowered
    assert "mover" in lowered
    assert "rental" in lowered


# --- get_door_score -----------------------------------------------------------


def test_get_door_score_returns_the_published_v2_answer(server):
    payload = invoke(server, "get_door_score", address="12 OAK ST")

    assert payload["pams_pin"] == OAK_PIN
    assert payload["score"] == 95
    assert isinstance(payload["score"], int)
    assert payload["confidence"] == "normal"
    assert payload["score_contract_version"] == "v2"
    assert [item["type"] for item in payload["evidence"]] == [
        item["type"] for item in OAK_PROPERTIES["evidence"]
    ]


def test_get_door_score_evidence_items_are_type_points_reason(server):
    payload = invoke(server, "get_door_score", address="12 OAK ST")

    assert payload["evidence"], "a scored door explains itself"
    for item in payload["evidence"]:
        assert {"type", "points", "reason"} <= set(item) <= {"type", "points", "reason", "imagery"}
        assert "sentence" not in item and "source" not in item


def test_get_door_score_on_an_unscored_door_explains_the_exclusion(server):
    payload = invoke(server, "get_door_score", address="99 BIRCH LN")

    assert payload["score"] is None
    assert payload["evidence"] == []
    assert payload["exclusion_reason"] == EXCLUSION_REASON
    assert payload["score_contract_version"] == "v2"


def test_no_v1_vocabulary_in_any_get_door_score_payload(server):
    payload = invoke(server, "get_door_score", address="12 OAK ST")

    assert set(every_key(payload)) & FORBIDDEN_KEYS == set()


# --- explain_score ------------------------------------------------------------


def test_explain_score_returns_the_full_v2_breakdown(server):
    payload = invoke(server, "explain_score", address="12 OAK ST")

    assert V2_DOOR_FIELDS <= set(payload)
    assert set(payload["categories"]) == CATEGORY_NAMES
    assert payload["categories"] == OAK_PROPERTIES["categories"]
    assert payload["base"] == 39
    assert payload["mover"] == OAK_PROPERTIES["mover"]
    assert payload["mover_lift"] == pytest.approx(55.875)
    assert payload["rental_modifier"] == 0
    assert payload["adjustment"] == pytest.approx(0.125)
    assert payload["score"] == 95
    assert payload["data_gaps"] == []


def test_explain_score_carries_no_v1_group_math(server):
    payload = invoke(server, "explain_score", address="12 OAK ST")

    assert set(every_key(payload)) & FORBIDDEN_KEYS == set()


@pytest.mark.parametrize(
    "address", ["12 OAK ST", "20 MAPLE AVE", "31 CEDAR CT"], ids=["mover", "flat", "rental-clamped"]
)
def test_explain_score_reconciles_evidence_to_the_displayed_score(server, address):
    """R7 at the tool boundary: base subtotals + mover_lift + rental_modifier ==
    the evidence sum, and adding the adjustment lands exactly on the score —
    including through the rental demotion and the 0-clamp."""
    payload = invoke(server, "explain_score", address=address)

    assert sum(payload["categories"].values()) == payload["base"]
    trail_sum = sum(item["points"] for item in payload["evidence"])
    assert trail_sum == pytest.approx(
        payload["base"] + payload["mover_lift"] + payload["rental_modifier"]
    )
    assert payload["score"] == pytest.approx(trail_sum + payload["adjustment"])


def test_explain_score_on_the_unscored_door_keeps_the_v2_shape(server):
    payload = invoke(server, "explain_score", address="99 BIRCH LN")

    assert payload["score"] is None
    assert payload["categories"] is None
    assert payload["exclusion_reason"] == EXCLUSION_REASON
    assert payload["score_contract_version"] == "v2"


# --- address resolution (R8.2, unchanged behaviour) ------------------------------


@pytest.mark.parametrize(
    "spelling",
    ["12 oak st", "12 OAK STREET", "12 Oak St.", "12 OAK ST, Ramsey NJ 07446"],
)
@pytest.mark.parametrize("tool", ["get_door_score", "explain_score"])
def test_address_spellings_resolve_through_the_normalizer_to_one_door(server, tool, spelling):
    payload = invoke(server, tool, address=spelling)

    assert payload.get("error") is None
    assert payload["pams_pin"] == OAK_PIN


@pytest.mark.parametrize("tool", ["get_door_score", "explain_score"])
def test_a_near_miss_address_returns_a_structured_error_with_the_nearest_match(server, tool):
    result = call(server, tool, address="12 OAK STRET")

    assert result.is_error is False, "an unknown address is an answer, not an exception"

    payload = payload_of(result)
    assert isinstance(payload["error"], str) and payload["error"]
    assert isinstance(payload["message"], str) and payload["message"]
    assert payload["suggestion"]["pams_pin"] == OAK_PIN
    assert isinstance(payload["suggestion"]["address"], str)


@pytest.mark.parametrize("tool", ["get_door_score", "explain_score"])
def test_an_address_in_another_town_keeps_the_same_error_shape(server, tool):
    result = call(server, tool, address="4400 NOWHERE PKWY")

    assert result.is_error is False
    payload = payload_of(result)
    assert set(payload) >= {"error", "message", "suggestion"}
    assert isinstance(payload["error"], str) and payload["error"]


# --- plan_route ---------------------------------------------------------------


def test_plan_route_returns_ordered_stops_each_with_a_talk_track_and_chip(server):
    payload = invoke(server, "plan_route", hours=2.0, start_point=list(START))

    stops = payload["stops"]
    assert [stop["pams_pin"] for stop in stops] == [OAK_PIN, MAPLE_PIN]
    assert payload["total_minutes"] == stops[-1]["cumulative_minutes"]
    assert isinstance(payload["estimate_disclosure"], str) and payload["estimate_disclosure"]
    for stop in stops:
        assert isinstance(stop["talk_track"], str) and stop["talk_track"].strip()
        assert stop["talk_track_branches"], "what to say after they answer"
        assert "reason_chip" in stop


def test_the_route_payload_carries_the_score_contract_version(server):
    """R30: a shared route payload names the contract its scores came from."""
    payload = invoke(server, "plan_route", hours=2.0, start_point=list(START))

    assert payload["score_contract_version"] == "v2"


def test_route_aggregates_come_from_the_displayed_v2_scores(server):
    """R30: aggregates are arithmetic over the published scores, nothing else."""
    payload = invoke(server, "plan_route", hours=2.0, start_point=list(START))

    stops = payload["stops"]
    assert [stop["score"] for stop in stops] == [95, 22]
    assert payload["average_score"] == pytest.approx((95 + 22) / 2)


def test_an_empty_route_has_no_average(server):
    payload = invoke(server, "plan_route", hours=0, start_point=list(START))

    assert payload["stops"] == []
    assert payload["average_score"] is None
    assert payload["score_contract_version"] == "v2"


def test_the_chip_for_each_stop_follows_the_selection_rule(server):
    """OAK's top entry is `mover_recency` (55.875); MAPLE's two top entries are
    unspeakable, so its chip falls through to `fit_lot` (PRD R7.2.1)."""
    stops = invoke(server, "plan_route", hours=2.0, start_point=list(START))["stops"]
    chips = {stop["pams_pin"]: stop["reason_chip"] for stop in stops}

    assert chips[OAK_PIN] == "mover_recency"
    assert chips[MAPLE_PIN] == "fit_lot"


def test_no_chip_and_no_talk_track_ever_names_an_unspeakable_signal(server):
    stops = invoke(server, "plan_route", hours=8.0, start_point=list(START))["stops"]

    for stop in stops:
        assert stop["reason_chip"] not in UNSPEAKABLE_TYPES
        lowered = stop["talk_track"].lower()
        for word in ["percentile", "assessed", "median", "comparable"]:
            assert word not in lowered, f"{word!r} spoken at {stop['pams_pin']}"


def test_plan_route_honours_the_max_doors_cap(server):
    payload = invoke(server, "plan_route", hours=2.0, start_point=list(START), max_doors=1)

    assert [stop["pams_pin"] for stop in payload["stops"]] == [OAK_PIN]


def test_plan_route_never_offers_an_unscored_or_geometryless_door(server):
    payload = invoke(server, "plan_route", hours=8.0, start_point=list(START))

    offered = {stop["pams_pin"] for stop in payload["stops"]}
    assert BIRCH_PIN not in offered, "unscored"
    assert CEDAR_PIN not in offered, "no geometry"


def test_plan_route_with_no_time_returns_an_empty_route_not_an_error(server):
    result = call(server, "plan_route", hours=0, start_point=list(START))

    assert result.is_error is False
    payload = payload_of(result)
    assert payload["stops"] == []
    assert payload["total_minutes"] == 0.0
    assert isinstance(payload["estimate_disclosure"], str) and payload["estimate_disclosure"]


def test_plan_route_delegates_to_the_shared_planner(server, monkeypatch):
    """R10.3: one implementation. Patch the planner and the tool changes with it."""
    calls = []

    def fake_plan_route(doors, hours, start_point, max_doors=None, network=None):
        calls.append(
            {
                "doors": doors,
                "hours": hours,
                "start_point": start_point,
                "max_doors": max_doors,
                "network": network,
            }
        )
        return Route(
            stops=(
                Stop(
                    pams_pin="STUB_PIN",
                    address="1 STUB ST, Ramsey NJ 07446",
                    score=99,
                    walk_minutes=1.5,
                    cumulative_minutes=1.5,
                    talk_track="stub opener",
                    reason_chip="project_active",
                ),
            ),
            total_minutes=1.5,
        )

    monkeypatch.setattr(route_module, "plan_route", fake_plan_route)

    payload = invoke(server, "plan_route", hours=2.0, start_point=list(START), max_doors=20)

    assert [stop["pams_pin"] for stop in payload["stops"]] == ["STUB_PIN"]
    assert payload["total_minutes"] == 1.5
    assert len(calls) == 1
    assert calls[0]["hours"] == 2.0
    assert tuple(calls[0]["start_point"]) == START
    assert calls[0]["max_doors"] == 20
    assert {door.pams_pin for door in calls[0]["doors"]} >= {OAK_PIN, MAPLE_PIN}
    assert all(isinstance(door, RouteDoor) for door in calls[0]["doors"])
