"""The vision stage entry point — including the no-API-key declination.

R6.1 says a missing signal degrades a score, it does not stop a run, and vision
is the stage most likely to be missing: it is the only one that needs a paid
credential. So "no `OPENAI_API_KEY`" is a first-class outcome here, not an
error path. The stage returns an empty-but-valid `VisionRun`, says why, logs it
at WARNING so the operator sees it once rather than discovering it in the
numbers, spends nothing, and never constructs a client. Every one of the ~540
doors still scores — just without the pool and condition terms.

The OpenAI SDK is imported inside `_build_client` for the same reason. A
module-level import would make `houseaccount.vision` unimportable wherever the
SDK is absent, which is precisely the environment the declination exists to
serve.

Composition is fixed here rather than at each call site: cache in front of
the model, so `make pipeline` twice in a row costs money once.
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
    OpenAIVisionProvider,
)
from houseaccount.vision.schema import Detection
from houseaccount.vision.tiles import Tile

logger = logging.getLogger(__name__)

#: What the operator reads when the stage sits out. Names the variable, so the
#: fix is obvious, and names the consequence, so a degraded run is not mistaken
#: for a clean one.
NO_KEY_REASON = (
    "OPENAI_API_KEY is not set, so the vision stage was skipped. "
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
    if not config.openai_api_key:
        logger.warning(NO_KEY_REASON)
        return VisionRun(detections=(), declined=True, reason=NO_KEY_REASON, parse_failures=())

    # Pacing belongs to the real account's allowance, so it is switched on by
    # the same condition that builds the real client: an injected client is a
    # test double with no rate limit, and must not make the suite sleep.
    live = client is None
    inner = OpenAIVisionProvider(
        client=_build_client(config) if live else client,
        ledger=ledger if ledger is not None else CostLedger(),
        batch_size=batch_size,
        tokens_per_minute=VISION_TOKENS_PER_MINUTE if live else None,
    )
    provider = CachedVisionProvider(inner, cache=cache) if cache is not None else inner

    detections = tuple(provider.detect(tiles))
    return VisionRun(
        detections=detections,
        declined=False,
        reason="",
        parse_failures=tuple(inner.parse_failures),
    )


#: The per-minute token allowance the live run paces itself against.
#:
#: Deliberately under the 200,000 TPM of a standard account: the limiter counts
#: tokens the API measured, this counts tokens it reported afterwards, and the
#: gap between those two is what the headroom pays for. Retries cover the rest.
#:
#: This is a throughput ceiling, not a budget — it changes how long a run takes,
#: never how much it costs. A territory is ~1M tokens, so a full run paces out
#: to roughly five minutes.
VISION_TOKENS_PER_MINUTE = 180_000

#: Seconds one vision request may take before it is abandoned and retried.
#:
#: The SDK's default read timeout is 600s, tuned for long reasoning turns. A
#: batch of four 640px tiles answers in seconds, so a request still open after
#: a minute is hung, not thinking. The default matters because it multiplies
#: with the retry budget: at 600s x `VISION_MAX_RETRIES` one stuck request can
#: hold a run for over an hour, which is indistinguishable from a crash but
#: burns the wall-clock of a working one. Bounding the attempt is what makes a
#: generous retry budget safe.
VISION_TIMEOUT_SECONDS = 60.0

#: How many times the SDK may retry one request before giving up.
#:
#: A full territory is ~1100 tiles, and at `DEFAULT_BATCH_SIZE` that is a few
#: hundred requests of a few thousand tokens each — comfortably more than a
#: standard 200k tokens-per-minute allowance can take in one go. So a 429 here
#: is the expected shape of a *healthy* run against a real account, not an
#: outage: the limiter refills continuously and the work simply has to pace
#: itself across several minutes.
#:
#: The SDK's default of 2 retries is tuned for interactive use and gives up
#: after a couple of seconds, which turns that ordinary throttling into a dead
#: run — and, because the cache is written per batch, a dead run that has
#: already paid for everything it fetched. The SDK honours the `retry-after`
#: the API sends, so raising the budget waits exactly as long as asked.
VISION_MAX_RETRIES = 8


def _build_client(config: Config) -> Any:
    """The only place the real SDK is touched. Imported lazily on purpose."""
    import openai

    return openai.OpenAI(
        api_key=config.openai_api_key,
        max_retries=VISION_MAX_RETRIES,
        timeout=VISION_TIMEOUT_SECONDS,
    )
