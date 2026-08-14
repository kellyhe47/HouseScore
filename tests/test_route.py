"""Route planning — one implementation, shared by MCP and the Map UI (T010, R10.1–R10.3).

The product question, verbatim: *"I have 2 hours in Ramsey — which 20 doors do I
knock, and what do I say?"*

**What a "door" is here.** The planner is called from three places that agree on
nothing: the pipeline holds `DoorFacts`, the published `doors.geojson` holds
GeoJSON features, the MCP tool holds JSON arguments. So the planner owns a small
input type of its own — `RouteDoor(pams_pin, address, score, centroid,
top_evidence)` — and everything else adapts into it. R10.3 says the MCP server
and the UI share this module; a planner that imported `houseaccount.resolve`
would drag the whole harvest/ACS/permits stack into a web process that only
needs to sort points on a map. The adapter for the pipeline is therefore
duck-typed (`route_door_from_facts`, reading `pams_pin`/`situs`/`centroid` off
anything that has them — a real `DoorFacts` does), and the server builds a
`RouteDoor` straight from a GeoJSON feature's properties. That is pinned by
`test_the_planner_does_not_import_the_pipeline`.

**Where the talk track comes from.** R7.2 makes it a one-sentence opener
generated from the door's *top evidence item*, presentation-layer only: it never
feeds the score and is never asserted in a golden fixture. The planner receives
the top evidence item's **sentence as a plain string** on `RouteDoor.top_evidence`
— not an `EvidenceItem`, not a callback — because the caller has already scored
the door and holds the item, and a plain string keeps the planner free of the
scoring stack for a value it only ever renders. `talk_track_for(door)` generates
the opener and the planner stamps it on every `Stop`. Tests assert its *shape*
(non-empty, one line, door-specific, differs per door) — never its prose.

**Where `exclude` lives.** On `Route`, as a method: the rep excludes a door from
a route they are looking at ("that house is vacant"), and `Route.exclude(pins)`
re-plans — it does not filter. `test_exclude_replans_rather_than_filtering` is
what makes that distinction real.

**Where garbage share links go.** `decode_share` returns `()` for anything it
cannot parse rather than raising, so a mangled URL fragment opens an empty
route instead of a 500. That requires the encoding to be self-identifying.

No network, no fixtures on disk: every door below is built in-module, including
the 540-door performance grid.
"""

import inspect
import math
import random
import time
from dataclasses import dataclass, replace

import pytest

from houseaccount import route as route_module
from houseaccount.normalize import situs_display
from houseaccount.resolve import DoorFacts
from houseaccount.route import (
    Route,
    RouteDoor,
    Stop,
    decode_share,
    encode_share,
    plan_route,
    route_door_from_facts,
    talk_track_for,
)

#: The territory centre, (lon, lat) — GeoJSON order, as everywhere in this codebase.
START = (-74.1560, 41.0447)

#: A start point on the other side of the continent. Used to prove that a rep
#: standing nowhere near Ramsey gets an empty route rather than an exception.
FAR_AWAY = (-122.4194, 37.7749)

#: 2*pi*R/360 with R = 6371008.8 m — metres per degree of latitude. Only used to
#: place synthetic doors at known distances; the planner's own geodesy is pinned
#: independently by `test_walk_minutes_are_haversine_times_detour_at_three_mph`.
METRES_PER_DEGREE_LAT = 111194.9266

#: Metres a rep covers per minute of *walking time* once the detour factor is
#: applied: 3 mph = 80.4672 m/min, divided by 1.3. Used only to hand-check the
#: worked example in the comment table below.
METRES_PER_WALK_MINUTE = (3.0 * 1609.344 / 60.0) / 1.3


# --- builders -----------------------------------------------------------------


def offset(origin, *, north_m=0.0, east_m=0.0):
    """A point `north_m`/`east_m` from `origin`, in (lon, lat) order."""
    lon, lat = origin
    dlat = north_m / METRES_PER_DEGREE_LAT
    dlon = east_m / (METRES_PER_DEGREE_LAT * math.cos(math.radians(lat)))
    return (lon + dlon, lat + dlat)


def door(pin, *, north_m=0.0, east_m=0.0, score=50, centroid=..., top_evidence=None, street="FAWN HILL RD"):
    """One candidate door, placed at a known offset from `START`."""
    number = int(pin.rsplit("_", 1)[-1])
    return RouteDoor(
        pams_pin=pin,
        address=situs_display(f"{number} {street}", "07446"),
        score=score,
        centroid=offset(START, north_m=north_m, east_m=east_m) if centroid is ... else centroid,
        top_evidence=top_evidence,
    )


