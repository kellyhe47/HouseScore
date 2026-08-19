"""The published run, read into memory — what both server surfaces answer from
(T011, R8/R9/R10.3).

The MCP tools and the REST endpoints are two front doors onto exactly one thing:
the artifacts `houseaccount.publish` wrote. If each surface loaded its own copy
they would drift — the map would colour a door the tool scores differently, and
the bug would live in whichever reader was wrong. So the reading happens once,
here, and `create_app` hands the same `Territory` to both.

**Why the server reads artifacts and never the pipeline.** A web process whose
job is looking up doors and sorting points on a map has no business importing
the harvest, ACS and vision stack. Everything below reads `doors.geojson` and
`houseaccount.sqlite` and nothing else: no network, no county API, no re-scoring.

**Why both files.** `doors.geojson` is the only artifact carrying geometry, and
the route planner needs a centroid per door, so it supplies geometry plus the
R11.1 published properties. SQLite supplies the R8.1 group math (`groups`,
`raw_total`), which is deliberately not in the browser-facing allowlist.

**Why the streets are built here.** The parcels are the only description of the
territory's roads there is (`houseaccount.streets` reads them out of the gaps),
they arrive with the run, and they cannot change under a running server — so
the network is derived once at load and handed to the planner with every route.

**Why two address keys.** R8.2 resolves a rep's typing through the same
normalizer the join uses, so "12 oak st" and "12 OAK STREET" land on one door.
But R9.1 also hands the rep the full situs string to copy — "12 OAK ST, Ramsey
NJ 07446" — and pasting that back into a tool has to work. Both spellings
normalize to different keys, so both are indexed.

**Why an unknown address gets a suggestion.** A near miss is a typo, not a
missing door, and the useful answer is "did you mean 12 OAK ST" rather than a
traceback. `suggest` matches against the short keys, because that is the form a
human reads back.

**Why a missing `data/` raises at construction.** A reviewer who clones and
starts the server before running the pipeline should be told which directory was
empty. The alternative — a live server answering 200 over an empty territory —
looks like a working deployment of a town with no houses in it.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from difflib import get_close_matches
from pathlib import Path
from typing import Any, Mapping, Sequence

from houseaccount import route as route_module
from houseaccount.config import Config
from houseaccount.normalize import normalize_address
from houseaccount.publish import (
    DOORS_GEOJSON_NAME,
    RUN_MANIFEST_NAME,
    SCORE_CONTRACT_VERSION,
    SQLITE_NAME,
)
from houseaccount.route import RouteDoor
from houseaccount.streets import build_walk_network

#: Where `make eval` writes its report, relative to the repository root. It
#: lives outside `data/` because it describes a scoring run against the golden
#: fixtures rather than the published territory.
EVAL_REPORT_RELATIVE = Path("eval") / "report.json"


class DataUnavailable(RuntimeError):
    """No published run under the directory the server was pointed at.

    Carries the path it looked in: "run `make pipeline` first" is only
    actionable when the reader knows which `data/` was empty.
    """


@dataclass(frozen=True)
class Door:
    """One published door: its browser-facing properties, plus where it is.

    `properties` is the V2 R11.1 allowlist verbatim, so `GET /api/door/{pin}`
    can serve it without reshaping and the evidence panel sees what the map
    saw. The V1 group math (`groups`, `raw_total`) is gone (R27/R28): the V2
    breakdown — categories, base, mover, lift, modifier, adjustment — ships in
    the properties themselves.
    """

    pams_pin: str
    properties: Mapping[str, Any]
    centroid: tuple[float, float] | None

    @property
    def situs(self) -> str:
        return self.properties["situs"]

    @property
    def score(self) -> int | None:
        return self.properties["score"]

    @property
    def confidence(self) -> str | None:
        return self.properties["confidence"]

    @property
    def evidence(self) -> Sequence[Mapping[str, Any]]:
        return self.properties["evidence"]

    @property
    def exclusion_reason(self) -> str | None:
        return self.properties["exclusion_reason"]

    @property
    def reason_chip(self) -> str | None:
        """The door's route reason chip (R30): the highest-point speakable
        evidence entry, computed by the planner's one rule so the map and the
        tools show one chip. Unspeakable types never surface (PRD R7.2.1)."""
        return route_module.reason_chip(self.evidence)


@dataclass(frozen=True)
class Territory:
    """Every published door, indexed the three ways the server looks doors up.

    `geojson_text` is the artifact's own bytes rather than a re-serialization of
    `doors`: `GET /api/doors.geojson` promises the map exactly what the pipeline
    wrote, and anything re-encoded here is a place the two can disagree.
    """

    data_dir: Path
    geojson_text: str
    doors: tuple[Door, ...]
    by_pin: Mapping[str, Door]
    by_address: Mapping[str, Door]
    #: The streets the rep can walk, derived from the parcels at boot, or None
    #: for a run whose parcels do not describe a street grid (see
    #: `houseaccount.streets`). The planner falls back to straight lines then.
    walk_network: Any = None

    @property
    def manifest_path(self) -> Path:
        """The run manifest beside the artifacts this run was read from.

        Not loaded at boot: the manifest describes the run, nothing here reads
        it, and a run published before the manifest existed still serves doors.
        The Data & Ethics page is its only consumer, so it is read per request.
        """
        return self.data_dir / RUN_MANIFEST_NAME

    def door(self, pams_pin: str) -> Door | None:
        return self.by_pin.get(pams_pin)

    def find(self, address: str) -> Door | None:
        """The door a rep's spelling of an address names, if it names one."""
        return self.by_address.get(normalize_address(address))

    def suggest(self, address: str) -> Door | None:
        """The nearest door to a spelling that matched nothing, or None.

        None is an ordinary answer: an address in another town is not a typo,
        and offering the closest street in Ramsey would be a worse reply than
        admitting there is no match.
        """
        key = normalize_address(address)
        if not key:
            return None
        short_keys = sorted({_short_key(door.situs) for door in self.doors})
        matches = get_close_matches(key, short_keys, n=1)
        if not matches:
            return None
        return self.by_address.get(matches[0])

    def route_doors(self) -> tuple[RouteDoor, ...]:
        """Every door as a route candidate, unscored and geometryless included.

        The planner already treats a missing score or centroid as unroutable
        (R10.1), so filtering here would only make two places responsible for
        the same rule.
        """
        return tuple(_route_door(door) for door in self.doors)


