"""The MCP tool surface: three tools, no more (T011, R8.1/R8.2/R10.3).

The rubric's motivating question is *"I have 2 hours in Ramsey — which 20 doors
do I knock, and what do I say?"*, and `plan_route` is the tool that answers it.
The other two answer the follow-up a rep asks at the door: what is this house's
score, and why.

**Exactly three tools.** R8.1 names `get_door_score`, `explain_score`,
`plan_route`, and the deliverable is graded on that list, so
`test_the_registry_holds_exactly_the_three_r8_tools` asserts the *sorted whole
list* off the live `MCPServer`. A missing tool and a fourth convenience tool both
fail it, which is the point — a registry test written as `"plan_route" in names`
grades nothing.

**Where the tools get their data.** `create_mcp_server(data_dir)` reads the
published artifacts and nothing else: no pipeline, no network, no county API. The
tests below build their own two-scored-plus-one-unscored `data_dir` under
`tmp_path` with the real `publish()`, so the suite is independent of whether
anyone has run `make pipeline`.

**Where the group math comes from is a scope question, not a test.** R8.1's
`explain_score` must return the per-group breakdown, and today's artifacts
publish only the final score, confidence and evidence trail. The assertion here
is therefore written against the *engine's* answer — `explain_score`'s `groups`
must equal `score_door(...).groups` — which is true whether the server reads a
column `publish` grows, reconstructs the groups from the evidence trail, or
recomputes. What is pinned is that the numbers agree with the engine; how they
get there is the implementer's call.

**Delegation, proven.** R10.3 allows exactly one route implementation. The proof
is `test_plan_route_delegates_to_the_shared_planner`: monkeypatch
`houseaccount.route.plan_route` and the tool's output changes with it. That only
holds if the tool reaches the planner through the module at call time, which is
what "no second implementation" means in practice.

**Unknown addresses are answers, not exceptions** (R8.2). A rep typing a street
name from memory gets a structured error carrying the nearest match, so the
client can offer "did you mean". Never a raised exception, never a 500.

No network, no fixtures on disk: every door below is built in-module and every
artifact is written under `tmp_path`.
"""

import asyncio
import json
from dataclasses import fields
from datetime import date, datetime, timezone

import pytest

from houseaccount import route as route_module
from houseaccount.normalize import situs_display
from houseaccount.publish import RunManifest, publish
from houseaccount.resolve import DoorFacts, ResolveReport
from houseaccount.route import Route, Stop
from houseaccount.scoring.engine import score_door
from houseaccount.server.mcp_tools import create_mcp_server

AS_OF = date(2026, 8, 14)
RUN_AT = datetime(2026, 8, 14, 6, 30, 0, tzinfo=timezone.utc)

TERRITORY_MEDIAN_VALUE = 700000.0
ACS_DUAL_INCOME_THRESHOLD = 0.35

#: The territory centre, (lon, lat) — GeoJSON order, as everywhere in this codebase.
START = (-74.1560, 41.0447)

#: The five groups R6 scores and R8.1 requires `explain_score` to break out.
GROUP_NAMES = {"mover", "hires_out", "capacity", "need", "modifier"}

#: The R7.1 evidence contract, as published.
EVIDENCE_FIELDS = {"type", "points", "sentence", "source", "retrieved", "imagery"}

#: A stop's shape is the planner's, not a second one invented at the tool layer.
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
    """One resolved door at a known point, ready to be scored and published."""
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


#: A door that just changed hands — the high scorer, and the one every address
#: spelling below has to land on.
OAK = facts(
    "0248_01101_00012",
    "12 OAK ST",
    deed=date(2026, 7, 20),
    yr_constr=1962,
    net_value=980000.0,
    point=START,
)

#: A long-tenured door 300 m up the road: scoreable, routable, lower.
MAPLE = facts(
    "0248_01101_00020",
    "20 MAPLE AVE",
    deed=date(2001, 3, 4),
    yr_constr=1998,
    net_value=520000.0,
    point=north_of(START, 300.0),
)

#: The R9.4 gap: county record carries nothing, so no score and no geometry.
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
    """What the real engine says about this door — the only source of truth."""
    return score_door(
        door.to_score_input(
            as_of=AS_OF,
            territory_median_value=TERRITORY_MEDIAN_VALUE,
            acs_dual_income_threshold=ACS_DUAL_INCOME_THRESHOLD,
        )
    )


@pytest.fixture
def data_dir(tmp_path):
    """A published two-scored-plus-one-unscored territory, written by `publish`."""
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
def server(data_dir):
    return create_mcp_server(data_dir=data_dir)


# --- calling a tool -----------------------------------------------------------


def call(server, name, **arguments):
    """Invoke a tool the way a client does, and hand back the raw result."""
    return asyncio.run(server.call_tool(name, arguments))


def payload_of(result):
    """The tool's JSON return value, however the SDK chose to carry it."""
    if result.structured_content is not None:
        return result.structured_content
    return json.loads(result.content[0].text)


def invoke(server, name, **arguments):
    return payload_of(call(server, name, **arguments))


def tool_names(server):
    return sorted(tool.name for tool in asyncio.run(server.list_tools()))


# --- the registry -------------------------------------------------------------


def test_the_registry_holds_exactly_the_three_r8_tools(server):
    """The whole list, sorted: a missing tool and a fourth one both fail (R8.1)."""
    assert tool_names(server) == ["explain_score", "get_door_score", "plan_route"]


# --- get_door_score -----------------------------------------------------------


