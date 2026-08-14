"""NJ parcel harvest — pagination, cache-first, identity redaction (T004, R1.2/R2.1).

The seam is the same one `tests/test_http.py` established: a transport callable

    transport(method, url, params, headers) -> Response(status, body, headers)

injected at construction. Nothing here opens a socket; the pages below are
synthetic GeoJSON shaped like the real ArcGIS service (field names, YYMMDD
`DEED_DATE`, `exceededTransferLimit`).

Two invariants get real teeth here:

* **Cache-first.** A second `fetch` over a warm cache must not call the
  transport at all — `ExplodingTransport` proves it.
* **No identity data (R11.1).** The source may only *ask* for fields on an
  allowlist, and a hostile response carrying owner/mailing attributes must not
  be able to smuggle them onto a `Parcel`. The allowlist form is deliberate:
  `src/` can satisfy it without ever naming a forbidden token (the permanent
  guard in `tests/test_redaction.py` bans those literals outside `tests/`).
"""

import json

import pytest

from houseaccount.cache import Cache
from houseaccount.http import Response
from houseaccount.sources.parcels import Parcel, ParcelSource

QUERY_URL = (
    "https://services2.arcgis.com/XVOqAjTOJ5P6ngMu/arcgis/rest/services/"
    "Parcels_Composite_NJ_WM/FeatureServer/0/query"
)

RAMSEY = "0248"

#: The service's own `maxRecordCount` — the default page size we must not exceed.
MAX_RECORD_COUNT = 2000

#: Every field the source is permitted to request. Owner name, mailing street
#: and mailing city/state are simply absent — the source asks for an allowlist,
#: never `*`, so identity data never crosses the wire in the first place.
ALLOWED_FIELDS = frozenset(
    {
        "OBJECTID",
        "PAMS_PIN",
        "GIS_PIN",
        "PCL_MUN",
        "PCLBLOCK",
        "PCLLOT",
        "PCLQCODE",
        "PROP_CLASS",
        "PROP_LOC",
        "ZIP5",
        "LAND_VAL",
        "IMPRVT_VAL",
        "NET_VALUE",
        "LAST_YR_TX",
        "BLDG_DESC",
        "BLDG_CLASS",
        "CALC_ACRE",
        "DEED_BOOK",
        "DEED_PAGE",
        "DEED_DATE",
        "YR_CONSTR",
        "SALES_CODE",
        "SALE_PRICE",
        "DWELL",
    }
)

#: What the `Parcel` dataclass needs, so the request cannot quietly under-ask.
REQUIRED_FIELDS = frozenset(
    {
        "PAMS_PIN",
        "PROP_CLASS",
        "PROP_LOC",
        "ZIP5",
        "PCLBLOCK",
        "PCLLOT",
        "DEED_DATE",
        "SALE_PRICE",
        "SALES_CODE",
        "YR_CONSTR",
        "NET_VALUE",
        "CALC_ACRE",
    }
)

#: Identity-bearing attributes a hostile/changed upstream might return anyway.
#: `tests/` is exempt from the repo-wide token guard, which is why they appear here.
HOSTILE_IDENTITY_ATTRS = {
    "OWNER_NAME": "ZZ-HOSTILE-OWNER-NAME",
    "ST_ADDRESS": "ZZ-HOSTILE-MAILING-STREET",
    "CITY_STATE": "ZZ-HOSTILE-MAILING-CITY",
}

SERVICE_ATTRS = {
    "PAMS_PIN": "0248_00101_00003",
    "PCL_MUN": RAMSEY,
    "PCLBLOCK": "101",
    "PCLLOT": "3",
    "PROP_CLASS": "2",
    "PROP_LOC": "12 MAPLE ST",
    "ZIP5": "07446",
    "LAND_VAL": 300000,
    "IMPRVT_VAL": 425000,
    "NET_VALUE": 725000,
    "LAST_YR_TX": 14200,
    "BLDG_DESC": "2SF",
    "CALC_ACRE": 0.34,
    "DEED_BOOK": "V1234",
    "DEED_PAGE": "56",
    "DEED_DATE": "080122",
    "YR_CONSTR": 1962,
    "SALES_CODE": "",
    "SALE_PRICE": 640000,
    "DWELL": 1,
}


# --- transports (same patterns as tests/test_http.py) ------------------------


class ScriptedTransport:
    """Returns each queued response in turn; records every call it received.

    `max_calls` is a seatbelt: a pagination loop that never notices a short page
    should fail the suite, not hang it.
    """

    def __init__(self, *responses, max_calls=25):
        self.queued = list(responses)
        self.max_calls = max_calls
        self.calls = []

    def __call__(self, method, url, params, headers):
        self.calls.append({"method": method, "url": url, "params": dict(params or {})})
        if len(self.calls) > self.max_calls:
            raise AssertionError(f"pagination did not terminate after {self.max_calls} calls")
        return self.queued.pop(0) if len(self.queued) > 1 else self.queued[0]

    @property
    def offsets(self):
        return [int(call["params"].get("resultOffset", 0)) for call in self.calls]


