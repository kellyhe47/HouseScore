"""Territory bootstrap — selection, GeoJSON artifact, median value (T004, R1.1/R6).

R1.1: the territory is *ours by definition* — the ~540 class-2 parcels nearest
Ramsey Golf & Country Club. No external polygon exists or is coming, so the
selection rule is the specification, and `data/territory.geojson` is the
artifact everything downstream reads.

Three properties matter more than the exact rule:

* **Deterministic.** Same input, same ordered output — otherwise `make pipeline`
  produces a different territory each night and R13 reproducibility is a lie.
* **Idempotent bytes.** Re-running the bootstrap must not churn the committed
  artifact, so the diff of a re-run is empty.
* **No identity data (R11.1).** The published GeoJSON is served to a browser;
  its properties are an allowlist, not a leftover attribute bag.

Parcels here are synthesised programmatically around the country club — a grid
dense enough to make "the nearest ~540" a real selection, plus decoys (wrong
class, wrong hemisphere, no geometry) that a sloppy rule would swallow.

Nothing in this module touches the network or the repo's `data/` directory.
"""

import json
import math
import random

import pytest

from houseaccount.sources.parcels import Parcel
from houseaccount.territory import (
    select_territory,
    territory_median_value,
    write_territory_geojson,
)

#: Ramsey Golf & Country Club, GeoJSON order: (lon, lat).
CENTER = (-74.1560, 41.0447)

#: Roughly 55 m at this latitude — tight enough that ~540 parcels stay local.
SPACING = 0.0006

#: Property keys the published artifact may carry. Owner/mailing fields are
#: absent by construction: an allowlist, so `src/` never names a banned token.
ALLOWED_PROPERTY_KEYS = frozenset(
    {
        "PAMS_PIN",
        "PROP_CLASS",
        "PROP_LOC",
        "ZIP5",
        "PCLBLOCK",
        "PCLLOT",
        "PCL_MUN",
        "DEED_DATE",
        "SALE_PRICE",
        "SALES_CODE",
        "YR_CONSTR",
        "NET_VALUE",
        "CALC_ACRE",
        "LAND_VAL",
        "IMPRVT_VAL",
        "BLDG_DESC",
        "DWELL",
        "OBJECTID",
        "GIS_PIN",
    }
)

# --- synthetic parcels -------------------------------------------------------


def square(lon, lat, size=0.0002):
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


def make_parcel(pin, lon=None, lat=None, *, prop_class="2", net_value=500000.0, geometry=...):
    """A `Parcel` as harvest would have produced it."""
    lon = CENTER[0] if lon is None else lon
    lat = CENTER[1] if lat is None else lat
    return Parcel(
        pams_pin=pin,
        prop_class=prop_class,
        prop_loc=f"{pin} MAPLE ST",
        zip5="07446",
        pclblock="101",
        pcllot=pin.split("_")[-1],
        deed_date="080122",
        sale_price=640000.0,
        sales_code="",
        yr_constr=1962,
        net_value=net_value,
        calc_acre=0.34,
        geometry=(
            {"type": "Polygon", "coordinates": square(lon, lat)} if geometry is ... else geometry
        ),
        centroid=(lon, lat) if geometry is ... or geometry is not None else None,
    )


def grid(center=CENTER, side=45, spacing=SPACING, prop_class="2", prefix="G"):
    """`side` x `side` parcels centred on `center`; ~2,000 of them by default."""
    lon0, lat0 = center
    offset = (side - 1) / 2
    return [
        make_parcel(
            f"0248_{prefix}_{row:03d}_{col:03d}",
            lon0 + (col - offset) * spacing,
            lat0 + (row - offset) * spacing,
            prop_class=prop_class,
        )
        for row in range(side)
        for col in range(side)
    ]


def haversine_m(a, b):
    """Great-circle metres between two (lon, lat) points."""
    (lon1, lat1), (lon2, lat2) = a, b
    radius = 6371008.8
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = phi2 - phi1
    dlambda = math.radians(lon2 - lon1)
    h = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(h))