def test_get_door_score_returns_score_confidence_and_evidence(server):
    result = engine_result(OAK)

    payload = invoke(server, "get_door_score", address="12 OAK ST")

    assert {"score", "confidence", "evidence"} <= set(payload)
    assert payload["score"] == result.score
    assert isinstance(payload["score"], int)
    assert payload["confidence"] == result.confidence
    assert [item["sentence"] for item in payload["evidence"]] == [
        item.sentence for item in result.evidence
    ]


def test_get_door_score_evidence_carries_the_r7_fields(server):
    payload = invoke(server, "get_door_score", address="12 OAK ST")

    assert payload["evidence"], "a scored door explains itself"
    for item in payload["evidence"]:
        assert set(item) == EVIDENCE_FIELDS


def test_get_door_score_on_an_unscored_door_explains_the_exclusion(server):
    """R9.4: the gap in "537 of 540" answers, rather than erroring."""
    payload = invoke(server, "get_door_score", address="99 BIRCH LN")

    assert payload["score"] is None
    assert payload["evidence"] == []
    assert isinstance(payload["exclusion_reason"], str) and payload["exclusion_reason"]


# --- explain_score ------------------------------------------------------------


def test_explain_score_returns_the_full_group_breakdown(server):
    """R8.1's "incl. group math": the five groups, the unclamped total, the score.

    Asserted against the engine's own answer, so this holds however the server
    obtains the breakdown — see the module docstring's scope note.
    """
    result = engine_result(OAK)

    payload = invoke(server, "explain_score", address="12 OAK ST")

    assert set(payload["groups"]) == GROUP_NAMES
    assert payload["groups"] == dict(result.groups)
    assert payload["raw_total"] == result.raw_total
    assert payload["score"] == result.score


def test_explain_score_reconciles_groups_evidence_and_total(server):
    """The breakdown has to add up, or it explains nothing."""
    payload = invoke(server, "explain_score", address="20 MAPLE AVE")

    assert sum(payload["groups"].values()) == payload["raw_total"]
    assert sum(item["points"] for item in payload["evidence"]) == payload["raw_total"]
    for item in payload["evidence"]:
        assert set(item) == EVIDENCE_FIELDS


# --- address resolution (R8.2) ------------------------------------------------


@pytest.mark.parametrize(
    "spelling",
    ["12 oak st", "12 OAK STREET", "12 Oak St.", "12 OAK ST, Ramsey NJ 07446"],
)
@pytest.mark.parametrize("tool", ["get_door_score", "explain_score"])
def test_address_spellings_resolve_through_the_normalizer_to_one_door(server, tool, spelling):
    """R8.2: the same normalizer the join uses, so a rep's typing is not a lookup key."""
    payload = invoke(server, tool, address=spelling)

    assert payload.get("error") is None
    assert payload["pams_pin"] == OAK.pams_pin


@pytest.mark.parametrize("tool", ["get_door_score", "explain_score"])
def test_a_near_miss_address_returns_a_structured_error_with_the_nearest_match(server, tool):
    """R8.2: "did you mean", not a traceback."""
    result = call(server, tool, address="12 OAK STRET")

    assert result.is_error is False, "an unknown address is an answer, not an exception"

    payload = payload_of(result)
    assert isinstance(payload["error"], str) and payload["error"]
    assert isinstance(payload["message"], str) and payload["message"]
    assert payload["suggestion"]["pams_pin"] == OAK.pams_pin
    assert isinstance(payload["suggestion"]["address"], str)


@pytest.mark.parametrize("tool", ["get_door_score", "explain_score"])
def test_an_address_in_another_town_keeps_the_same_error_shape(server, tool):
    """Nothing is close, so `suggestion` may be null — the shape does not change."""
    result = call(server, tool, address="4400 NOWHERE PKWY")

    assert result.is_error is False
    payload = payload_of(result)
    assert set(payload) >= {"error", "message", "suggestion"}
    assert isinstance(payload["error"], str) and payload["error"]


# --- plan_route ---------------------------------------------------------------


def test_plan_route_returns_ordered_stops_each_with_a_talk_track(server):
    """The graded question, answered: which doors, in what order, and what to say."""
    payload = invoke(server, "plan_route", hours=2.0, start_point=list(START))

    stops = payload["stops"]
    assert [stop["pams_pin"] for stop in stops] == [OAK.pams_pin, MAPLE.pams_pin]
    assert [stop["cumulative_minutes"] for stop in stops] == sorted(
        stop["cumulative_minutes"] for stop in stops
    )
    assert payload["total_minutes"] == stops[-1]["cumulative_minutes"]
    assert isinstance(payload["estimate_disclosure"], str) and payload["estimate_disclosure"]

    for stop in stops:
        assert set(stop) == STOP_FIELDS
        assert isinstance(stop["talk_track"], str) and stop["talk_track"].strip()
        assert stop["talk_track_branches"], "what to say after they answer"
    # Not per-door: the opener is authored, and two doors on one street opening
    # on one angle are meant to be opened the same way. These two differ because
    # they stand on different streets.
    assert stops[0]["talk_track"] != stops[1]["talk_track"]


def test_plan_route_honours_the_max_doors_cap(server):
    payload = invoke(server, "plan_route", hours=2.0, start_point=list(START), max_doors=1)

    assert [stop["pams_pin"] for stop in payload["stops"]] == [OAK.pams_pin]


def test_plan_route_never_offers_an_unscored_or_geometryless_door(server):
    payload = invoke(server, "plan_route", hours=8.0, start_point=list(START))

    assert BIRCH.pams_pin not in {stop["pams_pin"] for stop in payload["stops"]}


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
    assert {door.pams_pin for door in calls[0]["doors"]} >= {OAK.pams_pin, MAPLE.pams_pin}
