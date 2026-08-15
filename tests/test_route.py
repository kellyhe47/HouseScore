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

**Where the talk track comes from.** R7.2 makes it presentation-layer only: it
never feeds the score and is never asserted in a golden fixture. The planner
receives the top evidence item's **type as a plain string** on
`RouteDoor.evidence_type` — not the sentence, not an `EvidenceItem`, not a
callback. The type picks an authored angle out of `route.ANGLES`;
`talk_track_for(door)` returns the opener that ends on that angle's one open
question, `talk_track_branches_for(door)` returns what to say after the
homeowner answers, and the planner stamps both on every `Stop`.

The sentence is deliberately not passed. It is written for the evidence panel
and recites what the pipeline worked out about a household, which is the one
thing a rep must never say at a door — so the tests below assert *shape* and
*safety* (no file words spoken, no evidence sentence spoken, unsayable signals
falling through to an angle that mentions nothing) rather than prose.

**How a leg is measured.** A rep can only walk along a street, so `plan_route`
takes a `network` — anything answering `metres_from(origin, destinations)` and
`path(origin, destination)`, which is what `houseaccount.streets` derives from
the parcels. It is duck-typed for the same reason the door adapter is: the
planner sorts points and must not drag the geometry stack in to do it. Without
one, a leg is the old straight-line estimate (haversine x1.3 at 3 mph), which is
what every synthetic door in this module gets and what pins the fallback.
`StubNetwork` below stands in for the real thing by walking two sides of a
rectangle instead of the diagonal.

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
import re
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
    talk_track_branches_for,
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


