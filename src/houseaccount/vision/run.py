"""The vision stage entry point — including the no-API-key declination.

STUB — written by the test-writer so the failing tests name missing symbols
rather than a missing module. Contains no implementation.

What `tests/test_vision_provider.py` pins:

- ``VisionRun``          result shell: ``detections`` (sequence of Detection),
                         ``declined`` (bool), ``reason`` (str),
                         ``parse_failures`` (sequence).
- ``run_vision(tiles, *, config, ledger=None, cache=None, client=None,
   batch_size=...) -> VisionRun``
                         With no ``config.anthropic_api_key`` the stage returns
                         an empty-but-valid VisionRun, logs a WARNING
                         declination, spends nothing, and never constructs a
                         client. Every door must still score off
                         ``to_score_vision(result.detections)``.
"""

from __future__ import annotations
