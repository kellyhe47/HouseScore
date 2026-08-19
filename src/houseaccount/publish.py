"""Serving artifacts: doors.geojson + SQLite + run_manifest.json — V2 cutover
(ticket 103, plan R27/R28/R7/R37).

Three files come out of one call, and each answers a different question:

* `data/doors.geojson` is what the map renders and what the rep clicks. Every
  door in the territory appears exactly once — including the ones that could
  not be scored, because R9.4 makes them clickable and counts them in the
  "537 of 540" readout.
* `data/houseaccount.sqlite` is what the MCP server reads. Two tables, `doors`
  and `evidence`, joined on `pams_pin`. The V1 `groups`/`raw_total` columns
  are gone (R28); the doors table carries the V2 category fields instead.
* `data/run_manifest.json` is what makes the run reproducible: the once-per-run
  inputs, when each source was retrieved, what the run cost, which code
  produced it — and `score_contract_version` (R27/R28). The V1 fields
  `territory_median_value`, `top_band_days` and `doors_in_top_band` are gone.

**The seam.** `publish(scored, *, report, manifest, data_dir)` where `scored`
is a sequence of `(DoorFacts, envelope | None)` pairs in publication order and
`envelope` is the `score_door_v2` result mapping (the pipeline may have
enriched imagery-derived evidence entries with a re-openable `imagery` frame).
Publish re-derives nothing.

**What "unscored" means.** A `None` envelope is published as an exclusion via
`parcel_record_incomplete`: the county record carries none of the three parcel
facts a score could be built from. Anything less than that scores through V2's
data-gap path instead (typed gaps, `low` confidence at 2+ — R23/R37).

**Byte reproducibility (R28).** Publishing the same doors with the same
manifest into a fresh directory reproduces every artifact byte-identically:
sorted keys, fixed separators, one trailing newline for the JSON; the SQLite
database is dropped and rebuilt from the same ordered inserts.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from houseaccount.resolve import DoorFacts, ResolveReport

#: The three artifacts a run publishes.
DOORS_GEOJSON_NAME = "doors.geojson"
SQLITE_NAME = "houseaccount.sqlite"
RUN_MANIFEST_NAME = "run_manifest.json"

#: Published on a door the county record cannot support a score for (R9.4).
EXCLUSION_REASON = "parcel record incomplete in county data"

#: The scoring contract every published record names (R27).
SCORE_CONTRACT_VERSION = "v2"

#: V2's fresh-mover band (plan R4): full strength 0-90 days, decaying to 365.
#: This is the window `deed_vintage` discloses.
MOVER_WINDOW_DAYS = 90

#: Two tables, rebuilt on every publish. `seq` is explicit because
#: `explain_score` replays the trail in the order the engine built it, and rows
#: in a table have no inherent order to fall back on.
_SCHEMA = """
CREATE TABLE doors (
    pams_pin               TEXT PRIMARY KEY,
    score                  INTEGER,
    confidence             TEXT,
    situs                  TEXT NOT NULL,
    exclusion_reason       TEXT,
    score_contract_version TEXT NOT NULL,
    project                INTEGER,
    capacity               INTEGER,
    fit                    INTEGER,
    base                   INTEGER,
    mover_eligible         INTEGER,
    days_since_move        INTEGER,
    mover_strength         REAL,
    mover_lift             REAL,
    rental_modifier        INTEGER,
    adjustment             REAL
);
CREATE TABLE evidence (
    pams_pin  TEXT    NOT NULL,
    seq       INTEGER NOT NULL,
    type      TEXT    NOT NULL,
    points    REAL    NOT NULL,
    reason    TEXT    NOT NULL,
    imagery   TEXT,
    PRIMARY KEY (pams_pin, seq)
);
"""


@dataclass(frozen=True)
class RunManifest:
    """The once-per-run inputs a reader needs to reproduce the run (R28).

    The V1 fields (`territory_median_value`, `doors_in_top_band`) are gone from
    the seam, not just from the JSON: a caller still measuring them fails
    loudly at construction time.
    """

    run_at: datetime
    as_of: date
    code_version: str
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
    #: The vision stage's own state. `None` is "this run never measured the
    #: stage" and publishes no block rather than a claim nobody made.
    vision_available: bool | None = None
    vision_declination_reason: str | None = None
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
    """True when the county record carries no fact a score can be built from.

    Deliberately an *all three missing* test rather than an any-missing one: a
    record with only a deed date, or only a year built, or only an assessment
    still scores through V2's data-gap path, flagged `low` confidence with
    typed gaps. Geometry is absent from the test entirely — it is how a door
    is drawn, not what it is scored from.
    """
    return not (door.deed_date is not None or door.yr_constr > 0 or door.net_value > 0)


def publish(
    scored: Sequence[tuple[DoorFacts, Mapping[str, Any] | None]],
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
    doors_scored = sum(1 for _, envelope in pairs if envelope is not None)

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
    path: Path, pairs: Sequence[tuple[DoorFacts, Mapping[str, Any] | None]]
) -> Path:
    """One feature per door, in publication order, as deterministic bytes."""
    payload = {
        "type": "FeatureCollection",
        "features": [_feature(door, envelope) for door, envelope in pairs],
    }
    path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


def _feature(door: DoorFacts, envelope: Mapping[str, Any] | None) -> dict[str, Any]:
    """One door as GeoJSON. This property set is the R11.1 allowlist, V2-shaped.

    Written as a literal rather than projected off the record, so a widened
    upstream schema has nowhere to leak into. A door with no polygon ships
    `"geometry": null` instead of being dropped — R9.4 still has to count it.
    """
    scored = envelope is not None
    return {
        "type": "Feature",
        "geometry": dict(door.geometry) if door.geometry else None,
        "properties": {
            "PAMS_PIN": door.pams_pin,
            "score": envelope["score"] if scored else None,
            "confidence": envelope["confidence"] if scored else None,
            "evidence": [_evidence(item) for item in envelope["evidence"]] if scored else [],
            "situs": door.situs,
            "exclusion_reason": None if scored else EXCLUSION_REASON,
            "score_contract_version": SCORE_CONTRACT_VERSION,
            "categories": dict(envelope["categories"]) if scored else None,
            "base": envelope["base"] if scored else None,
            "mover": dict(envelope["mover"]) if scored else None,
            "mover_lift": envelope["mover_lift"] if scored else None,
            "rental_modifier": envelope["rental_modifier"] if scored else None,
            "adjustment": envelope["adjustment"] if scored else None,
            "data_gaps": [dict(gap) for gap in envelope["data_gaps"]] if scored else None,
        },
    }


def _evidence(item: Mapping[str, Any]) -> dict[str, Any]:
    """One V2 evidence line, with its re-openable frame when it has one."""
    frame = item.get("imagery")
    return {
        "type": item["type"],
        "points": item["points"],
        "reason": item["reason"],
        "imagery": dict(frame) if frame else None,
    }


# --- houseaccount.sqlite ------------------------------------------------------


def _write_sqlite(
    path: Path, pairs: Sequence[tuple[DoorFacts, Mapping[str, Any] | None]]
) -> Path:
    """Rebuild both tables from `pairs`. A re-publish replaces, never appends."""
    connection = sqlite3.connect(path)
    try:
        with connection:
            connection.execute("DROP TABLE IF EXISTS evidence")
            connection.execute("DROP TABLE IF EXISTS doors")
            connection.executescript(_SCHEMA)
            connection.executemany(
                "INSERT INTO doors"
                " (pams_pin, score, confidence, situs, exclusion_reason,"
                "  score_contract_version, project, capacity, fit, base,"
                "  mover_eligible, days_since_move, mover_strength, mover_lift,"
                "  rental_modifier, adjustment)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [_door_row(door, envelope) for door, envelope in pairs],
            )
            connection.executemany(
                "INSERT INTO evidence (pams_pin, seq, type, points, reason, imagery)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                list(_evidence_rows(pairs)),
            )
    finally:
        connection.close()
    return path


def _door_row(door: DoorFacts, envelope: Mapping[str, Any] | None) -> tuple[Any, ...]:
    scored = envelope is not None
    categories = envelope["categories"] if scored else {}
    mover = envelope["mover"] if scored else {}
    return (
        door.pams_pin,
        envelope["score"] if scored else None,
        envelope["confidence"] if scored else None,
        door.situs,
        None if scored else EXCLUSION_REASON,
        SCORE_CONTRACT_VERSION,
        categories.get("project"),
        categories.get("capacity"),
        categories.get("fit"),
        envelope["base"] if scored else None,
        (1 if mover.get("eligible") else 0) if scored else None,
        mover.get("days_since_move") if scored else None,
        mover.get("strength") if scored else None,
        envelope["mover_lift"] if scored else None,
        envelope["rental_modifier"] if scored else None,
        envelope["adjustment"] if scored else None,
    )


def _evidence_rows(
    pairs: Sequence[tuple[DoorFacts, Mapping[str, Any] | None]]
) -> Iterator[tuple[Any, ...]]:
    """Every evidence line of every scored door, numbered within its door."""
    for door, envelope in pairs:
        if envelope is None:
            continue
        for seq, item in enumerate(envelope["evidence"]):
            frame = item.get("imagery")
            yield (
                door.pams_pin,
                seq,
                item["type"],
                item["points"],
                item["reason"],
                json.dumps(dict(frame), sort_keys=True) if frame else None,
            )


# --- run_manifest.json --------------------------------------------------------


def _doors_with_imagery(
    pairs: Sequence[tuple[DoorFacts, Mapping[str, Any] | None]]
) -> int:
    """Doors whose published evidence carries at least one re-openable frame."""
    return sum(
        1
        for _, envelope in pairs
        if envelope is not None
        and any(item.get("imagery") for item in envelope["evidence"])
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
    """The run's inputs, its coverage, its cost and what it lost along the way."""
    payload = {
        "run_at": manifest.run_at.isoformat(),
        "as_of": manifest.as_of.isoformat(),
        "score_contract_version": SCORE_CONTRACT_VERSION,
        "code_version": manifest.code_version,
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
    """How old the deed data is, V2-shaped (R28).

    The vintage disclosure survives the cutover — a reader still needs to tell
    "no movers here right now" from "the mover rule is broken" — but the V1
    top-band fields and the 90-day-cutoff degradation prose are gone: V2's
    mover strength decays to 365 days, so "newest deed outside 90 days" no
    longer means the signal cannot fire. `null` is the honest answer for
    `latest_deed_date` when nothing in the feed parsed.
    """
    latest = manifest.latest_deed_date
    sale = manifest.latest_sale_date
    return {
        "latest_deed_date": latest.isoformat() if latest is not None else None,
        "mover_window_days": MOVER_WINDOW_DAYS,
        "doors_in_mover_window": manifest.doors_in_mover_window,
        "sales_register": {
            "latest_sale_date": sale.isoformat() if sale is not None else None,
            "source_files": list(manifest.sales_source_files),
            "doors_superseding_modiv": manifest.doors_with_sales_deed,
        },
    }


def _vision_block(manifest: RunManifest, doors_with_imagery: int) -> dict[str, Any] | None:
    """Whether the vision stage ran, and what it lost if it did.

        ran clean       available=True,  declination_reason=None, answers_lost 0
        ran, lost some  available=True,  declination_reason=None, answers_lost >0
        declined        available=False, declination_reason=<the refusal>

    `None` for a run that never measured any of this.
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
    """The graded resolve numbers (R3.2), as scalars."""
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
