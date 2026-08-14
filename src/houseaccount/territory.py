"""Territory bootstrap: the ~540 doors that are ours (T004, R1.1).

There is no external polygon for this territory and none is coming — the
territory *is* the ~540 class-2 (residential) parcels nearest Ramsey Golf &
Country Club. That makes the selection rule the specification, and it puts three
properties above the exact arithmetic:

*Deterministic.* Same parcels in, same ordered parcels out, whatever order they
arrived in. Distance ties are broken on `PAMS_PIN`, because a dense grid of
suburban lots produces exact ties and float ordering alone would let the
territory drift between runs.

*Idempotent bytes.* `data/territory.geojson` is committed and everything
downstream reads it. Writing it twice from the same parcels must produce
identical bytes, so a nightly `make pipeline` shows an empty diff rather than
churn (R13).

*No identity data.* The artifact is served to a browser. Feature properties are
an explicit allowlist projected off `Parcel`, never a leftover attribute bag
(R11.1).

Distance is equirectangular metres rather than great-circle: over a ~1 km
neighbourhood the two agree to well under a lot width, and the cheap form keeps
a 5,671-parcel sort instant.
"""

from __future__ import annotations

import json
import math
import statistics
from pathlib import Path
from typing import Any, Iterable, Sequence

from houseaccount.sources.parcels import Parcel, parcels_by_class

#: MOD-IV property class 2 — residential. The only class we sell to.
RESIDENTIAL_CLASS = "2"

#: Mean Earth radius, metres.
_EARTH_RADIUS_M = 6371008.8

#: The `Parcel` attributes published on each feature, mapped to the service's
#: own field names so the artifact reads like the source it came from.
_PUBLISHED_PROPERTIES: tuple[tuple[str, str], ...] = (
    ("PAMS_PIN", "pams_pin"),
    ("PROP_CLASS", "prop_class"),
    ("PROP_LOC", "prop_loc"),
    ("ZIP5", "zip5"),
    ("PCLBLOCK", "pclblock"),
    ("PCLLOT", "pcllot"),
    ("DEED_DATE", "deed_date"),
    ("SALE_PRICE", "sale_price"),
    ("SALES_CODE", "sales_code"),
    ("YR_CONSTR", "yr_constr"),
    ("NET_VALUE", "net_value"),
    ("CALC_ACRE", "calc_acre"),
)


def select_territory(
    parcels: Sequence[Parcel],
    center: tuple[float, float],
    target: int = 540,
) -> Sequence[Parcel]:
    """The `target` residential parcels nearest `center`, nearest first.

    `center` is (lon, lat) — GeoJSON order, the same order `Parcel.centroid`
    uses, so a swapped pair here would quietly select a different town.

    Parcels without a centroid cannot be ranked and are left out rather than
    sorted to one end; a population smaller than `target` is returned whole.
    """
    candidates = [
        parcel
        for parcel in parcels_by_class(parcels, RESIDENTIAL_CLASS)
        if parcel.centroid is not None
    ]
    ranked = sorted(
        candidates,
        key=lambda parcel: (_distance_m(parcel.centroid, center), parcel.pams_pin),
    )
    return ranked[: max(int(target), 0)]


def write_territory_geojson(parcels: Sequence[Parcel], path: Path) -> Path:
    """Write `parcels` as a FeatureCollection at `path`, and return `path`.

    Sorted keys, fixed separators and a single trailing newline make the bytes a
    pure function of the parcels, so a re-run leaves the committed artifact
    untouched. Parcels with no geometry are skipped — a null-geometry feature is
    not renderable and would only break the map.
    """
    path = Path(path)
    payload = {
        "type": "FeatureCollection",
        "features": [_feature(parcel) for parcel in parcels if parcel.geometry],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


def territory_median_value(parcels: Iterable[Parcel]) -> float:
    """Median assessed `NET_VALUE` across the residential parcels given.

    Zero-valued assessments are excluded, not averaged in: they mean "no
    assessment on record", and counting them would drag the capacity baseline
    (R6) below every real house on the street. A population with nothing left to
    measure returns 0.0 rather than raising — a degraded run still scores.
    """
    values = [
        float(parcel.net_value)
        for parcel in parcels_by_class(list(parcels), RESIDENTIAL_CLASS)
        if parcel.net_value and parcel.net_value > 0
    ]
    if not values:
        return 0.0
    return float(statistics.median(values))


# --- internals ---------------------------------------------------------------


def _feature(parcel: Parcel) -> dict[str, Any]:
    """One GeoJSON feature: allowlisted properties plus the parcel's geometry."""
    return {
        "type": "Feature",
        "geometry": json.loads(json.dumps(parcel.geometry)),
        "properties": {
            key: getattr(parcel, attribute) for key, attribute in _PUBLISHED_PROPERTIES
        },
    }


def _distance_m(point: tuple[float, float] | None, center: tuple[float, float]) -> float:
    """Equirectangular metres between two (lon, lat) points."""
    if point is None:
        return math.inf
    lon, lat = point
    center_lon, center_lat = center
    mean_lat = math.radians((lat + center_lat) / 2)
    x = math.radians(lon - center_lon) * math.cos(mean_lat)
    y = math.radians(lat - center_lat)
    return _EARTH_RADIUS_M * math.hypot(x, y)
