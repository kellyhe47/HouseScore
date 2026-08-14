"""Serving artifacts: doors.geojson + SQLite + run_manifest.json (T009, R2.2/R2.3).

Three files come out of one call, and each answers a different question:

* `data/doors.geojson` is what the map renders and what the rep clicks. Every
  door in the territory appears exactly once — including the ones that could not
  be scored, because R9.4 makes them clickable and counts them in the
  "537 of 540" readout. A door missing from the file is a door nobody can click.
* `data/houseaccount.sqlite` is what the MCP server reads (`get_door_score`,
  `explain_score`). Two tables, `doors` and `evidence`, joined on `PAMS_PIN`,
  with an explicit `seq` because the order the engine produced the trail in is
  part of the explanation and SQL rows have no order of their own. It is also
  where the R8.1 group math lands (`groups`, `raw_total`) — see `_SCHEMA`.
* `data/run_manifest.json` is what makes the run reproducible (R13): the
  once-per-run inputs the score was computed against, when each source was
  retrieved, what the run cost, and which code produced it.

**The seam.** `publish(scored, *, report, manifest, data_dir)` takes the
`(DoorFacts, ScoreResult | None)` pairs the pipeline already holds, in
publication order. Nothing is re-derived here — not the score, not the median,
not the retrieval dates — which is what keeps the three artifacts consistent
with each other and with the numbers the resolve report published.

**What "unscored" means.** A `None` score is published as an exclusion, and the
rule that produces one is `parcel_record_incomplete`: the county record carries
*none* of the three parcel facts the score is built from — no deed date, no year
built, no assessed value. Any one of them still scores (degraded, `low`
confidence, with a `data_gap` line — the engine's R6.1 path, deliberately not
this rule). Only a record with nothing in it at all would yield a number that is
pure fabrication, so that record gets no number and says why.

**Null geometry is published, not skipped.** `territory.write_territory_geojson`
drops geometry-less parcels because an unrenderable feature only breaks a map.
Here the opposite holds: the coverage readout and the exclusion panel are
per-door, so the feature ships with `"geometry": null` and the door stays
countable and clickable.

**The identity guard.** The published property set is an exact allowlist written
out once, in `_feature`. Nothing can reach a browser by being copied wholesale
off a record, which is how `src/` satisfies R11.1 without ever naming an
identity field.

**Idempotent bytes.** Sorted keys, fixed separators, one trailing newline: two
runs over the same doors write byte-identical `doors.geojson`, so a nightly
`make pipeline` shows an empty diff rather than churn (R13).
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from houseaccount.resolve import DoorFacts, ResolveReport
from houseaccount.scoring.engine import ScoreResult
from houseaccount.scoring.evidence import EvidenceItem
from houseaccount.scoring.weights import THRESHOLDS

#: The three artifacts a run publishes. `SQLITE_NAME` is already pinned by the
#: `clean` target in the Makefile.
DOORS_GEOJSON_NAME = "doors.geojson"
SQLITE_NAME = "houseaccount.sqlite"
RUN_MANIFEST_NAME = "run_manifest.json"

#: Published on a door the county record cannot support a score for (R9.4).
EXCLUSION_REASON = "parcel record incomplete in county data"

#: The mover window the disclosure below describes, read from the rule the engine
#: scores on rather than re-typed here. A manifest carrying its own copy would go
#: on saying "90-day window" the day the threshold moved, and the whole point of
#: the `deed_vintage` block is that a reader can trust it against the map.
MOVER_WINDOW_DAYS = int(THRESHOLDS["mover_90d_days"])

#: The top mover band, read from the same rule for the same reason.
TOP_BAND_DAYS = int(THRESHOLDS["mover_30d_days"])

#: Two tables, created on first publish. `seq` is explicit because `explain_score`
#: replays the trail in the order the engine built it, and rows in a table have
#: no inherent order to fall back on.
#:
#: `groups` and `raw_total` are the R8.1 group math, carried here and *only* here.
#: `explain_score` has to answer with the five group subtotals and the unclamped
#: sum, and the alternatives are worse: re-deriving them by bucketing evidence
#: points would put a second copy of the engine's group membership in the server,
#: and re-scoring in the web process would drag the whole harvest stack in. The
#: engine already computed both numbers, so the run records them. They stay out
#: of `doors.geojson`, whose property set is the R11.1 allowlist a browser sees.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS doors (
    pams_pin         TEXT PRIMARY KEY,
    score            INTEGER,
    confidence       TEXT,
    situs            TEXT NOT NULL,
    exclusion_reason TEXT,
    groups           TEXT,
    raw_total        INTEGER
);
CREATE TABLE IF NOT EXISTS evidence (
    pams_pin  TEXT    NOT NULL,
    seq       INTEGER NOT NULL,
    type      TEXT    NOT NULL,
    points    INTEGER NOT NULL,
    sentence  TEXT    NOT NULL,
    source    TEXT    NOT NULL,
    retrieved TEXT    NOT NULL,
    imagery   TEXT,
    PRIMARY KEY (pams_pin, seq)
);
"""


