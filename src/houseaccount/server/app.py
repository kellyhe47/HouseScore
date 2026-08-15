"""The deployed process: one FastAPI app serving both surfaces (T011, R8/R9/R12).

R12 puts the MCP server on Fly.io and the Map UI on Vercel, and R10.3 says the
map calls the MCP server's HTTP surface rather than growing a planner of its
own. So there is one process here with two front doors — the MCP tools under
`/mcp` and the REST endpoints under `/api` — both answering from a single read
of the published artifacts. Two processes would mean two reads, two deploys, and
a window in which the map and the tools describe different runs.

**The factory is the seam.** `create_app(data_dir)` builds the whole app from a
directory of artifacts, so `uvicorn houseaccount.server.app:create_app --factory`
serves the configured `data/` in production while a test drives a `tmp_path`
territory through exactly the same code. `data_dir=None` means "wherever
`Config.from_env()` says", which is the only thing `--factory` can pass.

**Missing artifacts fail the boot, not the request.** `create_app` reads the run
before it binds a port, so a reviewer who starts the server before running the
pipeline gets `DataUnavailable` naming the empty directory — rather than a
healthy-looking deployment of a town with no houses in it.

**CORS, because the UI is on another origin.** Vercel serves the map, Fly serves
this, and the browser will not call across without the header. The allowlist is
explicit rather than `*`: the endpoints are public reads, but an allowlist is
what makes adding a credentialed endpoint later a deliberate act.
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from houseaccount.config import Config
from houseaccount.server.api import build_router
from houseaccount.server.mcp_tools import mcp_server_for
from houseaccount.server.published import DataUnavailable, load_territory

__all__ = ["UI_ORIGINS", "DataUnavailable", "create_app"]

#: Where the Map UI is served from (R12) — the Vercel deployment, plus the two
#: spellings of a local dev server, so `npm run dev` against a deployed API
#: works without a proxy shim that then behaves differently from production.
UI_ORIGINS: tuple[str, ...] = (
    "https://houseaccount.vercel.app",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
)

#: The static Map UI, served by this same process when the directory is present.
#:
#: Two hosts (Fly + Vercel) and one host (Railway) are both supported, and the
#: difference is entirely whether `web/` is in the image. On Railway the
#: Dockerfile copies it, this mount serves it, and the UI calls a same-origin
#: `/api` — so there is no CORS preflight, no `HOUSEACCOUNT_API_BASE` to set and
#: no second dashboard. On Fly the directory is absent, the mount is skipped,
#: and Vercel serves the same files against the allowlist below.
WEB_DIR_NAME = "web"

#: Where MCP clients connect. The transport's route is adopted at exactly this
#: path rather than mounted under it: a `Mount` only matches the prefix *with* a
#: trailing slash, so `POST /mcp` would answer 307 and a client that does not
#: re-POST on redirect would see an empty body instead of a session.
MCP_PATH = "/mcp"


def create_app(data_dir: Path | None = None, eval_report: Path | None = None) -> FastAPI:
    """Build the app that serves the run published under `data_dir`.

    Raises `DataUnavailable` if that directory holds no published run.

    `eval_report` points at the eval harness's report *file*, which lives
    outside `data/` and defaults to the repository's own `eval/report.json`.
    Deliberately not part of the boot contract: `make pipeline` without `make
    eval` is an ordinary state, and the Data & Ethics page already degrades
    through a missing report — so it is a 404 on one endpoint, not a dead
    deployment.
    """
    territory = load_territory(data_dir)
    mcp_server = mcp_server_for(territory)
    mcp_app = mcp_server.streamable_http_app(streamable_http_path=MCP_PATH)

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        """Run the MCP session manager for the life of the process.

        A mounted sub-app's lifespan is not run by its parent, and the
        streamable-HTTP transport needs its session manager started before it
        will accept a connection — so the parent runs it explicitly.
        """
        async with mcp_server.session_manager.run():
            yield

    app = FastAPI(
        title="HouseAccount",
        description=(
            "House Scores, evidence and door-knocking routes for Ramsey, NJ, "
            "served from one published pipeline run."
        ),
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(UI_ORIGINS),
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )
    # Adopted rather than `include_router`-ed, for the same reason the MCP
    # routes are: since FastAPI 0.141 `include_router` stores an opaque
    # `_IncludedRouter` wrapper in `app.routes`, so the app can no longer be
    # asked which paths it serves. Nothing here uses a prefix or router-level
    # dependencies, so adopting the routes is behaviourally identical — and it
    # keeps `create_app(...).routes` introspectable, which is what lets a test
    # assert that the routes and `web/ethics.html`'s fetches are one decision.
    app.routes.extend(build_router(territory, eval_report=eval_report).routes)
    app.routes.extend(mcp_app.routes)

    # Mounted *last*, and only if the directory shipped. Starlette matches
    # routes in order, so a catch-all at "/" added after the API and MCP routes
    # cannot shadow them — `/api/doors.geojson` still reaches its endpoint and
    # only what nothing else claimed falls through to a file. Added first it
    # would swallow the entire server.
    #
    # `html=True` serves `index.html` at `/`, which is what makes the bare
    # deployment URL the map rather than a 404.
    web_dir = Config.from_env().repo_root / WEB_DIR_NAME
    if web_dir.is_dir():
        app.mount("/", StaticFiles(directory=web_dir, html=True), name="web")
    return app