class ExplodingTransport:
    """Any call at all is a failure of the cache-first contract."""

    def __call__(self, method, url, params, headers):
        raise AssertionError(f"transport was called for {url} — cache was not consulted")


# --- synthetic service payloads ---------------------------------------------


def square(lon, lat, size=0.0002):
    """A closed ring centred on (lon, lat) — GeoJSON order, lon first."""
    half = size / 2
    return [
        [
            [lon - half, lat - half],
            [lon + half, lat - half],
            [lon + half, lat + half],
            [lon - half, lat + half],
            [lon - half, lat - half],
        ]
    ]


def feature(pin, *, lon=-74.1560, lat=41.0447, geometry=..., **overrides):
    attrs = dict(SERVICE_ATTRS, PAMS_PIN=pin)
    attrs.update(overrides)
    return {
        "type": "Feature",
        "geometry": (
            {"type": "Polygon", "coordinates": square(lon, lat)} if geometry is ... else geometry
        ),
        "properties": attrs,
    }


def page(features, *, exceeded=False):
    payload = {
        "type": "FeatureCollection",
        "features": list(features),
        "exceededTransferLimit": exceeded,
    }
    return Response(status=200, body=json.dumps(payload).encode("utf-8"), headers={})


def pins(n, start=0):
    return [f"0248_{i:05d}_00001" for i in range(start, start + n)]


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


# --- pagination -------------------------------------------------------------


def test_two_pages_yield_every_record(cache):
    transport = ScriptedTransport(
        page([feature(pin) for pin in pins(2)], exceeded=True),
        page([feature(pin) for pin in pins(1, start=2)]),
    )
    parcels = ParcelSource(cache=cache, transport=transport, page_size=2).fetch(mun=RAMSEY)

    assert [parcel.pams_pin for parcel in parcels] == pins(3)


def test_pagination_walks_result_offset(cache):
    transport = ScriptedTransport(
        page([feature(pin) for pin in pins(2)], exceeded=True),
        page([feature(pin) for pin in pins(1, start=2)]),
    )
    ParcelSource(cache=cache, transport=transport, page_size=2).fetch(mun=RAMSEY)

    assert transport.offsets == [0, 2]


def test_pagination_stops_on_the_short_page(cache):
    transport = ScriptedTransport(
        page([feature(pin) for pin in pins(2)], exceeded=True),
        page([feature(pin) for pin in pins(1, start=2)]),
    )
    ParcelSource(cache=cache, transport=transport, page_size=2).fetch(mun=RAMSEY)

    assert len(transport.calls) == 2


def test_a_full_final_page_is_followed_by_an_empty_one(cache):
    """5671 % 2000 could just as easily have been 0 — the loop must survive it."""
    transport = ScriptedTransport(
        page([feature(pin) for pin in pins(2)], exceeded=True),
        page([feature(pin) for pin in pins(2, start=2)], exceeded=True),
        page([]),
    )
    parcels = ParcelSource(cache=cache, transport=transport, page_size=2).fetch(mun=RAMSEY)

    assert [parcel.pams_pin for parcel in parcels] == pins(4)
    assert transport.offsets == [0, 2, 4]


def test_empty_result_set_yields_no_parcels_and_one_call(cache):
    transport = ScriptedTransport(page([]))
    parcels = ParcelSource(cache=cache, transport=transport, page_size=2).fetch(mun=RAMSEY)

    assert list(parcels) == []
    assert len(transport.calls) == 1


# --- the request itself ------------------------------------------------------


def test_request_targets_the_arcgis_query_endpoint(cache):
    transport = ScriptedTransport(page([feature("0248_1")]))
    ParcelSource(cache=cache, transport=transport, page_size=2).fetch(mun=RAMSEY)

    assert transport.calls[0]["url"] == QUERY_URL


def test_request_asks_for_geojson(cache):
    transport = ScriptedTransport(page([feature("0248_1")]))
    ParcelSource(cache=cache, transport=transport, page_size=2).fetch(mun=RAMSEY)

    assert transport.calls[0]["params"]["f"] == "geojson"


def test_request_filters_to_the_requested_municipality(cache):
    transport = ScriptedTransport(page([feature("0248_1")]))
    ParcelSource(cache=cache, transport=transport, page_size=2).fetch(mun=RAMSEY)

    where = str(transport.calls[0]["params"].get("where", ""))
    assert "PCL_MUN" in where
    assert RAMSEY in where