@dataclass(frozen=True)
class RunManifest:
    """The once-per-run inputs a reader needs to reproduce the run (R13).

    `latest_deed_date` and `doors_in_mover_window` are the run's measurement of
    how old the MOD-IV extract is — the newest deed anywhere in the municipal
    feed, and how many territory doors that leaves inside the mover window. They
    default because they are a disclosure about the source rather than an input
    the score is computed against: an older caller that never measured them still
    constructs, and publishes a block that claims nothing.
    """

    run_at: datetime
    as_of: date
    code_version: str
    territory_median_value: float
    acs_dual_income_threshold: float
    retrieved: Mapping[str, date]
    cost_usd: float
    degradations: Sequence[str] = ()
    latest_deed_date: date | None = None
    doors_in_mover_window: int = 0
    #: The SR1A sales register's own vintage, kept separate from
    #: `latest_deed_date` so a reader can see which register supplied the
    #: freshness — and, when the register is missing, that it was MOD-IV alone.
    latest_sale_date: date | None = None
    sales_source_files: Sequence[str] = ()
    doors_with_sales_deed: int = 0
    #: Doors inside the top mover band. Published because a non-zero
    #: `doors_in_mover_window` with a zero here is the signature of the state's
    #: recording-and-publication lag, not of a rule that failed.
    doors_in_top_band: int = 0
    #: The vision stage's own state, on the same defaulting principle as the deed
    #: vintage above: `None` is "this run never measured the stage", which is what
    #: every publish before it did, and it publishes no block rather than a
    #: claim nobody made. `True` with a non-zero `vision_answers_lost` is the
    #: state that did not exist before — the stage ran and lost part of its
    #: answers, which is a partial loss and not a refusal.
    vision_available: bool | None = None
    vision_declination_reason: str | None = None
    #: One answer per request issued, and the ones that could not be read.
    vision_answers_total: int = 0
    vision_answers_lost: int = 0


@dataclass(frozen=True)
class PublishResult:
    """Where the artifacts landed, and how many doors they describe."""

    geojson_path: Path
    sqlite_path: Path
    manifest_path: Path
    doors_total: int
    doors_scored: int
    doors_unscored: int


def parcel_record_incomplete(door: DoorFacts) -> bool:
    """True when the county record carries no fact the score can be built from.

    Deliberately an *all three missing* test rather than an any-missing one: a
    record with only a deed date, or only a year built, or only an assessment
    still scores through R6.1's degraded path, flagged `low` confidence with a
    `data_gap` line. Excluding those would hide doors a rep can legitimately
    knock on. Geometry is absent from the test entirely — it is how a door is
    drawn, not what it is scored from.
    """
    return not (door.deed_date is not None or door.yr_constr > 0 or door.net_value > 0)


