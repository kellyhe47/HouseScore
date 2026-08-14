"""Serving artifacts: doors.geojson + SQLite + run_manifest.json (T009).

STUB — names only, so `tests/test_publish.py` can import. Every behaviour below
raises; the contract each one has to satisfy is pinned in that test module.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

#: The three artifacts a run publishes. `SQLITE_NAME` is already pinned by the
#: `clean` target in the Makefile.
DOORS_GEOJSON_NAME = "doors.geojson"
SQLITE_NAME = "houseaccount.sqlite"
RUN_MANIFEST_NAME = "run_manifest.json"

#: Published on a door the county record cannot support a score for (R9.4).
EXCLUSION_REASON = "parcel record incomplete in county data"


@dataclass(frozen=True)
class RunManifest:
    """The once-per-run inputs a reader needs to reproduce the run (R13)."""

    run_at: datetime
    as_of: date
    code_version: str
    territory_median_value: float
    acs_dual_income_threshold: float
    retrieved: Mapping[str, date]
    cost_usd: float
    degradations: Sequence[str] = ()


@dataclass(frozen=True)
class PublishResult:
    """Where the artifacts landed, and how many doors they describe."""

    geojson_path: Path
    sqlite_path: Path
    manifest_path: Path
    doors_total: int
    doors_scored: int
    doors_unscored: int


def parcel_record_incomplete(door: Any) -> bool:
    """True when the county record carries no fact the score can be built from."""
    raise NotImplementedError


def publish(
    scored: Sequence[tuple[Any, Any]],
    *,
    report: Any,
    manifest: RunManifest,
    data_dir: Path,
) -> PublishResult:
    """Write the three artifacts for one run and report what was written."""
    raise NotImplementedError
