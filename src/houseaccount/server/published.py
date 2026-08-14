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
import sqlite3
from dataclasses import asdict, dataclass
from difflib import get_close_matches
from pathlib import Path
from typing import Any, Mapping, Sequence

from houseaccount import route as route_module
from houseaccount.config import Config
from houseaccount.normalize import normalize_address
from houseaccount.publish import DOORS_GEOJSON_NAME, SQLITE_NAME
from houseaccount.route import RouteDoor


class DataUnavailable(RuntimeError):
    """No published run under the directory the server was pointed at.

    Carries the path it looked in: "run `make pipeline` first" is only
    actionable when the reader knows which `data/` was empty.
    """


@dataclass(frozen=True)
class Door:
    """One published door: its browser-facing properties, plus what only the
    server needs — where it is, and how its score broke down.

    `properties` is the R11.1 allowlist verbatim, so `GET /api/door/{pin}` can
    serve it without reshaping and the evidence panel sees what the map saw.
    """

    pams_pin: str
    properties: Mapping[str, Any]
    centroid: tuple[float, float] | None
    groups: Mapping[str, int] | None
    raw_total: int | None

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
    def top_evidence(self) -> str | None:
        """The sentence the talk track opens from: the highest-scoring line.

        Ties keep the engine's order, which is the order it built the trail in,
        so the same door always produces the same opener.
        """
        if not self.evidence:
            return None
        return max(self.evidence, key=lambda item: item["points"])["sentence"]


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
        return tuple(
            RouteDoor(
                pams_pin=door.pams_pin,
                address=door.situs,
                score=door.score,
                centroid=door.centroid,
                top_evidence=door.top_evidence,
            )
            for door in self.doors
        )


def resolve_data_dir(data_dir: Path | None) -> Path:
    """The directory to serve from: the caller's, or the one the process is
    configured with. `uvicorn ... --factory` passes nothing, so the default has
    to be the same `data/` the pipeline writes."""
    return Path(data_dir) if data_dir is not None else Config.from_env().data_dir


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

    group_math = _read_group_math(sqlite_path)
    doors = tuple(_door(feature, group_math) for feature in features)

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
    )


def route_payload(
    territory: Territory,
    *,
    hours: float,
    start_point: tuple[float, float],
    max_doors: int | None = None,
) -> dict[str, Any]:
    """The one route answer both surfaces return (R10.3).

    `houseaccount.route.plan_route` is reached through the module rather than
    bound at import, so there is exactly one planner and swapping it swaps it
    for the MCP tool and the map at once. The stops are serialized straight off
    the planner's `Stop`, so neither surface invents a stop shape of its own.
    """
    planned = route_module.plan_route(
        territory.route_doors(),
        hours=hours,
        start_point=start_point,
        max_doors=max_doors,
    )
    return {
        "stops": [asdict(stop) for stop in planned.stops],
        "total_minutes": planned.total_minutes,
        "estimate_disclosure": planned.estimate_disclosure,
    }


# --- internals ----------------------------------------------------------------


def _door(feature: Mapping[str, Any], group_math: Mapping[str, tuple[Any, Any]]) -> Door:
    properties = feature["properties"]
    pams_pin = properties["PAMS_PIN"]
    groups, raw_total = group_math.get(pams_pin, (None, None))
    return Door(
        pams_pin=pams_pin,
        properties=properties,
        centroid=_centroid(feature.get("geometry")),
        groups=groups,
        raw_total=raw_total,
    )


def _read_group_math(path: Path) -> dict[str, tuple[Mapping[str, int] | None, int | None]]:
    """Every scored door's group breakdown, keyed by PIN.

    A door published before the columns existed reads as `(None, None)` — the
    same shape an unscored door has — so an old database degrades to "no
    breakdown" instead of failing the boot.
    """
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(doors)")}
        if not {"groups", "raw_total"} <= columns:
            return {}
        rows = connection.execute("SELECT pams_pin, groups, raw_total FROM doors").fetchall()
    finally:
        connection.close()

    return {
        pams_pin: (json.loads(groups) if groups else None, raw_total)
        for pams_pin, groups, raw_total in rows
    }


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