def publish(
    scored: Sequence[tuple[DoorFacts, ScoreResult | None]],
    *,
    report: ResolveReport,
    manifest: RunManifest,
    data_dir: Path,
) -> PublishResult:
    """Write the three artifacts for one run and report what was written.

    `data_dir` is created if it does not exist: a fresh clone must be able to
    publish without a preparatory `mkdir` step (R2.2).
    """
    pairs = list(scored)
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)

    doors_total = len(pairs)
    doors_scored = sum(1 for _, result in pairs if result is not None)

    geojson_path = _write_geojson(data_dir / DOORS_GEOJSON_NAME, pairs)
    sqlite_path = _write_sqlite(data_dir / SQLITE_NAME, pairs)
    manifest_path = _write_manifest(
        data_dir / RUN_MANIFEST_NAME,
        manifest=manifest,
        report=report,
        doors_total=doors_total,
        doors_scored=doors_scored,
        doors_with_imagery=_doors_with_imagery(pairs),
    )

    return PublishResult(
        geojson_path=geojson_path,
        sqlite_path=sqlite_path,
        manifest_path=manifest_path,
        doors_total=doors_total,
        doors_scored=doors_scored,
        doors_unscored=doors_total - doors_scored,
    )


# --- doors.geojson -----------------------------------------------------------


def _write_geojson(
    path: Path, pairs: Sequence[tuple[DoorFacts, ScoreResult | None]]
) -> Path:
    """One feature per door, in publication order, as deterministic bytes."""
    payload = {
        "type": "FeatureCollection",
        "features": [_feature(door, result) for door, result in pairs],
    }
    path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


def _feature(door: DoorFacts, result: ScoreResult | None) -> dict[str, Any]:
    """One door as GeoJSON. This property set is the R11.1 allowlist.

    Written as a literal rather than projected off the record, so a widened
    upstream schema has nowhere to leak into. A door with no polygon ships
    `"geometry": null` instead of being dropped — R9.4 still has to count it.
    """
    return {
        "type": "Feature",
        "geometry": dict(door.geometry) if door.geometry else None,
        "properties": {
            "PAMS_PIN": door.pams_pin,
            "score": result.score if result is not None else None,
            "confidence": result.confidence if result is not None else None,
            "evidence": [_evidence(item) for item in result.evidence] if result else [],
            "situs": door.situs,
            "exclusion_reason": None if result is not None else EXCLUSION_REASON,
        },
    }


def _evidence(item: EvidenceItem) -> dict[str, Any]:
    """One R7.1 evidence line, with its re-openable frame when it has one."""
    return {
        "type": item.type,
        "points": item.points,
        "sentence": item.sentence,
        "source": item.source,
        "retrieved": item.retrieved.isoformat(),
        "imagery": dict(item.imagery) if item.imagery else None,
    }


# --- houseaccount.sqlite ------------------------------------------------------


def _write_sqlite(path: Path, pairs: Sequence[tuple[DoorFacts, ScoreResult | None]]) -> Path:
    """Rewrite both tables from `pairs`. A re-publish replaces, never appends.

    The rows are deleted rather than the file unlinked, so a reader holding the
    path keeps reading the same database across a re-run.
    """
    connection = sqlite3.connect(path)
    try:
        with connection:
            connection.executescript(_SCHEMA)
            _add_missing_columns(connection)
            connection.execute("DELETE FROM evidence")
            connection.execute("DELETE FROM doors")
            connection.executemany(
                "INSERT INTO doors"
                " (pams_pin, score, confidence, situs, exclusion_reason, groups, raw_total)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                [_door_row(door, result) for door, result in pairs],
            )
            connection.executemany(
                "INSERT INTO evidence"
                " (pams_pin, seq, type, points, sentence, source, retrieved, imagery)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                list(_evidence_rows(pairs)),
            )
    finally:
        connection.close()
    return path


def _add_missing_columns(connection: sqlite3.Connection) -> None:
    """Bring a database written by an older run up to the current `doors` shape.

    `CREATE TABLE IF NOT EXISTS` is a no-op against a table that already exists,
    so a `data/` left over from a previous run would keep the old column set and
    the insert below would fail. Adding the columns is cheaper — and less
    surprising to whoever is holding that path open — than unlinking the file.
    """
    existing = {row[1] for row in connection.execute("PRAGMA table_info(doors)")}
    for name, kind in (("groups", "TEXT"), ("raw_total", "INTEGER")):
        if name not in existing:
            connection.execute(f"ALTER TABLE doors ADD COLUMN {name} {kind}")


