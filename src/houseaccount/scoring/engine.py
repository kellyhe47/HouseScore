"""Deterministic House Score engine (R6) — STUB.

The value types below are the seam the tests and the pipeline both use; the
arithmetic, the evidence list, the confidence flag and the group breakdown are
the implementer's job. No I/O, no network: inputs arrive already normalised.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Mapping, Sequence

from houseaccount.scoring.evidence import EvidenceItem


@dataclass(frozen=True)
class Permit:
    """One construction permit joined to this parcel. `contractor` is None when
    the Socrata record carries no contractor field (excluded from the churn
    test, still worth permit points)."""

    permit_date: date | None = None
    permit_type: str = ""
    contractor: str | None = None


@dataclass(frozen=True)
class ScoreInput:
    """Everything the score needs for one door, already normalised."""

    as_of: date
    territory_median_value: float
    acs_dual_income_threshold: float
    pams_pin: str = ""
    deed_date: date | None = None
    sale_price: float = 0.0
    sales_code: str = ""
    yr_constr: int = 0
    net_value: float = 0.0
    calc_acre: float = 0.0
    permits: Sequence[Permit] = ()
    dual_income_pct: float | None = None
    median_hh_income: float | None = None
    rental_registration_match: bool = False
    vision: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_fixture(cls, given: Mapping[str, Any]) -> "ScoreInput":
        """Build from a golden fixture's `given` dict (also the pipeline's entry
        point). DEED_DATE goes through `normalize.parse_deed_date`, so both ISO
        and raw YYMMDD strings are accepted."""
        raise NotImplementedError


@dataclass(frozen=True)
class ScoreResult:
    """The scored door. `raw_total` is the UNCLAMPED sum of `groups`."""

    score: int
    confidence: str
    evidence: Sequence[EvidenceItem]
    groups: Mapping[str, int]
    raw_total: int


def score_door(door: ScoreInput) -> ScoreResult:
    raise NotImplementedError
