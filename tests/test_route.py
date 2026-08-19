"""Route planning on the V2 score contract (ticket 105, plan R30/R27, PRD R7.2.1).

The product question, verbatim: *"I have 2 hours in Ramsey — which 20 doors do I
knock, and what do I say?"*

**What changed under V2.** The planner's geometry — greedy score-per-walking-
minute, street networks, exclude-as-replan, share links — is untouched. What
this rewrite pins is everything R30 says about the *words and the version*:

* The talk-track template map (`route.ANGLES`) must cover **every** evidence
  type the V2 engine can emit — enumerated below and kept in sync with
  `scoring/v2.py` by a source-scan test — so a new engine signal can never ship
  without a deliberate decision about what a rep says.
* `capacity_territory_percentile` and `capacity_local_relative_value` are
  unspeakable at the door (PRD R7.2.1): they map to the angle that says nothing
  about the house, and they never become a route reason chip.
* The per-door route reason chip is the highest-point evidence entry, ties
  broken by descending points then ascending evidence type, unspeakable types
  excluded (`route.reason_chip`). Every `Stop` carries its door's chip.
* R15/R19 phrasing rules by template inspection: no template ever claims the
  household is dual-income, and no template speaks about the house's current
  condition.
* The module names the score contract it plans for
  (`route.SCORE_CONTRACT_VERSION == "v2"`), and share tokens carry it: a token
  minted under the old contract decodes to no pins, which is what makes a stale
  shared URL a refresh rather than a wrong route (R27/R30).

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

#: 2*pi*R/360 with R = 6371008.8 m — metres per degree of latitude.
METRES_PER_DEGREE_LAT = 111194.9266


# --- the V2 evidence-type registry ---------------------------------------------
#
# Every type `scoring/v2.py`'s engine can put on an evidence trail, sorted. The
# sync test below reads them out of the engine's own source, so this list cannot
# silently drift from the engine — and the exhaustiveness test then holds the
# talk-track template map to the full set (R30).

V2_EVIDENCE_TYPES = [
    "capacity_acs_dual_income_prior",
    "capacity_cap_adjustment",
    "capacity_local_relative_value",
    "capacity_territory_percentile",
    "fit_cap_adjustment",
    "fit_condition_decline",
    "fit_condition_superseded",
    "fit_home_age",
    "fit_lot",
    "fit_pool",
    "fit_roof_age",
    "fit_solar",
    "mover_invalid_sale",
    "mover_recency",
    "project_active",
    "project_cap_adjustment",
    "project_completed",
    "project_major",
    "project_multi_permit",
    "project_neutralized",
    "rental_registration",
    "rental_stale",
]

#: Unspeakable at the door per PRD R7.2.1 / plan R30: a rep cannot voice a
#: value percentile or a local price ratio without revealing the file. They
#: still score and still sort the route; they never pick the words and never
#: become a chip.
UNSPEAKABLE_TYPES = [
    "capacity_territory_percentile",
    "capacity_local_relative_value",
]


def test_the_registry_matches_every_type_the_v2_engine_can_emit():
    """Source-scan sync: the engine's own `add("...")` calls, no more, no less."""
    from houseaccount.scoring import v2 as v2_module

    emitted = set(re.findall(r'add\(\s*"([a-z_]+)"', inspect.getsource(v2_module)))
    assert emitted == set(V2_EVIDENCE_TYPES)
    assert V2_EVIDENCE_TYPES == sorted(V2_EVIDENCE_TYPES)


# --- builders -----------------------------------------------------------------


def offset(origin, *, north_m=0.0, east_m=0.0):
    """A point `north_m`/`east_m` from `origin`, in (lon, lat) order."""
    lon, lat = origin
    dlat = north_m / METRES_PER_DEGREE_LAT
    dlon = east_m / (METRES_PER_DEGREE_LAT * math.cos(math.radians(lat)))
    return (lon + dlon, lat + dlat)