@pytest.fixture
def population():
    """A realistic mix: a dense class-2 grid, plus commercial/vacant decoys
    sitting right on top of the country club so a missing class filter shows."""
    parcels = grid()
    parcels += [
        make_parcel(
            f"0248_C_{index:03d}",
            CENTER[0] + (index % 10) * 0.00005,
            CENTER[1] + (index // 10) * 0.00005,
            prop_class=prop_class,
        )
        for index, prop_class in enumerate(["1", "4A", "3B", "15C"] * 25)
    ]
    return parcels


# --- selection ---------------------------------------------------------------


def test_only_class_two_parcels_are_selected(population):
    territory = select_territory(population, CENTER)

    assert {parcel.prop_class for parcel in territory} == {"2"}


def test_territory_lands_in_the_five_forty_band(population):
    territory = select_territory(population, CENTER, target=540)

    assert 500 <= len(territory) <= 580


def test_selection_is_deterministic(population):
    first = select_territory(population, CENTER, target=540)
    second = select_territory(population, CENTER, target=540)

    assert [parcel.pams_pin for parcel in first] == [parcel.pams_pin for parcel in second]


def test_selection_does_not_depend_on_input_order(population):
    shuffled = list(population)
    random.Random(7).shuffle(shuffled)

    assert {parcel.pams_pin for parcel in select_territory(population, CENTER)} == {
        parcel.pams_pin for parcel in select_territory(shuffled, CENTER)
    }


def test_the_selected_parcels_are_the_near_ones(population):
    far = grid(center=(CENTER[0] + 0.15, CENTER[1]), side=25, prefix="FAR")
    territory = select_territory(population + far, CENTER, target=540)

    assert {parcel.pams_pin for parcel in territory}.isdisjoint(
        parcel.pams_pin for parcel in far
    )


def test_the_whole_territory_is_local_to_the_country_club(population):
    territory = select_territory(population, CENTER, target=540)

    assert max(haversine_m(parcel.centroid, CENTER) for parcel in territory) < 3000


def test_center_is_read_as_lon_lat(population):
    """A swapped centre would select the decoy cluster instead of Ramsey."""
    swapped = grid(center=(CENTER[1], CENTER[0]), side=25, prefix="SWAP")
    territory = select_territory(population + swapped, CENTER, target=540)

    assert {parcel.pams_pin for parcel in territory}.isdisjoint(
        parcel.pams_pin for parcel in swapped
    )


def test_parcels_without_geometry_are_left_out(population):
    ghost = make_parcel("0248_GHOST_1", geometry=None)
    territory = select_territory(population + [ghost], CENTER, target=540)

    assert ghost.pams_pin not in {parcel.pams_pin for parcel in territory}


def test_selection_returns_parcels(population):
    territory = select_territory(population, CENTER, target=540)

    assert all(isinstance(parcel, Parcel) for parcel in territory)


def test_a_population_smaller_than_the_target_is_returned_whole():
    parcels = grid(side=8)
    territory = select_territory(parcels, CENTER, target=540)

    assert {parcel.pams_pin for parcel in territory} == {parcel.pams_pin for parcel in parcels}


def test_no_class_two_parcels_means_an_empty_territory():
    territory = select_territory(grid(side=6, prop_class="4A"), CENTER, target=540)

    assert list(territory) == []


# --- the published artifact --------------------------------------------------


@pytest.fixture
def territory(population):
    return select_territory(population, CENTER, target=540)


def written(path, parcels):
    write_territory_geojson(parcels, path)
    return json.loads(path.read_text(encoding="utf-8"))


def test_artifact_is_a_feature_collection(tmp_path, territory):
    payload = written(tmp_path / "territory.geojson", territory)

    assert payload["type"] == "FeatureCollection"


def test_artifact_has_one_feature_per_parcel(tmp_path, territory):
    payload = written(tmp_path / "territory.geojson", territory)

    assert len(payload["features"]) == len(territory)


def test_every_feature_carries_a_pams_pin(tmp_path, territory):
    payload = written(tmp_path / "territory.geojson", territory)

    assert [feature["properties"]["PAMS_PIN"] for feature in payload["features"]] == [
        parcel.pams_pin for parcel in territory
    ]


def test_every_feature_carries_a_geometry(tmp_path, territory):
    payload = written(tmp_path / "territory.geojson", territory)

    assert all(feature.get("geometry") for feature in payload["features"])
    assert {feature["geometry"]["type"] for feature in payload["features"]} == {"Polygon"}


def test_feature_properties_are_an_allowlist(tmp_path, territory):
    payload = written(tmp_path / "territory.geojson", territory)

    keys = {key.upper() for feature in payload["features"] for key in feature["properties"]}
    assert keys <= ALLOWED_PROPERTY_KEYS, f"unpermitted properties: {sorted(keys - ALLOWED_PROPERTY_KEYS)}"


def test_a_parcel_without_geometry_is_not_published(tmp_path):
    parcels = [
        make_parcel("0248_A_1"),
        make_parcel("0248_GHOST_1", geometry=None),
        make_parcel("0248_A_2", CENTER[0] + SPACING, CENTER[1]),
    ]
    payload = written(tmp_path / "territory.geojson", parcels)

    assert [feature["properties"]["PAMS_PIN"] for feature in payload["features"]] == [
        "0248_A_1",
        "0248_A_2",
    ]


def test_writing_twice_produces_identical_bytes(tmp_path, territory):
    first, second = tmp_path / "one.geojson", tmp_path / "two.geojson"
    write_territory_geojson(territory, first)
    write_territory_geojson(territory, second)

    assert first.read_bytes() == second.read_bytes()


def test_rewriting_over_an_existing_file_is_a_no_op(tmp_path, territory):
    path = tmp_path / "territory.geojson"
    write_territory_geojson(territory, path)
    before = path.read_bytes()
    write_territory_geojson(territory, path)

    assert path.read_bytes() == before


def test_the_whole_bootstrap_is_idempotent(tmp_path, population):
    """Select + write, twice, from the same parcels — byte-identical artifact."""
    first, second = tmp_path / "one.geojson", tmp_path / "two.geojson"
    write_territory_geojson(select_territory(population, CENTER, target=540), first)
    write_territory_geojson(select_territory(population, CENTER, target=540), second)

    assert first.read_bytes() == second.read_bytes()


def test_write_creates_missing_parent_directories(tmp_path, territory):
    path = tmp_path / "data" / "nested" / "territory.geojson"
    write_territory_geojson(territory, path)

    assert path.is_file()


# --- territory median value (R6 Capacity) ------------------------------------


def values(*specs):
    """`specs` are (net_value, prop_class) pairs."""
    return [
        make_parcel(f"0248_M_{index}", prop_class=prop_class, net_value=net_value)
        for index, (net_value, prop_class) in enumerate(specs)
    ]


@pytest.mark.parametrize(
    "parcels, expected",
    [
        pytest.param(values((500000, "2")), 500000, id="single"),
        pytest.param(
            values((100000, "2"), (300000, "2"), (500000, "2")), 300000, id="odd-count"
        ),
        pytest.param(
            values((100000, "2"), (200000, "2"), (300000, "2"), (400000, "2")),
            250000,
            id="even-count-averages-the-middle-pair",
        ),
        pytest.param(
            values((0, "2"), (200000, "2"), (400000, "2")), 300000, id="zeros-excluded"
        ),
        pytest.param(
            values((100000, "2"), (300000, "2"), (500000, "2"), (9000000, "4A")),
            300000,
            id="non-class-two-excluded",
        ),
        pytest.param(values(), 0.0, id="empty-population"),
        pytest.param(values((0, "2"), (0, "2")), 0.0, id="every-value-zero"),
        pytest.param(values((9000000, "4A")), 0.0, id="no-class-two-parcels"),
    ],
)
def test_territory_median_value(parcels, expected):
    assert territory_median_value(parcels) == pytest.approx(expected)


def test_median_is_computed_over_the_territory_it_is_given(population):
    territory = select_territory(population, CENTER, target=540)

    assert territory_median_value(territory) == pytest.approx(500000)
