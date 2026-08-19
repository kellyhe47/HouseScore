"""The MCP tool surface: three tools over one published run (T011, R8.1/R8.2/R10.3).

*"I have 2 hours in Ramsey — which 20 doors do I knock, and what do I say?"*
`plan_route` is the tool that answers that. `get_door_score` and `explain_score`
answer the two questions a rep asks once they are standing at a door: what is
this house worth knocking, and why does the model think so.

**Three tools, and the descriptions are product.** An LLM reads these strings to
decide whether to call a tool and what to put in it, so they say what a House
Score means, what the evidence trail is, and what each argument wants. A tool
whose description is its function name is a tool the model calls at the wrong
moment.

**Unknown addresses are answers, not exceptions** (R8.2). A rep typing a street
from memory, or pasting a fragment of the situs string, gets
`{error, message, suggestion}` back as an ordinary result — never a raised
exception, so the client can render "did you mean 12 OAK ST" instead of a
traceback. `suggestion` is null when nothing is close: an address in another
town is not a typo.

**One planner** (R10.3). `plan_route` shapes no route of its own; it calls
`published.route_payload`, which reaches `houseaccount.route.plan_route` through
the module. The map's `POST /api/route` calls the same function, so the two
surfaces cannot drift.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version as _distribution_version
from pathlib import Path
from typing import Annotated, Any

from mcp.server import MCPServer
from pydantic import Field

from houseaccount.server.published import (
    DataUnavailable,
    Door,
    Territory,
    load_territory,
    route_payload,
)

__all__ = ["DataUnavailable", "create_mcp_server", "mcp_server_for"]

#: Shown to whoever is wiring the server into a client, so they know what the
#: three tools are about before reading any of them.
SERVER_INSTRUCTIONS = (
    "Door-knocking intelligence for Ramsey, NJ. Every single-family parcel in the "
    "town carries a House Score (0-100) estimating how likely the household is to "
    "be in the market for home-improvement work right now, built from public "
    "county, permit, census and aerial-imagery signals and published with the "
    "evidence trail behind it. Use plan_route to decide where a rep should walk, "
    "and get_door_score / explain_score to answer questions about one address."
)

#: Repeated in both address tools: the model has to know a fuzzy street name is
#: fine, and that the tool answers rather than throwing when it misses.
_ADDRESS_ARGUMENT = (
    "A street address in Ramsey, NJ. Either the short form the rep says "
    '("12 Oak St") or the full situs string the map copies '
    '("12 OAK ST, Ramsey NJ 07446"); spelling, case, punctuation and '
    '"Street" vs "St" are all normalized. If it matches no door, the tool '
    "returns an error object with the nearest match instead of failing."
)

_GET_DOOR_SCORE_DESCRIPTION = (
    "Look up one address's House Score with the evidence behind it. Returns the "
    "score (0-100, higher means more likely to be in the market for home "
    "improvement work now), a confidence level, the score_contract_version the "
    "run was published under, and the evidence trail: one entry per signal with "
    "its type, the points it contributed and a short reason. Some doors have no "
    "score - the county record is too thin to support one - and those return "
    "score: null with an exclusion_reason. Use this when asked what a specific "
    "house scores or why it is worth knocking."
)

_EXPLAIN_SCORE_DESCRIPTION = (
    "Explain one address's House Score in full: the same evidence trail as "
    "get_door_score, plus the arithmetic behind the number. Returns the three "
    "capped category subtotals - project (recent qualifying project activity), "
    "capacity (ability to pay for the work) and fit (the house suits the "
    "service) - whose sum is base; the mover state (a recent valid move blends "
    "the score toward the mover priority band) with its mover_lift; the rental "
    "registration modifier (a verified rental demotes the door); the rounding/"
    "clamp adjustment; the typed data_gaps behind the confidence level; and "
    "score, the final 0-100 integer. Use this when asked to justify or break "
    "down a score, or why one door outranks another."
)

_PLAN_ROUTE_DESCRIPTION = (
    "Plan a door-knocking walk: which doors to knock in the next N hours, in "
    "what order, and what to say at each one. This is the tool for "
    '"I have 2 hours in Ramsey - which 20 doors do I knock?". Picks the '
    "highest-scoring doors reachable on foot within the time budget, ordered "
    "greedily by score per walking minute from the starting point, and returns "
    "each stop with its address, House Score, walking minutes for that leg, "
    "cumulative minutes, a reason_chip naming why this door made the route, "
    "and a talk track - the opening line to use at that specific door. The "
    "payload carries the score_contract_version its scores came from. Walking "
    "times are straight-line estimates, and the returned estimate_disclosure "
    "says so; pass it on rather than presenting the times as turn-by-turn "
    "directions."
)


def create_mcp_server(data_dir: Path | None = None) -> MCPServer:
    """The MCP server for the run published under `data_dir` (R8).

    Reads the artifacts up front so a directory with no published run fails here
    with a path to look at, rather than on the first tool call.
    """
    return mcp_server_for(load_territory(data_dir))


def mcp_server_for(territory: Territory) -> MCPServer:
    """The same server over an already-loaded territory.

    `create_app` mounts the MCP surface beside the REST one and both answer from
    a single read of the artifacts; this is the seam that lets it share.
    """
    server = MCPServer(
        name="houseaccount",
        version=_server_version(),
        instructions=SERVER_INSTRUCTIONS,
    )

    @server.tool(name="get_door_score", description=_GET_DOOR_SCORE_DESCRIPTION)
    def get_door_score(
        address: Annotated[str, Field(description=_ADDRESS_ARGUMENT)],
    ) -> dict[str, Any]:
        door = territory.find(address)
        if door is None:
            return _unresolved(territory, address)
        properties = door.properties
        return {
            "pams_pin": door.pams_pin,
            "score": door.score,
            "confidence": door.confidence,
            "evidence": list(door.evidence),
            "exclusion_reason": door.exclusion_reason,
            "score_contract_version": properties["score_contract_version"],
        }

    @server.tool(name="explain_score", description=_EXPLAIN_SCORE_DESCRIPTION)
    def explain_score(
        address: Annotated[str, Field(description=_ADDRESS_ARGUMENT)],
    ) -> dict[str, Any]:
        door = territory.find(address)
        if door is None:
            return _unresolved(territory, address)
        properties = door.properties
        return {
            "pams_pin": door.pams_pin,
            "score": door.score,
            "confidence": door.confidence,
            "score_contract_version": properties["score_contract_version"],
            "categories": properties["categories"],
            "base": properties["base"],
            "mover": properties["mover"],
            "mover_lift": properties["mover_lift"],
            "rental_modifier": properties["rental_modifier"],
            "adjustment": properties["adjustment"],
            "data_gaps": properties["data_gaps"],
            "evidence": list(door.evidence),
            "exclusion_reason": door.exclusion_reason,
        }

    @server.tool(name="plan_route", description=_PLAN_ROUTE_DESCRIPTION)
    def plan_route(
        hours: Annotated[
            float,
            Field(
                description=(
                    "How long the rep has to walk, in hours. A leg that would "
                    "not fit in what is left of the budget is not offered, so a "
                    "small number simply returns a shorter route; 0 returns an "
                    "empty one."
                )
            ),
        ],
        start_point: Annotated[
            list[float],
            Field(
                description=(
                    "Where the rep is standing, as [longitude, latitude] - "
                    "GeoJSON order, longitude first. Ramsey sits near "
                    "[-74.141, 41.057]."
                )
            ),
        ],
        max_doors: Annotated[
            int | None,
            Field(
                description=(
                    "Optional cap on how many doors to plan, for a rep who "
                    "wants twenty stops rather than as many as the hours allow."
                )
            ),
        ] = None,
    ) -> dict[str, Any]:
        return route_payload(
            territory,
            hours=hours,
            start_point=tuple(start_point),
            max_doors=max_doors,
        )

    return server


def _server_version() -> str:
    """The version a client sees in `serverInfo`, taken from the installed
    distribution so it cannot drift from `pyproject.toml`. A source checkout
    that was never installed reports "0" rather than failing to start."""
    try:
        return _distribution_version("houseaccount")
    except PackageNotFoundError:
        return "0"


def _unresolved(territory: Territory, address: str) -> dict[str, Any]:
    """The R8.2 answer for an address that matched no door.

    Returned as a value, not raised: the client's job is to offer the nearest
    match back to the rep, and it cannot do that with an exception.
    """
    suggestion = territory.suggest(address)
    return {
        "error": "address_not_found",
        "message": (
            f"No door in Ramsey, NJ matches {address!r}."
            + (
                f" The closest is {suggestion.situs}."
                if suggestion is not None
                else " Nothing in the territory is close enough to suggest."
            )
        ),
        "suggestion": _suggestion(suggestion),
    }


def _suggestion(door: Door | None) -> dict[str, Any] | None:
    """The nearest door in the shape a client offers back: a PIN to look up and
    an address a human recognizes."""
    if door is None:
        return None
    return {"pams_pin": door.pams_pin, "address": door.situs}