def test_page_size_is_sent_as_result_record_count(cache):
    transport = ScriptedTransport(page([feature("0248_1")]))
    ParcelSource(cache=cache, transport=transport, page_size=2).fetch(mun=RAMSEY)

    assert int(transport.calls[0]["params"]["resultRecordCount"]) == 2


def test_default_page_size_respects_the_service_max_record_count(cache):
    transport = ScriptedTransport(page([feature("0248_1")]))
    ParcelSource(cache=cache, transport=transport).fetch(mun=RAMSEY)

    assert int(transport.calls[0]["params"]["resultRecordCount"]) == MAX_RECORD_COUNT


# --- cache-first -------------------------------------------------------------


def test_warm_cache_performs_zero_transport_calls(cache):
    cold = ScriptedTransport(
        page([feature(pin) for pin in pins(2)], exceeded=True),
        page([feature(pin) for pin in pins(1, start=2)]),
    )
    first = ParcelSource(cache=cache, transport=cold, page_size=2).fetch(mun=RAMSEY)

    warm = ParcelSource(cache=cache, transport=ExplodingTransport(), page_size=2)
    assert [parcel.pams_pin for parcel in warm.fetch(mun=RAMSEY)] == [
        parcel.pams_pin for parcel in first
    ]


def test_every_page_is_cached_separately(tmp_path):
    """Each page is its own address, so a resumed run re-fetches only what it must."""
    root = tmp_path / "cache"
    transport = ScriptedTransport(
        page([feature(pin) for pin in pins(2)], exceeded=True),
        page([feature(pin) for pin in pins(1, start=2)]),
    )
    ParcelSource(cache=Cache(root), transport=transport, page_size=2).fetch(mun=RAMSEY)

    assert len(list(root.glob("*"))) >= len(transport.calls)


def test_warm_cache_survives_a_fresh_cache_instance(tmp_path):
    root = tmp_path / "cache"
    ParcelSource(
        cache=Cache(root),
        transport=ScriptedTransport(page([feature("0248_1")])),
        page_size=2,
    ).fetch(mun=RAMSEY)

    parcels = ParcelSource(
        cache=Cache(root), transport=ExplodingTransport(), page_size=2
    ).fetch(mun=RAMSEY)
    assert [parcel.pams_pin for parcel in parcels] == ["0248_1"]


def test_a_different_municipality_is_a_cache_miss(cache):
    transport = ScriptedTransport(page([feature("0248_1")]))
    source = ParcelSource(cache=cache, transport=transport, page_size=2)
    source.fetch(mun=RAMSEY)
    source.fetch(mun="0299")

    assert len(transport.calls) == 2


# --- record -> Parcel --------------------------------------------------------


@pytest.mark.parametrize(
    "attribute, expected",
    [
        ("pams_pin", "0248_00101_00003"),
        ("prop_class", "2"),
        ("prop_loc", "12 MAPLE ST"),
        ("zip5", "07446"),
        ("pclblock", "101"),
        ("pcllot", "3"),
        ("deed_date", "080122"),
        ("sale_price", 640000),
        ("sales_code", ""),
        ("yr_constr", 1962),
        ("net_value", 725000),
        ("calc_acre", 0.34),
    ],
)
def test_service_attributes_map_onto_the_parcel(cache, attribute, expected):
    transport = ScriptedTransport(page([feature(SERVICE_ATTRS["PAMS_PIN"])]))
    (parcel,) = ParcelSource(cache=cache, transport=transport, page_size=2).fetch(mun=RAMSEY)

    assert getattr(parcel, attribute) == expected


def test_geometry_is_carried_through(cache):
    transport = ScriptedTransport(page([feature("0248_1", lon=-74.1560, lat=41.0447)]))
    (parcel,) = ParcelSource(cache=cache, transport=transport, page_size=2).fetch(mun=RAMSEY)

    assert parcel.geometry["type"] == "Polygon"
    assert parcel.geometry["coordinates"] == square(-74.1560, 41.0447)


def test_centroid_is_the_lon_lat_centre_of_the_polygon(cache):
    transport = ScriptedTransport(page([feature("0248_1", lon=-74.1500, lat=41.0500)]))
    (parcel,) = ParcelSource(cache=cache, transport=transport, page_size=2).fetch(mun=RAMSEY)

    lon, lat = parcel.centroid
    assert (lon, lat) == pytest.approx((-74.1500, 41.0500), abs=1e-9)


def test_a_parcel_is_a_parcel(cache):
    transport = ScriptedTransport(page([feature("0248_1")]))
    (parcel,) = ParcelSource(cache=cache, transport=transport, page_size=2).fetch(mun=RAMSEY)

    assert isinstance(parcel, Parcel)


