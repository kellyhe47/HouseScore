"""Per-door evidence (R7, T003) — every score has to be explainable.

Two halves:

1. The evidence assertions DECLARED INSIDE the golden fixtures
   (`evidence_must_include[].type`, `evidence_must_exclude[].type`,
   `must_not_contain_evidence_types[]`, `must_not_contain[]`) — read from disk
   and parametrized, so a new fixture's declarations are enforced for free.
2. The item contract itself (R7.1): `{type, points: int (signed), sentence: str,
   source: str, retrieved: date}` plus an optional `imagery` attachment
   `{image_url, bbox, model_confidence, capture_date}` for vision-derived items.

Items are only ever observed as `score_door` produced them — nothing here
constructs an EvidenceItem, because the contract under test is what the engine
emits, not what the dataclass will accept.

Wording is deliberately NOT asserted: sentences are generated prose. The one
substring the criteria do pin is "block group" in the ACS item (R6.2).
"""

import json
from datetime import date
from pathlib import Path

import pytest

from houseaccount.scoring.engine import ScoreInput, score_door

GOLDEN = Path(__file__).resolve().parents[1] / "eval" / "golden"

#: Phrasings that would turn the ACS block-group prior into a household claim.
HOUSEHOLD_CLAIMS = ("this household", "this home", "this house", "the household", "occupant")


def load_scored_fixtures():
    out = []
    for path in sorted(GOLDEN.glob("*.json")):
        fixture = json.loads(path.read_text(encoding="utf-8"))
        if "score" in fixture.get("expect", {}):
            out.append(fixture)
    return out


SCORED = load_scored_fixtures()
IDS = [f["name"] for f in SCORED]

assert len(SCORED) >= 10, f"fixture discovery found only {len(SCORED)} scored fixtures"


def by_name(name):
    return next(f for f in SCORED if f["name"] == name)


def score_given(given):
    return score_door(ScoreInput.from_fixture(given))


def evidence_of(name):
    return list(score_given(by_name(name)["given"]).evidence)


def types_of(evidence):
    return {item.type for item in evidence}


def one_of_type(evidence, type_):
    matches = [item for item in evidence if item.type == type_]
    assert len(matches) == 1, f"expected exactly one {type_!r}, got {len(matches)}"
    return matches[0]


# --- what the fixtures themselves declare -----------------------------------


@pytest.mark.parametrize("fixture", SCORED, ids=IDS)
def test_declared_evidence_is_included(fixture):
    required = [e["type"] for e in fixture["expect"].get("evidence_must_include", [])]
    if not required:
        pytest.skip("fixture declares no required evidence")
    present = types_of(score_given(fixture["given"]).evidence)
    assert set(required) <= present, f"missing {sorted(set(required) - present)}"


@pytest.mark.parametrize("fixture", SCORED, ids=IDS)
def test_declared_evidence_is_excluded(fixture):
    expect = fixture["expect"]
    forbidden = [e["type"] for e in expect.get("evidence_must_exclude", [])]
    forbidden += expect.get("must_not_contain_evidence_types", [])
    if not forbidden:
        pytest.skip("fixture declares no forbidden evidence")
    present = types_of(score_given(fixture["given"]).evidence)
    assert not (set(forbidden) & present), f"found {sorted(set(forbidden) & present)}"


@pytest.mark.parametrize("fixture", SCORED, ids=IDS)
def test_declared_forbidden_text_never_appears(fixture):
    # Fixture 08's `must_not_contain` is prose, not types: Daniel's Law (R11.1).
    forbidden = fixture["expect"].get("must_not_contain", [])
    if not forbidden:
        pytest.skip("fixture declares no forbidden text")
    blob = " ".join(
        f"{item.type} {item.sentence} {item.source}"
        for item in score_given(fixture["given"]).evidence
    ).lower()
    for phrase in forbidden:
        assert phrase.lower() not in blob


# --- the item contract (R7.1) -----------------------------------------------


@pytest.mark.parametrize("fixture", SCORED, ids=IDS)
def test_every_item_has_the_R7_1_shape(fixture):
    evidence = score_given(fixture["given"]).evidence
    assert evidence, "a scored door with no evidence is not explainable"
    for item in evidence:
        assert isinstance(item.type, str) and item.type
        assert isinstance(item.points, int) and not isinstance(item.points, bool)
        assert isinstance(item.sentence, str) and item.sentence.strip()
        assert isinstance(item.source, str) and item.source.strip()
        assert isinstance(item.retrieved, date)


@pytest.mark.parametrize("fixture", SCORED, ids=IDS)
def test_imagery_attachment_is_optional_but_well_formed(fixture):
    for item in score_given(fixture["given"]).evidence:
        imagery = getattr(item, "imagery", None)
        if imagery is None:
            continue
        fields = imagery if isinstance(imagery, dict) else vars(imagery)
        assert {"image_url", "bbox", "model_confidence", "capture_date"} <= set(fields)


@pytest.mark.parametrize("fixture", SCORED, ids=IDS)
def test_signed_points_account_for_the_whole_raw_total(fixture):
    """The audit trail is complete: nothing scores without an evidence line."""
    result = score_given(fixture["given"])
    assert sum(item.points for item in result.evidence) == result.raw_total


def test_zero_point_context_evidence_is_legal():
    # R7.3: fixture 05's `tenure` explains a low score without moving it.
    assert one_of_type(evidence_of("diy_long_tenure_low_signal"), "tenure").points == 0


def test_absentee_item_carries_the_negative_points():
    assert one_of_type(evidence_of("absentee_modifier"), "absentee_likely").points == -15


@pytest.mark.parametrize("name", ["nominal_sale_not_mover", "sales_code_only_nominal"])
def test_non_arms_length_item_is_zero_point_context(name):
    assert one_of_type(evidence_of(name), "non_arms_length_transfer").points == 0


def test_data_gap_item_explains_low_confidence():
    result = score_given(by_name("missing_fields_degrade")["given"])
    assert result.confidence == "low"
    assert one_of_type(result.evidence, "data_gap").points == 0


# --- ACS is neighbourhood context, never a household claim (R6.2) -----------


def test_acs_evidence_is_phrased_as_block_group_context():
    # Fixture 04 is the only shape where the +5 ACS prior fires; the prior is the
    # only 5-point weight, so it identifies the item without pinning its type.
    evidence = evidence_of("hires_out_permit_history")
    acs = [item for item in evidence if item.points == 5]
    assert len(acs) == 1, "expected exactly one +5 ACS block-group item"

    sentence = acs[0].sentence.lower()
    assert "block group" in sentence
    for claim in HOUSEHOLD_CLAIMS:
        assert claim not in sentence
