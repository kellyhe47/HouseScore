"""Street View pass-through for the evidence panel: context, never evidence.

The panel shows the rep the front of the house they are about to knock. That
photo is *orientation* — "am I at the right door" — and deliberately not part
of the score: nothing here touches the engine, the evidence trail, or the
published artifacts, and the UI labels the image as context only.

Two endpoints, both thin:

* `GET /api/streetview/{pin}` — asks Google's free Street View *metadata*
  endpoint whether outdoor imagery exists near the door's address, and answers
  with a small JSON shape the panel can render: `available` (with the capture
  date Google reports and a link into Google Maps), `unavailable` (no imagery,
  or no key configured — either way there is nothing to show, and the panel
  says so instead of erroring), or a 502 when Google itself misbehaved.
* `GET /api/streetview/{pin}/image` — proxies the actual JPEG so the browser
  never sees the API key. The bytes stream through with `Cache-Control:
  no-store` and are never written to disk or to the pipeline's cache: the
  project stores *derived signals*, not photographs of people's homes
  (see the Data & Ethics page's Street View section).

**Why a proxy and not a signed URL.** The Static API key would otherwise ship
in the page source. The metadata call is free and the image call is billed per
request either way, so the proxy costs nothing extra and keeps one secret in
one process.

**The transport is injected.** Like everything else that talks to the network
in this codebase, the Google call goes through a plain callable
`fetch(url, params) -> (status, body_bytes, content_type)`, so the tests run
offline against scripted responses and no test can ever spend a Street View
request.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Mapping

from fastapi import APIRouter, Response
from fastapi.responses import JSONResponse

from houseaccount.server.published import Territory

#: Google's two Street View endpoints. Metadata is free and is what turns "no
#: imagery here" into a designed panel state instead of a broken image.
METADATA_URL = "https://maps.googleapis.com/maps/api/streetview/metadata"
IMAGE_URL = "https://maps.googleapis.com/maps/api/streetview"

#: The two image sizes the UI asks for — the inline panel slot and the
#: full-screen "View house" state. A whitelist rather than a passthrough so the
#: proxy cannot be used to bill arbitrary image sizes. 640 is the Static API's
#: ceiling on the standard plan.
IMAGE_SIZES: Mapping[str, str] = {
    "panel": "640x400",
    "full": "640x640",
}

#: Metadata statuses that mean "no imagery near this address" — a fact about
#: the world, rendered as the panel's `unavailable` state, not an error.
_NO_IMAGERY_STATUSES = frozenset({"ZERO_RESULTS", "NOT_FOUND"})

#: `fetch(url, params) -> (status, body, content_type)`. The default wraps
#: `requests`; tests script it.
Fetch = Callable[[str, Mapping[str, Any]], tuple[int, bytes, str]]


def requests_fetch(url: str, params: Mapping[str, Any]) -> tuple[int, bytes, str]:
    """The production transport. Imported lazily so tests never need it."""
    import requests

    response = requests.get(url, params=params, timeout=15)
    return (
        response.status_code,
        response.content,
        response.headers.get("Content-Type", "application/octet-stream"),
    )


def _maps_url(door) -> str:
    """A link into Google Maps' own pano viewer at the door.

    Built from the parcel centroid rather than the address string so the link
    works even where Google's geocoder and the county's situs disagree.
    """
    if door.centroid is not None:
        lon, lat = door.centroid
        return (
            "https://www.google.com/maps/@?api=1&map_action=pano"
            f"&viewpoint={lat},{lon}"
        )
    return f"https://www.google.com/maps/search/?api=1&query={door.situs}"


def _location(door) -> str:
    """What Google is asked to photograph: the situs address.

    The address, not the centroid — Street View's own geocoding snaps an
    address to the panorama facing the front door, where a parcel centroid can
    pick the pano on the wrong bounding street of a corner lot.
    """
    return door.situs


def _door_404(pams_pin: str) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={
            "error": "door_not_found",
            "message": f"No door published with PAMS PIN {pams_pin!r}.",
        },
    )


def build_streetview_router(
    territory: Territory,
    google_maps_key: str | None,
    fetch: Fetch = requests_fetch,
) -> APIRouter:
    """The two endpoints, closed over the territory and the one secret."""
    router = APIRouter()

    @router.get("/api/streetview/{pams_pin}")
    def streetview_metadata(pams_pin: str) -> Response:
        """Whether a photo exists for this door, and when it was taken.

        No key configured is `unavailable`, not an error: a keyless deployment
        is a supported state everywhere in this project, and the panel's
        designed absence ("Street View not available") is the honest render of
        it. `reason` distinguishes the two for anyone debugging a deployment.
        """
        door = territory.door(pams_pin)
        if door is None:
            return _door_404(pams_pin)

        maps_url = _maps_url(door)
        if google_maps_key is None:
            return JSONResponse(
                content={
                    "status": "unavailable",
                    "reason": "not_configured",
                    "maps_url": maps_url,
                }
            )

        try:
            status, body, _ = fetch(
                METADATA_URL,
                {
                    "location": _location(door),
                    "source": "outdoor",
                    "key": google_maps_key,
                },
            )
        except Exception:
            status, body = 599, b""

        payload: Mapping[str, Any] = {}
        if status == 200:
            try:
                payload = json.loads(body)
            except ValueError:
                payload = {}

        google_status = str(payload.get("status", ""))
        if google_status == "OK":
            return JSONResponse(
                content={
                    "status": "available",
                    # Google reports capture month as "YYYY-MM"; passed through
                    # verbatim so the UI owns the wording.
                    "capture_date": payload.get("date"),
                    # Relative to the API base, not to this server's root: the
                    # map prepends its own `API_BASE` (which already ends in
                    # `/api`, or in the split deployment a full origin), so the
                    # path here must not repeat the prefix.
                    "image_url": f"/streetview/{pams_pin}/image",
                    "maps_url": maps_url,
                }
            )
        if google_status in _NO_IMAGERY_STATUSES:
            return JSONResponse(
                content={
                    "status": "unavailable",
                    "reason": "no_imagery",
                    "maps_url": maps_url,
                }
            )
        # Everything else — REQUEST_DENIED, OVER_QUERY_LIMIT, a non-200, an
        # unreachable Google — is the upstream failing us, which the panel
        # renders as its error state (with the Maps link still working).
        return JSONResponse(
            status_code=502,
            content={
                "error": "streetview_unavailable",
                "message": "Street View lookup failed; try again shortly.",
                "maps_url": maps_url,
            },
        )

    @router.get("/api/streetview/{pams_pin}/image")
    def streetview_image(pams_pin: str, view: str = "panel") -> Response:
        """The photo itself, streamed through so the key stays server-side.

        `Cache-Control: no-store` on the way out, and nothing written on the
        way through: this process holds the image for exactly one response.
        """
        door = territory.door(pams_pin)
        if door is None:
            return _door_404(pams_pin)
        if google_maps_key is None:
            return JSONResponse(
                status_code=404,
                content={
                    "error": "streetview_not_configured",
                    "message": "No GOOGLE_MAPS_KEY configured on this deployment.",
                },
            )

        size = IMAGE_SIZES.get(view, IMAGE_SIZES["panel"])
        try:
            status, body, content_type = fetch(
                IMAGE_URL,
                {
                    "location": _location(door),
                    "size": size,
                    "source": "outdoor",
                    "key": google_maps_key,
                },
            )
        except Exception:
            status, body, content_type = 599, b"", ""

        if status != 200:
            return JSONResponse(
                status_code=502,
                content={
                    "error": "streetview_unavailable",
                    "message": "Street View image fetch failed; try again shortly.",
                },
            )
        return Response(
            content=body,
            media_type=content_type or "image/jpeg",
            headers={"Cache-Control": "no-store"},
        )

    return router
