"""R3.3 detection schema and the vision -> score bridge (T007).

Two contracts live here.

1. `Detection` is the ONLY shape the rest of the system sees. R3.3 fixes it at
   exactly six fields — `{pams_pin, signal, present, confidence, image_ref,
   capture_date}` — and validation is at construction, so a hallucinated or
   malformed model row cannot travel any further than the parse site.
   Condition has nowhere else to live in a six-field schema, so the R6 ordinal
   rides in `signal` as `condition_<grade>`; the vintage rides in `capture_date`.

2. `to_score_vision` is the only path from vision into scoring. The engine reads
   `pool` / `condition_2015` / `condition_2020` off a plain mapping and reaches
   for an optional `vision["imagery"][signal]` attachment (see
   `scoring.evidence.imagery_for`), so this is where the confidence floor is
   enforced — below it, a detection never becomes a point.

The floor's *value* is the implementer's call and is asserted through the
module constant, not a literal: what the criteria pin is that a floor exists,
is documented, and actually drops things.
"""

import dataclasses

import pytest

from houseaccount.scoring.evidence import IMAGERY_FIELDS, imagery_for
# CONDITION_ORDER is the vision schema's own vocabulary once V1 weights.py is
# deleted (ticket 103); it must be importable from the schema module itself.
from houseaccount.vision.schema import CONDITION_ORDER
from houseaccount.vision.schema import (
    CONFIDENCE_FLOOR,
    SIGNALS,
    Detection,
    condition_declined,
    to_score_vision,
)
from houseaccount.vision.tiles import tile_url

PIN = "0248_00101_00003"
REF_2020 = "tiles/2020/0248_00101_00003.png"
REF_2015 = "tiles/2015/0248_00101_00003.png"
DATE_2020 = "2020-06-15"
DATE_2015 = "2015-06-15"

#: A Ramsey parcel centroid (lon, lat), used where a real tile URL is needed.
CENTROID = (-74.1560, 41.0447)

#: The six R3.3 fields, and nothing else.
R33_FIELDS = {"pams_pin", "signal", "present", "confidence", "image_ref", "capture_date"}


def detection(**overrides):
    """A valid pool detection unless a test overrides one field."""
    payload = {
        "pams_pin": PIN,
        "signal": "pool",
        "present": True,
        "confidence": 0.92,
        "image_ref": REF_2020,
        "capture_date": DATE_2020,
    }
    payload.update(overrides)
    return Detection(**payload)


def condition(grade, *, year, confidence=0.85):
    return Detection(
        pams_pin=PIN,
        signal=f"condition_{grade}",
        present=True,
        confidence=confidence,
        image_ref=REF_2020 if year == 2020 else REF_2015,
        capture_date=DATE_2020 if year == 2020 else DATE_2015,
    )


# --- the schema is exactly R3.3 ---------------------------------------------


def test_detection_carries_exactly_the_r33_fields():
    assert {f.name for f in dataclasses.fields(Detection)} == R33_FIELDS


def test_detection_is_frozen():
    """Detections are passed around and cached; nothing may mutate one."""
    with pytest.raises(dataclasses.FrozenInstanceError):
        detection().confidence = 0.1


def test_a_valid_detection_keeps_its_values():
    item = detection()
    assert (item.pams_pin, item.signal, item.present) == (PIN, "pool", True)
    assert (item.confidence, item.image_ref, item.capture_date) == (0.92, REF_2020, DATE_2020)


def test_detections_compare_by_value():
    """Equality is what the cache round-trip assertions rest on."""
    assert detection() == detection()
    assert detection() != detection(confidence=0.5)


# --- the signal vocabulary --------------------------------------------------


def test_signal_vocabulary_is_the_r4_signals_plus_the_r6_condition_grades():
    assert SIGNALS == {"pool", "solar"} | {f"condition_{grade}" for grade in CONDITION_ORDER}


