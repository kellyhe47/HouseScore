"""The vision provider seam: protocol, Claude implementation, cache wrapper.

STUB — written by the test-writer so the failing tests name missing symbols
rather than a missing module. Contains no implementation.

What `tests/test_vision_provider.py` pins:

- ``VisionProvider``     runtime-checkable protocol, ``detect(tiles) -> list[Detection]``.
- ``LEDGER_SOURCE``      the ``CostLedger`` source name vision spends under.
- ``DEFAULT_BATCH_SIZE`` tiles per model request.
- ``ClaudeVisionProvider(client=..., ledger=..., batch_size=...)``
                         `client` is injected and only ever used as
                         ``client.messages.create(**kwargs) -> response`` where
                         ``response.content`` is a list of blocks carrying
                         ``.text``. Requests structured JSON, parses the
                         envelope ``{"detections": [{image_ref, signal,
                         present, confidence}, ...]}``, attributes each entry
                         to its tile (pams_pin + capture_date come from the
                         tile, never the model), records spend, and exposes
                         ``parse_failures``.
- ``CachedVisionProvider(inner, cache=...)``
                         never re-requests a seen ``image_ref``.

The real Anthropic SDK must NOT be imported at module import time — this
environment has no ``ANTHROPIC_API_KEY``.
"""

from __future__ import annotations
