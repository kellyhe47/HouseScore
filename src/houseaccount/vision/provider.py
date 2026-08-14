"""The vision provider seam: protocol, OpenAI implementation, cache wrapper.

The OpenAI client is *injected*, never constructed here. That is what lets
the whole batching / cost / parse-failure contract be exercised against a fake
on a machine with no `OPENAI_API_KEY`, exactly like the T001 transport seam.
The SDK is not imported at module scope for the same reason — see
`houseaccount.vision.run`, which imports it lazily inside the one function that
needs a real client.

Three things this layer is responsible for, all of them cost or correctness:

*Batching* (R14). One request per tile over ~1,080 tiles is 1,080 round trips of
prompt overhead. Tiles go up in batches, and the model is told to label each
answer with the `image_ref` it came from.

*Provenance*. `pams_pin` and `capture_date` are taken from the tile, never from
the model, and a detection naming an `image_ref` we did not send is discarded.
A model that names the parcel is a model that can misname it, and a misattributed
pool sends a rep to the wrong door.

*Parse failure as a normal outcome*. A model that answers in prose has still
spent the money, so the ledger is written before the answer is read; and one
bad row must not lose its batch, nor one bad batch the remaining 500 parcels.
Failures accumulate on `parse_failures` for the eval harness to report.
"""

from __future__ import annotations

import base64
import json
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from houseaccount.cache import Cache
from houseaccount.cost import CostLedger
from houseaccount.vision.schema import SIGNALS, Detection
from houseaccount.vision.tiles import TILE_SPAN_METERS, Tile

#: The `CostLedger` source name vision spends under. One string, so the eval
#: harness's cost-per-door line and this module cannot disagree.
LEDGER_SOURCE = "openai_vision"

#: Mini tier per PRD R4.3: pool/solar presence on a 640px tile is a
#: recognition task, not a reasoning one. A module constant so re-tiering is a
#: one-line change rather than a search.
VISION_MODEL = "gpt-4o-mini"

#: USD per token, gpt-4o-mini list price ($0.15 / $0.60 per million).
INPUT_USD_PER_TOKEN = 0.15 / 1_000_000
OUTPUT_USD_PER_TOKEN = 0.60 / 1_000_000

#: Tiles per request. Big enough that prompt overhead amortises, small enough
#: that one unparseable answer costs four tiles rather than forty.
DEFAULT_BATCH_SIZE = 4

#: Output budget. The envelope is a handful of rows per tile; a generous cap
#: only risks paying for a model that decided to narrate.
MAX_OUTPUT_TOKENS = 2048

#: Bumped whenever the prompt or schema changes in a way that would make a
#: cached answer wrong. Part of the cache address, so old entries are simply
#: missed rather than silently reused against a new contract.
PROMPT_VERSION = "2026-08-r33-v2-openai"

_CACHE_NAMESPACE = "vision:detections"

#: `{span}` and `{signals}` are filled per request; `{{`/`}}` escape the JSON
#: braces in the example envelope.
SYSTEM_PROMPT_TEMPLATE = """\
You are an aerial imagery analyst reading New Jersey orthophotography of
single-family residential parcels. Each image is a {span}-metre square tile
centred on one parcel.

Report only what is visible in the tile. Do not infer from neighbouring
properties, and do not guess: a signal you cannot see is `present: false`, and a
signal you can barely make out is a low confidence, not an omission.

Reply with a single JSON object and nothing else — no prose before it, no
markdown fence around it:

{{"detections": [{{"image_ref": "<the label given with the image>", "signal": \
"<one of the signals below>", "present": true, "confidence": 0.0}}]}}

Rules:
- `image_ref` must be copied exactly from the label that accompanied the image.
  Never invent one, and never answer for a tile you were not shown.
- `signal` must be exactly one of: {signals}. No synonyms, no other values.
- `present` must be a JSON boolean.
- `confidence` must be a number between 0 and 1 — your own certainty that the
  claim is true of this tile.
- Emit one `pool` row and one `solar` row per tile, plus at most one
  `condition_<grade>` row per tile giving your single best read of the
  property's exterior and grounds condition.
- Do not report the parcel identifier or the capture date. Both are already
  known from the tile and are not yours to state.

Signal definitions:
- `pool`: a swimming pool of any kind on the parcel — in-ground or above-ground.
- `solar`: solar panels on the roof or elsewhere on the parcel.
- `condition_<grade>`: overall exterior condition, judged from roof and driveway
  surface, lawn and landscaping upkeep, and visible clutter or disrepair.
"""

