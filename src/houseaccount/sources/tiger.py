"""Census TIGERweb block-group boundaries + point-in-polygon index (T006, R3.1).

ACS publishes its statistics per *block group*; a MOD-IV parcel carries only a
lon/lat centroid. Nothing else in the system can bridge those two, so this
module exists for one question: which block group contains this centroid?

Ground truth (verified live, Phase 0):

    https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/
        tigerWMS_ACS2022/MapServer/8/query
    where=STATE='34' AND COUNTY='003'
    outFields=GEOID,STATE,COUNTY,TRACT,BLKGRP
    f=geojson

Layer 8 is "Census Block Groups". Free, no key, and the same ArcGIS query shape
`sources/parcels.py` already walks — so the fetch goes through `http.fetch_json`
and the `Cache`, which is what makes a re-run cost nothing (R2.3).

Three design points worth stating rather than rediscovering:

* `GEOID` is requested even though state+county+tract+block group would rebuild
  it, because ACS keys its rows on the concatenation and a join must compare the
  string both sides actually publish, not one this module assembles.
* The vintage is pinned to the ACS2022 TIGERweb service, matching
  `acs.ACS_YEAR`. Block-group boundaries are redrawn between vintages, so a
  2020-vintage polygon carrying a 2022 GEOID would attach the wrong
  neighbourhood statistics to a door.
* A point on a shared edge is *covered* by both neighbours. `geoid_for` resolves
  that by lowest index — first boundary in fetch order wins — because a lookup
  that flipped between runs would silently move a door into a different ACS row.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from shapely import STRtree
from shapely.geometry import Point, shape

from houseaccount.cache import Cache
from houseaccount.http import Transport, fetch_json, requests_transport
from houseaccount.sources.acs import COUNTY_BERGEN, STATE_NJ

#: MapServer layer number for "Census Block Groups" in the ACS2022 service.
BLOCK_GROUP_LAYER = 8

#: The TIGERweb block-group query endpoint (layer 8 of the ACS2022 service).
TIGERWEB_BLOCK_GROUPS_URL = (
    "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/"
    f"tigerWMS_ACS2022/MapServer/{BLOCK_GROUP_LAYER}/query"
)

#: The layer's own `maxRecordCount`. A county holds hundreds of block groups,
#: so one page covers Bergen many times over; the walk below is the seatbelt.
MAX_RECORD_COUNT = 100_000

#: Every field the request is allowed to ask for — an allowlist, never `*`.
#: These five are exactly what `BlockGroupBoundary` carries; the layer's other
#: columns (area, land/water splits, display names) buy nothing and stay unread.
BLOCK_GROUP_OUT_FIELDS: tuple[str, ...] = ("GEOID", "STATE", "COUNTY", "TRACT", "BLKGRP")


@dataclass(frozen=True)
class BlockGroupBoundary:
    """One block group's GEOID, geography parts and polygon."""

    geoid: str = ""
    state: str = ""
    county: str = ""
    tract: str = ""
    block_group: str = ""
    geometry: Mapping[str, Any] | None = None


