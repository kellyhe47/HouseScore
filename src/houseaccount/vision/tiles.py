"""NJ orthoimagery tiling — deterministic EPSG:3857 export requests.

STUB — written by the test-writer so the failing tests name missing symbols
rather than a missing module. Contains no implementation.

What `tests/test_vision_tiles.py` pins:

- ``ORTHO_SERVICES``     {2015: url, 2020: url} MapServer/export endpoints on
                         maps.nj.gov (public domain, no key).
- ``ORTHO_YEARS``        the supported vintages, ascending.
- ``TILE_SPAN_METERS``   the ground span of one square tile.
- ``DEFAULT_TILE_PIXELS`` 640, per PRD R4.3.
- ``tile_url(centroid, year, size=DEFAULT_TILE_PIXELS) -> str``
                         `centroid` is ``(lon, lat)`` in WGS84 degrees.
- ``Tile``               frozen dataclass carrying pams_pin, year, image_ref,
                         capture_date, image_bytes.
- ``tile_for(pams_pin, centroid, year, size=...) -> Tile``
- ``fetch_tile(tile, *, cache=None, transport=...) -> Tile``
                         cache-first via ``houseaccount.http.fetch_bytes``.
"""

from __future__ import annotations
