"""House Score engine against the golden fixtures (T003).

`eval/golden/*.json` is the contract in full, so this module DISCOVERS the files
at import time and parametrizes over them — a 13th fixture is picked up with no
edit here, and no expected number is ever hand-copied. Fixtures without
`expect.score` (09 vision-eval, 11 deed-parse) are skipped; 11 is covered by
tests/test_normalize.py plus `test_end_to_end_yymmdd_deed_matches_iso_fixture`
below.

Pinned seam (the implementer must satisfy exactly):

    from houseaccount.scoring.engine import Permit, ScoreInput, ScoreResult, score_door

    Permit(permit_date: date | None = None,
           permit_type: str = "",
           contractor: str | None = None)

    ScoreInput(as_of: date,                       # required
               territory_median_value: float,     # required
               acs_dual_income_threshold: float,  # required
               pams_pin: str = "",
               deed_date: date | None = None,     # already normalised (T002)
               sale_price: float = 0.0,
               sales_code: str = "",
               yr_constr: int = 0,
               net_value: float = 0.0,
               calc_acre: float = 0.0,
               permits: Sequence[Permit] = (),
               dual_income_pct: float | None = None,
               median_hh_income: float | None = None,
               rental_registration_match: bool = False,
               vision: Mapping[str, Any] = {})

    ScoreInput.from_fixture(given) -> ScoreInput
        Maps a fixture's `given` dict onto the fields above. DEED_DATE goes
        through `normalize.parse_deed_date`, so ISO ("2026-07-12") and raw
        MOD-IV YYMMDD ("260712") both work — that is what makes the end-to-end
        test possible, and it is the entry point the pipeline uses too.

    score_door(ScoreInput) -> ScoreResult
        .score       int, clamped 0..100
        .confidence  "normal" | "low"   (binary, R6.1)
        .evidence    sequence of EvidenceItem
        .groups      mapping mover/hires_out/capacity/need/modifier -> int
        .raw_total   int, the UNCLAMPED sum of .groups

Evidence *assertions declared inside the fixtures* live in tests/test_evidence.py.
"""

import copy
import json
import subprocess
from datetime import date, timedelta
from pathlib import Path

import pytest

from houseaccount.scoring.engine import Permit, ScoreInput, score_door
from houseaccount.scoring.weights import WEIGHTS

REPO = Path(__file__).resolve().parents[1]
GOLDEN = REPO / "eval" / "golden"

GROUP_NAMES = {"mover", "hires_out", "capacity", "need", "modifier"}


def load_scored_fixtures():
    """Every golden fixture that pins a score. Skips 09 and 11 by shape, not name."""
    out = []
    for path in sorted(GOLDEN.glob("*.json")):
        fixture = json.loads(path.read_text(encoding="utf-8"))
        if "score" in fixture.get("expect", {}):
            out.append(fixture)
    return out


SCORED = load_scored_fixtures()
IDS = [f["name"] for f in SCORED]

assert len(SCORED) >= 10, f"fixture discovery found only {len(SCORED)} scored fixtures"

COMPARATIVE = [f for f in SCORED if "baseline_score" in f.get("expect", {}).get("comparative", {})]
COMPARATIVE_IDS = [f["name"] for f in COMPARATIVE]

assert COMPARATIVE, "no comparative fixtures discovered"


def by_name(name):
    return next(f for f in SCORED if f["name"] == name)


def score_given(given):
    return score_door(ScoreInput.from_fixture(given))


def baseline_given(given, description, overrides=None):
    """The fixture's stated baseline mutation, applied to a copy of `given`.

    `overrides` is the structured `comparative.baseline_parcel` block — a
    comparative that changes a parcel field names the field rather than hiding
    it in prose this function would have to sniff for.
    """
    mutated = copy.deepcopy(given)
    if "vision={}" in description:
        mutated["vision"] = {}
    if "rental_registration_match=false" in description:
        mutated["rental_registration_match"] = False
    if overrides:
        mutated["parcel"] = {**mutated.get("parcel", {}), **overrides}
    return mutated


