"""R3.3 detection schema and the single bridge from vision into scoring.

STUB — written by the test-writer so the failing tests name missing symbols
rather than a missing module. Contains no implementation.

What `tests/test_vision_schema.py` pins:

- ``SIGNALS``            frozenset of the legal `signal` values:
                         ``{"pool", "solar"}`` plus ``f"condition_{grade}"``
                         for every grade in ``scoring.weights.CONDITION_ORDER``.
- ``Detection``          frozen dataclass with EXACTLY the R3.3 fields
                         ``{pams_pin, signal, present, confidence, image_ref,
                         capture_date}``; construction validates and raises
                         ``ValueError`` on a bad field.
- ``condition_declined(before, after) -> bool``
                         the R6 ordinal comparison, worst-first
                         ``CONDITION_ORDER``; True only on a >=1-step drop.
- ``CONFIDENCE_FLOOR``   the documented floor `to_score_vision` applies.
- ``to_score_vision(detections) -> dict``
                         the score engine's vision mapping
                         (``pool``, ``solar``, ``condition_2015``,
                         ``condition_2020``, optional ``imagery``).
"""

from __future__ import annotations
