"""NJ orthoimagery tiling — deterministic EPSG:3857 export requests (R4.1).

The imagery bill for this project is $0 because the tiles come from New Jersey's
public-domain ortho MapServers: no key, no quota, no ToS clause forbidding
derived datasets (PRD R2.1/R11.2). Street View would have been easier to point
at and is explicitly *not* used — Google Maps Platform ToS 3.2.3 forbids exactly
the bulk-derived dataset this project builds.

What has to be right here is the geometry. ArcGIS `export` takes a bbox in the
projection named by `bboxSR`, so a degrees/radians slip or a transposed
(lat, lon) pair does not fail — it silently returns a picture of somewhere else,
and the score engine cheerfully attributes the neighbour's pool to this parcel.
Hence the projection is written out longhand from the web-mercator definition
rather than pulled from a geospatial dependency, and the tile is framed as a
square centred on the parcel centroid.

The export URL doubles as the frame's identity: it is the `image_ref` carried on
every `Detection`, the cache address, and the link a rep opens to re-check a
claim. That forces determinism — the centroid is rounded to millimetres before
the half-span is added, so the same parcel produces byte-identical URLs across
processes and a re-run stays a cache hit rather than a re-download of 1,080
tiles.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from urllib.parse import parse_qs, urlencode, urlparse

from houseaccount.cache import Cache
from houseaccount.http import Transport, fetch_bytes, requests_transport

#: The two ortho vintages the R6 condition trajectory compares, ascending.
ORTHO_YEARS: tuple[int, ...] = (2015, 2020)

#: MapServer `export` endpoints, verified live during Phase 0. Public domain,
#: no API key, no rate limit worth engineering around.
_ORTHO_BASE = "https://maps.nj.gov/arcgis/rest/services/Basemap"
ORTHO_SERVICES: dict[int, str] = {
    2015: f"{_ORTHO_BASE}/Orthos_Natural_2015_NJ_WM/MapServer/export",
    2020: f"{_ORTHO_BASE}/Orthos_Natural_2020_NJ_WM/MapServer/export",
}

#: Ground span of one square tile, in metres. A Ramsey lot is ~0.25 acre, so
#: 120 m comfortably contains the house, the back yard and the pool that may sit
#: in it, without pulling in enough of the neighbours to confuse attribution.
TILE_SPAN_METERS: float = 120.0

#: Pixels per side. PRD R4.3 sizes the tile for the Haiku vision tier.
DEFAULT_TILE_PIXELS: int = 640

#: The NJ programme publishes a flight *season*, not a per-tile timestamp, so
#: the vintage year is the honest precision. The evidence line dates the claim
#: to the imagery, so this travels with the tile.
ORTHO_CAPTURE_DATES: dict[int, str] = {
    2015: "2015-01-01",
    2020: "2020-01-01",
}

#: Equatorial radius used by EPSG:3857. Web mercator treats the earth as a
#: sphere of exactly this radius — that is the definition, not an approximation
#: we are free to improve on.
_EARTH_RADIUS_M = 6378137.0

#: Decimal places kept on the projected centroid. A millimetre is far below the
#: resolution of the imagery and keeps the URL a stable content address.
_COORD_PRECISION = 3


@dataclass(frozen=True)
class Tile:
    """One parcel's frame for one ortho vintage.

    `image_ref` is the export URL and is the identity of the frame everywhere
    downstream: cache key, detection provenance, and the link on the evidence
    line. `image_bytes` is empty until `fetch_tile` fills it.
    """

    pams_pin: str
    year: int
    image_ref: str
    capture_date: str
    image_bytes: bytes = b""


def web_mercator(lon: float, lat: float) -> tuple[float, float]:
    """Project WGS84 degrees to EPSG:3857 metres.

    Written out rather than delegated: the whole failure mode this module
    guards against is a projection that is subtly wrong, and a one-line
    dependency would hide it behind a version bump.
    """
    x = math.radians(lon) * _EARTH_RADIUS_M
    y = math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) * _EARTH_RADIUS_M
    return x, y


def tile_url(
    centroid: tuple[float, float],
    year: int,
    size: int = DEFAULT_TILE_PIXELS,
) -> str:
    """The export URL for a square tile centred on `centroid`.

    `centroid` is `(lon, lat)` in WGS84 degrees — the order GeoJSON uses and the
    order the transposition test pins. An unsupported vintage raises rather than
    building a request to a MapServer that does not exist.
    """
    if year not in ORTHO_SERVICES:
        raise ValueError(
            f"no NJ ortho service for {year!r}; available vintages: {sorted(ORTHO_SERVICES)}"
        )

    lon, lat = centroid
    x, y = web_mercator(float(lon), float(lat))

    # Round the centre first, then add the half-span, so the bbox stays exactly
    # TILE_SPAN_METERS wide however the float lands.
    center_x = round(x, _COORD_PRECISION)
    center_y = round(y, _COORD_PRECISION)
    half = TILE_SPAN_METERS / 2.0

    bbox = ",".join(
        f"{value:.{_COORD_PRECISION}f}"
        for value in (center_x - half, center_y - half, center_x + half, center_y + half)
    )
    params = {
        "bbox": bbox,
        "bboxSR": "3857",
        "imageSR": "3857",
        "size": f"{int(size)},{int(size)}",
        "format": "png",
        "f": "image",
    }
    return f"{ORTHO_SERVICES[year]}?{urlencode(params)}"


def bbox_for_ref(image_ref: str) -> tuple[float, float, float, float] | None:
    """The `(minx, miny, maxx, maxy)` an export URL asks for, or None.

    Used by the evidence layer to say *where* a vision claim was made. Returns
    None for anything that is not a parseable export URL: a partial imagery
    attachment reads auditable without being re-openable, so the caller drops it
    entirely rather than shipping half of one.
    """
    try:
        query = parse_qs(urlparse(image_ref).query)
    except ValueError:
        return None
    raw = query.get("bbox")
    if not raw:
        return None
    parts = raw[0].split(",")
    if len(parts) != 4:
        return None
    try:
        minx, miny, maxx, maxy = (float(part) for part in parts)
    except ValueError:
        return None
    return minx, miny, maxx, maxy


def tile_for(
    pams_pin: str,
    centroid: tuple[float, float],
    year: int,
    size: int = DEFAULT_TILE_PIXELS,
) -> Tile:
    """Describe a parcel's frame without fetching it."""
    return Tile(
        pams_pin=pams_pin,
        year=year,
        image_ref=tile_url(centroid, year, size=size),
        capture_date=ORTHO_CAPTURE_DATES[year],
        image_bytes=b"",
    )


def fetch_tile(
    tile: Tile,
    *,
    cache: Cache | None = None,
    transport: Transport = requests_transport,
) -> Tile:
    """Return `tile` with its pixels, cache-first through the T001 seam.

    The whole URL is the cache address, so a warm run over 540 parcels x 2
    vintages opens no sockets at all. Upstream failures surface as `SourceError`
    carrying the status, which is what lets a partial run report exactly which
    tiles are missing instead of scoring them as "no pool".
    """
    body = fetch_bytes(tile.image_ref, cache=cache, transport=transport)
    return replace(tile, image_bytes=body)
