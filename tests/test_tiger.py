"""Census TIGERweb block-group boundaries + point-in-polygon index (T006, R3.1).

ACS statistics describe a *block group*; a parcel carries only a lon/lat
centroid. Nothing in the system could join the two, so this module fetches the
block-group polygons and answers one question: which block group contains this
centroid?

Ground truth (verified live, Phase 0):

    https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/
        tigerWMS_ACS2022/MapServer/8/query
    where=STATE='34' AND COUNTY='003'
    outFields=GEOID,STATE,COUNTY,TRACT,BLKGRP
    f=geojson

Layer 8 is "Census Block Groups". Free, no key. The fetch goes through the same
`http.fetch_json` + `Cache` plumbing every other source uses, which is what the
cache-first test below actually proves.

**No network.** The transport seam is the one `tests/test_http.py` established;
the polygons are synthetic squares near Ramsey, in GeoJSON (lon, lat) order.
"""

import json

import pytest

from houseaccount.cache import Cache
from houseaccount.http import Response
from houseaccount.sources.acs import COUNTY_BERGEN, STATE_NJ
from houseaccount.sources.tiger import (
    BLOCK_GROUP_OUT_FIELDS,
    TIGERWEB_BLOCK_GROUPS_URL,
    BlockGroupBoundary,
    BlockGroupIndex,
    TigerSource,
)

#: The endpoint, restated here so the module constant cannot drift unnoticed.
EXPECTED_URL = (
    "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/"
    "tigerWMS_ACS2022/MapServer/8/query"
)

#: Everything the boundary record needs. The request may ask for no more.
REQUIRED_FIELDS = frozenset({"GEOID", "STATE", "COUNTY", "TRACT", "BLKGRP"})

#: Extra descriptive fields TIGERweb offers that are harmless to request.
ALLOWED_FIELDS = REQUIRED_FIELDS | {"BASENAME", "NAME", "MTFCC", "OID", "CENTLAT", "CENTLON"}

#: Two adjacent block groups, GEOIDs shaped exactly as ACS builds them
#: (state 2 + county 3 + tract 6 + block group 1 = 12 characters).
BG_ONE_GEOID = "340030113001"
BG_TWO_GEOID = "340030114002"

#: Somewhere in Ramsey; the squares below straddle this longitude.
LON, LAT = -74.1560, 41.0447


# --- transports (same patterns as tests/test_http.py) ------------------------


class ScriptedTransport:
    """Returns each queued response in turn; records every call it received.

    `max_calls` is a seatbelt: a paginating implementation that never notices a
    short page should fail the suite rather than hang it.
    """

    def __init__(self, *responses, max_calls=10):
        self.queued = list(responses)
        self.max_calls = max_calls
        self.calls = []

    def __call__(self, method, url, params, headers):
        self.calls.append({"method": method, "url": url, "params": dict(params or {})})
        if len(self.calls) > self.max_calls:
            raise AssertionError(f"the fetch did not terminate after {self.max_calls} calls")
        return self.queued.pop(0) if len(self.queued) > 1 else self.queued[0]


class ExplodingTransport:
    """Any call at all is a failure of the cache-first contract."""

    def __call__(self, method, url, params, headers):
        raise AssertionError(f"transport was called for {url} — cache was not consulted")


# --- synthetic TIGERweb payloads ---------------------------------------------


def ring(west, south, east, north):
    """A closed rectangular ring in GeoJSON (lon, lat) order."""
    return [
        [
            [west, south],
            [east, south],
            [east, north],
            [west, north],
            [west, south],
        ]
    ]


#: Two squares sharing the meridian at LON: WEST to its left, EAST to its right.
WEST_RING = ring(LON - 0.01, LAT - 0.01, LON, LAT + 0.01)
EAST_RING = ring(LON, LAT - 0.01, LON + 0.01, LAT + 0.01)


