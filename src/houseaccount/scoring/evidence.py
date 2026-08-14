"""Per-door evidence items — why a door scored what it scored (R7.1).

A score with no audit trail is a number a rep cannot defend on a doorstep and a
number we cannot defend on the ethics page, so the engine's invariant is that
the signed `points` across these items sum to exactly the unclamped total.
That is why `points` is signed and why zero is legal (R7.3): context that
explains a score without moving it — a long tenure, a nominal transfer, a data
gap — still belongs in the trail.

`imagery` is attached only to vision-derived items, and carries enough to
re-open the exact frame a claim came from.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Mapping

#: What an imagery attachment must carry for a vision claim to be auditable.
IMAGERY_FIELDS = ("image_url", "bbox", "model_confidence", "capture_date")


@dataclass(frozen=True)
class EvidenceItem:
    """One explainable contribution to a score. `points` is signed and may be 0
    (R7.3: zero-point context evidence is legal). `imagery` is attached only for
    vision-derived items and carries {image_url, bbox, model_confidence,
    capture_date}."""

    type: str
    points: int
    sentence: str
    source: str
    retrieved: date
    imagery: Any | None = None


def imagery_for(vision: Mapping[str, Any], signal: str) -> Mapping[str, Any] | None:
    """The imagery attachment a vision payload carries for `signal`, if any.

    Returns None unless the payload holds a complete attachment: a partial one
    is worse than none, because it looks auditable without being re-openable.
    """
    attachments = vision.get("imagery")
    if not isinstance(attachments, Mapping):
        return None
    attachment = attachments.get(signal)
    if not isinstance(attachment, Mapping):
        return None
    if not set(IMAGERY_FIELDS) <= set(attachment):
        return None
    return attachment
