"""Ticket 101 unit tests: V2 engine acceptance criteria at the boundaries.

Golden fixtures (tests/test_v2_golden.py) prove the envelope end to end;
these tests pin the formula-level seams and band edges the ticket calls out:
M(d) exact values (R4), the blend (R5), round_half_up + clamp/adjustment
reconciliation (R7), mover eligibility (R3), comparables and percentile bands
(R13/R14), roof and home-age bands (R17/R18), rental currency (R6),
confidence gap count (R37), and identity-field exclusion (R25).

Written test-first: everything except pure canonicalizer/loader
infrastructure fails until the engine is implemented.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from houseaccount.scoring.v2 import (
    V2Bundle,
    blend,
    mover_strength,
    round_half_up,
    score_door_v2,
)

AS_OF = date(2026, 8, 18)
GOLDEN_DIR = Path(__file__).resolve().parent.parent / "eval" / "v2" / "golden"

# M(180) with no intermediate rounding, for the AE3 blend check.
M_180 = 40.00494550516092


def make_inputs(**overrides):
    """Fixture-shaped given.inputs with quiet defaults (base 0, no gaps)."""
    inputs = {
        "parcel": {
            "pin": "0248_UT_SUBJ",
            "assessed_value": 400000,
            "construction_year": 2020,
            "lot_acres": 0.2,
            "sdl_page_available": True,
        },
        "sales": [],
        "permits": [],
        "local_comparables": [
            {"pin": f"0248_CMP_{i:02d}", "assessed_value": 500000, "distance_m": 50 + i}
            for i in range(20)
        ],
        "territory_assessed_values": [400000] + [700000 + 10000 * i for i in range(10)],
        "acs_block_group": {"dual_income_pct": 0.3},
        "rental_registry": {"available": True, "registrations": []},
        "imagery": {"available": True, "observations": []},
    }
    inputs.update(overrides)
    return inputs


def score(**overrides):
    return score_door_v2(V2Bundle.from_fixture({"inputs": make_inputs(**overrides)}), AS_OF)


def evidence_points(result, etype):
    matches = [e["points"] for e in result["evidence"] if e["type"] == etype]
    assert len(matches) <= 1, f"duplicate evidence entries for {etype}"
    return matches[0] if matches else None


def gap_types(result):
    return sorted(g["type"] for g in result["data_gaps"])


# --- R4: M(d) exact values, no intermediate rounding ---


@pytest.mark.parametrize(
    ("days", "expected_3dp"),
    [
        (0, 90.0),
        (90, 90.0),  # last flat-band day
        (91, 89.246),  # first decay day
        (180, 40.005),  # AE3 anchor
        (364, 0.103),  # last nonzero day
        (365, 0.0),
        (400, 0.0),
    ],
)
def test_mover_strength(days, expected_3dp):
    assert round(mover_strength(days), 3) == expected_3dp


# --- R5: blend P = B + (M/90) * ((90 + B/8) - B) ---


@pytest.mark.parametrize(
    ("base", "strength", "expected_3dp"),
    [
        (0, 90.0, 90.0),  # AE1: full-strength mover, no base
        (40, 90.0, 95.0),  # AE? day-90 anchor (fixture G-003)
        (80, 90.0, 100.0),  # B_max maps to top of band
        (40, 0.0, 40.0),  # no mover -> base passes through
        (40, M_180, 64.447),  # AE3 pre_rounding
    ],
)
def test_blend(base, strength, expected_3dp):
    assert round(blend(base, strength), 3) == expected_3dp


# --- R7: round_half_up semantics ---


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0.4, 0), (0.5, 1), (1.5, 2), (2.5, 3), (64.447, 64), (64.5, 65), (99.5, 100)],
)
def test_round_half_up(value, expected):
    assert round_half_up(value) == expected


# --- R7: clamp + adjustment reconciliation ---


def test_clamp_floor_zero_reconciles_via_adjustment():
    result = score(
        rental_registry={"available": True, "registrations": [{"date": "2026-01-01"}]},
    )
    assert round(result["pre_rounding"], 3) == -25.0
    assert result["score"] == 0
    assert round(result["adjustment"], 3) == 25.0


def test_rounding_adjustment_reconciles():
    # Valid sale 180 days before as_of, base 0: pre_rounding = M(180) = 40.005.
    result = score(
        sales=[{"source": "SR1A", "date": "2026-02-19", "price": 850000, "sales_code": ""}],
    )
    assert result["mover"]["days_since_move"] == 180
    assert round(result["mover"]["strength"], 3) == 40.005
    assert round(result["pre_rounding"], 3) == 40.005
    assert result["score"] == 40
    assert round(result["adjustment"], 3) == -0.005


# --- R7 invariant: evidence ledger reconciles to the score ---


@pytest.mark.parametrize(
    "fixture_name",
    [
        "03-mover-flat-band-day-90.json",
        "18-project-category-cap-45.json",
        "30-need-all-signals-stack-to-48.json",
        "36-rental-clamp-floor-zero.json",
    ],
)
def test_r7_ledger_invariant(fixture_name):
    fixture = json.loads((GOLDEN_DIR / fixture_name).read_text())
    given = fixture["given"]
    as_of = date.fromisoformat(given["clock"]["as_of"][:10])
    result = score_door_v2(V2Bundle.from_fixture(given), as_of)
    cats = result["categories"]
    assert result["base"] == cats["project"] + cats["capacity"] + cats["fit"]
    ledger = result["base"] + result["mover_lift"] + result["rental_modifier"]
    assert round(ledger, 3) == round(result["pre_rounding"], 3)
    assert round(ledger + result["adjustment"], 3) == result["score"]
    assert round(sum(e["points"] for e in result["evidence"]), 3) == round(ledger, 3)


# --- R3: mover eligibility ---


@pytest.mark.parametrize(
    ("sales", "eligible", "days"),
    [
        # nominal $100 price is not a mover
        ([{"source": "SR1A", "date": "2026-07-02", "price": 100, "sales_code": ""}], False, None),
        # $101 with empty code and past date is
        ([{"source": "SR1A", "date": "2026-07-02", "price": 101, "sales_code": ""}], True, 47),
        # nonempty disqualifying sales code
        ([{"source": "SR1A", "date": "2026-07-02", "price": 850000, "sales_code": "26"}], False, None),
        # future-dated sale
        ([{"source": "SR1A", "date": "2026-09-01", "price": 850000, "sales_code": ""}], False, None),
        # disqualified newer sale does not shadow the older valid one
        (
            [
                {"source": "SR1A", "date": "2025-10-01", "price": 850000, "sales_code": ""},
                {"source": "SR1A", "date": "2026-08-01", "price": 10, "sales_code": ""},
            ],
            True,
            321,
        ),
    ],
)
def test_mover_eligibility(sales, eligible, days):
    result = score(sales=sales)
    assert result["mover"]["eligible"] is eligible
    assert result["mover"]["days_since_move"] == days
    if not eligible:
        assert round(result["mover"]["strength"], 3) == 0.0
        assert round(result["mover_lift"], 3) == 0.0


# --- R13: local comparables bands (median of nearest <=20) ---


@pytest.mark.parametrize(
    ("assessed", "points"),
    [
        (500000, None),  # exactly 1.0x: at median -> 0, no evidence entry
        (500001, 3),  # just above 1.0x
        (550000, 3),  # 1.1x
        (600000, 7),  # exactly 1.2x (inclusive)
        (700000, 7),  # 1.4x
        (750000, 10),  # exactly 1.5x (inclusive)
    ],
)
def test_local_comparables_bands(assessed, points):
    parcel = make_inputs()["parcel"] | {"assessed_value": assessed}
    # Territory far above subject so percentile contributes 0.
    result = score(parcel=parcel, territory_assessed_values=[2000000] * 11)
    assert evidence_points(result, "capacity_local_relative_value") == points
    assert result["categories"]["capacity"] == (points or 0)


def test_nine_comparables_neutral_with_gap():
    comps = make_inputs()["local_comparables"][:9]
    parcel = make_inputs()["parcel"] | {"assessed_value": 750000}
    result = score(parcel=parcel, local_comparables=comps, territory_assessed_values=[2000000] * 11)
    assert evidence_points(result, "capacity_local_relative_value") is None
    assert "local_comparables_insufficient" in gap_types(result)
    assert result["categories"]["capacity"] == 0


# --- R14: territory percentile bands, midrank ties, half-open at 50/75/90 ---


@pytest.mark.parametrize(
    ("assessed", "territory", "points"),
    [
        # below 4, equal 1 of 10 -> midrank 45th -> 0
        (500, [100, 200, 300, 400, 500, 600, 700, 800, 900, 1000], None),
        # below 5, equal 1 of 10 -> midrank 55th -> 3
        (600, [100, 200, 300, 400, 500, 600, 700, 800, 900, 1000], 3),
        # tie block: below 5, equal 5 of 10 -> midrank exactly 75th -> 7
        (900, [100] * 5 + [900] * 5, 7),
        # tie pair at top: below 8, equal 2 of 10 -> midrank exactly 90th -> 10
        (42, [1] * 8 + [42, 42], 10),
        # top of 10 distinct: below 9, equal 1 -> 95th -> 10
        (1000, [100, 200, 300, 400, 500, 600, 700, 800, 900, 1000], 10),
    ],
)
def test_territory_percentile_midrank_bands(assessed, territory, points):
    parcel = make_inputs()["parcel"] | {"assessed_value": assessed}
    # Comparables far above subject so the local signal contributes 0.
    comps = [
        {"pin": f"0248_CMP_{i:02d}", "assessed_value": 10**9, "distance_m": 50 + i}
        for i in range(20)
    ]
    result = score(parcel=parcel, local_comparables=comps, territory_assessed_values=territory)
    assert evidence_points(result, "capacity_territory_percentile") == points
    assert result["categories"]["capacity"] == (points or 0)


# --- R17: roof-age bands from explicit completed installations only ---


def roof_permit(completion_date):
    return {
        "id": "P-ROOF-UT",
        "sources": ["SDL"],
        "municipal_id": "SDL-UT-001",
        "description": "roof replacement - remove and reshingle",
        "issue_date": completion_date,
        "disposition": "completed",
        "completion_date": completion_date,
    }


@pytest.mark.parametrize(
    ("completion", "points"),
    [
        ("2017-01-01", 0),  # 9y: known-young roof -> explicit fit_roof_age: 0
        ("2016-08-18", 4),  # exactly 10y (truncated)
        ("2011-08-18", 8),  # exactly 15y
        ("2006-08-18", 12),  # exactly 20y
    ],
)
def test_roof_age_bands(completion, points):
    result = score(permits=[roof_permit(completion)])
    assert evidence_points(result, "fit_roof_age") == points
    assert result["categories"]["fit"] == points


# --- R18: home-age bands, truncated years, missing year neutral ---


@pytest.mark.parametrize(
    ("year", "points"),
    [
        (1997, None),  # 29y -> 0, no entry
        (1996, 5),  # exactly 30
        (1976, 8),  # exactly 50
        (1951, 10),  # exactly 75 (fixture G-029/G-039 anchor)
    ],
)
def test_home_age_bands(year, points):
    parcel = make_inputs()["parcel"] | {"construction_year": year}
    result = score(parcel=parcel)
    assert evidence_points(result, "fit_home_age") == points
    assert result["categories"]["fit"] == (points or 0)


# --- R6: rental currency ---

VALID_SALE = {"source": "SR1A", "date": "2026-05-01", "price": 850000, "sales_code": ""}


@pytest.mark.parametrize(
    ("sales", "registrations", "modifier", "etype"),
    [
        # registration dated after the latest valid sale -> current, demotes mover
        ([VALID_SALE], [{"date": "2026-06-01"}], -25, "rental_registration"),
        # registration before the valid sale -> stale, neutral
        ([VALID_SALE], [{"date": "2025-01-01"}], 0, "rental_stale"),
        # no valid sale: within the provisional 24-month window -> current
        ([], [{"date": "2025-01-01"}], -25, "rental_registration"),
        # no valid sale: older than 24 months -> stale
        ([], [{"date": "2023-01-01"}], 0, "rental_stale"),
    ],
)
def test_rental_currency(sales, registrations, modifier, etype):
    result = score(
        sales=sales,
        rental_registry={"available": True, "registrations": registrations},
    )
    assert result["rental_modifier"] == modifier
    assert evidence_points(result, etype) == (modifier or 0)


def test_rental_registry_missing_is_gap_not_penalty():
    result = score(rental_registry={"available": False, "registrations": []})
    assert result["rental_modifier"] == 0
    assert "rental_data_missing" in gap_types(result)


# --- R37: confidence low iff >= 2 data gaps ---


@pytest.mark.parametrize(
    ("overrides", "n_gaps", "confidence"),
    [
        ({}, 0, "normal"),
        ({"rental_registry": {"available": False, "registrations": []}}, 1, "normal"),
        (
            {
                "rental_registry": {"available": False, "registrations": []},
                "acs_block_group": {},
            },
            2,
            "low",
        ),
    ],
)
def test_confidence_gap_count(overrides, n_gaps, confidence):
    result = score(**overrides)
    assert len(result["data_gaps"]) == n_gaps
    assert result["confidence"] == confidence


# --- R25: owner/mailing identity fields never influence the score ---


def test_identity_fields_ignored_fixture_39_style():
    with_identity = make_inputs()
    with_identity["parcel"] = with_identity["parcel"] | {
        "owner_name": "JANE Q PUBLIC",
        "mailing_address": "PO BOX 7, RAMSEY NJ",
    }
    result_with = score_door_v2(V2Bundle.from_fixture({"inputs": with_identity}), AS_OF)
    result_without = score_door_v2(
        V2Bundle.from_fixture({"inputs": make_inputs()}), AS_OF
    )
    assert result_with == result_without
    # And no identity value leaks into the envelope anywhere.
    assert "JANE" not in json.dumps(result_with)
