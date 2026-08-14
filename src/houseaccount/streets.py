"""The walkable street network, derived from the parcels themselves (R10.2).

A rep walks along streets. The planner used to measure a leg as the straight
line between two parcel centroids, which is fine as an *estimate of time* and
wrong as a *description of the walk*: on the map it drew the rep through the
middle of a block, and between two houses that back onto each other it charged
40 metres for a walk that is really the length of two streets.

**Where a street network comes from when there is no basemap.** R12 rules out a
tile provider and a paid routing API, and the published run contains no road
layer — but it does contain every parcel polygon in the territory, and the
roadway is the one place no parcel covers. So the streets are the *gaps*, and
the centreline of a gap is its medial axis. The medial axis of the space between
polygons is approximated by the Voronoi diagram of points sampled along their
boundaries: every Voronoi edge is equidistant from the two nearest boundary
samples, so an edge running down a gap runs down the middle of the road.
`shapely` (already a dependency, for parcel geometry) computes it in a tenth of
a second for 540 parcels.

Three filters turn that diagram into streets:

* an edge that crosses a parcel is not walkable — it is somebody's house;
* an edge more than `MAX_CLEARANCE_M` from the nearest parcel is not a street
  but open ground beyond the edge of the territory;
* a dead-end branch shorter than `SPUR_MIN_M` is the Voronoi's answer to two
  neighbouring lot lines, not a road, and is pruned away.

What survives is one connected network of centrelines. Doors attach to it at
their nearest node — the parcel's frontage — and a leg is then
`door → frontage → along the streets → frontage → door`. The last hop is the
walk up the driveway, which is why it is measured from the parcel's centroid:
that is where the map draws the door and where the rep actually ends up.

**Why this is not the pipeline's job.** The network is derived entirely from
`doors.geojson`, has no source of its own, and is a property of the run rather
than a fact about a door. Building it at boot costs the server a third of a
second and keeps the published artifacts exactly what R11.1 says they are.

**Why an unusable network is `None` rather than an empty one.** The derivation
assumes parcels dense enough that the gaps between them are roads. Hand a
builder three squares in an empty field and the Voronoi edges between them are
not streets, so `build_walk_network` says so by returning `None` and the planner
falls back to its straight-line estimate. `_is_plausible` is that check.
"""

from __future__ import annotations

import heapq
import math
from collections import defaultdict
from typing import Any, Iterable, Mapping, Sequence

from shapely import normalize
from shapely.geometry import LineString, MultiPoint, Point, box, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform, unary_union, voronoi_diagram
from shapely.strtree import STRtree

Coordinate = tuple[float, float]

#: Metres per degree at Ramsey's latitude, as `web/js/streets.js` uses for the
#: same job: one local flat projection over a territory two kilometres across,
#: where the error in treating it as flat is centimetres.
M_PER_DEG_LAT = 110540.0
M_PER_DEG_LNG = 111320.0

#: How finely parcel boundaries are sampled before the Voronoi diagram. Smaller
#: is a smoother centreline and a bigger diagram; 6 m is well under the width of
#: any street and gives a centreline within a metre of the true medial axis.
BOUNDARY_STEP_M = 6.0

#: Beyond this from the nearest parcel a gap is not a street: it is the field
#: past the last house, or the space outside the territory's edge.
MAX_CLEARANCE_M = 30.0

#: A Voronoi edge longer than this spans open ground rather than a roadway.
MAX_EDGE_M = 60.0

#: Dead-end branches shorter than this are the diagram's answer to a side yard
#: — two neighbouring lot lines with a metre between them — not a road.
SPUR_MIN_M = 25.0

#: Endpoints within this distance are the same junction. The Voronoi's segments
#: already share exact endpoints; the grid is what makes floating-point equality
#: safe to rely on.
SNAP_M = 0.5

#: A network is only streets if the parcels it came from are close to it. Half a
#: wide road is about 15 m, so a median frontage walk above this says the lines
#: are not roadways and the network is refused whole (see `_is_plausible`).
MAX_PLAUSIBLE_MEDIAN_M = 25.0

#: Below this there is no network to speak of — a couple of stray segments.
MIN_NODES = 12

#: Simplification tolerance for the drawn path. The centreline carries a vertex
#: every few metres; a metre of tolerance drops the ones a straight run does not
#: need and leaves every corner where it was.
PATH_SIMPLIFY_M = 1.0