def _door_row(door: DoorFacts, result: ScoreResult | None) -> tuple[Any, ...]:
    return (
        door.pams_pin,
        result.score if result is not None else None,
        result.confidence if result is not None else None,
        door.situs,
        None if result is not None else EXCLUSION_REASON,
        json.dumps(dict(result.groups), sort_keys=True) if result is not None else None,
        result.raw_total if result is not None else None,
    )


def _evidence_rows(
    pairs: Sequence[tuple[DoorFacts, ScoreResult | None]]
) -> Iterator[tuple[Any, ...]]:
    """Every evidence line of every scored door, numbered within its door."""
    for door, result in pairs:
        if result is None:
            continue
        for seq, item in enumerate(result.evidence):
            yield (
                door.pams_pin,
                seq,
                item.type,
                item.points,
                item.sentence,
                item.source,
                item.retrieved.isoformat(),
                json.dumps(dict(item.imagery), sort_keys=True) if item.imagery else None,
            )


# --- run_manifest.json --------------------------------------------------------


def _doors_with_imagery(
    pairs: Sequence[tuple[DoorFacts, ScoreResult | None]]
) -> int:
    """Doors whose published evidence carries at least one re-openable frame.

    Counted off the same pairs `doors.geojson` is written from, exactly like
    `doors_scored`, because it is a fact about the artifact rather than an input
    to it: a number the caller handed in could disagree with the file sitting
    beside it, and this one is the numerator of the "119 of 540" the ethics page
    prints against `doors_total`.
    """
    return sum(
        1
        for _, result in pairs
        if result is not None and any(item.imagery for item in result.evidence)
    )