def pin(n):
    return f"0248_00101_{n:05d}"


def pins_of(route):
    return [stop.pams_pin for stop in route.stops]


# --- the worked example -------------------------------------------------------
#
# Four doors on one north-south line out of START. Walk-minutes = metres / 61.898
# (3 mph, detour 1.3). Greedy picks the best score-per-walk-minute from wherever
# the rep is now, so the answer is none of "nearest first", "highest score
# first", or input order:
#
#   door  offset  score | step 1 (from 0m)   step 2 (from 300m)  step 3 (150m)  step 4 (100m)
#   NEAR   100 m     10 | 10/1.616 =  6.19   10/3.231 =  3.10    10/0.808=12.38  —
#   MIDDLE 150 m     30 | 30/2.423 = 12.38   30/2.423 = 12.38 *  —               —
#   BEST   300 m     90 | 90/4.847 = 18.57 * —                   —               —
#   FAR    600 m     40 | 40/9.693 =  4.13   40/4.847 =  8.25    40/7.270= 5.50  40/8.078=4.95 *
#
# Visit order: BEST, MIDDLE, NEAR, FAR — cumulative 4.847, 7.270, 8.078, 16.156.
# PINs run 1..4 in *distance* order, so a planner that merely sorted by PIN, by
# distance, or by score would fail this test.

PIN_NEAR, PIN_MIDDLE, PIN_BEST, PIN_FAR = pin(1), pin(2), pin(3), pin(4)

WORKED_DOORS = [
    door(PIN_NEAR, north_m=100, score=10),
    door(PIN_MIDDLE, north_m=150, score=30),
    door(PIN_BEST, north_m=300, score=90),
    door(PIN_FAR, north_m=600, score=40),
]

WORKED_ORDER = [PIN_BEST, PIN_MIDDLE, PIN_NEAR, PIN_FAR]
WORKED_CUMULATIVE = [4.8467, 7.2701, 8.0778, 16.1557]

UNSCORED_DOORS = [replace(candidate, score=None) for candidate in WORKED_DOORS]


def worked_route(**kwargs):
    settings = {"hours": 2.0, "start_point": START}
    settings.update(kwargs)
    return plan_route(WORKED_DOORS, **settings)


# --- the 540-door territory ---------------------------------------------------


def grid_doors(count=540, *, spacing_m=40.0, columns=24):
    """`count` doors on a ~40 m lattice around START — the shape of the real
    territory (540 parcels in about a kilometre) without shipping a fixture."""
    doors = []
    for index in range(count):
        row, column = divmod(index, columns)
        doors.append(
            RouteDoor(
                pams_pin=pin(index),
                address=situs_display(f"{index} GRID ST", "07446"),
                # 20..100, spread deterministically so no two neighbours match.
                score=20 + (index * 37) % 81,
                centroid=offset(
                    START,
                    north_m=(row - count / columns / 2) * spacing_m,
                    east_m=(column - columns / 2) * spacing_m,
                ),
            )
        )
    return doors


# --- shape of the result ------------------------------------------------------


def test_plan_route_returns_a_route():
    assert isinstance(worked_route(), Route)


def test_every_stop_is_a_stop():
    assert all(isinstance(stop, Stop) for stop in worked_route().stops)


@pytest.mark.parametrize(
    "attribute, expected",
    [
        ("pams_pin", PIN_BEST),
        ("address", situs_display("3 FAWN HILL RD", "07446")),
        ("score", 90),
    ],
)
def test_a_stop_carries_the_door_it_came_from(attribute, expected):
    assert getattr(worked_route().stops[0], attribute) == expected


def test_the_route_discloses_that_walking_times_are_estimates():
    """R10.2: the UI renders this string; it must come from the module, not the UI."""
    disclosure = worked_route().estimate_disclosure.lower()

    assert "straight-line" in disclosure
    assert "estimate" in disclosure


def test_total_minutes_is_the_last_cumulative_offset():
    planned = worked_route()

    assert planned.total_minutes == pytest.approx(planned.stops[-1].cumulative_minutes)


def test_cumulative_minutes_is_the_running_sum_of_the_legs():
    running = 0.0
    for stop in worked_route().stops:
        running += stop.walk_minutes
        assert stop.cumulative_minutes == pytest.approx(running)