#: What the route says about its own numbers when it was measured on the
#: network. The planner ships this instead of its straight-line wording, so both
#: surfaces disclose the model that actually produced the figure (R10.2).
NETWORK_DISCLOSURE = (
    "Walking times follow the streets between the parcels, at 3 mph — "
    "estimated from parcel geometry, not turn-by-turn directions."
)


class WalkNetwork:
    """The territory's streets as a graph, and the walks measured along it.

    Two questions, in the order the planner asks them: `metres_from` measures
    one origin against every candidate door at once (one shortest-path sweep),
    then `path` draws the single leg that won. The sweep is memoised for exactly
    one origin, which is all it takes for the second question to be free —
    `plan_route` always asks it about the origin it just asked about.

    Coordinates in and out are `(lon, lat)`, GeoJSON order, as everywhere else.
    """

    def __init__(
        self,
        adjacency: Mapping[Any, Mapping[Any, float]],
        coordinates: Mapping[Any, Coordinate],
        access: Mapping[Coordinate, Any],
        scale: tuple[float, float],
        disclosure: str = NETWORK_DISCLOSURE,
    ) -> None:
        self._adjacency = adjacency
        self._coordinates = coordinates
        self._access = access
        self._scale = scale
        self.disclosure = disclosure

        # Indexed in metres, not degrees: a degree of longitude here is three
        # quarters of a degree of latitude, and "nearest" measured in degrees
        # would hand a door the street on the wrong side of it.
        self._nodes = list(adjacency)
        self._tree = STRtree(
            [Point(_project(coordinates[node], scale)) for node in self._nodes]
        )
        self._sweep: tuple[Coordinate, dict[Any, float], dict[Any, Any]] | None = None

    # --- what the planner asks -------------------------------------------------

    def metres_from(
        self, origin: Coordinate, destinations: Sequence[Coordinate]
    ) -> tuple[float | None, ...]:
        """Walked metres from `origin` to each destination, `None` where the
        network cannot join them.

        `None` is an ordinary answer — a door on an island of parcels the
        centrelines never reach — and the planner falls back to its straight
        line for that leg rather than dropping the door.
        """
        origin_node, distances, _ = self._sweep_from(origin)
        if origin_node is None:
            return tuple(None for _ in destinations)

        origin_walk = self._frontage_metres(origin, origin_node)
        legs: list[float | None] = []
        for destination in destinations:
            if destination == origin:
                legs.append(0.0)
                continue
            node = self._node_for(destination)
            along = None if node is None else distances.get(node)
            if along is None:
                legs.append(None)
                continue
            legs.append(origin_walk + along + self._frontage_metres(destination, node))
        return tuple(legs)

    def path(self, origin: Coordinate, destination: Coordinate) -> tuple[Coordinate, ...]:
        """The leg as a drawn line: off the origin, along the streets, up the
        destination's driveway. Empty when the two are not joined.

        The street run is simplified to `PATH_SIMPLIFY_M`, which removes the
        centreline's every-few-metres vertices from straight runs and leaves
        every corner: the map draws the same walk with a tenth of the payload.
        """
        if destination == origin:
            return (origin,)

        origin_node, _, previous = self._sweep_from(origin)
        node = self._node_for(destination)
        if origin_node is None or node is None or (node not in previous and node != origin_node):
            return ()

        chain = [node]
        while chain[-1] != origin_node:
            step = previous.get(chain[-1])
            if step is None:
                return ()
            chain.append(step)
        chain.reverse()

        street = [self._coordinates[step] for step in chain]
        return (origin, *self._simplified(street), destination)

    # --- internals -------------------------------------------------------------

    def _sweep_from(self, origin: Coordinate):
        """Dijkstra from the node `origin` attaches to, memoised for one origin.

        One entry is the whole cache: the planner measures every candidate from
        where the rep stands, then asks for the winning leg's line from that
        same place. A larger cache would hold routes nobody is going to ask for
        again — the rep has moved on.
        """
        if self._sweep is not None and self._sweep[0] == origin:
            return (self._node_for(origin), self._sweep[1], self._sweep[2])

        source = self._node_for(origin)
        if source is None:
            return (None, {}, {})

        distances: dict[Any, float] = {source: 0.0}
        previous: dict[Any, Any] = {}
        queue: list[tuple[float, Any]] = [(0.0, source)]
        while queue:
            walked, node = heapq.heappop(queue)
            if walked > distances.get(node, math.inf):
                continue
            for neighbour, length in self._adjacency[node].items():
                through = walked + length
                if through < distances.get(neighbour, math.inf):
                    distances[neighbour] = through
                    previous[neighbour] = node
                    heapq.heappush(queue, (through, neighbour))

        self._sweep = (origin, distances, previous)
        return (source, distances, previous)

    def _node_for(self, point: Coordinate) -> Any:
        """Where a point joins the streets.

        A door the network was built from has an attachment chosen from its
        whole parcel — the frontage, not the nearest thing to the middle of the
        lot, which on a corner plot can be the street round the back. Any other
        point (the rep's parking spot) simply snaps to the nearest node.
        """
        attached = self._access.get(point)
        if attached is not None:
            return attached
        if not self._nodes:
            return None
        found = self._tree.nearest(Point(_project(point, self._scale)))
        index = int(found[0]) if hasattr(found, "__len__") else int(found)
        return self._nodes[index]

    def _frontage_metres(self, point: Coordinate, node: Any) -> float:
        """The walk between a point and the street it joins the network at."""
        return _metres_between(point, self._coordinates[node], self._scale)

    def _simplified(self, street: Sequence[Coordinate]) -> tuple[Coordinate, ...]:
        if len(street) < 3:
            return tuple(street)
        kx, ky = self._scale
        flat = LineString([(lon * kx, lat * ky) for lon, lat in street])
        return tuple(
            (x / kx, y / ky) for x, y in flat.simplify(PATH_SIMPLIFY_M).coords
        )


