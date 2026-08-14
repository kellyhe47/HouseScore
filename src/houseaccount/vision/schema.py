"""R3.3 detection schema and the single bridge from vision into scoring.

Two contracts live here, and both exist to keep raw model output out of the
score engine.

`Detection` is the only shape the rest of the system sees. R3.3 fixes it at
exactly six fields, and validation happens at construction, so a hallucinated
signal or an out-of-range confidence dies at the parse site rather than three
layers down inside a scored door. Condition has nowhere else to live in a
six-field schema, so the R6 ordinal rides in `signal` as `condition_<grade>`
and the vintage rides in `capture_date` — the alternative was a seventh field
that only one signal would ever populate.

`to_score_vision` is the only path from vision into scoring. The engine reads
`pool` / `solar` / `condition_2015` / `condition_2020` off a plain mapping and
reaches for an optional `vision["imagery"][signal]` attachment, so this is
where the confidence floor is enforced: below it a detection never becomes a
point, and never becomes an evidence line claiming it was one.

`CONFIDENCE_FLOOR` is a judgement call, documented here rather than buried as a
literal. A model that is 60% sure it sees a pool is not evidence a rep can be
sent to a door on; a floor much higher would drop the genuinely ambiguous
above-ground pools this signal exists to find.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from houseaccount.scoring.weights import CONDITION_ORDER
from houseaccount.vision.tiles import bbox_for_ref

#: The legal `signal` vocabulary. The R4 booleans plus one entry per R6 grade —
#: derived from CONDITION_ORDER rather than restated, so the engine's ordinal
#: scale and the vision vocabulary cannot drift apart.
SIGNALS: frozenset[str] = frozenset(
    {"pool", "solar"} | {f"condition_{grade}" for grade in CONDITION_ORDER}
)

#: Below this, a detection is dropped rather than scored. See module docstring.
CONFIDENCE_FLOOR: float = 0.60

#: The condition signal prefix, once.
_CONDITION_PREFIX = "condition_"

#: Vintages the engine has a slot for. A condition read dated anything else has
#: nowhere to go, so it is dropped rather than silently rounded to a vintage.
_SCORED_VINTAGES: tuple[str, ...] = ("2015", "2020")


@dataclass(frozen=True)
class Detection:
    """One model claim about one parcel in one frame (R3.3).

    Frozen because detections are cached, compared and passed across the
    provider seam: a mutated confidence would let a cache round-trip disagree
    with the run that produced it.
    """

    pams_pin: str
    signal: str
    present: bool
    confidence: float
    image_ref: str
    capture_date: str

    def __post_init__(self) -> None:
        _require_text("pams_pin", self.pams_pin)
        _require_text("image_ref", self.image_ref)
        _require_text("capture_date", self.capture_date)

        if self.signal not in SIGNALS:
            raise ValueError(
                f"unknown vision signal {self.signal!r}; expected one of {sorted(SIGNALS)}"
            )

        # `present` is the claim itself. A truthy 1 or "yes" would score
        # identically here and mean something different at the parse site, so
        # the type is part of the contract.
        if type(self.present) is not bool:
            raise ValueError(f"present must be a bool, got {self.present!r}")

        if isinstance(self.confidence, bool) or not isinstance(self.confidence, (int, float)):
            raise ValueError(f"confidence must be a number, got {self.confidence!r}")
        if math.isnan(self.confidence) or not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be within [0, 1], got {self.confidence!r}")

    @property
    def condition_grade(self) -> str | None:
        """The R6 grade this detection carries, or None if it is not a condition."""
        if not self.signal.startswith(_CONDITION_PREFIX):
            return None
        return self.signal[len(_CONDITION_PREFIX) :]


def condition_declined(before: Any, after: Any) -> bool:
    """True when both vintages read and `after` is at least one step worse.

    One readable vintage is a condition, never a trajectory — and only a
    trajectory says the house is getting away from whoever maintains it. So an
    unreadable pass is False rather than an optimistic or pessimistic guess.
    """
    if before not in CONDITION_ORDER or after not in CONDITION_ORDER:
        return False
    return CONDITION_ORDER.index(after) < CONDITION_ORDER.index(before)


def to_score_vision(detections: Iterable[Detection]) -> dict[str, Any]:
    """Build the engine's `vision` mapping from R3.3 detections.

    Only the keys the engine understands come out. Sub-floor detections are
    dropped before anything is written, so a low-confidence read can neither
    score a point nor leave an imagery attachment implying it did.
    """
    vision: dict[str, Any] = {
        "pool": False,
        "solar": False,
        "condition_2015": None,
        "condition_2020": None,
    }
    imagery: dict[str, Mapping[str, Any]] = {}
    newest_condition: Detection | None = None

    for item in detections:
        if item.confidence < CONFIDENCE_FLOOR:
            continue

        grade = item.condition_grade
        if grade is None:
            vision[item.signal] = item.present
            if item.present:
                _attach(imagery, item.signal, item)
            continue

        # A condition read is a statement about the frame, so `present=False`
        # ("this is not a `fair` house") carries no grade we could record.
        if not item.present:
            continue
        key = f"condition_{item.capture_date[:4]}"
        if key not in vision:
            continue
        vision[key] = grade

        # The engine's condition sentence describes the newer pass, so that is
        # the frame a rep has to be able to open.
        if newest_condition is None or item.capture_date > newest_condition.capture_date:
            newest_condition = item

    if newest_condition is not None:
        _attach(imagery, "condition", newest_condition)

    vision["imagery"] = imagery
    return vision


# --- internals --------------------------------------------------------------


def _require_text(field: str, value: Any) -> None:
    """A detection that cannot be attached to a parcel and a frame is not one."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string, got {value!r}")


def _attach(imagery: dict[str, Mapping[str, Any]], key: str, item: Detection) -> None:
    """Record where a claim was made, or record nothing.

    `scoring.evidence.imagery_for` rejects a partial attachment, so a frame
    whose bbox cannot be recovered simply gets no attachment — an evidence line
    with no imagery is honest, one with half of it is not.
    """
    bbox = bbox_for_ref(item.image_ref)
    if bbox is None:
        return
    imagery[key] = {
        "image_url": item.image_ref,
        "bbox": bbox,
        "model_confidence": item.confidence,
        "capture_date": item.capture_date,
    }