def test_plan_route_keeps_the_signature_the_mcp_tool_binds_to():
    parameters = list(inspect.signature(plan_route).parameters)

    assert parameters[:4] == ["doors", "hours", "start_point", "max_doors"]
    assert inspect.signature(plan_route).parameters["max_doors"].default is None


# --- greedy selection ---------------------------------------------------------


def test_greedy_visits_the_worked_example_in_score_per_minute_order():
    assert pins_of(worked_route()) == WORKED_ORDER


def test_the_worked_example_arrives_at_the_hand_checked_offsets():
    planned = worked_route()

    assert [stop.cumulative_minutes for stop in planned.stops] == pytest.approx(
        WORKED_CUMULATIVE, rel=1e-3
    )


def test_the_first_leg_is_measured_from_the_start_point_not_from_a_door():
    """4.847 min is START -> BEST (300 m). A planner that started at door one
    would report 0.0 here."""
    assert worked_route().stops[0].walk_minutes == pytest.approx(4.8467, rel=1e-3)


def test_walk_minutes_are_haversine_times_detour_at_three_mph():
    """1000 m due north: 1000 * 1.3 / (3 mph in m/min) = 16.156 minutes."""
    (stop,) = plan_route([door(pin(1), north_m=1000)], hours=2.0, start_point=START).stops

    assert stop.walk_minutes == pytest.approx(1000 * 1.3 / (3.0 * 1609.344 / 60.0), rel=1e-3)


def test_the_walking_model_is_exposed_as_named_constants():
    """Read off the module rather than imported, so a missing constant fails
    this test alone instead of the whole module."""
    assert route_module.DETOUR_FACTOR == pytest.approx(1.3)
    assert route_module.WALKING_SPEED_MPH == pytest.approx(3.0)


def test_a_door_at_the_rep_feet_costs_no_walking_time():
    planned = plan_route([door(pin(1), north_m=0)], hours=2.0, start_point=START)

    assert planned.stops[0].walk_minutes == pytest.approx(0.0, abs=1e-9)


# --- the hour budget ----------------------------------------------------------


def test_the_budget_cuts_the_route_short():
    """9 minutes buys BEST, MIDDLE and NEAR (8.078 cumulative); FAR needs 16.156."""
    assert pins_of(worked_route(hours=9 / 60)) == [PIN_BEST, PIN_MIDDLE, PIN_NEAR]


def test_no_stop_ever_exceeds_the_budget():
    planned = plan_route(grid_doors(), hours=2.0, start_point=START)

    assert planned.stops
    assert planned.stops[-1].cumulative_minutes <= 2.0 * 60


def test_a_budget_too_small_for_any_door_returns_an_empty_route_rather_than_raising():
    """0.6 minutes; the nearest door is 1.616 minutes away."""
    planned = worked_route(hours=0.01)

    assert planned.stops == ()
    assert planned.total_minutes == pytest.approx(0.0)


@pytest.mark.parametrize(
    "label, doors, hours, start_point, max_doors",
    [
        ("no candidate doors at all", [], 2.0, START, None),
        ("every candidate unscored", UNSCORED_DOORS, 2.0, START, None),
        ("zero hours", WORKED_DOORS, 0.0, START, None),
        ("negative hours", WORKED_DOORS, -1.0, START, None),
        ("max_doors of zero", WORKED_DOORS, 2.0, START, 0),
        ("a start point on the far coast", WORKED_DOORS, 2.0, FAR_AWAY, None),
    ],
)
def test_degenerate_inputs_yield_an_empty_route(label, doors, hours, start_point, max_doors):
    planned = plan_route(doors, hours=hours, start_point=start_point, max_doors=max_doors)

    assert planned.stops == (), label
    assert planned.total_minutes == pytest.approx(0.0), label
    assert planned.estimate_disclosure, label


# --- max_doors ----------------------------------------------------------------


@pytest.mark.parametrize("max_doors, expected", [(0, 0), (1, 1), (3, 3), (4, 4), (99, 4), (None, 4)])
def test_max_doors_caps_the_stop_count(max_doors, expected):
    assert len(worked_route(max_doors=max_doors).stops) == expected


def test_a_capped_route_is_the_uncapped_route_truncated():
    assert pins_of(worked_route(max_doors=2)) == WORKED_ORDER[:2]


# --- doors that cannot be routed ----------------------------------------------