# --- degenerate records (they exist in the real 5,671) -----------------------


def test_null_deed_date_becomes_none(cache):
    transport = ScriptedTransport(page([feature("0248_1", DEED_DATE=None)]))
    (parcel,) = ParcelSource(cache=cache, transport=transport, page_size=2).fetch(mun=RAMSEY)

    assert parcel.deed_date is None


def test_year_zero_construction_is_preserved_not_invented(cache):
    """R6.1 degrades on YR_CONSTR == 0; harvest must not paper over it."""
    transport = ScriptedTransport(page([feature("0248_1", YR_CONSTR=0)]))
    (parcel,) = ParcelSource(cache=cache, transport=transport, page_size=2).fetch(mun=RAMSEY)

    assert parcel.yr_constr == 0


@pytest.mark.parametrize(
    "attribute, missing",
    [
        ("net_value", {"NET_VALUE": None}),
        ("sale_price", {"SALE_PRICE": None}),
        ("calc_acre", {"CALC_ACRE": None}),
    ],
)
def test_null_numerics_become_zero_not_none(cache, attribute, missing):
    transport = ScriptedTransport(page([feature("0248_1", **missing)]))
    (parcel,) = ParcelSource(cache=cache, transport=transport, page_size=2).fetch(mun=RAMSEY)

    assert getattr(parcel, attribute) == 0


def test_a_parcel_with_no_geometry_still_maps_without_a_centroid(cache):
    transport = ScriptedTransport(page([feature("0248_1", geometry=None)]))
    (parcel,) = ParcelSource(cache=cache, transport=transport, page_size=2).fetch(mun=RAMSEY)

    assert parcel.pams_pin == "0248_1"
    assert parcel.geometry is None
    assert parcel.centroid is None


# --- no identity data (R11.1) ------------------------------------------------


def test_out_fields_are_an_explicit_allowlist(cache):
    transport = ScriptedTransport(page([feature("0248_1")]))
    ParcelSource(cache=cache, transport=transport, page_size=2).fetch(mun=RAMSEY)

    fields = requested_fields(transport.calls[0])
    assert fields is not None, "the request must name its fields, never default to all of them"
    assert "*" not in fields
    assert fields <= ALLOWED_FIELDS, f"unpermitted fields requested: {sorted(fields - ALLOWED_FIELDS)}"


def test_out_fields_cover_everything_the_parcel_needs(cache):
    transport = ScriptedTransport(page([feature("0248_1")]))
    ParcelSource(cache=cache, transport=transport, page_size=2).fetch(mun=RAMSEY)

    assert REQUIRED_FIELDS <= requested_fields(transport.calls[0])


def test_every_page_request_uses_the_same_allowlist(cache):
    transport = ScriptedTransport(
        page([feature(pin) for pin in pins(2)], exceeded=True),
        page([feature(pin) for pin in pins(1, start=2)]),
    )
    ParcelSource(cache=cache, transport=transport, page_size=2).fetch(mun=RAMSEY)

    assert [requested_fields(call) for call in transport.calls] == [
        requested_fields(transport.calls[0])
    ] * len(transport.calls)


def test_identity_attributes_in_a_response_never_reach_a_parcel(cache):
    """The live service returns these empty. A changed upstream might not."""
    transport = ScriptedTransport(page([feature("0248_1", **HOSTILE_IDENTITY_ATTRS)]))
    (parcel,) = ParcelSource(cache=cache, transport=transport, page_size=2).fetch(mun=RAMSEY)

    rendered = repr(parcel)
    for value in HOSTILE_IDENTITY_ATTRS.values():
        assert value not in rendered


def test_parcel_exposes_no_identity_named_attribute(cache):
    transport = ScriptedTransport(page([feature("0248_1", **HOSTILE_IDENTITY_ATTRS)]))
    (parcel,) = ParcelSource(cache=cache, transport=transport, page_size=2).fetch(mun=RAMSEY)

    exposed = {name.upper() for name in dir(parcel) if not name.startswith("_")}
    assert exposed.isdisjoint(HOSTILE_IDENTITY_ATTRS)


def test_parcel_carries_no_raw_attribute_bag(cache):
    """A stashed `properties` dict would launder every banned field back in."""
    transport = ScriptedTransport(page([feature("0248_1", **HOSTILE_IDENTITY_ATTRS)]))
    (parcel,) = ParcelSource(cache=cache, transport=transport, page_size=2).fetch(mun=RAMSEY)

    for value in vars(parcel).values() if hasattr(parcel, "__dict__") else ():
        assert not (isinstance(value, dict) and set(value) & set(HOSTILE_IDENTITY_ATTRS))