SYSTEM_PROMPT = SYSTEM_PROMPT_TEMPLATE.format(
    span=int(TILE_SPAN_METERS),
    signals=", ".join(sorted(SIGNALS)),
)

_TASK_PROMPT = (
    "Analyse every tile above. Reply with the JSON object described in your "
    "instructions, labelling each detection with that tile's exact image_ref."
)


@runtime_checkable
class VisionProvider(Protocol):
    """Anything that turns tiles into R3.3 detections.

    A protocol rather than a base class so the pipeline, the cache wrapper and
    a fixture-backed stub are interchangeable without an inheritance tree.
    """

    def detect(self, tiles: Sequence[Tile]) -> list[Detection]: ...


@dataclass(frozen=True)
class ParseFailure:
    """One thing the model said that could not become a detection.

    Kept rather than logged-and-forgotten: a run where 30% of answers failed to
    parse scored 30% of doors as "no pool", and the eval harness has to be able
    to say so.
    """

    image_ref: str | None
    reason: str
    raw: str


class OpenAIVisionProvider:
    """Detect R4 signals on ortho tiles via an injected OpenAI client.

    The client is used through exactly one call —
    `client.chat.completions.create(**kw)` returning an object with `.choices`
    (each carrying `.message.content`) and `.usage` (`.prompt_tokens` /
    `.completion_tokens`). That narrow surface is the whole contract, which is
    why a fake satisfies it in full.
    """

    def __init__(
        self,
        *,
        client: Any,
        ledger: CostLedger,
        batch_size: int = DEFAULT_BATCH_SIZE,
        model: str = VISION_MODEL,
    ) -> None:
        self._client = client
        self._ledger = ledger
        self._batch_size = max(1, int(batch_size))
        self._model = model
        self.parse_failures: list[ParseFailure] = []

    def detect(self, tiles: Sequence[Tile]) -> list[Detection]:
        """Detections for `tiles`, in batch order. Never raises on bad output."""
        batch_list = list(tiles)
        found: list[Detection] = []
        for start in range(0, len(batch_list), self._batch_size):
            found.extend(self._detect_batch(batch_list[start : start + self._batch_size]))
        return found

    # --- one request --------------------------------------------------------

    def _detect_batch(self, batch: Sequence[Tile]) -> list[Detection]:
        if not batch:
            return []

        response = self._client.chat.completions.create(**self._request(batch))

        # Bill first. The money left the account whatever the model said, and a
        # run that under-reports its own spend is worse than one that overspends.
        self._ledger.record(LEDGER_SOURCE, units=len(batch), usd=_usd_for(response))

        text = _response_text(response)
        payload = _parse_envelope(text)
        if payload is None:
            self.parse_failures.append(
                ParseFailure(
                    image_ref=None,
                    reason="response was not a {'detections': [...]} JSON object",
                    raw=text,
                )
            )
            return []

        by_ref = {tile.image_ref: tile for tile in batch}
        return [
            detection
            for entry in payload
            if (detection := self._to_detection(entry, by_ref, text)) is not None
        ]

    def _to_detection(
        self,
        entry: Any,
        by_ref: Mapping[str, Tile],
        raw: str,
    ) -> Detection | None:
        """One model row -> one Detection, or a recorded failure. Never raises."""
        if not isinstance(entry, Mapping):
            return self._fail(None, f"detection entry was not an object: {entry!r}", raw)

        image_ref = entry.get("image_ref")
        tile = by_ref.get(image_ref) if isinstance(image_ref, str) else None
        if tile is None:
            # No frame we sent means no parcel to attach it to. That is a
            # hallucination, not a detection.
            return self._fail(
                image_ref if isinstance(image_ref, str) else None,
                f"detection names an image_ref that was not in this batch: {image_ref!r}",
                raw,
            )

        try:
            return Detection(
                pams_pin=tile.pams_pin,
                signal=entry.get("signal"),
                present=entry.get("present"),
                confidence=entry.get("confidence"),
                image_ref=tile.image_ref,
                capture_date=tile.capture_date,
            )
        except ValueError as error:
            return self._fail(tile.image_ref, str(error), raw)

    def _fail(self, image_ref: str | None, reason: str, raw: str) -> None:
        self.parse_failures.append(ParseFailure(image_ref=image_ref, reason=reason, raw=raw))
        return None

    def _request(self, batch: Sequence[Tile]) -> dict[str, Any]:
        """The `chat.completions.create` kwargs for one batch.

        Each image is preceded by its `image_ref` label — without it the model's
        answers cannot be attributed, and the whole batching saving evaporates.
        """
        content: list[dict[str, Any]] = []
        for tile in batch:
            content.append({"type": "text", "text": f"Tile image_ref: {tile.image_ref}"})
            encoded = base64.b64encode(tile.image_bytes).decode("ascii")
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{encoded}"},
                }
            )
        content.append({"type": "text", "text": _TASK_PROMPT})

        return {
            "model": self._model,
            "max_tokens": MAX_OUTPUT_TOKENS,
            # Server-side JSON mode. The prompt still spells out the envelope —
            # JSON mode guarantees syntax, not the `detections` key — so a wrong
            # shape stays a recorded parse failure rather than a crash.
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ],
        }