@pytest.mark.parametrize(
    "reason, unroutable",
    [
        ("score is None — unscored doors are never routed", {"score": None}),
        ("centroid is None — a parcel with no geometry cannot be placed", {"centroid": None}),
    ],
)
def test_an_unroutable_door_is_never_a_stop(reason, unroutable):
    doors = WORKED_DOORS + [door(pin(9), north_m=50, score=100, **unroutable)]

    planned = plan_route(doors, hours=2.0, start_point=START)

    assert pin(9) not in pins_of(planned), reason
    assert pins_of(planned) == WORKED_ORDER, "the routable doors are unaffected"


def test_a_zero_scored_door_is_still_routed():
    """Zero is a real score (a cold door), unlike None which is no score at all."""
    planned = plan_route([door(pin(1), north_m=100, score=0)], hours=2.0, start_point=START)

    assert pins_of(planned) == [pin(1)]


# --- determinism --------------------------------------------------------------


def test_shuffling_the_candidates_does_not_move_a_single_stop():
    doors = grid_doors()
    reference = plan_route(doors, hours=2.0, start_point=START)

    shuffled = list(doors)
    random.Random(20260814).shuffle(shuffled)
    planned = plan_route(shuffled, hours=2.0, start_point=START)

    assert pins_of(planned) == pins_of(reference)
    assert [stop.walk_minutes for stop in planned.stops] == [
        stop.walk_minutes for stop in reference.stops
    ]


def test_replanning_the_same_inputs_reproduces_the_same_route():
    assert worked_route().stops == worked_route().stops


def test_an_exact_score_per_minute_tie_is_broken_by_pams_pin():
    """Two doors 200 m either side of the rep, same score: identical ratio."""
    doors = [
        door(pin(7), north_m=200, score=50),
        door(pin(3), north_m=-200, score=50),
    ]

    assert pins_of(plan_route(doors, hours=2.0, start_point=START))[0] == pin(3)


def test_the_tie_break_follows_the_pin_not_the_geometry():
    """Same two positions, PINs swapped — the winner must swap with them."""
    doors = [
        door(pin(3), north_m=200, score=50),
        door(pin(7), north_m=-200, score=50),
    ]

    winner = plan_route(doors, hours=2.0, start_point=START).stops[0]

    assert winner.pams_pin == pin(3)
    assert winner.address == situs_display("3 FAWN HILL RD", "07446")


def test_coincident_doors_are_ordered_deterministically():
    """Both at the rep's feet: zero walk time, so score-per-minute cannot
    separate them and the PIN must."""
    doors = [door(pin(6), north_m=0, score=10), door(pin(2), north_m=0, score=90)]

    assert pins_of(plan_route(doors, hours=2.0, start_point=START)) == [pin(2), pin(6)]


# --- performance --------------------------------------------------------------


def test_540_candidate_doors_plan_in_under_two_seconds():
    """R10.2. The doors are built outside the timed region; only `plan_route` is timed."""
    doors = grid_doors(540)

    started = time.perf_counter()
    planned = plan_route(doors, hours=2.0, start_point=START)
    elapsed = time.perf_counter() - started

    assert planned.stops, "a two-hour walk over 540 doors must find some doors"
    assert elapsed < 2.0, f"planning 540 doors took {elapsed:.3f}s"


# --- exclude ------------------------------------------------------------------


def test_exclude_drops_the_named_doors():
    planned = worked_route().exclude({PIN_BEST})

    assert PIN_BEST not in pins_of(planned)
    assert set(pins_of(planned)) == {PIN_MIDDLE, PIN_NEAR, PIN_FAR}


def test_exclude_replans_rather_than_filtering():
    """Without MIDDLE the rep walks BEST -> FAR -> NEAR: from 300 m, FAR scores
    8.25 per minute against NEAR's 3.10. Filtering the old order would have
    left NEAR ahead of FAR."""
    assert pins_of(worked_route().exclude({PIN_MIDDLE})) == [PIN_BEST, PIN_FAR, PIN_NEAR]


def test_exclude_matches_planning_without_those_doors():
    remaining = [candidate for candidate in WORKED_DOORS if candidate.pams_pin != PIN_MIDDLE]

    assert pins_of(worked_route().exclude({PIN_MIDDLE})) == pins_of(
        plan_route(remaining, hours=2.0, start_point=START)
    )


