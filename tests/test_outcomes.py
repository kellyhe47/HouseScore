"""Ticket 104 / R36: outcome-log contract + band-lift helper.

Pinned record shape:

    door_id                 str (PAMS pin)
    attempt_time            ISO-8601 str
    score_contract_version  str, required (historical record per R33)
    attempt_score           int 0-100, required
    primary_outcome         one of {receptive_conversation, not_receptive,
                                    no_answer}
    secondary_outcomes      {follow_up_requested: bool, service_booked: bool}

`validate_outcome_record(record) -> dict` returns the record or raises
OutcomeValidationError. `band_lift(records, quantile_edges) -> list[dict]`
gives per-band primary/secondary rates — directional lift only; no absolute
conversion target exists anywhere in this module.
"""

from __future__ import annotations

import copy
import inspect
import re

import pytest

import houseaccount.outcomes as outcomes_module
from houseaccount.outcomes import (
    PRIMARY_OUTCOMES,
    OutcomeValidationError,
    band_lift,
    validate_outcome_record,
)


def make_record(**overrides) -> dict:
    record = {
        "door_id": "0248_3502_8.01",
        "attempt_time": "2026-08-19T14:05:00-04:00",
        "score_contract_version": "v2",
        "attempt_score": 64,
        "primary_outcome": "receptive_conversation",
        "secondary_outcomes": {
            "follow_up_requested": True,
            "service_booked": False,
        },
    }
    record.update(overrides)
    return record


# --------------------------------------------------------------- contract


def test_primary_outcome_vocabulary_pinned():
    assert PRIMARY_OUTCOMES == {
        "receptive_conversation",
        "not_receptive",
        "no_answer",
    }


def test_valid_record_accepted():
    record = make_record()
    assert validate_outcome_record(record) == record


@pytest.mark.parametrize("outcome", sorted(
    {"receptive_conversation", "not_receptive", "no_answer"}
))
def test_every_primary_outcome_accepted(outcome):
    assert validate_outcome_record(make_record(primary_outcome=outcome))


@pytest.mark.parametrize("missing", [
    "score_contract_version",  # R36: version must be retained
    "attempt_score",           # R36: attempt-time score must be retained
    "door_id",
    "attempt_time",
    "primary_outcome",
    "secondary_outcomes",
])
def test_missing_field_rejected(missing):
    record = make_record()
    del record[missing]
    with pytest.raises(OutcomeValidationError):
        validate_outcome_record(record)


def test_unknown_primary_outcome_rejected():
    with pytest.raises(OutcomeValidationError):
        validate_outcome_record(make_record(primary_outcome="sold_on_the_spot"))


def test_non_boolean_secondary_rejected():
    bad = make_record()
    bad["secondary_outcomes"]["service_booked"] = "yes"
    with pytest.raises(OutcomeValidationError):
        validate_outcome_record(bad)


# -------------------------------------------------------------- band lift


EDGES = [20.0, 40.0, 60.0, 80.0]  # 5 bands over 0-100


def sample_records() -> list[dict]:
    rows = [
        # (score, primary, follow_up, booked)
        (5, "no_answer", False, False),
        (12, "not_receptive", False, False),
        (25, "receptive_conversation", False, False),
        (35, "no_answer", False, False),
        (45, "receptive_conversation", True, False),
        (55, "not_receptive", False, False),
        (64, "receptive_conversation", True, True),
        (70, "receptive_conversation", False, False),
        (85, "receptive_conversation", True, True),
        (95, "receptive_conversation", True, False),
    ]
    return [
        make_record(
            door_id=f"0248_TEST_{i}",
            attempt_score=score,
            primary_outcome=primary,
            secondary_outcomes={
                "follow_up_requested": follow,
                "service_booked": booked,
            },
        )
        for i, (score, primary, follow, booked) in enumerate(rows)
    ]


def test_band_lift_shape_and_rates():
    bands = band_lift(sample_records(), EDGES)
    assert len(bands) == len(EDGES) + 1
    for i, band in enumerate(bands):
        assert band["band"] == i
        assert {"attempts", "receptive_rate", "follow_up_rate",
                "service_booked_rate"} <= set(band)
    # Top band: scores 85 and 95 — both receptive, both follow-up, one booked.
    top = bands[-1]
    assert top["attempts"] == 2
    assert top["receptive_rate"] == pytest.approx(1.0)
    assert top["follow_up_rate"] == pytest.approx(1.0)
    assert top["service_booked_rate"] == pytest.approx(0.5)
    # Bottom band: 5 and 12 — no receptive conversations.
    assert bands[0]["attempts"] == 2
    assert bands[0]["receptive_rate"] == pytest.approx(0.0)


def test_band_lift_deterministic_and_pure():
    records = sample_records()
    before = copy.deepcopy(records)
    assert band_lift(records, EDGES) == band_lift(records, EDGES)
    assert band_lift(list(reversed(records)), EDGES) == band_lift(records, EDGES)
    assert records == before  # inputs not mutated


def test_empty_band_reports_zero_attempts_not_crash():
    bands = band_lift([make_record(attempt_score=95)], EDGES)
    assert bands[0]["attempts"] == 0
    assert bands[-1]["attempts"] == 1


def test_no_absolute_conversion_target_anywhere():
    """R36: an absolute conversion target must not be invented before field
    outcomes exist. The module exposes rates only — no threshold/target
    constant, and no target key in the band output."""
    source = inspect.getsource(outcomes_module)
    assert not re.search(r"(?i)conversion[_ ]?(target|threshold)", source)
    bands = band_lift(sample_records(), EDGES)
    for band in bands:
        assert not any("target" in key for key in band)
