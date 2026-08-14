"""NJ ortho tiling — deterministic EPSG:3857 export requests (T007).

The imagery bill for this project is $0 because the tiles come from NJ's
public-domain ortho MapServers, which need no key (PRD R2.1/R11.2). What has to
be right is the *geometry*: an export request whose bbox is off by a projection
mistake silently scores the neighbour's pool.

So the maths is pinned against an independently computed web-mercator value
rather than against whatever the implementation produces, and the two shape
properties the criteria name — square, centred — are asserted directly.

The URL string itself is deliberately NOT pinned. Parameter order is not part of
the contract; the parsed parameters are. Determinism is asserted as a property
(same input -> same URL), which is what the content-addressed cache depends on.

Tile fetching reuses the T001 transport seam, so nothing here opens a socket.
"""

import json
import math
import time
from urllib.parse import parse_qs, urlparse

import pytest

from houseaccount.cache import Cache
from houseaccount.http import Response, SourceError
from houseaccount.vision.tiles import (
    DEFAULT_TILE_PIXELS,
    ORTHO_SERVICES,
    ORTHO_YEARS,
    TILE_SPAN_METERS,
    Tile,
    fetch_tile,
    tile_for,
    tile_url,
)

#: A parcel centroid in Ramsey, NJ, as (lon, lat) WGS84 degrees.
CENTROID = (-74.1560, 41.0447)

#: The same point in EPSG:3857, computed independently:
#:     x = radians(lon) * 6378137
#:     y = log(tan(pi/4 + radians(lat)/2)) * 6378137
MERCATOR_X = -8255008.16
MERCATOR_Y = 5018937.14

#: Metres. Generous enough that a different-but-correct earth radius constant
#: passes, tight enough that a degrees/radians slip cannot.
TOLERANCE_M = 1.0

PIN = "0248_00101_00003"

#: The 2020 endpoint, verified live during Phase 0.
VERIFIED_2020_EXPORT = (
    "https://maps.nj.gov/arcgis/rest/services/Basemap/"
    "Orthos_Natural_2020_NJ_WM/MapServer/export"
)

PNG = b"\x89PNG\r\n\x1a\n ortho tile bytes"


@pytest.fixture(autouse=True)
def no_real_sleeping(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)


class ScriptedTransport:
    """Returns each queued response in turn; records every call it received."""

    def __init__(self, *responses):
        self.queued = list(responses)
        self.calls = []

    def __call__(self, method, url, params, headers):
        self.calls.append({"method": method, "url": url, "params": params, "headers": headers})
        return self.queued.pop(0) if len(self.queued) > 1 else self.queued[0]


class ExplodingTransport:
    """Any call at all is a failure of the cache-first contract."""

    def __call__(self, method, url, params, headers):
        raise AssertionError(f"transport was called for {url} — cache was not consulted")


def params_of(url):
    """The export request's parameters, flattened to single values."""
    return {key: values[0] for key, values in parse_qs(urlparse(url).query).items()}


def bbox_of(url):
    return [float(part) for part in params_of(url)["bbox"].split(",")]


# --- the endpoints ----------------------------------------------------------


def test_both_ortho_vintages_are_available():
    assert tuple(ORTHO_YEARS) == (2015, 2020)
    assert set(ORTHO_SERVICES) == {2015, 2020}


def test_the_2020_service_is_the_verified_endpoint():
    assert ORTHO_SERVICES[2020] == VERIFIED_2020_EXPORT


def test_the_2015_service_is_the_sibling_of_the_2020_one():
    url = ORTHO_SERVICES[2015]
    assert urlparse(url).netloc == "maps.nj.gov"
    assert url.endswith("/MapServer/export")
    assert "2015" in url and "2020" not in url


@pytest.mark.parametrize("year", [2015, 2020])
def test_tile_url_targets_that_years_service(year):
    assert tile_url(CENTROID, year).startswith(ORTHO_SERVICES[year])


def test_the_two_vintages_are_different_requests():
    assert tile_url(CENTROID, 2015) != tile_url(CENTROID, 2020)


@pytest.mark.parametrize("year", [2010, 2018, 2021, 2024, 0])
def test_a_vintage_we_have_no_service_for_is_refused(year):
    """Better a loud ValueError than a request to a URL that does not exist."""
    with pytest.raises(ValueError):
        tile_url(CENTROID, year)


# --- the export parameters --------------------------------------------------


@pytest.mark.parametrize("year", [2015, 2020])
def test_export_is_requested_in_web_mercator_as_a_png_image(year):
    params = params_of(tile_url(CENTROID, year))
    assert params["bboxSR"] == "3857"
    assert params["imageSR"] == "3857"
    assert params["format"] == "png"
    assert params["f"] == "image"


def test_tiles_are_640_pixels_square_by_default():
    assert DEFAULT_TILE_PIXELS == 640
    assert params_of(tile_url(CENTROID, 2020))["size"] == "640,640"


def test_pixel_size_is_overridable_and_stays_square():
    assert params_of(tile_url(CENTROID, 2020, size=512))["size"] == "512,512"


# --- the projection ---------------------------------------------------------


@pytest.mark.parametrize("year", [2015, 2020])
def test_bbox_is_centred_on_the_parcel_in_web_mercator(year):
    minx, miny, maxx, maxy = bbox_of(tile_url(CENTROID, year))
    assert (minx + maxx) / 2 == pytest.approx(MERCATOR_X, abs=TOLERANCE_M)
    assert (miny + maxy) / 2 == pytest.approx(MERCATOR_Y, abs=TOLERANCE_M)