def resolve_data_dir(data_dir: Path | None) -> Path:
    """The directory to serve from: the caller's, or the one the process is
    configured with. `uvicorn ... --factory` passes nothing, so the default has
    to be the same `data/` the pipeline writes."""
    return Path(data_dir) if data_dir is not None else Config.from_env().data_dir


def resolve_eval_report(eval_report: Path | None) -> Path:
    """Where the eval report is, for the deployment and for a test alike.

    The same shape as `resolve_data_dir`, and for the same reason: `--factory`
    can pass nothing, so the default is the repository's own `eval/report.json`
    — the file `make eval` writes. It is a *file* rather than a directory
    because it is the only artifact outside `data/` the UI reads (R12).
    """
    if eval_report is not None:
        return Path(eval_report)
    return Config.from_env().repo_root / EVAL_REPORT_RELATIVE


def read_json_artifact(path: Path) -> Any | None:
    """One published JSON artifact, or `None` if there is not one there.

    Absent and malformed collapse to the same answer on purpose. `make eval`
    interrupted halfway leaves half a JSON file behind, and the Data & Ethics
    page treats any non-`ok` response as "no published run" and says so — so a
    truncated file has to reach the browser as that same honest 404 rather than
    as a 500 out of the JSON decoder.
    """
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def load_territory(data_dir: Path | None = None) -> Territory:
    """Read one published run, or say which directory did not hold one."""
    resolved = resolve_data_dir(data_dir)
    geojson_path = resolved / DOORS_GEOJSON_NAME
    sqlite_path = resolved / SQLITE_NAME

    missing = [path for path in (geojson_path, sqlite_path) if not path.is_file()]
    if missing:
        raise DataUnavailable(
            f"no published run in {resolved}: "
            f"{', '.join(path.name for path in missing)} not found. "
            "Run `make pipeline` to publish one."
        )

    geojson_text = geojson_path.read_text(encoding="utf-8")
    try:
        features = json.loads(geojson_text)["features"]
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise DataUnavailable(f"{geojson_path} is not a published door collection") from error

    # R27: mixed-version outputs are rejected at boot. A published record still
    # claiming another contract version is a stale run, not a servable
    # territory — the failure names the offending version so the operator
    # knows to re-run the pipeline.
    stale = sorted(
        {
            str(feature.get("properties", {}).get("score_contract_version"))
            for feature in features
        }
        - {SCORE_CONTRACT_VERSION}
    )
    if stale:
        raise DataUnavailable(
            f"the published run in {resolved} carries records with "
            f"score_contract_version {', '.join(stale)}; this server serves only "
            f"{SCORE_CONTRACT_VERSION!r}. Re-run `make pipeline` to publish a fresh run."
        )

    doors = tuple(_door(feature) for feature in features)

    by_pin: dict[str, Door] = {}
    by_address: dict[str, Door] = {}
    for door in doors:
        by_pin.setdefault(door.pams_pin, door)
        # Publication order breaks ties, so two parcels sharing a situs resolve
        # to the same door on every boot rather than whichever was hashed first.
        by_address.setdefault(_short_key(door.situs), door)
        by_address.setdefault(normalize_address(door.situs), door)

    return Territory(
        data_dir=resolved,
        geojson_text=geojson_text,
        doors=doors,
        by_pin=by_pin,
        by_address=by_address,
        walk_network=_walk_network(features),
    )