def build_walk_network(
    parcels: Iterable[Mapping[str, Any] | BaseGeometry],
    door_points: Mapping[Coordinate, Mapping[str, Any] | BaseGeometry] | None = None,
) -> WalkNetwork | None:
    """The streets between `parcels`, or `None` when they do not describe any.

    `door_points` maps the point a door is drawn at — its centroid — to the
    parcel it belongs to, so each door attaches to the network at its own
    frontage. Without it every point snaps to the nearest centreline node,
    which is the same answer for all but deep and corner lots.
    """
    shapes = [geometry for geometry in map(_as_shape, parcels) if geometry is not None]
    if len(shapes) < 2:
        return None

    # Everything below is order-independent on purpose: two processes handed the
    # same run in different orders must plan the same walk (R10.2's determinism
    # is about the route, and the route is measured on this). The scale comes
    # from the territory's extent rather than from whichever parcel arrived
    # first, and the union is normalised before it is sampled because GEOS is
    # free to hand back the same rings starting at a different vertex.
    bounds = unary_union([geometry.envelope for geometry in shapes]).bounds
    scale = _scale_at((bounds[1] + bounds[3]) / 2)
    flat = [_flatten(geometry, scale) for geometry in shapes]
    blocks = normalize(unary_union(flat))
    if blocks.is_empty:
        return None

    samples = sorted(_boundary_samples(blocks))
    if len(samples) < 3:
        return None

    adjacency, coordinates = _centrelines(samples, flat, blocks.bounds, scale)
    adjacency = _largest_component(_pruned(adjacency))
    if len(adjacency) < MIN_NODES:
        return None

    network_points = {node: coordinates[node] for node in adjacency}
    access = _access_points(door_points or {}, network_points, scale)
    if not _is_plausible(flat, network_points, scale):
        return None

    return WalkNetwork(adjacency, network_points, access, scale)


# --- derivation ---------------------------------------------------------------


def _as_shape(parcel: Mapping[str, Any] | BaseGeometry | None) -> BaseGeometry | None:
    """A parcel geometry as shapely, or `None` for anything unreadable.

    A door with no polygon is an ordinary state in the published data (R10.1),
    so it contributes nothing to the streets rather than failing the build.
    """
    if parcel is None:
        return None
    if isinstance(parcel, BaseGeometry):
        geometry = parcel
    else:
        try:
            geometry = shape(dict(parcel))
        except (AttributeError, KeyError, TypeError, ValueError):
            return None
    if geometry.is_empty or geometry.geom_type not in ("Polygon", "MultiPolygon"):
        return None
    return geometry