@pytest.mark.parametrize("signal", sorted({"pool", "solar"} | {f"condition_{g}" for g in CONDITION_ORDER}))
def test_every_declared_signal_is_accepted(signal):
    assert detection(signal=signal).signal == signal


@pytest.mark.parametrize(
    "signal",
    [
        "swimming_pool",  # a plausible synonym is still not the vocabulary
        "trampoline",  # a signal we never asked for
        "condition",  # the grade is part of the signal, not implied
        "condition_pristine",  # not on the R6 ordinal scale
        "POOL",  # case is part of the contract
        "",
        None,
    ],
)
def test_unknown_signal_is_rejected(signal):
    with pytest.raises(ValueError):
        detection(signal=signal)


# --- field validation -------------------------------------------------------


@pytest.mark.parametrize("confidence", [0.0, 0.5, 1.0])
def test_confidence_bounds_are_inclusive(confidence):
    assert detection(confidence=confidence).confidence == confidence


@pytest.mark.parametrize("confidence", [1.5, -0.1, 100, -1, float("nan"), None, "0.9"])
def test_confidence_outside_zero_to_one_is_rejected(confidence):
    with pytest.raises(ValueError):
        detection(confidence=confidence)


@pytest.mark.parametrize("pams_pin", ["", "   ", None])
def test_missing_pams_pin_is_rejected(pams_pin):
    """A detection that cannot be attached to a parcel is not a detection."""
    with pytest.raises(ValueError):
        detection(pams_pin=pams_pin)


@pytest.mark.parametrize("present", [1, 0, "true", "yes", None, 1.0])
def test_non_bool_present_is_rejected(present):
    """`present` is the claim itself — a truthy 1 must not stand in for it."""
    with pytest.raises(ValueError):
        detection(present=present)


@pytest.mark.parametrize("field", ["image_ref", "capture_date"])
@pytest.mark.parametrize("value", ["", None])
def test_provenance_fields_are_required(field, value):
    """image_ref + capture_date are what make a vision claim re-openable."""
    with pytest.raises(ValueError):
        detection(**{field: value})


# --- condition is the R6 ordinal --------------------------------------------


def test_condition_scale_is_the_engines_scale_not_a_copy():
    assert CONDITION_ORDER == ("poor", "fair", "good", "excellent")


@pytest.mark.parametrize(
    "before,after,declined",
    [
        # A drop of one step or more is a decline.
        ("excellent", "good", True),
        ("excellent", "fair", True),
        ("excellent", "poor", True),
        ("good", "fair", True),
        ("good", "poor", True),
        ("fair", "poor", True),
        # Flat is not a decline, however bad the level.
        ("excellent", "excellent", False),
        ("good", "good", False),
        ("fair", "fair", False),
        ("poor", "poor", False),
        # Improvement is not a decline.
        ("poor", "fair", False),
        ("fair", "good", False),
        ("good", "excellent", False),
        ("poor", "excellent", False),
    ],
)
def test_condition_decline_needs_at_least_a_one_step_drop(before, after, declined):
    assert condition_declined(before, after) is declined


@pytest.mark.parametrize(
    "before,after",
    [("good", None), (None, "fair"), (None, None), ("good", "unreadable"), ("", "poor")],
)
def test_an_unreadable_vintage_is_not_a_decline(before, after):
    """One readable vintage is a condition, never a trajectory."""
    assert condition_declined(before, after) is False


# --- to_score_vision: the only path into scoring -----------------------------


def test_empty_detections_produce_a_valid_empty_vision_dict():
    vision = to_score_vision([])
    assert vision["pool"] is False
    assert vision["solar"] is False
    assert vision["condition_2015"] is None
    assert vision["condition_2020"] is None


@pytest.mark.parametrize(
    "detections",
    [
        [],
        [detection()],
        [detection(signal="solar")],
        [condition("good", year=2015), condition("fair", year=2020)],
    ],
)
def test_vision_dict_carries_only_keys_the_engine_understands(detections):
    """No raw model field may leak into the score engine's input."""
    vision = to_score_vision(detections)
    assert set(vision) >= {"pool", "solar", "condition_2015", "condition_2020"}
    assert set(vision) <= {"pool", "solar", "condition_2015", "condition_2020", "imagery"}