def route_payload(
    territory: Territory,
    *,
    hours: float,
    start_point: tuple[float, float],
    max_doors: int | None = None,
    exclude: Sequence[str] | None = None,
    score_contract_version: str | None = None,
) -> dict[str, Any]:
    """The one route answer both surfaces return (R10.3).

    `houseaccount.route.plan_route` is reached through the module rather than
    bound at import, so there is exactly one planner and swapping it swaps it
    for the MCP tool and the map at once. The stops are serialized straight off
    the planner's `Stop`, so neither surface invents a stop shape of its own.

    **`exclude` re-plans, it does not filter.** `Route.exclude` drops the named
    doors from the candidate list and plans again from the same start and
    budget, because removing a stop changes where the rep is standing for every
    later decision — a filtered route would keep its detours around a house
    nobody is visiting. Doing that in the browser instead would put route
    ordering in JavaScript, which R10.3 forbids. Unknown PINs name no candidate
    and therefore change nothing.
    """
    if (
        score_contract_version is not None
        and score_contract_version != route_module.SCORE_CONTRACT_VERSION
    ):
        # R27/R30: a share link minted under a dead contract must not replay
        # as a current route — the answer is refresh, and no stops.
        return {
            "refresh_required": True,
            "score_contract_version": route_module.SCORE_CONTRACT_VERSION,
            "stops": [],
            "total_minutes": 0.0,
            "average_score": None,
            "estimate_disclosure": route_module.ESTIMATE_DISCLOSURE,
        }

    planned = route_module.plan_route(
        territory.route_doors(),
        hours=hours,
        start_point=start_point,
        max_doors=max_doors,
        network=territory.walk_network,
    )
    if exclude:
        planned = planned.exclude(exclude)
    scores = [stop.score for stop in planned.stops]
    return {
        "stops": [asdict(stop) for stop in planned.stops],
        "total_minutes": planned.total_minutes,
        # R30: aggregates are arithmetic over the displayed V2 scores.
        "average_score": (sum(scores) / len(scores)) if scores else None,
        "score_contract_version": route_module.SCORE_CONTRACT_VERSION,
        "estimate_disclosure": planned.estimate_disclosure,
    }


def door_payload(door: Door) -> dict[str, Any]:
    """One door as `GET /api/door/{pin}` serves it (R7.2, R9.1, R30).

    The published V2 properties verbatim — the whole breakdown already lives
    there — plus exactly three presentation fields only a single-door lookup
    carries: `talk_track`, `talk_track_branches`, `reason_chip`. All built
    through `route` off the same `RouteDoor` the planner builds, so the panel
    and the route list are one script rather than two implementations.

    All three are `None` for an unscored door: no score, no opener, no chip —
    the panel shows its exclusion instead (R9.4).
    """
    payload = dict(door.properties)
    scored = door.score is not None
    candidate = _route_door(door)

    payload["talk_track"] = route_module.talk_track_for(candidate) if scored else None
    payload["talk_track_branches"] = (
        [asdict(branch) for branch in route_module.talk_track_branches_for(candidate)]
        if scored
        else None
    )
    payload["reason_chip"] = door.reason_chip if scored else None
    return payload


# --- internals ----------------------------------------------------------------


def _route_door(door: Door) -> RouteDoor:
    """One door in the planner's vocabulary.

    The single place a `Door` becomes a `RouteDoor`, so the talk track on the
    evidence panel is built from exactly the same candidate the planner builds
    its stops from. Two spellings of this would be two openers for one door the
    day either side changed (R7.2).
    """
    return RouteDoor(
        pams_pin=door.pams_pin,
        address=door.situs,
        score=door.score,
        centroid=door.centroid,
        # The chip drives the words too: it is speakable by construction
        # (PRD R7.2.1), so the angle it selects never recites the file.
        evidence_type=door.reason_chip,
        reason_chip=door.reason_chip,
    )


def _door(feature: Mapping[str, Any]) -> Door:
    properties = feature["properties"]
    return Door(
        pams_pin=properties["PAMS_PIN"],
        properties=properties,
        centroid=_centroid(feature.get("geometry")),
    )


def _walk_network(features: Sequence[Mapping[str, Any]]) -> Any:
    """The territory's streets, derived from its parcels once at boot.

    A third of a second for 540 parcels, spent here rather than per request:
    every route the process ever plans walks the same streets, and the input is
    an artifact that cannot change under a running server.

    A derivation that raises is a server that still serves doors — the planner
    reverts to straight-line legs and says so in its own disclosure — because a
    territory whose geometry defeats the medial axis is a worse route, not a
    dead deployment.
    """
    geometries = [feature.get("geometry") for feature in features]
    door_points: dict[tuple[float, float], Mapping[str, Any]] = {}
    for geometry in geometries:
        centroid = _centroid(geometry)
        if centroid is not None and geometry is not None:
            door_points.setdefault(centroid, geometry)

    try:
        return build_walk_network(geometries, door_points)
    except Exception:  # pragma: no cover - shapely refusing a published run
        return None


def _centroid(geometry: Mapping[str, Any] | None) -> tuple[float, float] | None:
    """The door's point on the map, or None for a parcel with no polygon.

    Computed with the same shapely call the parcel source used, so the planner
    walks to the point the map drew the door at.
    """
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


def _short_key(situs: str) -> str:
    """The house-number-and-street key: what a rep says, without the town."""
    return normalize_address((situs or "").split(",")[0])