def door(pin, *, north_m=0.0, east_m=0.0, score=50, centroid=..., evidence_type=None,
         reason_chip=None, street="FAWN HILL RD"):
    """One candidate door, placed at a known offset from `START`."""
    number = int(pin.rsplit("_", 1)[-1])
    # `reason_chip` is a V2 field `RouteDoor` must grow (R30); passed only when
    # a test pins it, so the V2-neutral planner tests still collect and run red
    # or green on their own merits rather than dying at import time.
    extra = {} if reason_chip is None else {"reason_chip": reason_chip}
    return RouteDoor(
        pams_pin=pin,
        address=situs_display(f"{number} {street}", "07446"),
        score=score,
        centroid=offset(START, north_m=north_m, east_m=east_m) if centroid is ... else centroid,
        evidence_type=evidence_type,
        **extra,
    )


def pin(n):
    return f"0248_00101_{n:05d}"


def pins_of(route):
    return [stop.pams_pin for stop in route.stops]


# --- the worked example (unchanged geometry) ------------------------------------

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


def grid_doors(count=540, *, spacing_m=40.0, columns=24):
    doors = []
    for index in range(count):
        row, column = divmod(index, columns)
        doors.append(
            RouteDoor(
                pams_pin=pin(index),
                address=situs_display(f"{index} GRID ST", "07446"),
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


# --- the score contract version (R27/R30) --------------------------------------


def test_the_module_names_the_score_contract_it_plans_for():
    """One authority for what version a shared route was planned under."""
    assert route_module.SCORE_CONTRACT_VERSION == "v2"


def test_a_share_token_carries_the_score_contract_version():
    """R30: a shared route URL carries the version. The token self-identifies —
    a fragment minted today must be distinguishable from a V1-era one."""
    encoded = encode_share(worked_route().stops)

    assert route_module.share_token_version(encoded) == "v2"


def test_a_v1_era_share_token_decodes_to_no_pins():
    """R27's mismatch-refresh, at the decode seam: the browser's old `r1` token
    (this exact string once round-tripped) now names a route scored under a
    dead contract, so it opens empty and the UI asks for a fresh route."""
    v1_token = "r1eJwzMDKxiDcwNDQwjDcwMDA00jFAETAygAgYmxoYxVvoGRgCAPz0Clk"

    assert route_module.share_token_version(v1_token) != "v2"
    assert decode_share(v1_token) == ()


def test_a_current_share_token_round_trips_the_exact_ordered_pins():
    planned = worked_route()

    assert list(decode_share(encode_share(planned.stops))) == WORKED_ORDER


# --- greedy selection (V2-neutral, unchanged) -----------------------------------


def test_greedy_visits_the_worked_example_in_score_per_minute_order():
    assert pins_of(worked_route()) == WORKED_ORDER


def test_the_worked_example_arrives_at_the_hand_checked_offsets():
    planned = worked_route()

    assert [stop.cumulative_minutes for stop in planned.stops] == pytest.approx(
        WORKED_CUMULATIVE, rel=1e-3
    )


def test_the_first_leg_is_measured_from_the_start_point_not_from_a_door():
    assert worked_route().stops[0].walk_minutes == pytest.approx(4.8467, rel=1e-3)


def test_walk_minutes_are_haversine_times_detour_at_three_mph():
    (stop,) = plan_route([door(pin(1), north_m=1000)], hours=2.0, start_point=START).stops

    assert stop.walk_minutes == pytest.approx(1000 * 1.3 / (3.0 * 1609.344 / 60.0), rel=1e-3)


def test_the_walking_model_is_exposed_as_named_constants():
    assert route_module.DETOUR_FACTOR == pytest.approx(1.3)
    assert route_module.WALKING_SPEED_MPH == pytest.approx(3.0)


def test_a_door_at_the_rep_feet_costs_no_walking_time():
    planned = plan_route([door(pin(1), north_m=0)], hours=2.0, start_point=START)

    assert planned.stops[0].walk_minutes == pytest.approx(0.0, abs=1e-9)


# --- walking along streets (V2-neutral, unchanged) ------------------------------


class StubNetwork:
    """A network that walks two sides of the rectangle instead of the diagonal."""

    disclosure = "walking times follow the stub network"

    def __init__(self, unreachable=()):
        self.unreachable = set(unreachable)
        self.sweeps = []
        self.paths = []

    def corner(self, origin, destination):
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
    east_north = door(pin(1), east_m=400, north_m=300)

    (stop,) = plan_route(
        [east_north], hours=2.0, start_point=START, network=StubNetwork()
    ).stops

    assert stop.walk_minutes == pytest.approx(700 / (3.0 * 1609.344 / 60.0), rel=1e-3)


def test_a_network_leg_carries_no_detour_factor():
    straight = door(pin(1), north_m=1000)

    (stop,) = plan_route(
        [straight], hours=2.0, start_point=START, network=StubNetwork()
    ).stops

    assert stop.walk_minutes == pytest.approx(1000 / (3.0 * 1609.344 / 60.0), rel=1e-3)


def test_a_stop_carries_the_line_the_planner_measured():
    network = StubNetwork()
    target = door(pin(1), east_m=400, north_m=300)

    (stop,) = plan_route([target], hours=2.0, start_point=START, network=network).stops

    assert stop.path == (START, network.corner(START, target.centroid), target.centroid)


def test_without_a_network_the_line_is_the_straight_line_the_estimate_assumed():
    target = door(pin(1), north_m=300)

    (stop,) = plan_route([target], hours=2.0, start_point=START).stops

    assert stop.path == (START, target.centroid)


def test_every_leg_starts_where_the_previous_one_ended():
    planned = plan_route(
        WORKED_DOORS, hours=2.0, start_point=START, network=StubNetwork()
    )

    position = START
    for stop in planned.stops:
        assert stop.path[0] == position
        position = stop.path[-1]


def test_a_door_the_network_cannot_reach_falls_back_to_the_straight_line():
    stranded = door(pin(1), north_m=1000)
    network = StubNetwork(unreachable={stranded.centroid})

    (stop,) = plan_route([stranded], hours=2.0, start_point=START, network=network).stops

    assert stop.walk_minutes == pytest.approx(1000 * 1.3 / (3.0 * 1609.344 / 60.0), rel=1e-3)
    assert stop.path == (START, stranded.centroid)


def test_the_network_decides_the_order_it_measured():
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
    network = StubNetwork()

    planned = plan_route(grid_doors(), hours=2.0, start_point=START, max_doors=20, network=network)

    assert len(planned.stops) == 20
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


# --- the hour budget (unchanged) ------------------------------------------------


def test_the_budget_cuts_the_route_short():
    assert pins_of(worked_route(hours=9 / 60)) == [PIN_BEST, PIN_MIDDLE, PIN_NEAR]


def test_no_stop_ever_exceeds_the_budget():
    planned = plan_route(grid_doors(), hours=2.0, start_point=START)

    assert planned.stops
    assert planned.stops[-1].cumulative_minutes <= 2.0 * 60


def test_a_budget_too_small_for_any_door_returns_an_empty_route_rather_than_raising():
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


# --- max_doors ------------------------------------------------------------------


@pytest.mark.parametrize("max_doors, expected", [(0, 0), (1, 1), (3, 3), (4, 4), (99, 4), (None, 4)])
def test_max_doors_caps_the_stop_count(max_doors, expected):
    assert len(worked_route(max_doors=max_doors).stops) == expected


def test_a_capped_route_is_the_uncapped_route_truncated():
    assert pins_of(worked_route(max_doors=2)) == WORKED_ORDER[:2]


# --- doors that cannot be routed --------------------------------------------------


@pytest.mark.parametrize(
    "reason, unroutable",
    [
        ("score is None — unscored doors are never routed", {"score": None}),
        ("centroid is None — a parcel with no geometry cannot be placed", {"centroid": None}),
    ],
)
def test_an_unroutable_door_is_never_a_stop(reason, unroutable):
    doors = WORKED_DOORS + [door(pin(9), north_m=50, **{"score": 100, **unroutable})]

    planned = plan_route(doors, hours=2.0, start_point=START)

    assert pin(9) not in pins_of(planned), reason
    assert pins_of(planned) == WORKED_ORDER, "the routable doors are unaffected"


def test_a_zero_scored_door_is_still_routed():
    planned = plan_route([door(pin(1), north_m=100, score=0)], hours=2.0, start_point=START)

    assert pins_of(planned) == [pin(1)]


# --- determinism ------------------------------------------------------------------


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
    doors = [
        door(pin(7), north_m=200, score=50),
        door(pin(3), north_m=-200, score=50),
    ]

    assert pins_of(plan_route(doors, hours=2.0, start_point=START))[0] == pin(3)


def test_the_tie_break_follows_the_pin_not_the_geometry():
    doors = [
        door(pin(3), north_m=200, score=50),
        door(pin(7), north_m=-200, score=50),
    ]

    winner = plan_route(doors, hours=2.0, start_point=START).stops[0]

    assert winner.pams_pin == pin(3)
    assert winner.address == situs_display("3 FAWN HILL RD", "07446")


def test_coincident_doors_are_ordered_deterministically():
    doors = [door(pin(6), north_m=0, score=10), door(pin(2), north_m=0, score=90)]

    assert pins_of(plan_route(doors, hours=2.0, start_point=START)) == [pin(2), pin(6)]


# --- performance -------------------------------------------------------------------


def test_540_candidate_doors_plan_in_under_two_seconds():
    doors = grid_doors(540)

    started = time.perf_counter()
    planned = plan_route(doors, hours=2.0, start_point=START)
    elapsed = time.perf_counter() - started

    assert planned.stops, "a two-hour walk over 540 doors must find some doors"
    assert elapsed < 2.0, f"planning 540 doors took {elapsed:.3f}s"


# --- exclude ------------------------------------------------------------------------


def test_exclude_drops_the_named_doors():
    planned = worked_route().exclude({PIN_BEST})

    assert PIN_BEST not in pins_of(planned)
    assert set(pins_of(planned)) == {PIN_MIDDLE, PIN_NEAR, PIN_FAR}


def test_exclude_replans_rather_than_filtering():
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


# --- the route reason chip (R30) ---------------------------------------------------
#
# The chip is the one word the route list shows beside a door — *why this door*.
# Selection is pure and deterministic: the highest-point evidence entry wins,
# ties break by descending points then ascending evidence type, and the two
# unspeakable capacity types are never eligible however many points they carry
# (PRD R7.2.1). `route.reason_chip(evidence)` is that rule, in one place.


def entry(etype, points):
    return {"type": etype, "points": points, "reason": f"reason for {etype}"}


def test_the_chip_is_the_highest_point_evidence_entry():
    evidence = [entry("fit_lot", 5), entry("project_active", 15), entry("fit_pool", 5)]

    assert route_module.reason_chip(evidence) == "project_active"


def test_a_points_tie_breaks_by_ascending_evidence_type():
    """Descending points, then ascending type: `fit_pool` < `fit_solar`."""
    evidence = [entry("fit_solar", 5), entry("fit_pool", 5)]

    assert route_module.reason_chip(evidence) == "fit_pool"
    assert route_module.reason_chip(list(reversed(evidence))) == "fit_pool"


def test_the_chip_ignores_input_order_entirely():
    evidence = [entry("project_completed", 8), entry("fit_roof_age", 12), entry("fit_home_age", 8)]

    forward = route_module.reason_chip(evidence)
    backward = route_module.reason_chip(list(reversed(evidence)))

    assert forward == backward == "fit_roof_age"


@pytest.mark.parametrize("unspeakable", UNSPEAKABLE_TYPES)
def test_an_unspeakable_type_never_becomes_the_chip_even_when_it_leads(unspeakable):
    """PRD R7.2.1: the percentile and the local ratio scored the door and sorted
    the route; the chip falls to the next-best speakable entry."""
    evidence = [entry(unspeakable, 10), entry("fit_lot", 5)]

    assert route_module.reason_chip(evidence) == "fit_lot"


def test_evidence_that_is_entirely_unspeakable_yields_no_chip():
    evidence = [entry(t, 10) for t in UNSPEAKABLE_TYPES]

    assert route_module.reason_chip(evidence) is None


def test_no_evidence_yields_no_chip():
    assert route_module.reason_chip([]) is None


def test_a_negative_entry_never_outranks_a_positive_one():
    """`rental_registration` carries -25 points; magnitude is not points."""
    evidence = [entry("rental_registration", -25), entry("fit_lot", 5)]

    assert route_module.reason_chip(evidence) == "fit_lot"


def test_every_stop_carries_its_door_reason_chip():
    """The planner stamps the chip on the stop, exactly as handed in — the same
    field both surfaces serialize, so the map and the tool show one chip."""
    doors = [replace(candidate, reason_chip="project_active") for candidate in WORKED_DOORS]

    planned = plan_route(doors, hours=2.0, start_point=START)

    assert planned.stops
    for stop in planned.stops:
        assert stop.reason_chip == "project_active"


def test_a_door_without_a_chip_plans_with_a_none_chip():
    (stop,) = plan_route([door(pin(1), north_m=100)], hours=2.0, start_point=START).stops

    assert stop.reason_chip is None


# --- talk track: the V2 template map (R30, R15, R19, PRD R7.2.1) --------------------
#
# Deterministic templates keyed by V2 evidence type. The map must cover every
# type the engine can emit; the templates are inspected — not generated — so
# the phrasing rules are testable as strings.

#: The engine's own V2 reason prose, verbatim from `scoring/v2.py`. Written for
#: the evidence panel; never spoken at a door.
V2_REASON_SENTENCES = [
    "active qualifying project with recent lifecycle activity",
    "assessed value above the median of the nearest comparables",
    "assessed value ranks high among territory single-family properties",
    "neighborhood-level ACS dual-income prior at or above 35% (block-group prior, not a household claim)",
    "verified historical exterior-condition decline between the 2015 and 2020 imagery vintages",
    "current verified rental registration demotes the door",
    "recent valid arm's-length move blends the score toward the mover priority band",
]

#: Words that only appear in prose derived from the file. None may be spoken.
FILE_WORDS = [
    "permit",
    "assessed",
    "median",
    "percentile",
    "comparable",
    "census",
    "block group",
    "block-group",
    "dual-income",
    "dual income",
    "acs",
    "imagery",
    "vintage",
    "deed",
    "parcel",
    "registration",
    "rental",
    "score",
    "evidence",
    "category",
]

ALL_ANGLES = sorted(
    {id(angle): angle for angle in [*route_module.ANGLES.values(), route_module.DEFAULT_ANGLE]}.values(),
    key=lambda angle: angle.hook,
)

#: Words no sentence a rep reads aloud may end on.
DANGLING_ENDINGS = {"and", "or", "but", "than", "rather", "the", "a", "an", "of", "which"}


def sentences_of(track):
    return [part.strip() for part in re.split(r"[.?!]", track) if part.strip()]


def test_the_template_map_covers_every_v2_evidence_type():
    """R30 exhaustiveness: an engine signal without a template cannot ship.
    Coverage is explicit keys — falling through to the default is a decision
    the map records, not an accident of a missing key."""
    assert set(V2_EVIDENCE_TYPES) <= set(route_module.ANGLES), sorted(
        set(V2_EVIDENCE_TYPES) - set(route_module.ANGLES)
    )


def test_the_template_map_holds_no_v1_evidence_types():
    """No key of the map names a V1 signal: the old vocabulary is gone (R27)."""
    v1_types = {
        "deed_recency",
        "tenure",
        "non_arms_length_transfer",
        "condition_trajectory",
        "deferred_maintenance",
        "permit_history",
        "provider_churn",
        "pool",
        "home_age",
        "lot_size",
        "assessed_value",
        "acs_dual_income_prior",
        "absentee_likely",
    }

    assert set(route_module.ANGLES) & v1_types == set()


@pytest.mark.parametrize("unspeakable", UNSPEAKABLE_TYPES)
def test_percentile_and_local_ratio_map_to_the_angle_that_says_nothing(unspeakable):
    """PRD R7.2.1: unspeakable at the door — the template map sends them to the
    default angle, which never mentions the house."""
    candidate = door(pin(1), north_m=100, evidence_type=unspeakable)

    assert route_module.angle_for(candidate) is route_module.DEFAULT_ANGLE


def test_the_acs_prior_never_reaches_the_doorstep_as_a_household_claim():
    """R15: the prior is neighborhood-level. The safest true phrasing at a door
    is none at all — no census, income or prior vocabulary is ever spoken."""
    track = talk_track_for(
        door(pin(1), north_m=100, evidence_type="capacity_acs_dual_income_prior")
    ).lower()

    for word in ["dual-income", "dual income", "census", "block group", "income", "prior"]:
        assert word not in track, f"{word!r} reached the doorstep"


def test_condition_decline_is_never_spoken_as_current_condition():
    """R19: the signal is historical decline between imagery vintages, and no
    template speaks about the state of the house at all."""
    track = talk_track_for(door(pin(1), north_m=100, evidence_type="fit_condition_decline")).lower()

    for word in ["condition", "declin", "poor", "run down", "run-down", "slipping", "deferred"]:
        assert word not in track


def test_no_template_anywhere_claims_the_household_is_dual_income():
    """R15 by template inspection, over every authored hook and branch line."""
    for angle in ALL_ANGLES:
        spoken = " ".join([angle.hook, *[b.line for b in angle.branches]]).lower()
        assert "dual-income" not in spoken
        assert "dual income" not in spoken
        assert "two incomes" not in spoken


def test_no_template_anywhere_speaks_about_current_condition():
    """R19 by template inspection: nothing a rep says describes the house's
    present state."""
    for angle in ALL_ANGLES:
        spoken = " ".join([angle.hook, *[b.line for b in angle.branches]]).lower()
        for phrase in ["poor condition", "falling apart", "run down", "run-down", "declining"]:
            assert phrase not in spoken


@pytest.mark.parametrize("evidence_type", V2_EVIDENCE_TYPES + [None, "brand_new_signal"])
def test_no_opener_ever_recites_the_file(evidence_type):
    """R7.2: the words a homeowner hears never reveal a parcel record — checked
    across the full V2 registry, the empty case, and an unmapped future type."""
    track = talk_track_for(door(pin(1), north_m=100, evidence_type=evidence_type)).lower()

    for word in FILE_WORDS:
        assert word not in track, f"{word!r} reached the doorstep for {evidence_type!r}"


@pytest.mark.parametrize("sentence", V2_REASON_SENTENCES, ids=range(len(V2_REASON_SENTENCES)))
def test_an_evidence_reason_is_never_spoken_whole_or_in_part(sentence):
    """The panel shows the V2 reason; the rep does not say it."""
    run = sentence.strip().lower()[:40]

    for evidence_type in [*V2_EVIDENCE_TYPES, None]:
        spoken = talk_track_for(door(pin(1), north_m=100, evidence_type=evidence_type)).lower()
        assert run not in spoken


def test_the_template_map_is_deterministic():
    """Same type, same words — twice, and across two doors on one street."""
    first = talk_track_for(door(pin(1), north_m=100, evidence_type="project_active"))
    second = talk_track_for(door(pin(2), north_m=200, evidence_type="project_active"))

    assert first == talk_track_for(door(pin(1), north_m=100, evidence_type="project_active"))
    assert first == second


@pytest.mark.parametrize("evidence_type", V2_EVIDENCE_TYPES)
def test_every_v2_type_opens_on_its_mapped_angle_hook(evidence_type):
    """Template inspection is only meaningful if the opener really ends on the
    mapped template — nothing appended after the question."""
    candidate = door(pin(1), north_m=100, evidence_type=evidence_type)

    assert talk_track_for(candidate).endswith(route_module.angle_for(candidate).hook)


def test_a_talk_track_is_a_one_line_opener():
    track = talk_track_for(door(pin(1), north_m=100, evidence_type="mover_recency"))

    assert isinstance(track, str)
    assert track.strip()
    assert "\n" not in track
    assert len(track) <= 200
    assert track.strip().endswith("?"), "the opener stops on a question and waits"


def test_an_unknown_evidence_type_degrades_to_a_safe_opener():
    candidate = door(pin(1), north_m=100, evidence_type="signal_invented_next_quarter")

    assert route_module.angle_for(candidate) is route_module.DEFAULT_ANGLE
    assert talk_track_for(candidate).strip().endswith("?")


@pytest.mark.parametrize("angle", ALL_ANGLES, ids=lambda angle: angle.hook[:24])
def test_no_hook_is_a_tag_question(angle):
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
    for branch in angle.branches:
        assert branch.line.strip().endswith((".", "?", "!"))
        assert "\n" not in branch.line
        for sentence in sentences_of(branch.line):
            assert sentence.split()[-1].lower() not in DANGLING_ENDINGS, sentence


@pytest.mark.parametrize("angle", ALL_ANGLES, ids=lambda angle: angle.hook[:24])
def test_no_branch_opens_on_a_scripted_acknowledgement(angle):
    for branch in angle.branches:
        first = branch.line.split()[0].strip(",.").lower()
        assert first not in {"figured", "exactly", "thought", "knew"}


def test_a_door_with_no_evidence_still_gets_a_door_specific_opener():
    track = talk_track_for(door(pin(1), north_m=100, evidence_type=None))

    assert "FAWN HILL" in track.upper()


def test_the_opener_says_the_street_the_way_a_rep_would():
    track = talk_track_for(door(pin(1), north_m=100, evidence_type=None))

    assert "Fawn Hill Rd" in track
    assert "FAWN HILL RD" not in track


def test_a_door_with_no_readable_street_still_opens_somewhere_sayable():
    nameless = replace(door(pin(1), north_m=100), address="")

    assert "this block" in talk_track_for(nameless)


def test_every_stop_carries_the_talk_track_and_branches_the_module_generates():
    doors = [replace(candidate, evidence_type="mover_recency") for candidate in WORKED_DOORS]
    by_pin = {candidate.pams_pin: candidate for candidate in doors}

    planned = plan_route(doors, hours=2.0, start_point=START)

    assert planned.stops
    for stop in planned.stops:
        assert stop.talk_track == talk_track_for(by_pin[stop.pams_pin])
        assert stop.talk_track_branches == talk_track_branches_for(by_pin[stop.pams_pin])
        assert stop.talk_track_branches


def test_the_talk_track_never_moves_the_score_or_the_order():
    loud = [replace(candidate, evidence_type="fit_pool") for candidate in WORKED_DOORS]

    planned = plan_route(loud, hours=2.0, start_point=START)
    reference = worked_route()

    assert pins_of(planned) == pins_of(reference)
    assert [stop.score for stop in planned.stops] == [stop.score for stop in reference.stops]


# --- share links (mechanics unchanged; versioning pinned above) ----------------------


def test_a_share_link_preserves_order_not_just_membership():
    decoded = decode_share(encode_share(worked_route().stops))

    assert list(decoded) != sorted(decoded)


def test_a_share_link_round_trips_a_long_route():
    stops = plan_route(grid_doors(), hours=2.0, start_point=START).stops[:20]

    assert list(decode_share(encode_share(stops))) == [stop.pams_pin for stop in stops]


def test_a_share_link_needs_no_url_escaping():
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


# --- adapting the pipeline's doors ---------------------------------------------------


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
        ("evidence_type", "mover_recency"),
    ],
)
def test_a_door_facts_adapts_into_a_route_door(attribute, expected):
    adapted = route_door_from_facts(facts(), score=81, evidence_type="mover_recency")

    assert getattr(adapted, attribute) == expected


def test_the_adapter_is_duck_typed_not_bound_to_door_facts():
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
    source = inspect.getsource(route_module)

    for forbidden in ("houseaccount.resolve", "houseaccount.sources", "houseaccount.territory"):
        assert forbidden not in source, f"route.py must not import {forbidden}"


def test_the_planner_does_not_import_the_dead_v1_engine():
    """R27: engine.py/weights.py are deleted; nothing in route.py may name them."""
    source = inspect.getsource(route_module)

    for forbidden in ("scoring.engine", "scoring.weights", "import engine"):
        assert forbidden not in source, f"route.py must not reference {forbidden}"