# --- the fixtures themselves ------------------------------------------------


@pytest.mark.parametrize("fixture", SCORED, ids=IDS)
def test_fixture_score_reproduces_exactly(fixture):
    assert score_given(fixture["given"]).score == fixture["expect"]["score"]


@pytest.mark.parametrize("fixture", SCORED, ids=IDS)
def test_fixture_confidence(fixture):
    # Complete-data fixtures are "normal"; 06 declares "low". Binary, R6.1.
    expected = fixture["expect"].get("confidence", "normal")
    assert score_given(fixture["given"]).confidence == expected


@pytest.mark.parametrize("fixture", SCORED, ids=IDS)
def test_result_shape_and_clamp_relationship(fixture):
    result = score_given(fixture["given"])
    assert set(result.groups) == GROUP_NAMES
    assert all(isinstance(v, int) for v in result.groups.values())
    assert result.raw_total == sum(result.groups.values())
    assert result.score == max(0, min(100, result.raw_total))
    assert 0 <= result.score <= 100
    assert result.confidence in {"normal", "low"}


@pytest.mark.parametrize("fixture", COMPARATIVE, ids=COMPARATIVE_IDS)
def test_fixture_comparative_baseline(fixture):
    comparative = fixture["expect"]["comparative"]
    result = score_given(fixture["given"])
    baseline = score_given(
        baseline_given(
            fixture["given"], comparative["baseline"], comparative.get("baseline_parcel")
        )
    )

    assert baseline.score == comparative["baseline_score"]
    # e.g. "score - baseline_score == 10" / "score == baseline_score - 15"
    assert eval(  # noqa: S307 - fixture-authored arithmetic, no builtins in scope
        comparative["assertion"],
        {"__builtins__": {}},
        {"score": result.score, "baseline_score": baseline.score},
    )


def test_high_clamp_is_a_real_clamp():
    fixture = by_name("new_mover_high_score")
    result = score_given(fixture["given"])
    assert result.raw_total > 100
    assert result.score == 100


@pytest.mark.parametrize("name", ["nominal_sale_not_mover", "sales_code_only_nominal"])
def test_non_arms_length_zeroes_the_mover_group(name):
    # 03 isolates the SALE_PRICE branch, 12 the SALES_CODE branch of the OR-rule.
    assert score_given(by_name(name)["given"]).groups["mover"] == 0


# --- synthetic doors: the rules the fixtures only sample --------------------

AS_OF = date(2026, 8, 1)


def make_input(**overrides):
    params = dict(
        as_of=AS_OF,
        territory_median_value=500_000,
        acs_dual_income_threshold=0.35,
    )
    params.update(overrides)
    return ScoreInput(**params)


def permit(days_ago, contractor="ABC Roofing LLC", permit_type="roofing"):
    return Permit(
        permit_date=AS_OF - timedelta(days=days_ago),
        permit_type=permit_type,
        contractor=contractor,
    )


@pytest.mark.parametrize(
    "days, expected",
    [(0, 100), (1, 100), (30, 100), (31, 85), (60, 85), (61, 70), (90, 70), (91, 0), (400, 0)],
)
def test_mover_bands(days, expected):
    result = score_door(
        make_input(deed_date=AS_OF - timedelta(days=days), sale_price=600_000)
    )
    assert result.groups["mover"] == expected


@pytest.mark.parametrize(
    "sale_price, sales_code, expected",
    [
        (600_000, "", 100),
        (100, "", 0),  # <= $100 is nominal
        (1, "", 0),
        (600_000, "26", 0),  # non-empty MOD-IV sales code
        (1, "26", 0),
        (101, "", 100),  # just above the nominal threshold
    ],
)
def test_non_arms_length_or_rule(sale_price, sales_code, expected):
    result = score_door(
        make_input(
            deed_date=AS_OF - timedelta(days=10),
            sale_price=sale_price,
            sales_code=sales_code,
        )
    )
    assert result.groups["mover"] == expected