def tiger_feature(geoid, ring_coords, *, tract=None, blkgrp=None, geometry=...):
    """One feature shaped like the live layer-8 GeoJSON response."""
    return {
        "type": "Feature",
        "geometry": (
            {"type": "Polygon", "coordinates": ring_coords} if geometry is ... else geometry
        ),
        "properties": {
            "GEOID": geoid,
            "STATE": geoid[:2],
            "COUNTY": geoid[2:5],
            "TRACT": tract if tract is not None else geoid[5:11],
            "BLKGRP": blkgrp if blkgrp is not None else geoid[11:],
            "NAME": f"Block Group {geoid[11:]}",
            "MTFCC": "G5030",
        },
    }


def collection(*features):
    payload = {"type": "FeatureCollection", "features": list(features)}
    return Response(status=200, body=json.dumps(payload).encode("utf-8"), headers={})


TWO_BLOCK_GROUPS = (
    tiger_feature(BG_ONE_GEOID, WEST_RING),
    tiger_feature(BG_TWO_GEOID, EAST_RING),
)


@pytest.fixture
def cache(tmp_path):
    return Cache(tmp_path / "cache")


def requested_fields(call):
    """The `outFields` a call asked for, as a set. Accepts list or CSV string."""
    raw = call["params"].get("outFields")
    if raw is None:
        return None
    if isinstance(raw, str):
        return {token.strip() for token in raw.split(",") if token.strip()}
    return {str(token).strip() for token in raw}


def boundaries(*features):
    """Build `BlockGroupBoundary` records straight from feature dicts."""
    return [
        BlockGroupBoundary(
            geoid=f["properties"]["GEOID"],
            state=f["properties"]["STATE"],
            county=f["properties"]["COUNTY"],
            tract=f["properties"]["TRACT"],
            block_group=f["properties"]["BLKGRP"],
            geometry=f["geometry"],
        )
        for f in features
    ]


# --- the request --------------------------------------------------------------


def test_the_endpoint_constant_is_the_verified_layer_8_query_url():
    assert TIGERWEB_BLOCK_GROUPS_URL == EXPECTED_URL


def test_request_targets_the_tigerweb_endpoint(cache):
    transport = ScriptedTransport(collection(*TWO_BLOCK_GROUPS), collection())
    TigerSource(cache=cache, transport=transport).fetch()

    assert transport.calls[0]["url"] == TIGERWEB_BLOCK_GROUPS_URL


def test_request_asks_for_geojson_with_geometry(cache):
    transport = ScriptedTransport(collection(*TWO_BLOCK_GROUPS), collection())
    TigerSource(cache=cache, transport=transport).fetch()

    params = transport.calls[0]["params"]
    assert params["f"] == "geojson"
    assert str(params.get("returnGeometry")).lower() in {"true", "1"}


def test_request_filters_to_state_and_county(cache):
    transport = ScriptedTransport(collection(*TWO_BLOCK_GROUPS), collection())
    TigerSource(cache=cache, transport=transport).fetch(state=STATE_NJ, county=COUNTY_BERGEN)

    where = str(transport.calls[0]["params"].get("where", ""))
    assert "STATE" in where and STATE_NJ in where
    assert "COUNTY" in where and COUNTY_BERGEN in where


def test_new_jersey_and_bergen_are_the_defaults(cache):
    transport = ScriptedTransport(collection(*TWO_BLOCK_GROUPS), collection())
    TigerSource(cache=cache, transport=transport).fetch()

    where = str(transport.calls[0]["params"].get("where", ""))
    assert STATE_NJ in where and COUNTY_BERGEN in where


def test_out_fields_are_an_explicit_allowlist_covering_the_boundary(cache):
    transport = ScriptedTransport(collection(*TWO_BLOCK_GROUPS), collection())
    TigerSource(cache=cache, transport=transport).fetch()

    fields = requested_fields(transport.calls[0])
    assert fields is not None, "the request must name its fields, never default to all of them"
    assert "*" not in fields
    assert REQUIRED_FIELDS <= fields
    assert fields <= ALLOWED_FIELDS, f"unexpected fields requested: {sorted(fields - ALLOWED_FIELDS)}"


