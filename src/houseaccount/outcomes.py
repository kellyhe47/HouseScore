"""Ticket 104 / R36: field-outcome record contract and band-lift helper.

Outcome records retain the score-contract version and the attempt-time
score (historical records per R33, never published score surfaces).
Primary outcome is per answered door; secondary outcomes are booleans.
The success test is directional ranking lift across score bands — no
absolute conversion target exists anywhere in this module.

Test-first stub: tests/test_outcomes.py pins the shape; NotImplementedError
until the implementer lands it.
"""

from __future__ import annotations

PRIMARY_OUTCOMES = frozenset(
    {"receptive_conversation", "not_receptive", "no_answer"}
)

SECONDARY_OUTCOME_FIELDS = ("follow_up_requested", "service_booked")


class OutcomeValidationError(ValueError):
    """An outcome record is missing or violates a contract field."""


def validate_outcome_record(record: dict) -> dict:
    """Validate one field-outcome record; return it. Raises
    OutcomeValidationError on any contract violation (missing
    score_contract_version, missing attempt_score, unknown primary
    outcome, missing attempt_time or door_id, ...)."""
    raise NotImplementedError("ticket 104: outcome validator not implemented")


def band_lift(records: list[dict], quantile_edges: list[float]) -> list[dict]:
    """Per-band primary/secondary outcome rates given validated records and
    ascending quantile edges from the R34 report. Deterministic. Reports
    rates only — never an absolute conversion target."""
    raise NotImplementedError("ticket 104: band lift not implemented")