@pytest.mark.parametrize("year", [2015, 2020])
def test_bbox_is_square(year):
    minx, miny, maxx, maxy = bbox_of(tile_url(CENTROID, year))
    assert (maxx - minx) == pytest.approx(maxy - miny, abs=1e-6)
    assert (maxx - minx) == pytest.approx(TILE_SPAN_METERS, abs=1e-6)


def test_the_tile_span_covers_a_residential_parcel_without_swallowing_the_street():
    """Documented as a constant so the framing is a decision, not an accident."""
    assert 20 <= TILE_SPAN_METERS <= 500


def test_latitude_and_longitude_are_not_transposed():
    """The classic projection bug: (lat, lon) instead of (lon, lat). Ramsey sits
    west of the prime meridian and north of the equator, so x is negative and y
    is positive — transposing makes both wrong."""
    minx, miny, maxx, maxy = bbox_of(tile_url(CENTROID, 2020))
    assert maxx < 0 < miny


def test_moving_the_centroid_moves_the_bbox():
    east = (CENTROID[0] + 0.01, CENTROID[1])
    assert bbox_of(tile_url(east, 2020))[0] > bbox_of(tile_url(CENTROID, 2020))[0]


def test_web_mercator_matches_the_independent_formula():
    """Pins the maths itself, not just the framing."""
    lon, lat = CENTROID
    expected_x = math.radians(lon) * 6378137.0
    expected_y = math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) * 6378137.0
    minx, miny, maxx, maxy = bbox_of(tile_url(CENTROID, 2020))
    assert (minx + maxx) / 2 == pytest.approx(expected_x, abs=TOLERANCE_M)
    assert (miny + maxy) / 2 == pytest.approx(expected_y, abs=TOLERANCE_M)


# --- determinism ------------------------------------------------------------


@pytest.mark.parametrize("year", [2015, 2020])
def test_same_input_same_url(year):
    assert tile_url(CENTROID, year) == tile_url(CENTROID, year)


def test_same_url_across_fresh_calls_with_equal_but_distinct_centroids():
    """The cache addresses on this string; an unstable float repr would make
    every run a cold cache."""
    assert tile_url((-74.1560, 41.0447), 2020) == tile_url(CENTROID, 2020)


def test_different_parcels_get_different_urls():
    other = (-74.1400, 41.0500)
    assert tile_url(other, 2020) != tile_url(CENTROID, 2020)


# --- Tile -------------------------------------------------------------------


@pytest.mark.parametrize("year", [2015, 2020])
def test_tile_for_addresses_the_frame_by_its_export_url(year):
    tile = tile_for(PIN, CENTROID, year)
    assert isinstance(tile, Tile)
    assert tile.pams_pin == PIN
    assert tile.year == year
    assert tile.image_ref == tile_url(CENTROID, year)


@pytest.mark.parametrize("year", [2015, 2020])
def test_tile_capture_date_is_the_ortho_vintage(year):
    """The evidence line dates the claim, so the vintage travels with the tile."""
    assert tile_for(PIN, CENTROID, year).capture_date.startswith(str(year))


def test_a_fresh_tile_carries_no_pixels_yet():
    assert tile_for(PIN, CENTROID, 2020).image_bytes == b""


# --- fetching (T001 transport seam; no socket is ever opened) ----------------


def test_fetch_tile_requests_the_tiles_own_url(tmp_path):
    tile = tile_for(PIN, CENTROID, 2020)
    transport = ScriptedTransport(Response(status=200, body=PNG, headers={}))
    fetch_tile(tile, cache=Cache(tmp_path / "cache"), transport=transport)
    assert len(transport.calls) == 1
    call = transport.calls[0]
    requested = call["url"]
    if call["params"]:
        requested = f"{requested}?" + "&".join(
            f"{key}={value}" for key, value in dict(call["params"]).items()
        )
    assert params_of(requested) == params_of(tile.image_ref)


def test_fetch_tile_returns_a_tile_carrying_the_pixels(tmp_path):
    tile = tile_for(PIN, CENTROID, 2020)
    fetched = fetch_tile(
        tile,
        cache=Cache(tmp_path / "cache"),
        transport=ScriptedTransport(Response(status=200, body=PNG, headers={})),
    )
    assert fetched.image_bytes == PNG
    assert fetched.image_ref == tile.image_ref
    assert fetched.pams_pin == tile.pams_pin
    assert fetched.capture_date == tile.capture_date


def test_a_warm_cache_refetches_nothing(tmp_path):
    """Re-running the pipeline must not re-download 1,080 tiles."""
    cache = Cache(tmp_path / "cache")
    tile = tile_for(PIN, CENTROID, 2020)
    fetch_tile(tile, cache=cache, transport=ScriptedTransport(Response(status=200, body=PNG, headers={})))
    assert fetch_tile(tile, cache=cache, transport=ExplodingTransport()).image_bytes == PNG


def test_an_upstream_failure_surfaces_as_a_source_error(tmp_path):
    tile = tile_for(PIN, CENTROID, 2020)
    with pytest.raises(SourceError) as excinfo:
        fetch_tile(
            tile,
            cache=Cache(tmp_path / "cache"),
            transport=ScriptedTransport(Response(status=404, body=b"no coverage", headers={})),
        )
    assert excinfo.value.status == 404


def test_the_tile_url_is_json_serialisable_state(tmp_path):
    """Tiles are logged and cached; nothing about them may be un-serialisable."""
    tile = tile_for(PIN, CENTROID, 2020)
    assert json.loads(json.dumps({"image_ref": tile.image_ref}))["image_ref"] == tile.image_ref
