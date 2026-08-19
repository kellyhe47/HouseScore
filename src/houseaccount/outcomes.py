"""Ticket 104 / R36: field-outcome record contract and band-lift helper.

Outcome records retain the score-contract version and the attempt-time
score (historical records per R33, never published score surfaces).
Primary outcome is per answered door; secondary outcomes are booleans.
The success test is directional ranking lift across score bands — this
module reports rates only, and deliberately defines no absolute success
threshold: none exists until real field outcomes have been collected.
"""

from __future__ import annotations

from bisect import bisect_right
from datetime import datetime

PRIMARY_OUTCOMES = frozenset(
    {"receptive_conversation", "not_receptive", "no_answer"}
)

SECONDARY_OUTCOME_FIELDS = ("follow_up_requested", "service_booked")

_REQUIRED_FIELDS = (
    "door_id",
    "attempt_time",
    "score_contract_version",
    "attempt_score",
    "primary_outcome",
    "secondary_outcomes",
)


class OutcomeValidationError(ValueError):
    """An outcome record is missing or violates a contract field."""


def validate_outcome_record(record: dict) -> dict:
    """Validate one field-outcome record; return it unchanged.

    Raises OutcomeValidationError on any contract violation: a missing
    field (score_contract_version and attempt_score are the R36
    non-negotiables), an unparseable attempt_time, an out-of-range score,
    an unknown primary outcome, or a non-boolean secondary outcome.
    """
    if not isinstance(record, dict):
        raise OutcomeValidationError(
            f"outcome record must be a dict, got {type(record).__name__}"
        )

    missing = [field for field in _REQUIRED_FIELDS if field not in record]
    if missing:
        raise OutcomeValidationError(f"outcome record missing fields: {missing}")

    door_id = record["door_id"]
    if not isinstance(door_id, str) or not door_id.strip():
        raise OutcomeValidationError("door_id must be a non-empty string")

    attempt_time = record["attempt_time"]
    if not isinstance(attempt_time, str):
        raise OutcomeValidationError("attempt_time must be an ISO-8601 string")
    try:
        datetime.fromisoformat(attempt_time)
    except ValueError as error:
        raise OutcomeValidationError(f"attempt_time not ISO-8601: {error}") from error

    version = record["score_contract_version"]
    if not isinstance(version, str) or not version.strip():
        raise OutcomeValidationError(
            "score_contract_version must be a non-empty string (R36: the "
            "version travels with the record)"
        )

    score = record["attempt_score"]
    if isinstance(score, bool) or not isinstance(score, int) or not 0 <= score <= 100:
        raise OutcomeValidationError(
            "attempt_score must be an integer 0-100 (R36: the attempt-time "
            "score travels with the record)"
        )

    primary = record["primary_outcome"]
    if primary not in PRIMARY_OUTCOMES:
        raise OutcomeValidationError(
            f"unknown primary_outcome {primary!r}; expected one of "
            f"{sorted(PRIMARY_OUTCOMES)}"
        )

    secondary = record["secondary_outcomes"]
    if not isinstance(secondary, dict):
        raise OutcomeValidationError("secondary_outcomes must be a dict")
    for field in SECONDARY_OUTCOME_FIELDS:
        if field not in secondary:
            raise OutcomeValidationError(f"secondary_outcomes missing {field!r}")
        if not isinstance(secondary[field], bool):
            raise OutcomeValidationError(
                f"secondary_outcomes.{field} must be a boolean, got "
                f"{secondary[field]!r}"
            )

    return record


def band_lift(records: list[dict], quantile_edges: list[float]) -> list[dict]:
    """Per-band primary/secondary outcome rates.

    `quantile_edges` are ascending score cut points (for example the decile
    stops from the R34 report); N edges define N+1 bands. Deterministic and
    pure: input order does not matter and nothing is mutated. Reports rates
    only — directional lift is the read, and nothing here defines what rate
    would count as success.
    """
    edges = list(quantile_edges)
    if edges != sorted(edges):
        raise OutcomeValidationError("quantile_edges must be ascending")

    counts = [
        {"attempts": 0, "receptive": 0, "follow_up": 0, "service_booked": 0}
        for _ in range(len(edges) + 1)
    ]

    for record in records:
        validated = validate_outcome_record(record)
        band = bisect_right(edges, validated["attempt_score"])
        tally = counts[band]
        tally["attempts"] += 1
        if validated["primary_outcome"] == "receptive_conversation":
            tally["receptive"] += 1
        if validated["secondary_outcomes"]["follow_up_requested"]:
            tally["follow_up"] += 1
        if validated["secondary_outcomes"]["service_booked"]:
            tally["service_booked"] += 1

    def rate(numerator: int, attempts: int) -> float:
        return numerator / attempts if attempts else 0.0

    return [
        {
            "band": index,
            "attempts": tally["attempts"],
            "receptive_rate": rate(tally["receptive"], tally["attempts"]),
            "follow_up_rate": rate(tally["follow_up"], tally["attempts"]),
            "service_booked_rate": rate(tally["service_booked"], tally["attempts"]),
        }
        for index, tally in enumerate(counts)
    ]
