"""The REST surface the Map UI eats: four endpoints over one published run
(T011, R9/R10.3/R12).

The browser cannot speak MCP, so the map gets plain HTTP — but it gets it from
the same `Territory` the MCP tools answer from, so what the map draws and what
an LLM says about a door are the same numbers by construction.

* `GET /health` — the liveness probe R12's deployment needs, and the first thing
  anyone curls when the site looks wrong.
* `GET /api/doors.geojson` — the published collection, byte for byte. The map
  renders the artifact the pipeline wrote; anything reshaped in flight is a
  place the map and the artifact can disagree, so nothing is.
* `GET /api/door/{pams_pin}` — one door's published properties *plus* its group
  math and talk track, for the evidence panel (R9.1/R8.1/R7.2). The extra three
  fields live here rather than in the artifact because 540 doors' worth of
  arithmetic is a download the map would never draw. Unknown PIN is a 404 with a
  body, not a stack trace.
* `POST /api/route` — the map's route request, delegated to the same planner the
  MCP tool uses (R10.3), including the `exclude` re-plan behind frame 4c's ✕.

**Errors are shapes.** Every failure carries `error` (a stable machine-readable
slug) and `message` (something to show a person), so the UI's one error banner
(R9.3) renders whatever went wrong without a branch per endpoint. That is why
the 404 below builds its own `JSONResponse` rather than raising `HTTPException`,
whose body is `{"detail": ...}`.

**A malformed route request is rejected before the planner sees it.** The
request model is the validation: a missing `hours`, a `start_point` that is not
two numbers, and "two" where a number belongs all become 422s from FastAPI, so
the planner is only ever called with arguments it can plan from.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from houseaccount.server.published import (
    Territory,
    door_payload,
    read_json_artifact,
    resolve_eval_report,
    route_payload,
)

#: `doors.geojson` is GeoJSON, and saying so lets a client that cares (QGIS, a
#: fetch that branches on type) treat it as such. It is still `+json`, so
#: everything that just parses JSON keeps working.
GEOJSON_MEDIA_TYPE = "application/geo+json"


class RouteRequest(BaseModel):
    """What the map sends when the rep asks for a walk (R10.1).

    `start_point` is (lon, lat) — GeoJSON order, as everywhere in this codebase,
    and as the map's own coordinates already are. Typed as a two-float tuple so
    a half a point or a place name is a 422 rather than an `IndexError` deep in
    the planner.
    """

    hours: float = Field(description="How long the rep has to walk, in hours.")
    start_point: tuple[float, float] = Field(
        description="Where the rep is standing, as [longitude, latitude]."
    )
    max_doors: int | None = Field(
        default=None, description="Optional cap on how many doors to plan."
    )
    exclude: list[str] | None = Field(
        default=None,
        description=(
            "PAMS PINs to leave out and re-plan around — the ✕ on a route row. "
            "Unknown PINs are ignored."
        ),
    )
    score_contract_version: str | None = Field(
        default=None,
        description=(
            "The score contract the caller's shared route was planned under. "
            "A stale version gets a refresh signal instead of a mixed-version "
            "route (R27/R30); omitted means current."
        ),
    )


def _artifact_or_404(path: Path, *, error: str) -> Response:
    """One published JSON artifact, or the "no published run" 404 (R12).

    The Data & Ethics page renders `response.ok ? body : null` and already says
    plainly when there is nothing to describe, so absent and malformed both
    become that same 404 rather than an exception the browser sees as a 500.
    """
    content = read_json_artifact(path)
    if content is None:
        return JSONResponse(
            status_code=404,
            content={
                "error": error,
                "message": (
                    f"No readable {path.name} in this deployment. "
                    "Run `make pipeline` and `make eval` to publish one."
                ),
            },
        )
    return JSONResponse(content=content)


def build_router(territory: Territory, eval_report: Path | None = None) -> APIRouter:
    """The endpoints, closed over the run they serve.

    A router built per territory rather than reading a global is what lets a
    test drive a `tmp_path` territory and a deployment drive `data/` through the
    identical code path. `eval_report` is the one artifact that lives outside
    `data/`, so it is addressed separately; `None` means the repository's own.
    """
    router = APIRouter()
    eval_report_path = resolve_eval_report(eval_report)

    @router.get("/health")
    def health() -> dict[str, Any]:
        """Liveness only: the process is up and its territory loaded (R12).

        It reports the door count too, because "up but serving an empty town"
        is the failure this probe is otherwise blind to.
        """
        return {"status": "ok", "doors": len(territory.doors)}

    @router.get("/api/doors.geojson")
    def doors_geojson() -> Response:
        """The published collection verbatim — including the doors that could
        not be scored, which R9.4 still counts and makes clickable."""
        return Response(content=territory.geojson_text, media_type=GEOJSON_MEDIA_TYPE)

    @router.get("/api/door/{pams_pin}")
    def door(pams_pin: str) -> Response:
        """One door in full, for the evidence panel (R9.1).

        Wider than the published allowlist: this is where the panel gets the
        group math its breakdown draws (R8.1) and the rep's opener (R7.2),
        neither of which belongs in a 540-door download.
        """
        found = territory.door(pams_pin)
        if found is None:
            return JSONResponse(
                status_code=404,
                content={
                    "error": "door_not_found",
                    "message": f"No door published with PAMS PIN {pams_pin!r}.",
                },
            )
        return JSONResponse(content=door_payload(found))

    @router.get("/api/eval/report.json")
    def eval_report_json() -> Response:
        """The eval report the Data & Ethics page draws its numbers from (R12).

        The path mirrors `web/ethics.html`'s own `artifact('eval/report.json')`,
        so a deploy points `HOUSEACCOUNT_ARTIFACT_BASE` at this app's `/api` and
        nothing in `web/` changes. Read per request rather than at boot: `make
        pipeline` without `make eval` is an ordinary state, not a failed deploy.
        """
        return _artifact_or_404(eval_report_path, error="eval_report_unavailable")

    @router.get("/api/data/run_manifest.json")
    def run_manifest_json() -> Response:
        """What actually ran on the published run: sources, dates, degradations.

        Served from the same directory the doors were read from, so the page's
        provenance table and the map's doors can never describe two runs.
        """
        return _artifact_or_404(territory.manifest_path, error="run_manifest_unavailable")

    @router.post("/api/route")
    def route(request: RouteRequest) -> dict[str, Any]:
        """The rep's walk, planned by the one shared planner (R10.3)."""
        return route_payload(
            territory,
            hours=request.hours,
            start_point=request.start_point,
            max_doors=request.max_doors,
            exclude=request.exclude,
            score_contract_version=request.score_contract_version,
        )

    return router