def _write_manifest(
    path: Path,
    *,
    manifest: RunManifest,
    report: ResolveReport,
    doors_total: int,
    doors_scored: int,
    doors_with_imagery: int,
) -> Path:
    """The run's inputs, its coverage, its cost and what it lost along the way.

    `coverage` here is the R9.4 readout — the share of doors that got a score,
    which is what "537 of 540" states. The resolve report's own coverage (doors
    carrying any joined signal) is a different number and lives, unrenamed, in
    the `resolve` block below.
    """
    payload = {
        "run_at": manifest.run_at.isoformat(),
        "as_of": manifest.as_of.isoformat(),
        "code_version": manifest.code_version,
        "territory_median_value": manifest.territory_median_value,
        "acs_dual_income_threshold": manifest.acs_dual_income_threshold,
        "retrieved": {name: day.isoformat() for name, day in manifest.retrieved.items()},
        "cost_usd": manifest.cost_usd,
        "cost_per_door": _ratio(manifest.cost_usd, doors_scored),
        "doors_total": doors_total,
        "doors_scored": doors_scored,
        "doors_unscored": doors_total - doors_scored,
        "coverage": _ratio(doors_scored, doors_total),
        "deed_vintage": _deed_vintage_block(manifest),
        "vision": _vision_block(manifest, doors_with_imagery),
        "degradations": list(manifest.degradations),
        "resolve": _resolve_block(report),
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def _deed_vintage_block(manifest: RunManifest) -> dict[str, Any]:
    """How old the deed data is, and what that costs the heaviest signal (R13).

    The Mover group is worth more than any other group in the model, and whether
    it fired at all is a property of the extract rather than of the code: if the
    newest deed in the county feed predates the window, no door can earn a mover
    point however correct the rule is. Publishing the vintage beside the run is
    what lets a reader tell "nobody moved here recently" from "the mover rule is
    broken" — a distinction the map alone cannot make.

    `null` is the honest answer for `latest_deed_date` when nothing in the feed
    parsed: a date invented to fill the slot would be worse than the gap.

    This block only *reports* the measurement. The sentence that says the group
    could not fire is recorded by the run, in `degradations`, where every other
    lost signal is named — see `_write_manifest`, which copies that list through
    verbatim rather than adding to it.
    """
    latest = manifest.latest_deed_date
    sale = manifest.latest_sale_date
    return {
        "latest_deed_date": latest.isoformat() if latest is not None else None,
        "mover_window_days": MOVER_WINDOW_DAYS,
        "doors_in_mover_window": manifest.doors_in_mover_window,
        "doors_in_top_band": manifest.doors_in_top_band,
        "top_band_days": TOP_BAND_DAYS,
        # Which register the freshness came from. An empty `source_files` with a
        # populated `latest_deed_date` says plainly that only MOD-IV was read.
        "sales_register": {
            "latest_sale_date": sale.isoformat() if sale is not None else None,
            "source_files": list(manifest.sales_source_files),
            "doors_superseding_modiv": manifest.doors_with_sales_deed,
        },
    }


def _vision_block(manifest: RunManifest, doors_with_imagery: int) -> dict[str, Any] | None:
    """Whether the vision stage ran, and what it lost if it did.

    The stage has three outcomes and the manifest used to record only a
    *sentence* about it, in `degradations`. Prose cannot carry the distinction:
    a run that answered 270 requests, lost 31 of them and left imagery evidence
    on 119 doors recorded a sentence that pattern-matched as a refusal, so the
    Data & Ethics page printed DECLINED beside a stage whose evidence a reviewer
    could click on the map. The three states are told apart here instead:

        ran clean       available=True,  declination_reason=None, answers_lost 0
        ran, lost some  available=True,  declination_reason=None, answers_lost >0
        declined        available=False, declination_reason=<the refusal>

    `answers_*` is what the run measured — one answer per request issued — and is
    carried through unchanged. `doors_with_imagery` is counted from the features
    being written, so the numerator of "119 of 540" cannot disagree with
    `doors.geojson`; `doors_total` above is its denominator.

    `None` for a run that never measured any of this. An unmeasured stage
    published as `available: true, answers_total: 0` would be a claim nobody
    made, on the same principle as the null `latest_deed_date` above.
    """
    if manifest.vision_available is None:
        return None
    return {
        "available": manifest.vision_available,
        "declination_reason": manifest.vision_declination_reason,
        "answers_total": manifest.vision_answers_total,
        "answers_lost": manifest.vision_answers_lost,
        "doors_with_imagery": doors_with_imagery,
    }


def _resolve_block(report: ResolveReport) -> dict[str, Any]:
    """The graded resolve numbers (R3.2), as scalars.

    The per-record `unmatched` list stays out: it is a working artifact for
    whoever is fixing the join, not part of what makes a *run* reproducible,
    and a manifest that grows with the feed stops being readable.
    """
    return {
        "doors_total": report.doors_total,
        "doors_with_signal": report.doors_with_signal,
        "coverage": report.coverage,
        "permits_total": report.permits_total,
        "permits_in_territory": report.permits_in_territory,
        "permits_matched": report.permits_matched,
        "permits_unmatched": len(report.unmatched),
        "permits_in_window": report.permits_in_window,
        "permits_matched_municipal": report.permits_matched_municipal,
        # Both rates ship, labelled: `permit_match_rate` is territory-scoped and
        # `municipal_match_rate` is the one R3.2's floor grades (see
        # `ResolveReport`). A manifest carrying one unlabelled number is how a
        # reader ends up grading the wrong denominator.
        "permit_match_rate": report.permit_match_rate,
        "municipal_match_rate": report.municipal_match_rate,
        "block_lot_match_rate": report.block_lot_match_rate,
        "address_match_rate": report.address_match_rate,
        "doors_with_block_group": report.doors_with_block_group,
        "acs_available": report.acs_available,
        "acs_reason": report.acs_reason,
        "rental_declination_reason": report.rental_declination_reason,
    }


def _ratio(numerator: float, denominator: int) -> float:
    """A ratio that reports 0.0 rather than dividing by an empty territory."""
    return numerator / denominator if denominator else 0.0