def _scale_at(latitude: float) -> tuple[float, float]:
    """Metres per degree of longitude and of latitude, locally."""
    return (M_PER_DEG_LNG * math.cos(math.radians(latitude)), M_PER_DEG_LAT)


def _flatten(geometry: BaseGeometry, scale: tuple[float, float]) -> BaseGeometry:
    kx, ky = scale
    return transform(lambda x, y: (x * kx, y * ky), geometry)


def _boundary_samples(blocks: BaseGeometry) -> list[Coordinate]:
    """Points every `BOUNDARY_STEP_M` around the built-up edges.

    The Voronoi diagram of these is what the centrelines are read off: an edge
    of that diagram is equidistant from the two boundary points nearest it, so
    an edge inside a gap sits down the middle of the gap.
    """
    samples: list[Coordinate] = []
    polygons = blocks.geoms if blocks.geom_type == "MultiPolygon" else [blocks]
    for polygon in polygons:
        for ring in (polygon.exterior, *polygon.interiors):
            line = LineString(ring.coords)
            steps = max(int(line.length // BOUNDARY_STEP_M), 1)
            for index in range(steps):
                point = line.interpolate(index / steps, normalized=True)
                samples.append((point.x, point.y))
    return samples


def _centrelines(
    samples: Sequence[Coordinate],
    parcels: Sequence[BaseGeometry],
    bounds: tuple[float, float, float, float],
    scale: tuple[float, float],
) -> tuple[dict[Any, dict[Any, float]], dict[Any, Coordinate]]:
    """The Voronoi edges that are roadway, as a graph in (lon, lat).

    Nodes are keyed by their position on a half-metre grid, which is what welds
    the diagram's separate segments into one network: two segments that meet at
    a junction produce the same key and therefore the same node.
    """
    minx, miny, maxx, maxy = bounds
    envelope = box(minx - 50, miny - 50, maxx + 50, maxy + 50)
    diagram = voronoi_diagram(MultiPoint(list(samples)), envelope=envelope, edges=True)

    tree = STRtree(parcels)
    kx, ky = scale
    adjacency: dict[Any, dict[Any, float]] = defaultdict(dict)
    coordinates: dict[Any, Coordinate] = {}

    for line in _lines(diagram):
        points = list(line.coords)
        for start, end in zip(points, points[1:]):
            segment = LineString([start, end])
            if segment.length > MAX_EDGE_M or not envelope.contains(segment):
                continue
            # Through a house is not a walk, and a gap this far from any parcel
            # is open ground rather than a street.
            if len(tree.query(segment, predicate="intersects")):
                continue
            middle = segment.interpolate(0.5, normalized=True)
            if _nearest_distance(tree, parcels, middle) > MAX_CLEARANCE_M:
                continue

            head, tail = _key(start), _key(end)
            if head == tail:
                continue
            coordinates.setdefault(head, (start[0] / kx, start[1] / ky))
            coordinates.setdefault(tail, (end[0] / kx, end[1] / ky))
            length = segment.length
            if adjacency[head].get(tail, math.inf) > length:
                adjacency[head][tail] = length
                adjacency[tail][head] = length

    return dict(adjacency), coordinates


def _lines(geometry: BaseGeometry) -> Iterable[BaseGeometry]:
    """Every single-part geometry inside a possibly nested collection."""
    if hasattr(geometry, "geoms"):
        for part in geometry.geoms:
            yield from _lines(part)
    else:
        yield geometry


def _key(point: Sequence[float]) -> tuple[int, int]:
    return (round(point[0] / SNAP_M), round(point[1] / SNAP_M))


def _nearest_distance(
    tree: STRtree, candidates: Sequence[BaseGeometry], geometry: BaseGeometry
) -> float:
    """How far `geometry` is from the nearest of the indexed `candidates`."""
    found = tree.nearest(geometry)
    index = int(found[0]) if hasattr(found, "__len__") else int(found)
    return candidates[index].distance(geometry)


def _pruned(adjacency: dict[Any, dict[Any, float]]) -> dict[Any, dict[Any, float]]:
    """Drop dead-end branches shorter than `SPUR_MIN_M`.

    The diagram grows a stub into every side yard and every notch in a lot line.
    They cost nothing to route through — nothing leads anywhere off them — but
    they attract door attachments to a "street" that is really the gap between
    two houses, so they go. Removing one can expose another, so this repeats
    until nothing changes.
    """
    graph = {node: dict(edges) for node, edges in adjacency.items()}
    changed = True
    while changed:
        changed = False
        for node in [node for node, edges in graph.items() if len(edges) == 1]:
            if node not in graph:
                continue
            branch, length = _branch_from(graph, node)
            if length >= SPUR_MIN_M:
                continue
            for member in branch:
                for neighbour in list(graph.get(member, {})):
                    graph[neighbour].pop(member, None)
                graph.pop(member, None)
            changed = True
    return {node: edges for node, edges in graph.items() if edges}


def _branch_from(graph: dict[Any, dict[Any, float]], leaf: Any) -> tuple[list[Any], float]:
    """The run from a dead end up to the first junction, and how long it is."""
    branch = [leaf]
    length = 0.0
    previous, current = None, leaf
    while True:
        onward = [node for node in graph[current] if node != previous]
        if len(onward) != 1:
            return branch, length
        step = onward[0]
        length += graph[current][step]
        previous, current = current, step
        if len(graph[current]) > 2 or current in branch:
            return branch, length
        branch.append(current)
        if len(graph[current]) == 1:
            return branch, length


def _largest_component(adjacency: dict[Any, dict[Any, float]]) -> dict[Any, dict[Any, float]]:
    """The one network everything routes on.

    A territory's streets are joined; the leftovers are short fragments the
    filters cut off from the rest, and a door attached to one of those could
    reach nothing. Keeping only the biggest component means such a door attaches
    to the real network instead, a little further away.
    """
    seen: set[Any] = set()
    best: list[Any] = []
    for node in adjacency:
        if node in seen:
            continue
        stack, component = [node], []
        seen.add(node)
        while stack:
            current = stack.pop()
            component.append(current)
            for neighbour in adjacency[current]:
                if neighbour not in seen:
                    seen.add(neighbour)
                    stack.append(neighbour)
        if len(component) > len(best):
            best = component

    keep = set(best)
    return {
        node: {other: length for other, length in edges.items() if other in keep}
        for node, edges in adjacency.items()
        if node in keep
    }


def _access_points(
    door_points: Mapping[Coordinate, Mapping[str, Any] | BaseGeometry],
    network_points: Mapping[Any, Coordinate],
    scale: tuple[float, float],
) -> dict[Coordinate, Any]:
    """Each door's node on the network: the one nearest its *parcel*.

    Nearest-to-the-centroid is the wrong question on a deep lot or a corner
    plot, where the closest centreline can be the street the house backs onto.
    The parcel reaches its own frontage, so the parcel is what gets measured.
    """
    if not door_points or not network_points:
        return {}

    nodes = list(network_points)
    tree = STRtree([Point(_project(network_points[node], scale)) for node in nodes])

    access: dict[Coordinate, Any] = {}
    for point, parcel in door_points.items():
        geometry = _as_shape(parcel)
        if geometry is None:
            continue
        found = tree.nearest(_flatten(geometry, scale))
        index = int(found[0]) if hasattr(found, "__len__") else int(found)
        access[point] = nodes[index]
    return access


def _is_plausible(
    parcels: Sequence[BaseGeometry],
    network_points: Mapping[Any, Coordinate],
    scale: tuple[float, float],
) -> bool:
    """Are these lines the streets of this territory, or lines in a field?

    The medial axis of *any* set of polygons exists; it is only a street network
    when the polygons are a neighbourhood — parcels packed either side of the
    gaps. So the test is the typical parcel's walk to the network: half a road
    where that holds, and much further where the "streets" are the empty space
    between a handful of scattered lots. A territory that fails this gets no
    network and the planner keeps its straight-line estimate.
    """
    nodes = [Point(_project(point, scale)) for point in network_points.values()]
    if not nodes:
        return False

    tree = STRtree(nodes)
    walks = sorted(_nearest_distance(tree, nodes, parcel) for parcel in parcels)
    return walks[len(walks) // 2] <= MAX_PLAUSIBLE_MEDIAN_M


def _project(point: Coordinate, scale: tuple[float, float]) -> Coordinate:
    kx, ky = scale
    return (point[0] * kx, point[1] * ky)


def _metres_between(
    origin: Coordinate, destination: Coordinate, scale: tuple[float, float]
) -> float:
    kx, ky = scale
    return math.hypot((destination[0] - origin[0]) * kx, (destination[1] - origin[1]) * ky)