def door(pin, *, north_m=0.0, east_m=0.0, score=50, centroid=..., evidence_type=None, street="FAWN HILL RD"):
    """One candidate door, placed at a known offset from `START`."""
    number = int(pin.rsplit("_", 1)[-1])
    return RouteDoor(
        pams_pin=pin,
        address=situs_display(f"{number} {street}", "07446"),
        score=score,
        centroid=offset(START, north_m=north_m, east_m=east_m) if centroid is ... else centroid,
        evidence_type=evidence_type,
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


# --- walking along streets ----------------------------------------------------
#
# A rep can only walk along a street, so a leg is measured on a street network
# when the caller has one. The planner is duck-typed against it — two questions,
# `metres_from` and `path` — which is what lets `houseaccount.streets` build one
# out of shapely and geometry while this module stays stdlib. `StubNetwork` is
# that contract with the geometry taken out: legs go round a corner rather than
# across the diagonal, which is exactly how a street grid differs from a
# straight line, and it can be told to refuse a door outright.


class StubNetwork:
    """A network that walks two sides of the rectangle instead of the diagonal.

    Its `disclosure` is deliberately not the real one: the route has to carry
    the network's own words rather than a string this module knows.
    """

    disclosure = "walking times follow the stub network"

    def __init__(self, unreachable=()):
        self.unreachable = set(unreachable)
        self.sweeps = []
        self.paths = []

    def corner(self, origin, destination):
        """Where the two legs of the dogleg meet: east first, then north."""
        return (destination[0], origin[1])

    def metres(self, origin, destination):
        corner = self.corner(origin, destination)
        east = abs(destination[0] - origin[0]) * METRES_PER_DEGREE_LAT * math.cos(
            math.radians(origin[1])
        )
        north = abs(destination[1] - corner[1]) * METRES_PER_DEGREE_LAT
        return east + north

    def metres_from(self, origin, destinations):
        self.sweeps.append((origin, tuple(destinations)))
        return tuple(
            None if destination in self.unreachable else self.metres(origin, destination)
            for destination in destinations
        )

    def path(self, origin, destination):
        self.paths.append((origin, destination))
        if destination in self.unreachable:
            return ()
        return (origin, self.corner(origin, destination), destination)


def test_a_leg_is_measured_on_the_network_when_there_is_one():
    """400 m east then 300 m north is a 700 m walk, not the 500 m diagonal."""
    east_north = door(pin(1), east_m=400, north_m=300)

    (stop,) = plan_route(
        [east_north], hours=2.0, start_point=START, network=StubNetwork()
    ).stops

    assert stop.walk_minutes == pytest.approx(700 / (3.0 * 1609.344 / 60.0), rel=1e-3)


def test_a_network_leg_carries_no_detour_factor():
    """The 1.3 stands in for streets nobody measured. Measured, it goes."""
    straight = door(pin(1), north_m=1000)

    (stop,) = plan_route(
        [straight], hours=2.0, start_point=START, network=StubNetwork()
    ).stops

    assert stop.walk_minutes == pytest.approx(1000 / (3.0 * 1609.344 / 60.0), rel=1e-3)


def test_a_stop_carries_the_line_the_planner_measured():
    """R10.3: the map draws the planner's walk, so the planner ships it."""
    network = StubNetwork()
    target = door(pin(1), east_m=400, north_m=300)

    (stop,) = plan_route([target], hours=2.0, start_point=START, network=network).stops

    assert stop.path == (START, network.corner(START, target.centroid), target.centroid)


def test_without_a_network_the_line_is_the_straight_line_the_estimate_assumed():
    target = door(pin(1), north_m=300)

    (stop,) = plan_route([target], hours=2.0, start_point=START).stops

    assert stop.path == (START, target.centroid)


def test_every_leg_starts_where_the_previous_one_ended():
    """The legs join up into one walk — the map draws them end to end."""
    planned = plan_route(
        WORKED_DOORS, hours=2.0, start_point=START, network=StubNetwork()
    )

    position = START
    for stop in planned.stops:
        assert stop.path[0] == position
        position = stop.path[-1]


def test_a_door_the_network_cannot_reach_falls_back_to_the_straight_line():
    """A gap in the derived streets is not a house that stopped existing."""
    stranded = door(pin(1), north_m=1000)
    network = StubNetwork(unreachable={stranded.centroid})

    (stop,) = plan_route([stranded], hours=2.0, start_point=START, network=network).stops

    assert stop.walk_minutes == pytest.approx(1000 * 1.3 / (3.0 * 1609.344 / 60.0), rel=1e-3)
    assert stop.path == (START, stranded.centroid)


def test_the_network_decides_the_order_it_measured():
    """The walk changes when the walking does.

    `DIAGONAL` is the closer of the two on the map — 424 m against 500 — and the
    further of the two on foot, because reaching it means 300 m along one street
    and 300 m along another. Same scores, so score-per-minute is distance alone:
    straight lines knock the diagonal first, streets knock it second.
    """
    up_the_road = door(pin(1), north_m=500, score=50)
    diagonal = door(pin(2), north_m=300, east_m=300, score=50)
    doors = [up_the_road, diagonal]

    assert pins_of(plan_route(doors, hours=2.0, start_point=START)) == [pin(2), pin(1)]

    on_streets = plan_route(doors, hours=2.0, start_point=START, network=StubNetwork())

    assert pins_of(on_streets) == [pin(1), pin(2)]


def test_the_route_carries_the_network_own_disclosure():
    planned = plan_route(WORKED_DOORS, hours=2.0, start_point=START, network=StubNetwork())

    assert planned.estimate_disclosure == StubNetwork.disclosure


def test_a_route_planned_without_a_network_still_discloses_the_straight_line():
    assert worked_route().estimate_disclosure == route_module.ESTIMATE_DISCLOSURE


def test_the_network_is_asked_once_per_stop_not_once_per_candidate():
    """Every candidate is measured from where the rep stands in one sweep — the
    shape that keeps a 540-door territory inside the two-second budget."""
    network = StubNetwork()

    planned = plan_route(grid_doors(), hours=2.0, start_point=START, max_doors=20, network=network)

    assert len(planned.stops) == 20
    # One sweep per chosen stop, plus the sweep that found nothing affordable
    # if the budget ran out first.
    assert len(network.sweeps) <= len(planned.stops) + 1
    assert network.sweeps[0][0] == START


def test_excluding_a_door_replans_on_the_same_network():
    network = StubNetwork()
    planned = plan_route(WORKED_DOORS, hours=2.0, start_point=START, network=network)

    replanned = planned.exclude([planned.stops[0].pams_pin])

    assert replanned.estimate_disclosure == StubNetwork.disclosure
    assert pins_of(replanned) == pins_of(
        plan_route(
            [candidate for candidate in WORKED_DOORS if candidate.pams_pin != planned.stops[0].pams_pin],
            hours=2.0,
            start_point=START,
            network=network,
        )
    )


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
    # Nearest door in the set and top-scoring, so it would lead the route if it
    # were routable at all. `unroutable` overrides that baseline, one field at a time.
    doors = WORKED_DOORS + [door(pin(9), north_m=50, **{"score": 100, **unroutable})]

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


# --- talk track (presentation only — shape and safety, never prose) -----------
#
# The opener used to be the door's top evidence *sentence* folded into a
# template, which meant the first thing a rep said at a stranger's house was a
# recitation of what we had worked out about them ("4 permits filed here in the
# last 24 months (Alteration)"). It is now authored per evidence *type*: the
# type picks an angle out of `route.ANGLES`, and the sentence never leaves the
# evidence panel.
#
# Two whole classes of bug go with it. Nothing is quoted, so nothing can be cut
# mid-clause (ticket 021 — the boundary-cutting tests below became assertions
# that every authored line is whole). And a type the rep could not say without
# revealing the file falls through to an angle that does not mention the house
# at all, which is what the "unsayable" tests pin.

#: The engine's own prose, verbatim — a short line and the long permit line that
#: ticket 021 was filed about. Neither may ever reach a doorstep.
EVIDENCE_SENTENCES = [
    "A SKYLIGHT permit was filed 3 weeks after the deed recorded.",
    "1 permit filed here in the last 24 months (Alteration) — work at this address "
    "gets contracted out rather than done in-house.",
    "Assessed at $1,240,000, more than 1.5x the $610,000 territory median — top of "
    "the range you cover.",
    "Census block group: 63% dual-income, at or above the 55% threshold.",
    "Exterior condition read as fair on the 2015 orthoimagery and poor on the 2020 "
    "pass — the trend is downward, not just low.",
]

#: Evidence types the rep cannot speak without telling the homeowner we hold a
#: file on them, or without insulting them. Every one must land on the angle
#: that says nothing about the house.
UNSAYABLE_TYPES = [
    "assessed_value",
    "acs_dual_income_prior",
    "absentee_likely",
    "data_gap",
]

#: Words that only appear in prose derived from the parcel record. None of them
#: belongs in something said out loud on a doorstep.
FILE_WORDS = [
    "permit",
    "assessed",
    "median",
    "census",
    "block group",
    "dual-income",
    "orthoimagery",
    "deed",
    "parcel",
    "registration",
    "score",
]

ALL_ANGLES = sorted(
    {id(angle): angle for angle in [*route_module.ANGLES.values(), route_module.DEFAULT_ANGLE]}.values(),
    key=lambda angle: angle.hook,
)


def sentences_of(track):
    return [part.strip() for part in re.split(r"[.?!]", track) if part.strip()]


def test_a_talk_track_is_a_one_line_opener():
    track = talk_track_for(door(pin(1), north_m=100, evidence_type="deed_recency"))

    assert isinstance(track, str)
    assert track.strip()
    assert "\n" not in track
    assert len(track) <= 200
    assert track.strip().endswith("?"), "the opener stops on a question and waits"


def test_the_opener_ends_on_its_angle_hook():
    """The last thing the rep says before the homeowner speaks is the hook,
    verbatim — nothing is appended after the question."""
    candidate = door(pin(1), north_m=100, evidence_type="provider_churn")

    assert talk_track_for(candidate).endswith(route_module.ANGLES["provider_churn"].hook)


@pytest.mark.parametrize("evidence_type", sorted(route_module.ANGLES) + [None, "brand_new_signal"])
def test_no_opener_ever_recites_the_file(evidence_type):
    """R7.2, and the whole point of the rewrite: the words a homeowner hears
    never reveal that the door was chosen from a parcel record."""
    track = talk_track_for(door(pin(1), north_m=100, evidence_type=evidence_type)).lower()

    for word in FILE_WORDS:
        assert word not in track, f"{word!r} reached the doorstep for {evidence_type!r}"


@pytest.mark.parametrize("sentence", EVIDENCE_SENTENCES, ids=range(len(EVIDENCE_SENTENCES)))
def test_an_evidence_sentence_is_never_spoken_whole_or_in_part(sentence):
    """The panel shows the sentence; the rep does not say it. Checked against
    every angle, on the sentence's longest distinctive run."""
    body = sentence.strip().rstrip(".!?").lower()
    run = body[:40]

    for evidence_type in [*route_module.ANGLES, None]:
        spoken = talk_track_for(door(pin(1), north_m=100, evidence_type=evidence_type)).lower()
        assert run not in spoken


@pytest.mark.parametrize("evidence_type", UNSAYABLE_TYPES)
def test_an_unsayable_signal_falls_through_to_the_default_angle(evidence_type):
    """Assessed value, the census prior, the rental flag and a data gap still
    score and still sort the route — they just never pick the words."""
    candidate = door(pin(1), north_m=100, evidence_type=evidence_type)

    assert route_module.angle_for(candidate) is route_module.DEFAULT_ANGLE


def test_an_unknown_evidence_type_degrades_to_a_safe_opener():
    """A type added to the engine and not yet to `ANGLES` gets the angle that
    says nothing about the door, not a crash and not silence."""
    candidate = door(pin(1), north_m=100, evidence_type="signal_invented_next_quarter")

    assert route_module.angle_for(candidate) is route_module.DEFAULT_ANGLE
    assert talk_track_for(candidate).strip().endswith("?")


def test_a_declining_exterior_never_reaches_the_homeowner():
    """`condition_trajectory` is a true thing nobody says to someone's face. It
    opens on tenure instead."""
    track = talk_track_for(door(pin(1), north_m=100, evidence_type="condition_trajectory")).lower()

    for word in ["condition", "declin", "slipping", "deferred", "put off"]:
        assert word not in track


@pytest.mark.parametrize("angle", ALL_ANGLES, ids=lambda angle: angle.hook[:24])
def test_no_hook_is_a_tag_question(angle):
    """"…right?" asks for confirmation, which tells the homeowner the rep
    already knew. Every hook is genuinely open."""
    hook = angle.hook.lower().rstrip()

    assert hook.endswith("?")
    for tag in [", right?", ", yeah?", ", correct?", "aren't you?", "didn't you?"]:
        assert not hook.endswith(tag)


@pytest.mark.parametrize("angle", ALL_ANGLES, ids=lambda angle: angle.hook[:24])
def test_every_angle_offers_branches_with_distinct_triggers(angle):
    triggers = [branch.trigger for branch in angle.branches]

    assert len(triggers) >= 2
    assert len(set(triggers)) == len(triggers)


@pytest.mark.parametrize("angle", ALL_ANGLES, ids=lambda angle: angle.hook[:24])
def test_every_branch_line_is_whole_sentences(angle):
    """Ticket 021, now by construction rather than by cutting: authored lines
    have no width limit to collide with, so none of them can dangle."""
    for branch in angle.branches:
        assert branch.line.strip().endswith((".", "?", "!"))
        assert "\n" not in branch.line
        for sentence in sentences_of(branch.line):
            assert sentence.split()[-1].lower() not in DANGLING_ENDINGS, sentence


@pytest.mark.parametrize("angle", ALL_ANGLES, ids=lambda angle: angle.hook[:24])
def test_no_branch_opens_on_a_scripted_acknowledgement(angle):
    """"Figured." in the slot after the hook admits the answer was never in
    doubt. Every branch reacts to what was actually said instead."""
    for branch in angle.branches:
        first = branch.line.split()[0].strip(",.").lower()
        assert first not in {"figured", "exactly", "thought", "knew"}


def test_a_door_with_no_evidence_still_gets_a_door_specific_opener():
    track = talk_track_for(door(pin(1), north_m=100, evidence_type=None))

    assert "FAWN HILL" in track.upper()


def test_the_opener_says_the_street_the_way_a_rep_would():
    """"FAWN HILL RD" is a thing to read off a form; the rep is speaking."""
    track = talk_track_for(door(pin(1), north_m=100, evidence_type=None))

    assert "Fawn Hill Rd" in track
    assert "FAWN HILL RD" not in track


def test_a_door_with_no_readable_street_still_opens_somewhere_sayable():
    nameless = replace(door(pin(1), north_m=100), address="")

    assert "this block" in talk_track_for(nameless)


def test_two_doors_with_different_evidence_get_different_talk_tracks():
    first = talk_track_for(door(pin(1), north_m=100, evidence_type="deed_recency"))
    second = talk_track_for(door(pin(2), north_m=100, evidence_type="pool"))

    assert first != second


def test_two_doors_on_one_angle_get_the_same_words():
    """The opener is drawn from a small authored set, not generated per door —
    two permit-led doors on one street are opened the same way on purpose."""
    first = talk_track_for(door(pin(1), north_m=100, evidence_type="permit_history"))
    second = talk_track_for(door(pin(2), north_m=200, evidence_type="provider_churn"))

    assert first == second


def test_every_stop_carries_the_talk_track_and_branches_the_module_generates():
    doors = [replace(candidate, evidence_type="deed_recency") for candidate in WORKED_DOORS]
    by_pin = {candidate.pams_pin: candidate for candidate in doors}

    planned = plan_route(doors, hours=2.0, start_point=START)

    assert planned.stops
    for stop in planned.stops:
        assert stop.talk_track == talk_track_for(by_pin[stop.pams_pin])
        assert stop.talk_track_branches == talk_track_branches_for(by_pin[stop.pams_pin])
        assert stop.talk_track_branches


def test_the_talk_track_never_moves_the_score_or_the_order():
    """R7.2: presentation only. Same doors, wildly different evidence types."""
    loud = [replace(candidate, evidence_type="pool") for candidate in WORKED_DOORS]

    planned = plan_route(loud, hours=2.0, start_point=START)
    reference = worked_route()

    assert pins_of(planned) == pins_of(reference)
    assert [stop.score for stop in planned.stops] == [stop.score for stop in reference.stops]


#: Words no sentence a rep reads aloud may end on (ticket 021's original
#: symptom: "…gets contracted out rather than."). Deliberately only conjunctions,
#: articles and relatives — English sentences end on a preposition all the time
#: ("…meaning to get to."), so a list that catches those catches good prose.
DANGLING_ENDINGS = {
    "and",
    "or",
    "but",
    "than",
    "rather",
    "the",
    "a",
    "an",
    "of",
    "which",
}


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
        ("evidence_type", "deed_recency"),
    ],
)
def test_a_door_facts_adapts_into_a_route_door(attribute, expected):
    adapted = route_door_from_facts(facts(), score=81, evidence_type="deed_recency")

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
