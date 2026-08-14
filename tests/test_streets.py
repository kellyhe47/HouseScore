"""The walkable street network derived from parcels (R10.2).

*"A person can only walk along a street."* That is the whole requirement, and
everything here is a way of asking it. The planner used to charge a rep the
straight-line distance between two centroids, which on the map drew a walk
through the middle of a block and through other people's houses.

**Why the fixtures are a town and not a fixture file.** The derivation reads the
gaps between parcels, so the input has to be a layout with gaps in it — a
handful of squares on disk would prove nothing about a street grid. `town()`
below lays out blocks of parcels either side of roads at a stated width, in
metres, around a point in Ramsey; every distance assertion is then in metres a
reader can pace out. The nastiest case in the product is two houses that back
onto each other: forty metres apart, and a walk of two streets between them.

**Why `None` is a first-class answer.** The medial axis of any set of polygons
exists, but it is only a street network when the polygons are a neighbourhood.
Three lots scattered in a field produce lines through open ground, so
`build_walk_network` refuses and the planner keeps its straight-line estimate
(`test_scattered_parcels_are_not_a_street_network`). That refusal is what keeps
every existing caller — and the whole test suite's synthetic doors — working.

No network, no fixtures on disk.
"""

import math
import time

import pytest
from shapely.geometry import LineString, Point, shape
from shapely.ops import unary_union

from houseaccount.route import RouteDoor, plan_route
from houseaccount.streets import (
    MAX_CLEARANCE_M,
    NETWORK_DISCLOSURE,
    build_walk_network,
)

#: Somewhere in the middle of the published territory, (lon, lat).
ORIGIN = (-74.1560, 41.0447)

#: Metres per degree at that latitude; the same figures `streets.py` projects with.
M_PER_DEG_LAT = 110540.0
M_PER_DEG_LNG = 111320.0 * math.cos(math.radians(ORIGIN[1]))

#: The fixture town's shape, in metres: lots this deep and this wide, in blocks
#: two lots deep (so back-to-back neighbours share a rear lot line) and
#: `LOTS_PER_BLOCK` wide, with a road along every block edge — the cross streets
#: matter as much as the long ones, because a network of parallel roads that
#: never meet is not a network.
LOT_WIDTH_M = 25.0
LOT_DEPTH_M = 35.0
LOTS_PER_BLOCK = 5
ROAD_WIDTH_M = 12.0
BLOCK_DEPTH_M = 2 * LOT_DEPTH_M
BLOCK_WIDTH_M = LOTS_PER_BLOCK * LOT_WIDTH_M

#: How many blocks the town is, each way. Four, because only a block with
#: another block on every side has a road on every side, and the assertions
#: below are about lots that front a street — which is every real lot in a
#: territory and only the interior ones in a grid that stops somewhere.
BLOCKS = 4


def at(east_m: float, north_m: float) -> tuple[float, float]:
    """A point `east_m`/`north_m` from `ORIGIN`, in (lon, lat)."""
    return (ORIGIN[0] + east_m / M_PER_DEG_LNG, ORIGIN[1] + north_m / M_PER_DEG_LAT)


def metres_apart(origin: tuple[float, float], destination: tuple[float, float]) -> float:
    return math.hypot(
        (destination[0] - origin[0]) * M_PER_DEG_LNG,
        (destination[1] - origin[1]) * M_PER_DEG_LAT,
    )


def lot(east_m: float, north_m: float, width: float, depth: float) -> dict:
    """One parcel as GeoJSON, its south-west corner at (east_m, north_m)."""
    corners = [
        at(east_m, north_m),
        at(east_m + width, north_m),
        at(east_m + width, north_m + depth),
        at(east_m, north_m + depth),
        at(east_m, north_m),
    ]
    return {"type": "Polygon", "coordinates": [[list(point) for point in corners]]}


def centroid(parcel: dict) -> tuple[float, float]:
    point = shape(parcel).centroid
    return (point.x, point.y)


def town(blocks: int = BLOCKS, lots: int = LOTS_PER_BLOCK) -> dict[str, dict]:
    """A grid of lots in blocks, separated by roads, keyed by a readable name.

    `S{row}{column}{index}` is the `index`th lot along the south side of the
    block at (`row`, `column`), and `N…` the north side. The two sides share a
    rear lot line, so `S110` and `N110` back onto each other with no way
    between them — the case the straight-line planner got wrong.
    """
    parcels: dict[str, dict] = {}
    block_width = lots * LOT_WIDTH_M
    for row in range(blocks):
        north = row * (BLOCK_DEPTH_M + ROAD_WIDTH_M)
        for column in range(blocks):
            east = column * (block_width + ROAD_WIDTH_M)
            for index in range(lots):
                left = east + index * LOT_WIDTH_M
                # The south side fronts the road below the block, the north side
                # the road above it.
                parcels[f"S{row}{column}{index}"] = lot(
                    left, north, LOT_WIDTH_M, LOT_DEPTH_M
                )
                parcels[f"N{row}{column}{index}"] = lot(
                    left, north + LOT_DEPTH_M, LOT_WIDTH_M, LOT_DEPTH_M
                )
    return parcels