def test_the_out_fields_constant_matches_what_is_requested(cache):
    transport = ScriptedTransport(collection(*TWO_BLOCK_GROUPS), collection())
    TigerSource(cache=cache, transport=transport).fetch()

    assert set(BLOCK_GROUP_OUT_FIELDS) == requested_fields(transport.calls[0])


# --- feature -> BlockGroupBoundary --------------------------------------------


def test_features_map_onto_boundaries_in_response_order(cache):
    transport = ScriptedTransport(collection(*TWO_BLOCK_GROUPS), collection())
    fetched = TigerSource(cache=cache, transport=transport).fetch()

    assert [b.geoid for b in fetched] == [BG_ONE_GEOID, BG_TWO_GEOID]


@pytest.mark.parametrize(
    "attribute, expected",
    [
        ("geoid", BG_ONE_GEOID),
        ("state", "34"),
        ("county", "003"),
        ("tract", "011300"),
        ("block_group", "1"),
    ],
)
def test_geography_parts_map_field_for_field(cache, attribute, expected):
    transport = ScriptedTransport(collection(tiger_feature(BG_ONE_GEOID, WEST_RING)), collection())
    (boundary,) = TigerSource(cache=cache, transport=transport).fetch()

    assert getattr(boundary, attribute) == expected


def test_the_geoid_is_the_concatenated_geography_acs_also_builds(cache):
    """ACS keys its block groups on state+county+tract+block group. If TIGER's
    GEOID were assembled any other way the two datasets could never join."""
    transport = ScriptedTransport(collection(tiger_feature(BG_ONE_GEOID, WEST_RING)), collection())
    (boundary,) = TigerSource(cache=cache, transport=transport).fetch()

    assert boundary.geoid == boundary.state + boundary.county + boundary.tract + boundary.block_group


def test_the_polygon_is_carried_through(cache):
    transport = ScriptedTransport(collection(tiger_feature(BG_ONE_GEOID, WEST_RING)), collection())
    (boundary,) = TigerSource(cache=cache, transport=transport).fetch()

    assert boundary.geometry["type"] == "Polygon"
    assert boundary.geometry["coordinates"] == WEST_RING


def test_an_empty_collection_yields_no_boundaries(cache):
    transport = ScriptedTransport(collection())
    assert list(TigerSource(cache=cache, transport=transport).fetch()) == []


def test_a_feature_with_no_geoid_is_dropped_rather_than_indexed_blank(cache):
    """A block group with no GEOID can never join to ACS, so it is not a boundary."""
    nameless = tiger_feature(BG_ONE_GEOID, WEST_RING)
    nameless["properties"]["GEOID"] = ""
    transport = ScriptedTransport(
        collection(nameless, tiger_feature(BG_TWO_GEOID, EAST_RING)), collection()
    )

    fetched = TigerSource(cache=cache, transport=transport).fetch()
    assert [b.geoid for b in fetched] == [BG_TWO_GEOID]


# --- cache-first ---------------------------------------------------------------


def test_warm_cache_performs_zero_transport_calls(cache):
    cold = ScriptedTransport(collection(*TWO_BLOCK_GROUPS), collection())
    first = TigerSource(cache=cache, transport=cold).fetch()

    warm = TigerSource(cache=cache, transport=ExplodingTransport())
    assert [b.geoid for b in warm.fetch()] == [b.geoid for b in first]


def test_warm_cache_survives_a_fresh_cache_instance(tmp_path):
    root = tmp_path / "cache"
    TigerSource(
        cache=Cache(root), transport=ScriptedTransport(collection(*TWO_BLOCK_GROUPS), collection())
    ).fetch()

    fetched = TigerSource(cache=Cache(root), transport=ExplodingTransport()).fetch()
    assert [b.geoid for b in fetched] == [BG_ONE_GEOID, BG_TWO_GEOID]


