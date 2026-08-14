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

#: Where MCP clients connect. The transport's route is adopted at exactly this
#: path rather than mounted under it: a `Mount` only matches the prefix *with* a
#: trailing slash, so `POST /mcp` would answer 307 and a client that does not
#: re-POST on redirect would see an empty body instead of a session.
MCP_PATH = "/mcp"


def create_app(data_dir: Path | None = None) -> FastAPI:
    """Build the app that serves the run published under `data_dir`.

    Raises `DataUnavailable` if that directory holds no published run.
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
    app.include_router(build_router(territory))
    app.routes.extend(mcp_app.routes)
    return app
