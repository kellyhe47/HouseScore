"""`make pipeline` — harvest -> resolve -> vision -> score -> publish (T009, R2.2).

STUB — names only, so `tests/test_pipeline.py` can import. Every behaviour below
raises; the contract each one has to satisfy is pinned in that test module.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Sequence

from houseaccount.config import Config
from houseaccount.cost import CostLedger
from houseaccount.http import Transport, requests_transport
from houseaccount.publish import PublishResult, RunManifest
from houseaccount.resolve import ResolveReport
from houseaccount.sources.parcels import DEFAULT_MUN

#: Ramsey Golf & Country Club, (lon, lat) — the territory's anchor (R1.1).
TERRITORY_CENTER: tuple[float, float] = (-74.1560, 41.0447)

#: The ~540 doors the territory is defined as.
TERRITORY_TARGET = 540

#: PRD R6 capacity prior: a block group at or above this share of dual-income
#: families earns the +5 nudge.
ACS_DUAL_INCOME_THRESHOLD = 0.35


@dataclass(frozen=True)
class PipelineResult:
    """One completed run: what resolved, what was published, what degraded."""

    report: ResolveReport
    manifest: RunManifest
    published: PublishResult
    ledger: CostLedger
    degradations: tuple[str, ...] = ()


def code_version() -> str:
    """The git short SHA if this is a checkout, else the package version."""
    raise NotImplementedError


def run_pipeline(
    *,
    config: Config,
    transport: Transport = requests_transport,
    client: Any | None = None,
    rental_provider: Any | None = None,
    as_of: date | None = None,
    now: datetime | None = None,
    center: tuple[float, float] = TERRITORY_CENTER,
    target: int = TERRITORY_TARGET,
    mun: str = DEFAULT_MUN,
) -> PipelineResult:
    """Run all five stages in order and publish the artifacts."""
    raise NotImplementedError


def main(argv: Sequence[str] | None = None) -> int:
    """`python -m houseaccount.pipeline`."""
    raise NotImplementedError