@pytest.fixture(scope="module")
def parcels() -> dict[str, dict]:
    return town()


@pytest.fixture(scope="module")
def network(parcels):
    points = {centroid(parcel): parcel for parcel in parcels.values()}
    built = build_walk_network(list(parcels.values()), points)
    assert built is not None, "a grid of lots either side of roads is a street network"
    return built


def door(parcels, name: str) -> tuple[float, float]:
    return centroid(parcels[name])


def walked(network, origin, destination) -> float:
    (metres,) = network.metres_from(origin, [destination])
    return metres


# --- the requirement ----------------------------------------------------------


def test_two_neighbours_on_the_same_street_walk_about_one_lot_width(parcels, network):
    """Next door is next door: out to the road, along one frontage, back in."""
    walk = walked(network, door(parcels, "S111"), door(parcels, "S112"))

    # Two driveways (half a lot deep each way) plus one lot width along the road.
    assert walk == pytest.approx(LOT_DEPTH_M + LOT_WIDTH_M, abs=15.0)


def test_back_to_back_neighbours_walk_round_the_block_not_through_it(parcels, network):
    """The case that made this necessary.

    `S111` and `N111` share a rear lot line — thirty-five metres apart as the crow
    flies, and no way between them that is not somebody's back garden. The walk
    is out to one road, along to the end of the block, and back up the other.
    """
    south, north = door(parcels, "S111"), door(parcels, "N111")

    straight = metres_apart(south, north)
    walk = walked(network, south, north)

    assert straight == pytest.approx(LOT_DEPTH_M, abs=1.0)
    assert walk > 4 * straight, f"{walk:.0f} m for a walk round the block"


def test_a_leg_never_crosses_a_parcel_except_up_the_two_driveways(parcels, network):
    """Between the frontages, the drawn walk stays on the road.

    The first and last hops are the driveways — they cross the parcel by
    construction, because the door is drawn in the middle of the lot. Every
    hop in between is on the network, and the network is the gaps.
    """
    blocks = unary_union([shape(parcel) for parcel in parcels.values()])
    path = network.path(door(parcels, "S111"), door(parcels, "N331"))

    assert len(path) > 3, "a walk across the town is more than a straight line"
    street = LineString(path[1:-1])
    overlap = street.intersection(blocks).length / street.length

    assert overlap < 0.02, "the street run of the leg is not on anybody's parcel"


def test_the_walk_between_two_doors_is_longer_than_the_straight_line(parcels, network):
    """Never shorter, whatever the pair: a street network can only detour."""
    names = ["S111", "S112", "N111", "N221", "S330", "N012"]
    doors = [door(parcels, name) for name in names]

    for origin in doors:
        for destination in doors:
            if origin == destination:
                continue
            assert walked(network, origin, destination) >= metres_apart(origin, destination)


def test_a_door_is_no_distance_from_itself(parcels, network):
    """The rep is already standing there — the leg is zero, not two driveways."""
    here = door(parcels, "S111")

    assert walked(network, here, here) == 0.0
    assert network.path(here, here) == (here,)


def test_the_path_starts_at_the_origin_and_ends_at_the_door(parcels, network):
    origin, destination = door(parcels, "S111"), door(parcels, "S113")

    path = network.path(origin, destination)

    assert path[0] == origin
    assert path[-1] == destination


def test_the_drawn_path_is_as_long_as_the_measured_walk(parcels, network):
    """The line on the map and the minutes beside it describe one walk.

    Not to the centimetre: the drawn line is simplified to a metre, which rounds
    the corners it keeps. A few metres over a few hundred is the tolerance that
    buys a payload the map can carry; anything further apart than that would be
    a route whose picture and whose numbers disagree.
    """
    origin, destination = door(parcels, "S111"), door(parcels, "N221")
    path = network.path(origin, destination)

    drawn = sum(metres_apart(step, following) for step, following in zip(path, path[1:]))

    assert drawn == pytest.approx(walked(network, origin, destination), rel=0.05)


def test_one_sweep_answers_for_every_candidate_at_once(parcels, network):
    """`metres_from` is asked about all remaining doors per step, and each answer
    is the same one it gives alone."""
    origin = door(parcels, "S111")
    destinations = [door(parcels, name) for name in ("S112", "N111", "S221", "N133")]

    together = network.metres_from(origin, destinations)
    apart = [walked(network, origin, destination) for destination in destinations]

    assert list(together) == apart


def test_the_same_town_builds_the_same_network(parcels):
    """Two builds, one answer: a rep who re-plans gets the walk they had."""
    points = {centroid(parcel): parcel for parcel in parcels.values()}
    first = build_walk_network(list(parcels.values()), points)
    second = build_walk_network(list(parcels.values())[::-1], points)

    origin, destination = door(parcels, "S111"), door(parcels, "N221")
    assert walked(first, origin, destination) == pytest.approx(
        walked(second, origin, destination), rel=1e-9
    )


def test_the_network_states_the_model_its_metres_came_from(network):
    assert network.disclosure == NETWORK_DISCLOSURE
    assert "3 mph" in network.disclosure
    assert "not turn-by-turn" in network.disclosure


