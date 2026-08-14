"""NJ statewide parcel / MOD-IV harvest (T004, R1.2/R2.1).

The whole territory is bootstrapped from one ArcGIS FeatureServer layer, so this
module is where two project invariants are actually enforced rather than merely
asserted.

*Identity data never crosses the wire.* The layer carries owner names and
mailing addresses. Asking for `outFields=*` would pull them into the cache, onto
disk, and eventually into a browser. So the request names an explicit allowlist
of non-identity fields, and `Parcel` is a closed frozen dataclass with no raw
attribute bag — a changed upstream that starts returning extra attributes has
nowhere to put them (PRD R11.1).

*A re-run is free.* The service caps a response at `maxRecordCount` (2000), so
5,671 Ramsey parcels take four requests. Each page is cached at its own address
(the offset is part of the params), which means a resumed run re-fetches only
the pages it never got, and a warm run calls the transport zero times (R2.3).

Pagination terminates on the *short page*, not on `exceededTransferLimit`: a
record count that happens to be an exact multiple of the page size returns a
full final page with the flag set, and only the following empty page proves the
walk is done.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from houseaccount.cache import Cache
from houseaccount.http import Transport, fetch_json, requests_transport

#: The NJ statewide composite parcel layer's query endpoint.
QUERY_URL = (
    "https://services2.arcgis.com/XVOqAjTOJ5P6ngMu/arcgis/rest/services/"
    "Parcels_Composite_NJ_WM/FeatureServer/0/query"
)

#: The layer's own `maxRecordCount`. Asking for more silently gets you this.
MAX_RECORD_COUNT = 2000

#: Ramsey Borough — the only municipality this product ships with.
DEFAULT_MUN = "0248"

#: Every field the harvest is allowed to request, in request order.
#:
#: This is an allowlist, never `*`. Owner and mailing attributes are absent by
#: construction, which is how `src/` satisfies R11.1 without ever naming them.
OUT_FIELDS: tuple[str, ...] = (
    "OBJECTID",
    "PAMS_PIN",
    "PCL_MUN",
    "PCLBLOCK",
    "PCLLOT",
    # The condominium qualifier. Without it the twenty-five units sharing block
    # 4001 / lot 22 are one indistinguishable parcel, and an SR1A sale of any one
    # of them would land on all of them (see `normalize.sale_key`).
    "PCLQCODE",
    "PROP_CLASS",
    "PROP_LOC",
    "ZIP5",
    "LAND_VAL",
    "IMPRVT_VAL",
    "NET_VALUE",
    "LAST_YR_TX",
    "BLDG_DESC",
    "CALC_ACRE",
    "DEED_BOOK",
    "DEED_PAGE",
    "DEED_DATE",
    "YR_CONSTR",
    "SALES_CODE",
    "SALE_PRICE",
    "DWELL",
)


@dataclass(frozen=True)
class Parcel:
    """One parcel, stripped to the non-identity fields the score needs."""

    pams_pin: str = ""
    prop_class: str = ""
    prop_loc: str = ""
    zip5: str = ""
    pclblock: str = ""
    pcllot: str = ""
    #: Condominium qualifier ("C0115"), blank on a detached parcel.
    qualifier: str = ""
    deed_date: str | None = None
    sale_price: float = 0.0
    sales_code: str = ""
    yr_constr: int = 0
    net_value: float = 0.0
    calc_acre: float = 0.0
    geometry: Mapping[str, Any] | None = None
    centroid: tuple[float, float] | None = None


class ParcelSource:
    """Paginated, cache-first reader for the NJ parcel FeatureServer."""

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

    def fetch(self, mun: str = DEFAULT_MUN) -> Sequence[Parcel]:
        """Return every parcel in `mun`, walking `resultOffset` until short."""
        parcels: list[Parcel] = []
        offset = 0
        while True:
            features = self._page(mun=mun, offset=offset)
            parcels.extend(_to_parcel(feature) for feature in features)
            if len(features) < self.page_size:
                return parcels
            offset += len(features)

    # --- internals ----------------------------------------------------------

    def _page(self, *, mun: str, offset: int) -> list[Mapping[str, Any]]:
        """One page of GeoJSON features, from cache when warm."""
        payload = fetch_json(
            QUERY_URL,
            params=self._params(mun=mun, offset=offset),
            cache=self.cache,
            transport=self.transport,
        )
        features = payload.get("features") if isinstance(payload, Mapping) else None
        return list(features or [])

    def _params(self, *, mun: str, offset: int) -> dict[str, Any]:
        """The query for one page. `outFields` is identical on every page, so a
        widened harvest can never be served a narrow page out of the cache."""
        return {
            "where": f"PCL_MUN='{mun}'",
            "outFields": ",".join(OUT_FIELDS),
            "returnGeometry": "true",
            "outSR": 4326,
            # Without a stable sort the service may reshuffle between pages and
            # an offset walk would both skip and duplicate records.
            "orderByFields": "OBJECTID",
            "resultOffset": offset,
            "resultRecordCount": self.page_size,
            "f": "geojson",
        }


# --- record -> Parcel --------------------------------------------------------


def _to_parcel(feature: Mapping[str, Any]) -> Parcel:
    """Map one GeoJSON feature onto a `Parcel`.

    Only the named fields are read. Anything else the service returns — now or
    after an upstream schema change — is dropped on the floor here, which is the
    second half of the R11.1 guarantee.
    """
    properties = feature.get("properties") or {}
    geometry = feature.get("geometry") or None
    return Parcel(
        pams_pin=_text(properties.get("PAMS_PIN")),
        prop_class=_text(properties.get("PROP_CLASS")),
        prop_loc=_text(properties.get("PROP_LOC")),
        zip5=_text(properties.get("ZIP5")),
        pclblock=_text(properties.get("PCLBLOCK")),
        pcllot=_text(properties.get("PCLLOT")),
        qualifier=_text(properties.get("PCLQCODE")).strip(),
        # A missing deed date is a real signal (R6.1 degrades on it), so it stays
        # None rather than collapsing to "".
        deed_date=_optional_text(properties.get("DEED_DATE")),
        sale_price=_number(properties.get("SALE_PRICE")),
        sales_code=_text(properties.get("SALES_CODE")),
        yr_constr=int(_number(properties.get("YR_CONSTR"))),
        net_value=_number(properties.get("NET_VALUE")),
        calc_acre=_number(properties.get("CALC_ACRE")),
        geometry=geometry,
        centroid=_centroid(geometry),
    )


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _number(value: Any) -> float:
    """Null numerics become 0.0 — absent value, not absent field.

    A real 0 (`YR_CONSTR == 0` on the ~200 parcels that carry it) is preserved
    exactly, so downstream scoring can still tell "unknown" from "old".
    """
    if value is None or value == "":
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _centroid(geometry: Mapping[str, Any] | None) -> tuple[float, float] | None:
    """The polygon's area centroid as (lon, lat) — GeoJSON order throughout."""
    if not geometry:
        return None
    from shapely.geometry import shape

    try:
        point = shape(dict(geometry)).centroid
    except (AttributeError, KeyError, TypeError, ValueError):
        return None
    if point.is_empty:
        return None
    return (float(point.x), float(point.y))


def parcels_by_class(parcels: Iterable[Parcel], prop_class: str) -> list[Parcel]:
    """Every parcel of one MOD-IV property class, order preserved."""
    return [parcel for parcel in parcels if parcel.prop_class == prop_class]