@pytest.mark.parametrize("signal", ["pool", "solar"])
@pytest.mark.parametrize("present", [True, False])
def test_boolean_signals_pass_through_their_claim(signal, present):
    vision = to_score_vision([detection(signal=signal, present=present)])
    assert vision[signal] is present


def test_condition_vintages_land_on_the_year_they_were_captured():
    vision = to_score_vision([condition("good", year=2015), condition("fair", year=2020)])
    assert vision["condition_2015"] == "good"
    assert vision["condition_2020"] == "fair"


def test_a_single_condition_vintage_leaves_the_other_unknown():
    vision = to_score_vision([condition("fair", year=2020)])
    assert vision["condition_2020"] == "fair"
    assert vision["condition_2015"] is None


# --- the documented confidence floor ----------------------------------------


def test_the_confidence_floor_is_documented_and_meaningful():
    """A floor of 0 would not be a floor; a floor near 1 would drop everything."""
    assert 0.1 <= CONFIDENCE_FLOOR <= 0.95


def test_a_detection_at_the_floor_survives():
    assert to_score_vision([detection(confidence=CONFIDENCE_FLOOR)])["pool"] is True


def test_a_detection_below_the_floor_is_dropped():
    below = round(CONFIDENCE_FLOOR - 0.05, 6)
    assert to_score_vision([detection(confidence=below)])["pool"] is False


def test_a_low_confidence_condition_read_is_dropped_not_guessed():
    below = round(CONFIDENCE_FLOOR - 0.05, 6)
    vision = to_score_vision(
        [condition("good", year=2015), condition("fair", year=2020, confidence=below)]
    )
    assert vision["condition_2015"] == "good"
    assert vision["condition_2020"] is None


def test_a_below_floor_detection_cannot_reach_the_imagery_attachment():
    below = round(CONFIDENCE_FLOOR - 0.05, 6)
    vision = to_score_vision([detection(confidence=below)])
    assert imagery_for(vision, "pool") is None


# --- the imagery attachment the evidence layer re-opens ----------------------


def pool_detection_on_a_real_tile():
    return detection(image_ref=tile_url(CENTROID, 2020))


def test_imagery_attachment_is_complete_enough_to_reopen_the_frame():
    vision = to_score_vision([pool_detection_on_a_real_tile()])
    attachment = imagery_for(vision, "pool")
    assert attachment is not None, "an incomplete attachment reads auditable without being so"
    assert set(IMAGERY_FIELDS) <= set(attachment)


def test_imagery_attachment_points_at_the_detections_own_frame():
    item = pool_detection_on_a_real_tile()
    attachment = imagery_for(to_score_vision([item]), "pool")
    assert attachment["image_url"] == item.image_ref
    assert attachment["model_confidence"] == item.confidence
    assert attachment["capture_date"] == item.capture_date


def test_imagery_attachment_bbox_is_four_numbers():
    attachment = imagery_for(to_score_vision([pool_detection_on_a_real_tile()]), "pool")
    bbox = list(attachment["bbox"])
    assert len(bbox) == 4
    assert all(isinstance(value, (int, float)) for value in bbox)
    minx, miny, maxx, maxy = bbox
    assert minx < maxx and miny < maxy


def test_condition_attaches_the_newer_vintage_the_claim_is_made_from():
    """The engine's condition sentence describes the 2020 pass, so that is the
    frame a rep must be able to open."""
    before = condition("good", year=2015)
    after = condition("fair", year=2020)
    after = dataclasses.replace(after, image_ref=tile_url(CENTROID, 2020))
    before = dataclasses.replace(before, image_ref=tile_url(CENTROID, 2015))

    attachment = imagery_for(to_score_vision([before, after]), "condition")
    assert attachment is not None
    assert attachment["capture_date"] == after.capture_date
    assert attachment["image_url"] == after.image_ref
