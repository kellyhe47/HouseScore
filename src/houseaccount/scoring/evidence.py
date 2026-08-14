"""Per-door evidence items — why a door scored what it scored (R7.1).

STUB — shape pinned by tests/test_evidence.py, which only ever observes items
produced by `score_door`, never constructs one directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any


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