class CachedVisionProvider:
    """Cache-first wrapper: a seen `image_ref` never reaches the model again.

    This is the R2.3 re-run guarantee for the one stage that costs real money.
    Caching is per *tile*, not per batch, so a second run over 540 parcels plus
    30 new ones pays for the 30.

    Empty results are cached too — "we looked and found nothing" is an answer,
    and re-asking for it is the same spend as asking the first time.
    """

    def __init__(self, inner: VisionProvider, *, cache: Cache) -> None:
        self._inner = inner
        self._cache = cache

    @property
    def parse_failures(self) -> Sequence[Any]:
        return getattr(self._inner, "parse_failures", ())

    def detect(self, tiles: Sequence[Tile]) -> list[Detection]:
        batch = list(tiles)
        warm: dict[str, list[Detection]] = {}
        cold: list[Tile] = []

        for tile in batch:
            cached = self._cache.get(self._key(tile))
            if cached is None:
                cold.append(tile)
            else:
                warm[tile.image_ref] = [Detection(**row) for row in cached]

        if cold:
            fresh: dict[str, list[Detection]] = {tile.image_ref: [] for tile in cold}
            for detection in self._inner.detect(cold):
                fresh.setdefault(detection.image_ref, []).append(detection)
            for tile in cold:
                rows = fresh.get(tile.image_ref, [])
                self._cache.put(self._key(tile), [asdict(row) for row in rows])
                warm[tile.image_ref] = rows

        return [detection for tile in batch for detection in warm.get(tile.image_ref, [])]

    def _key(self, tile: Tile) -> str:
        # The prompt version rides in the address so a changed prompt cannot be
        # served yesterday's answers.
        return self._cache.key(
            _CACHE_NAMESPACE,
            {"image_ref": tile.image_ref, "prompt_version": PROMPT_VERSION},
        )


# --- reading the model's answer ---------------------------------------------


def _response_text(response: Any) -> str:
    """Concatenate the message text of a reply. Missing/odd choices yield ''."""
    choices = getattr(response, "choices", None) or ()
    return "".join(
        text
        for choice in choices
        if isinstance(text := getattr(getattr(choice, "message", None), "content", None), str)
    )


def _usd_for(response: Any) -> float:
    """Token spend for one request, at the mini-tier list price."""
    usage = getattr(response, "usage", None)
    input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
    return input_tokens * INPUT_USD_PER_TOKEN + output_tokens * OUTPUT_USD_PER_TOKEN


def _parse_envelope(text: str) -> list[Any] | None:
    """The `detections` list from a model reply, or None if it isn't one.

    Tolerates a markdown fence — models wrap JSON in one often enough that
    refusing would burn real batches — but nothing beyond that. Prose, a bare
    list, or the wrong envelope key is a parse failure, not a rescue attempt.
    """
    candidate = _strip_fence(text)
    if not candidate:
        return None
    try:
        payload = json.loads(candidate)
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, Mapping):
        return None
    detections = payload.get("detections")
    if not isinstance(detections, list):
        return None
    return detections


def _strip_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()
