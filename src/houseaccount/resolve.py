"""Entity resolution: every signal joined onto a territory door (T006, R3).

STUB — written by the test-writer so `tests/test_resolve.py` can import. Every
name below is an empty shell; the implementation is ticket 006's job.

The canonical key is `PAMS_PIN`. Permits carry `block`/`lot` and no situs
address, so the primary join is `normalize.parcel_key(mun, block, lot)` and the
normalized situs address is the fallback for any permit-like record that does
carry one. Nothing in-territory is ever silently dropped: what fails to join is
reported, and what is deliberately excluded is counted.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable, Mapping, Sequence

from houseaccount.scoring.engine import ScoreInput
from houseaccount.sources.acs import AcsResult
from houseaccount.sources.parcels import DEFAULT_MUN, Parcel
from houseaccount.sources.permits import PERMIT_WINDOW_DAYS
from houseaccount.sources.rental import RentalRegistrationProvider

#: Provenance keys — one per signal a door can carry.
SIGNAL_PARCEL = "parcel"
SIGNAL_PERMITS = "permits"
SIGNAL_ACS = "acs"
SIGNAL_RENTAL = "rental"


@dataclass(frozen=True)
class Provenance:
    """Where one signal on one door came from, and when it was retrieved."""


@dataclass(frozen=True)
class UnmatchedPermit:
    """An in-territory permit that joined to no door, and why."""


@dataclass(frozen=True)
class DoorFacts:
    """Everything known about one door, ready to become a `ScoreInput`."""

    def to_score_input(
        self,
        *,
        as_of: date,
        territory_median_value: float,
        acs_dual_income_threshold: float,
        vision: Mapping[str, Any] | None = None,
    ) -> ScoreInput:
        raise NotImplementedError("T006: DoorFacts.to_score_input")


@dataclass(frozen=True)
class ResolveReport:
    """The match-rate and coverage numbers the rubric grades (R3.2)."""


@dataclass(frozen=True)
class ResolveResult:
    """Resolved doors plus the report explaining what joined and what did not."""


def resolve(
    parcels: Sequence[Parcel],
    permits: Iterable[Any],
    acs: AcsResult,
    rental_provider: RentalRegistrationProvider,
    as_of: date,
    *,
    block_group_index: Any | None = None,
    mun: str = DEFAULT_MUN,
    permit_window_days: int = PERMIT_WINDOW_DAYS,
    retrieved_at: Mapping[str, date] | None = None,
) -> ResolveResult:
    raise NotImplementedError("T006: resolve")