class TigerSource:
    """Cache-first reader for TIGERweb block-group boundaries."""

    def __init__(
        self,
        *,
        cache: Cache | None = None,
        transport: Transport = requests_transport,
        page_size: int | None = None,
    ) -> None:
        self.cache = cache
        self.transport = transport
        #: Never exceed the service cap: a larger ask is silently truncated,
        #: which would make the short-page terminator fire a page early.
        self.page_size = min(int(page_size or MAX_RECORD_COUNT), MAX_RECORD_COUNT)

    def fetch(
        self, state: str = STATE_NJ, county: str = COUNTY_BERGEN
    ) -> Sequence[BlockGroupBoundary]:
        """Every block group in one county, in the order the service returns it.

        Paged like every other source: an exactly-full page cannot be assumed
        final, so the walk stops on the short page. In practice a county fits in
        one request and the transport is called exactly once.
        """
        boundaries: list[BlockGroupBoundary] = []
        offset = 0
        while True:
            features = self._page(state=state, county=county, offset=offset)
            boundaries.extend(_to_boundaries(features))
            if len(features) < self.page_size:
                return boundaries
            offset += len(features)

    # --- internals ----------------------------------------------------------

    def _page(self, *, state: str, county: str, offset: int) -> list[Mapping[str, Any]]:
        """One page of GeoJSON features, from cache when warm."""
        payload = fetch_json(
            TIGERWEB_BLOCK_GROUPS_URL,
            params=self._params(state=state, county=county, offset=offset),
            cache=self.cache,
            transport=self.transport,
        )
        features = payload.get("features") if isinstance(payload, Mapping) else None
        return [feature for feature in (features or []) if isinstance(feature, Mapping)]

    def _params(self, *, state: str, county: str, offset: int) -> dict[str, Any]:
        return {
            "where": f"STATE='{state}' AND COUNTY='{county}'",
            "outFields": ",".join(BLOCK_GROUP_OUT_FIELDS),
            "returnGeometry": "true",
            "outSR": 4326,
            # Without a stable sort the service may reshuffle between pages and
            # an offset walk would both skip and duplicate block groups.
            "orderByFields": "GEOID",
            "resultOffset": offset,
            "resultRecordCount": self.page_size,
            "f": "geojson",
        }


class BlockGroupIndex:
    """Point-in-polygon lookup: a parcel centroid -> a block-group GEOID.

    Backed by an R-tree because the real run asks this ~5,700 times (one per
    Ramsey parcel) against every block group in Bergen; a linear scan would be
    the slowest thing in the pipeline for no reason.
    """

    def __init__(self, boundaries: Iterable[BlockGroupBoundary] = ()) -> None:
        geoids: list[str] = []
        shapes: list[Any] = []
        for boundary in boundaries:
            polygon = _polygon(boundary.geometry)
            # A block group with no usable polygon can never contain a point.
            # Dropping it here keeps `geoid_for` free of per-lookup guards.
            if polygon is None:
                continue
            geoids.append(boundary.geoid)
            shapes.append(polygon)
        self._geoids: tuple[str, ...] = tuple(geoids)
        self._tree = STRtree(shapes)

    def geoid_for(self, centroid: tuple[float, float] | None) -> str | None:
        """The GEOID of the block group covering `centroid`, or None.

        `covered_by` rather than `within`, so a centroid landing exactly on a
        block-group edge still resolves; the lowest matching index breaks the
        tie, which makes the answer identical in every process and every run.
        (The predicate reads `point.covered_by(block_group)` — shapely applies
        it query-geometry first, which is the opposite of how it scans.)
        """
        if centroid is None or not self._geoids:
            return None
        lon, lat = centroid
        candidates = self._tree.query(Point(float(lon), float(lat)), predicate="covered_by")
        if len(candidates) == 0:
            return None
        return self._geoids[min(int(index) for index in candidates)]

    def __len__(self) -> int:
        return len(self._geoids)


# --- feature -> BlockGroupBoundary -------------------------------------------


def _to_boundaries(features: Iterable[Mapping[str, Any]]) -> list[BlockGroupBoundary]:
    """Map GeoJSON features onto boundaries, dropping the unjoinable ones.

    A feature with no GEOID cannot be matched to an ACS row no matter what its
    polygon says, so it is not a boundary — indexing it would only let a door
    resolve to a block group carrying no statistics.
    """
    boundaries: list[BlockGroupBoundary] = []
    for feature in features:
        properties = feature.get("properties") or {}
        geoid = str(properties.get("GEOID") or "").strip()
        if not geoid:
            continue
        boundaries.append(
            BlockGroupBoundary(
                geoid=geoid,
                state=str(properties.get("STATE") or ""),
                county=str(properties.get("COUNTY") or ""),
                tract=str(properties.get("TRACT") or ""),
                block_group=str(properties.get("BLKGRP") or ""),
                geometry=feature.get("geometry") or None,
            )
        )
    return boundaries


def _polygon(geometry: Mapping[str, Any] | None) -> Any | None:
    """A GeoJSON geometry -> a shapely shape, or None when it is unusable."""
    if not geometry:
        return None
    try:
        polygon = shape(dict(geometry))
    except (AttributeError, KeyError, TypeError, ValueError):
        return None
    return None if polygon.is_empty else polygon