@pytest.mark.parametrize(
    "permits, expected, why",
    [
        ((), 0, "no permits"),
        ((permit(10),), 20, "one permit"),
        ((permit(10), permit(20, "Bergen HVAC Inc")), 60, "2 distinct: 40 + churn 20"),
        (
            (permit(10), permit(20, "Bergen HVAC Inc"), permit(30, "Sparks Electric")),
            60,
            "permit points cap at 40, churn 20",
        ),
        (
            tuple(permit(d) for d in (10, 20, 30, 40, 50)),
            40,
            "same contractor 5x: cap 40, no churn",
        ),
        (
            (permit(10), permit(20), permit(30, "Bergen HVAC Inc")),
            40,
            "a contractor repeats -> no churn",
        ),
        (
            (permit(10, contractor=None), permit(20)),
            40,
            "only one named contractor -> no churn, both still earn points",
        ),
        (
            (permit(10, contractor=None), permit(20), permit(30, "Bergen HVAC Inc")),
            60,
            "contractor-less permit excluded from the churn test, not from points",
        ),
        ((permit(730),), 20, "730 days is inside the rolling window"),
        ((permit(731),), 0, "731 days is outside it"),
        (
            (permit(10), permit(731, "Bergen HVAC Inc")),
            20,
            "an out-of-window permit earns nothing and cannot create churn",
        ),
    ],
)
def test_hires_out_group(permits, expected, why):
    assert score_door(make_input(permits=permits)).groups["hires_out"] == expected, why


@pytest.mark.parametrize(
    "net_value, dual_income_pct, expected",
    [
        (0, None, 0),
        (499_999, None, 0),
        (500_000, None, 15),
        (749_999, None, 15),
        (750_000, None, 25),
        (500_000, 0.35, 20),  # threshold is inclusive
        (500_000, 0.34, 15),
        (750_000, 0.41, 30),
        (100_000, 0.41, 5),  # the ACS prior stands alone
    ],
)
def test_capacity_group(net_value, dual_income_pct, expected):
    result = score_door(make_input(net_value=net_value, dual_income_pct=dual_income_pct))
    assert result.groups["capacity"] == expected


@pytest.mark.parametrize(
    "kwargs, expected, why",
    [
        (dict(yr_constr=1996), 8, "exactly 30 years old"),
        (dict(yr_constr=1997), 0, "29 years old"),
        (dict(yr_constr=2001), 0, "25 years old"),
        (dict(yr_constr=0), 0, "unknown year built: age component skipped"),
        (dict(vision={"pool": True}), 8, "pool"),
        (dict(vision={"pool": False}), 0, "no pool"),
        (dict(calc_acre=0.5), 4, "lot at the 0.5 acre threshold"),
        (dict(calc_acre=0.49), 0, "lot below threshold"),
        (
            dict(vision={"condition_2015": "good", "condition_2020": "fair"}),
            6,
            "one-step decline",
        ),
        (
            dict(vision={"condition_2015": "excellent", "condition_2020": "poor"}),
            6,
            "three-step decline still scores once",
        ),
        (
            dict(vision={"condition_2015": "good", "condition_2020": "good"}),
            0,
            "no change",
        ),
        (
            dict(vision={"condition_2015": "fair", "condition_2020": "good"}),
            0,
            "improvement is not decline",
        ),
        (dict(vision={"condition_2020": "poor"}), 0, "one vintage only -> no trajectory"),
        (
            dict(yr_constr=1968, vision={"condition_2015": "good", "condition_2020": "fair"}),
            18,
            "age 8 + decline 6 + deferred-maintenance combo 4",
        ),
        (
            dict(
                yr_constr=1968,
                permits=(permit(10),),
                vision={"condition_2015": "good", "condition_2020": "fair"},
            ),
            14,
            "a permit in the window kills the deferred-maintenance combo",
        ),
        (
            dict(yr_constr=0, calc_acre=0.6, vision={"pool": True, "condition_2015": "good", "condition_2020": "fair"}),
            18,
            "YR_CONSTR=0: pool/lot/decline still score, age and combo do not",
        ),
    ],
)
def test_need_group(kwargs, expected, why):
    assert score_door(make_input(**kwargs)).groups["need"] == expected, why