def test_a_different_county_is_a_cache_miss(cache):
    transport = ScriptedTransport(collection(*TWO_BLOCK_GROUPS), collection())
    source = TigerSource(cache=cache, transport=transport)
    source.fetch(state=STATE_NJ, county=COUNTY_BERGEN)
    after_bergen = len(transport.calls)
    source.fetch(state=STATE_NJ, county="017")

    assert after_bergen > 0
    assert len(transport.calls) > after_bergen, "a different county must not reuse Bergen's page"


# --- BlockGroupIndex.geoid_for -------------------------------------------------


INSIDE_WEST = (LON - 0.005, LAT)
INSIDE_EAST = (LON + 0.005, LAT)


@pytest.mark.parametrize(
    "centroid, expected",
    [
        (INSIDE_WEST, BG_ONE_GEOID),
        (INSIDE_EAST, BG_TWO_GEOID),
        ((LON - 0.0099, LAT - 0.0099), BG_ONE_GEOID),
        ((LON + 5.0, LAT), None),
        ((LON, LAT + 0.5), None),
        (None, None),
    ],
    ids=["west", "east", "near-corner", "far-east", "far-north", "no-centroid"],
)
def test_geoid_for_a_centroid(centroid, expected):
    index = BlockGroupIndex(boundaries(*TWO_BLOCK_GROUPS))
    assert index.geoid_for(centroid) == expected


def test_an_empty_index_matches_nothing():
    assert BlockGroupIndex([]).geoid_for(INSIDE_WEST) is None


def test_the_index_reports_how_many_block_groups_it_holds():
    assert len(BlockGroupIndex(boundaries(*TWO_BLOCK_GROUPS))) == 2


def test_a_boundary_with_no_polygon_is_skipped_not_crashed_on():
    broken = tiger_feature(BG_ONE_GEOID, WEST_RING, geometry=None)
    index = BlockGroupIndex(boundaries(broken, tiger_feature(BG_TWO_GEOID, EAST_RING)))

    assert index.geoid_for(INSIDE_EAST) == BG_TWO_GEOID
    assert index.geoid_for(INSIDE_WEST) is None


def test_a_multipolygon_block_group_is_indexed():
    """Real block groups are split by water and rail; MultiPolygon is ordinary."""
    multi = tiger_feature(
        BG_ONE_GEOID,
        WEST_RING,
        geometry={"type": "MultiPolygon", "coordinates": [WEST_RING, EAST_RING]},
    )
    index = BlockGroupIndex(boundaries(multi))

    assert index.geoid_for(INSIDE_WEST) == BG_ONE_GEOID
    assert index.geoid_for(INSIDE_EAST) == BG_ONE_GEOID


def test_a_point_on_a_shared_edge_resolves_deterministically():
    """Two block groups meet at LON exactly. Whichever wins must win every time,
    in every process, or a re-run would move a door into a different ACS row."""
    shared_edge = (LON, LAT)
    first = BlockGroupIndex(boundaries(*TWO_BLOCK_GROUPS)).geoid_for(shared_edge)
    second = BlockGroupIndex(boundaries(*TWO_BLOCK_GROUPS)).geoid_for(shared_edge)

    assert first == second
    assert first in {BG_ONE_GEOID, BG_TWO_GEOID}


def test_repeated_lookups_are_stable():
    index = BlockGroupIndex(boundaries(*TWO_BLOCK_GROUPS))
    assert [index.geoid_for(INSIDE_WEST) for _ in range(5)] == [BG_ONE_GEOID] * 5


def test_an_index_can_be_built_from_a_fetch_without_touching_the_network(cache):
    transport = ScriptedTransport(collection(*TWO_BLOCK_GROUPS), collection())
    fetched = TigerSource(cache=cache, transport=transport).fetch()
    index = BlockGroupIndex(fetched)

    assert index.geoid_for(INSIDE_WEST) == BG_ONE_GEOID
    assert index.geoid_for(INSIDE_EAST) == BG_TWO_GEOID