def test_exclude_leaves_the_original_route_untouched():
    planned = worked_route()
    planned.exclude({PIN_BEST})

    assert pins_of(planned) == WORKED_ORDER


def test_excluding_a_door_that_is_not_on_the_route_changes_nothing():
    assert pins_of(worked_route().exclude({"0248_99999_99999"})) == WORKED_ORDER


def test_excluding_nothing_changes_nothing():
    assert pins_of(worked_route().exclude(set())) == WORKED_ORDER


def test_excluding_every_door_yields_an_empty_route():
    planned = worked_route().exclude(set(WORKED_ORDER))

    assert planned.stops == ()
    assert planned.total_minutes == pytest.approx(0.0)


def test_exclusions_compose_in_any_order():
    once = worked_route().exclude({PIN_MIDDLE}).exclude({PIN_FAR})
    other_way = worked_route().exclude({PIN_FAR}).exclude({PIN_MIDDLE})
    together = worked_route().exclude({PIN_MIDDLE, PIN_FAR})

    assert pins_of(once) == pins_of(other_way) == pins_of(together)


def test_exclude_is_deterministic_under_a_shuffled_input():
    shuffled = list(WORKED_DOORS)
    random.Random(7).shuffle(shuffled)

    planned = plan_route(shuffled, hours=2.0, start_point=START).exclude({PIN_MIDDLE})

    assert pins_of(planned) == pins_of(worked_route().exclude({PIN_MIDDLE}))


def test_exclude_still_respects_max_doors():
    planned = plan_route(WORKED_DOORS, hours=2.0, start_point=START, max_doors=2)

    assert len(planned.exclude({PIN_BEST}).stops) == 2


# --- talk track (presentation only — shape, never prose) ----------------------


EVIDENCE_SENTENCE = "A SKYLIGHT permit was filed 3 weeks after the deed recorded."


@pytest.mark.parametrize(
    "top_evidence", [EVIDENCE_SENTENCE, None], ids=["from evidence", "no evidence"]
)
def test_a_talk_track_is_a_one_line_opener(top_evidence):
    track = talk_track_for(door(pin(1), north_m=100, top_evidence=top_evidence))

    assert isinstance(track, str)
    assert track.strip()
    assert "\n" not in track
    assert len(track) <= 200
    assert track.strip().endswith((".", "!", "?"))


def test_a_talk_track_speaks_to_the_door_top_evidence():
    track = talk_track_for(door(pin(1), north_m=100, top_evidence=EVIDENCE_SENTENCE))

    assert "SKYLIGHT" in track.upper()


def test_a_door_with_no_evidence_still_gets_a_door_specific_opener():
    track = talk_track_for(door(pin(1), north_m=100, top_evidence=None))

    assert "FAWN HILL" in track.upper()


def test_two_doors_with_different_evidence_get_different_talk_tracks():
    first = talk_track_for(door(pin(1), north_m=100, top_evidence="They just moved in."))
    second = talk_track_for(door(pin(2), north_m=100, top_evidence="The roof is failing."))

    assert first != second


def test_every_stop_carries_the_talk_track_the_module_generates():
    doors = [replace(candidate, top_evidence=EVIDENCE_SENTENCE) for candidate in WORKED_DOORS]
    by_pin = {candidate.pams_pin: candidate for candidate in doors}

    planned = plan_route(doors, hours=2.0, start_point=START)

    for stop in planned.stops:
        assert stop.talk_track == talk_track_for(by_pin[stop.pams_pin])


def test_the_talk_track_never_moves_the_score_or_the_order():
    """R7.2: presentation only. Same doors, wildly different evidence prose."""
    loud = [replace(candidate, top_evidence="A" * 150) for candidate in WORKED_DOORS]

    planned = plan_route(loud, hours=2.0, start_point=START)
    reference = worked_route()

    assert pins_of(planned) == pins_of(reference)
    assert [stop.score for stop in planned.stops] == [stop.score for stop in reference.stops]


# --- share links --------------------------------------------------------------


def test_a_share_link_round_trips_the_exact_ordered_pins():
    planned = worked_route()

    assert list(decode_share(encode_share(planned.stops))) == WORKED_ORDER


def test_a_share_link_preserves_order_not_just_membership():
    """WORKED_ORDER is neither sorted nor reverse-sorted, so a set-shaped
    encoding cannot fake this."""
    decoded = decode_share(encode_share(worked_route().stops))

    assert list(decoded) != sorted(decoded)


