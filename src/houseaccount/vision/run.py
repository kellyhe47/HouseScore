"""The vision stage entry point — including the no-API-key declination.

R6.1 says a missing signal degrades a score, it does not stop a run, and vision
is the stage most likely to be missing: it is the only one that needs a paid
credential. So "no `ANTHROPIC_API_KEY`" is a first-class outcome here, not an
error path. The stage returns an empty-but-valid `VisionRun`, says why, logs it
at WARNING so the operator sees it once rather than discovering it in the
numbers, spends nothing, and never constructs a client. Every one of the ~540
doors still scores — just without the pool and condition terms.

The Anthropic SDK is imported inside `_build_client` for the same reason. A
module-level import would make `houseaccount.vision` unimportable wherever the
SDK is absent, which is precisely the environment the declination exists to
serve.

Composition is fixed here rather than at each call site: cache in front of
Claude, so `make pipeline` twice in a row costs money once.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Sequence

from houseaccount.cache import Cache
from houseaccount.config import Config
from houseaccount.cost import CostLedger
from houseaccount.vision.provider import (
    DEFAULT_BATCH_SIZE,
    CachedVisionProvider,
    ClaudeVisionProvider,
)
from houseaccount.vision.schema import Detection
from houseaccount.vision.tiles import Tile

logger = logging.getLogger(__name__)

#: What the operator reads when the stage sits out. Names the variable, so the
#: fix is obvious, and names the consequence, so a degraded run is not mistaken
#: for a clean one.
NO_KEY_REASON = (
    "ANTHROPIC_API_KEY is not set, so the vision stage was skipped. "
    "Pool, solar and exterior-condition signals are unavailable; every door "
    "still scores, but without the R4 imagery terms."
)


@dataclass(frozen=True)
class VisionRun:
    """The vision stage's result, whether or not it ran.

    `declined is True` is the "we chose not to" outcome and is distinct from
    "we ran and found nothing" — both carry an empty `detections`, and only the
    first should make an operator go looking for a key.
    """

    detections: Sequence[Detection] = ()
    declined: bool = False
    reason: str = ""
    parse_failures: Sequence[Any] = field(default_factory=tuple)


def run_vision(
    tiles: Sequence[Tile],
    *,
    config: Config,
    ledger: CostLedger | None = None,
    cache: Cache | None = None,
    client: Any | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> VisionRun:
    """Detect R4 signals across `tiles`, or decline if there is no key.

    `client` is the injected seam: pass a fake in tests, leave it None in the
    pipeline and the real SDK client is built lazily. An empty `tiles` with a
    key configured is a completed run with nothing to look at — not a
    declination.
    """
    if not config.anthropic_api_key:
        logger.warning(NO_KEY_REASON)
        return VisionRun(detections=(), declined=True, reason=NO_KEY_REASON, parse_failures=())

    inner = ClaudeVisionProvider(
        client=client if client is not None else _build_client(config),
        ledger=ledger if ledger is not None else CostLedger(),
        batch_size=batch_size,
    )
    provider = CachedVisionProvider(inner, cache=cache) if cache is not None else inner

    detections = tuple(provider.detect(tiles))
    return VisionRun(
        detections=detections,
        declined=False,
        reason="",
        parse_failures=tuple(inner.parse_failures),
    )


def _build_client(config: Config) -> Any:
    """The only place the real SDK is touched. Imported lazily on purpose."""
    import anthropic

    return anthropic.Anthropic(api_key=config.anthropic_api_key)