# --- what is not a street network ---------------------------------------------


def test_scattered_parcels_are_not_a_street_network():
    """Three lots in a field: the gaps between them are fields, not roads."""
    scattered = [
        lot(0, 0, 20, 20),
        lot(400, 0, 20, 20),
        lot(0, 400, 20, 20),
    ]

    assert build_walk_network(scattered) is None


def test_one_parcel_is_not_a_street_network():
    assert build_walk_network([lot(0, 0, 30, 30)]) is None


def test_no_parcels_at_all_is_not_a_street_network():
    assert build_walk_network([]) is None


def test_a_door_with_no_geometry_contributes_nothing_and_breaks_nothing(parcels):
    """Unroutable doors are ordinary in the published data (R10.1)."""
    geometries = [*parcels.values(), None, {"type": "Point", "coordinates": [0, 0]}]

    assert build_walk_network(geometries) is not None


def test_a_point_far_outside_the_town_still_joins_the_network(parcels, network):
    """A rep parked off the edge of the territory walks in from wherever they are.

    They snap to the nearest street rather than becoming unroutable — the
    distance they are charged is the honest one, and it is large.
    """
    far = at(-2000.0, -2000.0)

    walk = walked(network, far, door(parcels, "S111"))

    assert walk is not None
    assert walk > 2000.0


# --- the derivation's own rules -----------------------------------------------


def test_every_node_is_in_a_gap_between_parcels(parcels, network):
    """The network is the roadway: outside every parcel, and beside one.

    Outside, because a line through a house is not walkable. Beside, because a
    line far from every parcel is open country rather than a street — that is
    what `MAX_CLEARANCE_M` draws the line at.
    """
    blocks = unary_union([shape(parcel) for parcel in parcels.values()])
    flat = unary_union(
        [
            shape(
                {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [x * M_PER_DEG_LNG, y * M_PER_DEG_LAT]
                            for x, y in shape(parcel).exterior.coords
                        ]
                    ],
                }
            )
            for parcel in parcels.values()
        ]
    )

    for point in network._coordinates.values():
        assert not blocks.covers(Point(point)), "a street is not inside a parcel"
        flat_point = Point(point[0] * M_PER_DEG_LNG, point[1] * M_PER_DEG_LAT)
        assert flat_point.distance(flat) <= MAX_CLEARANCE_M, "this is a field, not a road"


# --- planning on it -----------------------------------------------------------


def test_a_territory_of_540_doors_plans_on_the_streets_inside_the_budget():
    """R10.2's two seconds, against the model that measures the real walk.

    The derivation itself is a boot cost — the server builds the network once
    per published run, never per request — so it is timed separately and given
    room. What has to stay inside two seconds is what a rep waits for: the plan.
    """
    parcels = town(blocks=6, lots=8)
    assert len(parcels) >= 540, "a territory-sized fixture, like the published one"

    points = {centroid(parcel): parcel for parcel in parcels.values()}
    started = time.perf_counter()
    network = build_walk_network(list(parcels.values()), points)
    built = time.perf_counter() - started

    doors = [
        RouteDoor(
            pams_pin=f"0248_00101_{index:05d}",
            address=f"{index} GRID ST, Ramsey NJ 07446",
            # 20..100, spread so no two neighbours agree and greedy has to work.
            score=20 + (index * 37) % 81,
            centroid=point,
        )
        for index, point in enumerate(points)
    ]

    started = time.perf_counter()
    planned = plan_route(
        doors, hours=2.0, start_point=at(100, 100), max_doors=20, network=network
    )
    took = time.perf_counter() - started

    assert len(planned.stops) == 20
    assert took < 2.0, f"planning took {took:.2f}s"
    assert built < 5.0, f"deriving the streets took {built:.2f}s"


def test_a_planned_walk_stays_on_the_streets_the_whole_way(parcels, network):
    """End to end: every leg of a real plan is a walk along the roads.

    The route the rep sees is the one thing all of this is for, so the assertion
    is made against it rather than against the network alone — legs joined up,
    and no leg cutting across a block between its two driveways.
    """
    blocks = unary_union([shape(parcel) for parcel in parcels.values()])
    doors = [
        RouteDoor(
            pams_pin=name,
            address=f"{name} GRID ST, Ramsey NJ 07446",
            score=20 + (index * 37) % 81,
            centroid=centroid(parcel),
        )
        for index, (name, parcel) in enumerate(parcels.items())
    ]

    planned = plan_route(
        doors, hours=1.0, start_point=at(100, 100), max_doors=8, network=network
    )

    assert len(planned.stops) == 8
    position = at(100, 100)
    for stop in planned.stops:
        assert stop.path[0] == position
        assert stop.path[-1] == stop_centroid(parcels, stop.pams_pin)

        street = LineString(stop.path[1:-1]) if len(stop.path) > 3 else None
        if street is not None:
            overlap = street.intersection(blocks).length / street.length
            assert overlap < 0.02, f"leg to {stop.pams_pin} crosses a parcel"

        position = stop.path[-1]


def stop_centroid(parcels, name: str) -> tuple[float, float]:
    return centroid(parcels[name])