def test_absentee_modifier_applies_to_a_fresh_mover():
    result = score_door(
        make_input(
            deed_date=AS_OF - timedelta(days=5),
            sale_price=600_000,
            rental_registration_match=True,
        )
    )
    assert result.groups["modifier"] == -15
    assert result.groups["mover"] == 100
    assert result.score == 85


def test_low_clamp_floors_a_negative_raw_total_at_zero():
    result = score_door(make_input(rental_registration_match=True))
    assert result.raw_total == -15
    assert result.score == 0


# --- degraded data (R6.1) ---------------------------------------------------


def test_null_deed_skips_mover_and_lowers_confidence():
    result = score_door(make_input(deed_date=None, net_value=510_000))
    assert result.groups["mover"] == 0
    assert result.confidence == "low"


def test_unparseable_deed_string_is_treated_as_null():
    given = copy.deepcopy(by_name("mover_30d_flat_with_absentee")["given"])
    given["parcel"]["DEED_DATE"] = "not-a-date"
    result = score_given(given)
    assert result.groups["mover"] == 0
    assert result.confidence == "low"


def test_missing_vision_dict_is_not_a_crash():
    result = score_door(make_input(yr_constr=1968, vision={}))
    assert result.groups["need"] == 8


def test_complete_data_is_normal_confidence():
    result = score_door(
        make_input(deed_date=date(2026, 7, 12), sale_price=600_000, yr_constr=1995, net_value=510_000)
    )
    assert result.confidence == "normal"


# --- end-to-end with the T002 normalizer (fixture 11's expect.end_to_end) ---


def test_end_to_end_yymmdd_deed_matches_iso_fixture():
    """Fixture 10 with its ISO deed replaced by the raw MOD-IV YYMMDD string."""
    fixture = by_name("mover_30d_flat_with_absentee")
    iso = score_given(fixture["given"])

    raw_given = copy.deepcopy(fixture["given"])
    assert raw_given["parcel"]["DEED_DATE"] == "2026-07-12"
    raw_given["parcel"]["DEED_DATE"] = "260712"
    yymmdd = score_given(raw_given)

    assert yymmdd.groups["mover"] == 100
    assert yymmdd.score == iso.score == fixture["expect"]["score"]
    assert yymmdd.groups == iso.groups
    assert yymmdd.confidence == iso.confidence


# --- weights table (T014 imports it; the engine must not re-type numbers) ---


@pytest.mark.parametrize(
    "key, value",
    [
        ("mover_30d", 100),
        ("mover_60d", 85),
        ("mover_90d", 70),
        ("permit_each", 20),
        ("permit_cap", 40),
        ("provider_churn", 20),
        ("capacity_median", 15),
        ("capacity_1_5x", 25),
        ("capacity_acs_prior", 5),
        ("need_home_age", 8),
        ("need_pool", 8),
        ("need_lot", 4),
        ("need_condition_decline", 6),
        ("need_deferred_maintenance", 4),
        ("absentee_modifier", -15),
    ],
)
def test_weights_table_publishes_every_weight(key, value):
    assert WEIGHTS[key] == value


def test_engine_group_totals_come_from_the_weights_table():
    fixture = by_name("hires_out_permit_history")
    result = score_given(fixture["given"])
    assert result.groups["hires_out"] == WEIGHTS["permit_cap"] + WEIGHTS["provider_churn"]
    assert result.groups["capacity"] == WEIGHTS["capacity_median"] + WEIGHTS["capacity_acs_prior"]

    absentee = score_given(by_name("absentee_modifier")["given"])
    assert absentee.groups["modifier"] == WEIGHTS["absentee_modifier"]


# --- regression guard (PASSES ALREADY — the fixtures reproduce today) -------


def test_verify_claims_still_exits_zero():
    """Not a red test: `eval/verify_claims.py` is the stdlib-only reference that
    proves the fixtures are internally consistent. It must keep exiting 0 after
    this ticket, so it is pinned here."""
    proc = subprocess.run(
        ["python3", str(REPO / "eval" / "verify_claims.py")],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