def test_a_share_link_round_trips_a_long_route():
    stops = plan_route(grid_doors(), hours=2.0, start_point=START).stops[:20]

    assert list(decode_share(encode_share(stops))) == [stop.pams_pin for stop in stops]


def test_a_share_link_needs_no_url_escaping():
    """It rides in a URL fragment, so it must be unreserved characters only."""
    encoded = encode_share(worked_route().stops)

    assert encoded
    assert set(encoded) <= set(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
    )


def test_a_share_link_is_compact():
    stops = plan_route(grid_doors(), hours=2.0, start_point=START).stops[:20]
    naive = ",".join(stop.pams_pin for stop in stops)

    assert len(encode_share(stops)) < len(naive)


def test_encoding_the_same_stops_twice_gives_the_same_link():
    planned = worked_route()

    assert encode_share(planned.stops) == encode_share(planned.stops)


def test_an_empty_route_shares_and_decodes_to_nothing():
    encoded = encode_share(())

    assert isinstance(encoded, str)
    assert decode_share(encoded) == ()


@pytest.mark.parametrize(
    "garbage",
    ["", "   ", "!!!", "%%%%", "not-a-route", "eyJ", "0248_00101_00003", "../../etc/passwd"],
    ids=[
        "empty",
        "whitespace",
        "punctuation",
        "percent signs",
        "plausible words",
        "truncated base64",
        "a bare pin",
        "a path traversal",
    ],
)
def test_decode_share_of_garbage_yields_no_pins(garbage):
    assert decode_share(garbage) == ()


# --- adapting the pipeline's doors --------------------------------------------


def facts(**overrides):
    fields = dict(
        pams_pin=pin(1),
        prop_class="2",
        prop_loc="12 FAWN HILL RD",
        zip5="07446",
        pclblock="101",
        pcllot="1",
        parcel_key="0248_00101_00001",
        address_key="12 FAWN HILL RD",
        situs=situs_display("12 FAWN HILL RD", "07446"),
        centroid=START,
        geometry=None,
        deed_date=None,
        sale_price=640000.0,
        sales_code="",
        yr_constr=1962,
        net_value=725000.0,
        calc_acre=0.34,
    )
    fields.update(overrides)
    return DoorFacts(**fields)


@pytest.mark.parametrize(
    "attribute, expected",
    [
        ("pams_pin", pin(1)),
        ("address", situs_display("12 FAWN HILL RD", "07446")),
        ("score", 81),
        ("centroid", START),
        ("top_evidence", EVIDENCE_SENTENCE),
    ],
)
def test_a_door_facts_adapts_into_a_route_door(attribute, expected):
    adapted = route_door_from_facts(facts(), score=81, top_evidence=EVIDENCE_SENTENCE)

    assert getattr(adapted, attribute) == expected


def test_the_adapter_is_duck_typed_not_bound_to_door_facts():
    """The MCP server adapts GeoJSON features through the same door."""

    @dataclass(frozen=True)
    class Feature:
        pams_pin: str
        situs: str
        centroid: tuple[float, float]

    adapted = route_door_from_facts(
        Feature(pams_pin=pin(2), situs="9 GRID ST, Ramsey NJ 07446", centroid=START), score=44
    )

    assert isinstance(adapted, RouteDoor)
    assert (adapted.pams_pin, adapted.address, adapted.score) == (pin(2), "9 GRID ST, Ramsey NJ 07446", 44)


def test_an_unscored_door_facts_adapts_to_an_unroutable_route_door():
    adapted = route_door_from_facts(facts(), score=None)

    assert adapted.score is None
    assert plan_route([adapted], hours=2.0, start_point=START).stops == ()


def test_adapted_doors_plan_like_any_other():
    adapted = [
        route_door_from_facts(facts(pams_pin=pin(1), centroid=offset(START, north_m=100)), score=10),
        route_door_from_facts(facts(pams_pin=pin(3), centroid=offset(START, north_m=300)), score=90),
    ]

    assert pins_of(plan_route(adapted, hours=2.0, start_point=START)) == [pin(3), pin(1)]


def test_the_planner_does_not_import_the_pipeline():
    """R10.3: the MCP server and the UI share this module, and neither should
    have to drag the harvest/ACS/permits stack into a web process."""
    source = inspect.getsource(route_module)

    for forbidden in ("houseaccount.resolve", "houseaccount.sources", "houseaccount.territory"):
        assert forbidden not in source, f"route.py must not import {forbidden}"
