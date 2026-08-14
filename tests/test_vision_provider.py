"""The vision provider seam (T007) — batching, cost, caching, declination.

`OPENAI_API_KEY` is unset on this machine and no test may reach the network,
so the OpenAI client is an *injected seam*, exactly like the T001 transport.
The fake below is the whole contract the provider may rely on:

    client.chat.completions.create(**kwargs) -> response
    response.choices -> [choice, ...]   with choice.message.content carrying JSON
    response.usage   -> .prompt_tokens / .completion_tokens

It records every request it received, which is what makes batch size and call
counts assertable rather than inferred.

Three things the criteria care about, and one they imply:

- *Batching* is a cost control (R14), so it is asserted as call arithmetic.
- *Caching* is the re-run guarantee (R2.3): a second run over a seen image_ref
  must reach the model zero times.
- *Parse failure* is a normal outcome, not an exception. A model that answers in
  prose costs the same money and must not take down a 540-parcel run.
- The money is spent whether or not the answer parses, so the ledger records the
  batch either way.

Request *content* is asserted at the shape level only — that JSON was asked for,
and that every tile in the batch is labelled — because prompt wording is the
implementer's to tune.
"""

import importlib
import json
import logging
import sys
import threading
import time
from collections.abc import Mapping
from pathlib import Path

import pytest

from houseaccount.cache import Cache
from houseaccount.config import Config
from houseaccount.cost import CostLedger
from houseaccount.scoring.engine import ScoreInput, score_door
from houseaccount.vision.provider import (
    DEFAULT_BATCH_SIZE,
    LEDGER_SOURCE,
    CachedVisionProvider,
    OpenAIVisionProvider,
    VisionProvider,
)
from houseaccount.vision.run import (
    VISION_MAX_RETRIES,
    VISION_TIMEOUT_SECONDS,
    VisionRun,
    _build_client,
    run_vision,
)
from houseaccount.vision.schema import Detection, to_score_vision
from houseaccount.vision.tiles import Tile

GOLDEN = Path(__file__).resolve().parents[1] / "eval" / "golden"

PNG = b"\x89PNG\r\n\x1a\n ortho tile bytes"


def tile(index, year=2020):
    pin = f"0248_00101_{index:05d}"
    return Tile(
        pams_pin=pin,
        year=year,
        image_ref=f"tiles/{year}/{pin}.png",
        capture_date=f"{year}-06-15",
        image_bytes=PNG,
    )


def tiles(count, year=2020):
    return [tile(index, year) for index in range(1, count + 1)]


# --- the fake OpenAI client -------------------------------------------------


class FakeMessage:
    def __init__(self, text):
        self.role = "assistant"
        self.content = text


class FakeChoice:
    def __init__(self, text):
        self.index = 0
        self.message = FakeMessage(text)
        self.finish_reason = "stop"


class FakeUsage:
    def __init__(self, prompt_tokens=1200, completion_tokens=180):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


class FakeReply:
    def __init__(self, text):
        self.choices = [FakeChoice(text)]
        self.usage = FakeUsage()
        self.model = "fake-mini"


class FakeCompletions:
    def __init__(self, client):
        self._client = client

    def create(self, **kwargs):
        self._client.calls.append(kwargs)
        return FakeReply(self._client.responder(kwargs))


class FakeChat:
    def __init__(self, client):
        self.completions = FakeCompletions(client)


class FakeOpenAI:
    """Records every request; answers with whatever `responder` returns.

    The default responder claims a pool on every tile whose `image_ref` it can
    see in the request — which is only possible because the provider labels the
    images it sends, and is therefore the same contract the real model works to.
    """

    def __init__(self, known_tiles=(), responder=None, confidence=0.93):
        self.calls = []
        self.confidence = confidence
        self.known_refs = [item.image_ref for item in known_tiles]
        self.responder = responder or self._default_responder
        self.chat = FakeChat(self)

    def _default_responder(self, kwargs):
        seen = [ref for ref in self.known_refs if ref in serialise(kwargs)]
        return json.dumps(
            {
                "detections": [
                    {
                        "image_ref": ref,
                        "signal": "pool",
                        "present": True,
                        "confidence": self.confidence,
                    }
                    for ref in seen
                ]
            }
        )


def responding(text):
    """A responder that always answers with the same literal text."""
    return lambda _kwargs: text


def serialise(value):
    return json.dumps(value, default=repr)


def count_image_blocks(value):
    """How many image content blocks a request carries, wherever they sit."""
    if isinstance(value, Mapping):
        here = 1 if value.get("type") == "image_url" else 0
        return here + sum(count_image_blocks(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return sum(count_image_blocks(item) for item in value)
    return 0


def openai_provider(client, ledger=None, **kwargs):
    return OpenAIVisionProvider(client=client, ledger=ledger or CostLedger(), **kwargs)


# --- the protocol -----------------------------------------------------------


class RecordingProvider:
    """A minimal conforming provider: one pool detection per tile, calls logged."""

    def __init__(self, confidence=0.9):
        self.calls = []
        self.confidence = confidence

    def detect(self, tiles):
        batch = tuple(tiles)
        self.calls.append(batch)
        return [
            Detection(
                pams_pin=item.pams_pin,
                signal="pool",
                present=True,
                confidence=self.confidence,
                image_ref=item.image_ref,
                capture_date=item.capture_date,
            )
            for item in batch
        ]


def test_anything_with_detect_is_a_vision_provider():
    assert isinstance(RecordingProvider(), VisionProvider)


def test_an_object_without_detect_is_not_a_vision_provider():
    assert not isinstance(object(), VisionProvider)


def test_both_shipped_providers_satisfy_the_protocol(tmp_path):
    assert isinstance(openai_provider(FakeOpenAI()), VisionProvider)
    assert isinstance(
        CachedVisionProvider(RecordingProvider(), cache=Cache(tmp_path / "cache")), VisionProvider
    )


def test_detect_returns_r33_detections():
    batch = tiles(2)
    found = openai_provider(FakeOpenAI(batch)).detect(batch)
    assert len(found) == 2
    assert all(isinstance(item, Detection) for item in found)
    assert {item.image_ref for item in found} == {item.image_ref for item in batch}


def test_provenance_comes_from_the_tile_not_the_model():
    """The model may not name the parcel or date its own evidence."""
    batch = tiles(1)
    client = FakeOpenAI(
        batch,
        responder=responding(
            json.dumps(
                {
                    "detections": [
                        {
                            "image_ref": batch[0].image_ref,
                            "pams_pin": "0000_99999_99999",
                            "capture_date": "1999-01-01",
                            "signal": "pool",
                            "present": True,
                            "confidence": 0.9,
                        }
                    ]
                }
            )
        ),
    )
    found = openai_provider(client).detect(batch)
    assert [item.pams_pin for item in found] == [batch[0].pams_pin]
    assert [item.capture_date for item in found] == [batch[0].capture_date]


# --- batching ---------------------------------------------------------------


def test_the_default_batch_size_actually_batches():
    assert DEFAULT_BATCH_SIZE > 1


@pytest.mark.parametrize(
    "tile_count,batch_size,expected_calls",
    [
        (7, 3, 3),  # 3 + 3 + 1
        (6, 3, 2),
        (2, 3, 1),  # fewer tiles than a batch is still one call
        (1, 1, 1),
        (5, 1, 5),  # batching off
        (10, 10, 1),
    ],
)
def test_tiles_are_sent_in_batches(tile_count, batch_size, expected_calls):
    batch = tiles(tile_count)
    client = FakeOpenAI(batch)
    openai_provider(client, batch_size=batch_size).detect(batch)
    assert len(client.calls) == expected_calls


def test_no_request_exceeds_the_batch_size():
    batch = tiles(7)
    client = FakeOpenAI(batch)
    openai_provider(client, batch_size=3).detect(batch)
    counts = [count_image_blocks(call) for call in client.calls]
    assert max(counts) <= 3
    assert sum(counts) == 7


def test_every_tile_is_detected_once_across_the_batches():
    batch = tiles(7)
    found = openai_provider(FakeOpenAI(batch), batch_size=3).detect(batch)
    assert sorted(item.image_ref for item in found) == sorted(item.image_ref for item in batch)


def test_an_empty_tile_list_costs_nothing_and_calls_nothing():
    client = FakeOpenAI()
    ledger = CostLedger()
    assert list(openai_provider(client, ledger=ledger).detect([])) == []
    assert client.calls == []
    assert ledger.total_usd() == 0.0


# --- what the request has to say --------------------------------------------


def test_the_request_asks_for_structured_json():
    batch = tiles(2)
    client = FakeOpenAI(batch)
    openai_provider(client).detect(batch)
    assert "json" in serialise(client.calls[0]).lower()


def test_the_request_labels_every_tile_it_carries():
    """Without a per-image label the model's answers cannot be attributed."""
    batch = tiles(3)
    client = FakeOpenAI(batch)
    openai_provider(client, batch_size=3).detect(batch)
    request = serialise(client.calls[0])
    for item in batch:
        assert item.image_ref in request


def test_the_request_carries_the_tile_pixels():
    batch = tiles(2)
    client = FakeOpenAI(batch)
    openai_provider(client).detect(batch)
    assert count_image_blocks(client.calls[0]) == 2


# --- cost -------------------------------------------------------------------


def test_spend_is_recorded_against_the_vision_source():
    batch = tiles(4)
    ledger = CostLedger()
    openai_provider(FakeOpenAI(batch), ledger=ledger, batch_size=2).detect(batch)
    assert LEDGER_SOURCE in ledger.as_dict()["sources"]
    assert ledger.total_usd() > 0


def test_billed_units_are_the_tiles_looked_at():
    batch = tiles(7)
    ledger = CostLedger()
    openai_provider(FakeOpenAI(batch), ledger=ledger, batch_size=3).detect(batch)
    assert ledger.as_dict()["sources"][LEDGER_SOURCE]["units"] == 7


def test_a_second_run_accumulates_rather_than_replaces():
    batch = tiles(2)
    ledger = CostLedger()
    provider = openai_provider(FakeOpenAI(batch), ledger=ledger)
    provider.detect(batch)
    first = ledger.total_usd()
    provider.detect(batch)
    assert ledger.total_usd() == pytest.approx(2 * first)


def test_an_unparseable_answer_is_still_billed():
    """The money left the account whatever the model said."""
    batch = tiles(2)
    ledger = CostLedger()
    client = FakeOpenAI(batch, responder=responding("I'm afraid I can't help with that."))
    openai_provider(client, ledger=ledger).detect(batch)
    assert ledger.total_usd() > 0


# --- parse failures ---------------------------------------------------------


def test_a_clean_run_records_no_parse_failures():
    batch = tiles(3)
    provider = openai_provider(FakeOpenAI(batch))
    provider.detect(batch)
    assert list(provider.parse_failures) == []


@pytest.mark.parametrize(
    "answer",
    [
        "I'm afraid I can't help with that.",  # prose instead of JSON
        "",  # nothing at all
        "{",  # truncated JSON
        "```json\n{ nope }\n```",  # fenced but broken
        json.dumps({"result": "ok"}),  # valid JSON, wrong envelope
        json.dumps([]),  # a bare list, not the envelope
        json.dumps({"detections": "a pool"}),  # detections not a list
    ],
)
def test_unparseable_output_is_recorded_and_excluded(answer):
    batch = tiles(2)
    provider = openai_provider(FakeOpenAI(batch, responder=responding(answer)))
    assert list(provider.detect(batch)) == []
    assert len(provider.parse_failures) >= 1


@pytest.mark.parametrize(
    "bad_entry",
    [
        {"signal": "trampoline", "present": True, "confidence": 0.9},  # unknown signal
        {"signal": "pool", "present": True, "confidence": 1.5},  # above 1
        {"signal": "pool", "present": True, "confidence": -0.1},  # below 0
        {"signal": "pool", "present": "yes", "confidence": 0.9},  # present not a bool
        {"signal": "pool", "present": True},  # no confidence at all
        {"present": True, "confidence": 0.9},  # no signal at all
        "a pool, probably",  # not even an object
    ],
)
def test_a_bad_row_is_dropped_without_taking_the_batch_with_it(bad_entry):
    batch = tiles(2)
    good, bad = batch
    entry = dict(bad_entry) if isinstance(bad_entry, dict) else bad_entry
    if isinstance(entry, dict):
        entry["image_ref"] = bad.image_ref
    payload = {
        "detections": [
            {
                "image_ref": good.image_ref,
                "signal": "pool",
                "present": True,
                "confidence": 0.88,
            },
            entry,
        ]
    }
    provider = openai_provider(FakeOpenAI(batch, responder=responding(json.dumps(payload))))
    found = list(provider.detect(batch))
    assert [item.image_ref for item in found] == [good.image_ref]
    assert len(provider.parse_failures) >= 1


def test_a_detection_for_an_image_we_never_sent_is_a_parse_failure():
    """No image_ref means no parcel to attach it to — that is a hallucination,
    not a detection."""
    batch = tiles(1)
    payload = {
        "detections": [
            {
                "image_ref": "tiles/2020/0248_99999_99999.png",
                "signal": "pool",
                "present": True,
                "confidence": 0.99,
            }
        ]
    }
    provider = openai_provider(FakeOpenAI(batch, responder=responding(json.dumps(payload))))
    assert list(provider.detect(batch)) == []
    assert len(provider.parse_failures) >= 1


def test_one_bad_batch_does_not_stop_the_later_ones():
    batch = tiles(4)

    def responder(kwargs):
        request = serialise(kwargs)
        if batch[0].image_ref in request:
            return "the first batch went sideways"
        seen = [item for item in batch if item.image_ref in request]
        return json.dumps(
            {
                "detections": [
                    {
                        "image_ref": item.image_ref,
                        "signal": "pool",
                        "present": True,
                        "confidence": 0.9,
                    }
                    for item in seen
                ]
            }
        )

    provider = openai_provider(FakeOpenAI(batch, responder=responder), batch_size=2)
    found = list(provider.detect(batch))
    assert sorted(item.image_ref for item in found) == sorted(
        item.image_ref for item in batch[2:]
    )
    assert len(provider.parse_failures) >= 1


# --- caching ----------------------------------------------------------------


@pytest.fixture
def cache(tmp_path):
    return Cache(tmp_path / "vision-cache")


def test_a_second_run_over_seen_tiles_never_reaches_the_inner_provider(cache):
    batch = tiles(3)
    inner = RecordingProvider()
    provider = CachedVisionProvider(inner, cache=cache)

    first = list(provider.detect(batch))
    second = list(provider.detect(batch))

    assert len(inner.calls) == 1
    assert second == first


def test_the_cache_survives_a_fresh_wrapper(cache):
    """A re-run in a new process is the case that has to be free."""
    batch = tiles(2)
    warm = list(CachedVisionProvider(RecordingProvider(), cache=cache).detect(batch))
    inner = RecordingProvider()
    assert list(CachedVisionProvider(inner, cache=cache).detect(batch)) == warm
    assert inner.calls == []


def test_only_the_unseen_tiles_are_forwarded(cache):
    first_two = tiles(2)
    all_three = tiles(3)
    inner = RecordingProvider()
    provider = CachedVisionProvider(inner, cache=cache)

    provider.detect(first_two)
    provider.detect(all_three)

    assert len(inner.calls) == 2
    assert tuple(item.image_ref for item in inner.calls[1]) == (all_three[2].image_ref,)


def test_a_mixed_run_returns_cached_and_fresh_detections_together(cache):
    all_three = tiles(3)
    provider = CachedVisionProvider(RecordingProvider(), cache=cache)
    provider.detect(all_three[:2])
    found = list(provider.detect(all_three))
    assert sorted(item.image_ref for item in found) == sorted(
        item.image_ref for item in all_three
    )


def test_caching_nothing_calls_nothing(cache):
    inner = RecordingProvider()
    assert list(CachedVisionProvider(inner, cache=cache).detect([])) == []
    assert inner.calls == []


def test_the_cache_wraps_the_real_provider_too(cache):
    """The composition the pipeline actually uses: cache in front of the model."""
    batch = tiles(2)
    client = FakeOpenAI(batch)
    provider = CachedVisionProvider(openai_provider(client), cache=cache)
    provider.detect(batch)
    provider.detect(batch)
    assert len(client.calls) == 1


# --- the no-key declination -------------------------------------------------


@pytest.fixture
def no_key(monkeypatch):
    """The real state of this machine: no OPENAI_API_KEY at all."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    return Config.from_env()


@pytest.fixture
def with_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-key")
    return Config.from_env()


def test_without_a_key_the_stage_declines_instead_of_failing(no_key):
    result = run_vision(tiles(3), config=no_key)
    assert isinstance(result, VisionRun)
    assert result.declined is True
    assert list(result.detections) == []


def test_the_declination_says_why(no_key):
    assert run_vision(tiles(3), config=no_key).reason.strip() != ""


def test_the_declination_is_logged(no_key, caplog):
    with caplog.at_level(logging.WARNING):
        run_vision(tiles(3), config=no_key)
    assert [record for record in caplog.records if record.levelno >= logging.WARNING]


def test_declining_spends_nothing_and_touches_no_client(no_key):
    client = FakeOpenAI(tiles(3))
    ledger = CostLedger()
    run_vision(tiles(3), config=no_key, ledger=ledger, client=client)
    assert client.calls == []
    assert ledger.total_usd() == 0.0


def test_the_declined_result_is_a_valid_empty_vision_dict(no_key):
    vision = to_score_vision(run_vision(tiles(3), config=no_key).detections)
    assert vision["pool"] is False
    assert vision["condition_2015"] is None
    assert vision["condition_2020"] is None


def scored_fixtures():
    out = []
    for path in sorted(GOLDEN.glob("*.json")):
        fixture = json.loads(path.read_text(encoding="utf-8"))
        if "score" in fixture.get("expect", {}):
            out.append(fixture)
    return out


FIXTURES = scored_fixtures()

assert len(FIXTURES) >= 10, f"fixture discovery found only {len(FIXTURES)} scored fixtures"


@pytest.mark.parametrize("fixture", FIXTURES, ids=[f["name"] for f in FIXTURES])
def test_every_door_still_scores_when_vision_declines(no_key, fixture):
    """R6.1: a missing vision stage degrades the score, it does not stop the run."""
    given = dict(fixture["given"])
    given["vision"] = to_score_vision(run_vision([], config=no_key).detections)
    result = score_door(ScoreInput.from_fixture(given))
    assert isinstance(result.score, int)
    assert 0 <= result.score <= 100


# --- the configured path ----------------------------------------------------


def test_with_a_key_the_injected_client_does_the_work(with_key):
    batch = tiles(2)
    client = FakeOpenAI(batch)
    ledger = CostLedger()
    result = run_vision(batch, config=with_key, ledger=ledger, client=client)
    assert result.declined is False
    assert len(list(result.detections)) == 2
    assert ledger.total_usd() > 0


def test_a_configured_run_with_no_tiles_is_not_a_declination(with_key):
    client = FakeOpenAI()
    result = run_vision([], config=with_key, client=client)
    assert result.declined is False
    assert list(result.detections) == []
    assert client.calls == []


def test_parse_failures_surface_on_the_run(with_key):
    batch = tiles(2)
    client = FakeOpenAI(batch, responder=responding("not json"))
    result = run_vision(batch, config=with_key, client=client)
    assert list(result.detections) == []
    assert len(result.parse_failures) >= 1


# --- the real client's retry budget -----------------------------------------
#
# A whole territory is a few hundred requests and far more tokens than a
# standard per-minute allowance, so 429s are the normal texture of a real run.
# The SDK's default budget of 2 gives up seconds into a wait the API asked for,
# which loses a run that was working. Nothing else in this suite constructs the
# real client, so without this the budget could silently go back to the default.


# --- pacing against the per-minute allowance --------------------------------
#
# Retries and pacing solve different halves of the same 429. Retries ride out a
# transient limit; pacing is what keeps a run that is legitimately bigger than
# one minute's allowance from emptying the bucket in its first few seconds.


#: What one `FakeUsage` reply totals: 1200 prompt + 180 completion. It carries
#: no `total_tokens`, so this also pins the summed fallback in `_tokens_for`.
FAKE_TOTAL_TOKENS = 1200 + 180


def paced(batch, *, tpm, concurrency=1, batch_size=DEFAULT_BATCH_SIZE):
    """Run `batch` against a frozen clock; return the provider and its waits.

    The clock never advances, so what accumulates is the *reservation* — the
    point on the shared timeline the allowance has been committed to. That is
    the quantity the ceiling is made of, and unlike wall-clock it is identical
    however many threads did the spending.
    """
    slept = []
    provider = OpenAIVisionProvider(
        client=FakeOpenAI(batch),
        ledger=CostLedger(),
        batch_size=batch_size,
        concurrency=concurrency,
        tokens_per_minute=tpm,
        sleep=slept.append,
        monotonic=lambda: 0.0,
    )
    provider.detect(batch)
    return provider, slept


def test_pacing_reserves_time_in_proportion_to_tokens_spent():
    # 60_000 tokens/min is 1_000 tokens/second, so one batch reserves tokens/1_000.
    provider, slept = paced(tiles(16), tpm=60_000, batch_size=4)
    assert slept and any(pause > 0 for pause in slept)
    assert provider._allowance_free_at == pytest.approx(4 * FAKE_TOTAL_TOKENS / 1_000.0)


def test_a_slower_allowance_reserves_longer():
    fast, _ = paced(tiles(16), tpm=120_000, batch_size=4)
    slow, _ = paced(tiles(16), tpm=60_000, batch_size=4)
    assert slow._allowance_free_at == pytest.approx(2 * fast._allowance_free_at)


def test_without_an_allowance_nothing_sleeps():
    """The default path — and every injected-client test — must not sleep."""
    slept = []
    batch = tiles(4)
    OpenAIVisionProvider(client=FakeOpenAI(batch), ledger=CostLedger(), sleep=slept.append).detect(
        batch
    )
    assert slept == []


def test_an_injected_client_never_paces(with_key, monkeypatch):
    """`run_vision` with a test double must stay instant."""
    monkeypatch.setattr(
        "houseaccount.vision.provider.time.sleep",
        lambda _s: pytest.fail("a test double must not pace"),
    )
    batch = tiles(2)
    assert run_vision(batch, config=with_key, client=FakeOpenAI(batch)).declined is False


# --- concurrency ------------------------------------------------------------
#
# The stage waits on the model far longer than it spends allowance, so requests
# overlap. Two things must survive that: the order callers read positionally,
# and the ceiling `_pace` holds.


class BlockingClient(FakeOpenAI):
    """Counts how many calls are in flight at once, and answers slowly."""

    def __init__(self, known_tiles=(), hold=0.05):
        super().__init__(known_tiles)
        self.hold = hold
        self.peak = 0
        self._live = 0
        self._lock = threading.Lock()
        inner_create = self.chat.completions.create

        def counting_create(**kwargs):
            with self._lock:
                self._live += 1
                self.peak = max(self.peak, self._live)
            try:
                time.sleep(self.hold)
                return inner_create(**kwargs)
            finally:
                with self._lock:
                    self._live -= 1

        self.chat.completions.create = counting_create


def test_concurrent_requests_actually_overlap():
    batch = tiles(16)
    client = BlockingClient(batch)
    openai_provider(client, batch_size=4, concurrency=4).detect(batch)
    assert client.peak > 1


def test_concurrency_preserves_batch_order():
    """The evidence panel reads these positionally; faster must not mean reordered."""
    batch = tiles(16)
    serial = openai_provider(FakeOpenAI(batch), batch_size=4).detect(batch)
    parallel = openai_provider(FakeOpenAI(batch), batch_size=4, concurrency=4).detect(batch)
    assert [d.image_ref for d in parallel] == [d.image_ref for d in serial]


def test_concurrency_does_not_widen_the_allowance():
    """Four workers must not spend four times the tokens per minute.

    This is the whole safety argument for turning concurrency on: the ceiling
    is a property of the shared timeline, not of how many threads race for it.
    """
    batch = tiles(16)
    serial, _ = paced(batch, tpm=60_000, batch_size=4, concurrency=1)
    parallel, _ = paced(batch, tpm=60_000, batch_size=4, concurrency=4)
    assert parallel._allowance_free_at == pytest.approx(serial._allowance_free_at)


def test_every_batch_is_billed_exactly_once_under_concurrency():
    batch = tiles(16)
    ledger = CostLedger()
    openai_provider(FakeOpenAI(batch), ledger=ledger, batch_size=4, concurrency=4).detect(batch)
    assert ledger.as_dict()["sources"][LEDGER_SOURCE]["units"] == 16


def test_parse_failures_are_not_lost_under_concurrency():
    batch = tiles(16)
    provider = openai_provider(
        FakeOpenAI(batch, responder=responding("not json")), batch_size=4, concurrency=4
    )
    provider.detect(batch)
    assert len(provider.parse_failures) == 4


class StubOpenAIModule:
    """Stands in for the `openai` package: records how OpenAI(...) was called."""

    def __init__(self):
        self.kwargs = None

    def OpenAI(self, **kwargs):  # noqa: N802 — mirrors the SDK's own name
        self.kwargs = kwargs
        return object()


@pytest.fixture
def stub_openai(monkeypatch):
    stub = StubOpenAIModule()
    monkeypatch.setitem(sys.modules, "openai", stub)
    return stub


def test_the_real_client_is_built_with_a_raised_retry_budget(with_key, stub_openai):
    _build_client(with_key)
    assert stub_openai.kwargs["max_retries"] == VISION_MAX_RETRIES


def test_the_retry_budget_beats_the_sdk_default_of_two(with_key, stub_openai):
    _build_client(with_key)
    assert stub_openai.kwargs["max_retries"] > 2


def test_the_real_client_gets_the_configured_key(with_key, stub_openai):
    _build_client(with_key)
    assert stub_openai.kwargs["api_key"] == with_key.openai_api_key


def test_the_real_client_bounds_each_attempt(with_key, stub_openai):
    """An unbounded attempt multiplied by the retry budget is an hour-long hang."""
    _build_client(with_key)
    assert stub_openai.kwargs["timeout"] == VISION_TIMEOUT_SECONDS


def test_the_worst_case_hang_stays_bounded(with_key, stub_openai):
    """Timeout x retries is the real ceiling; keep it inside a coffee break."""
    _build_client(with_key)
    worst_case = stub_openai.kwargs["timeout"] * (stub_openai.kwargs["max_retries"] + 1)
    assert worst_case <= 15 * 60


def test_a_configured_run_feeds_the_score_engine(with_key):
    batch = tiles(2)
    result = run_vision(batch, config=with_key, client=FakeOpenAI(batch))
    assert to_score_vision(result.detections)["pool"] is True


def test_the_run_caches_across_two_invocations(with_key, tmp_path):
    """`make pipeline` twice in a row must cost money once."""
    batch = tiles(2)
    client = FakeOpenAI(batch)
    cache = Cache(tmp_path / "vision-cache")
    run_vision(batch, config=with_key, client=client, cache=cache)
    run_vision(batch, config=with_key, client=client, cache=cache)
    assert len(client.calls) == 1


# --- the SDK stays out of import time ---------------------------------------


def test_importing_the_provider_does_not_import_the_openai_sdk(monkeypatch):
    """No key here, so a module-level SDK import would break every other module
    that touches vision."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    saved = {
        name: module
        for name, module in sys.modules.items()
        if name == "openai" or name.startswith(("openai.", "houseaccount.vision"))
    }
    for name in saved:
        sys.modules.pop(name, None)
    try:
        importlib.import_module("houseaccount.vision.provider")
        importlib.import_module("houseaccount.vision.run")
        assert "openai" not in sys.modules
    finally:
        for name in [n for n in sys.modules if n.startswith("houseaccount.vision")]:
            sys.modules.pop(name, None)
        sys.modules.update(saved)


# --- banking answers as they arrive -----------------------------------------
#
# This stage is the only one that spends money. A run that died on its last
# request used to discard every answer it had already bought, so the retry paid
# twice for the same tiles — which is exactly what three killed runs did.


class ExplodingAfter:
    """Answers for the first `ok_tiles` tiles, then dies like a killed run."""

    def __init__(self, ok_tiles):
        self.ok_tiles = ok_tiles
        self.seen = 0

    def detect(self, tiles):
        batch = list(tiles)
        if self.seen >= self.ok_tiles:
            raise RuntimeError("killed mid-run")
        self.seen += len(batch)
        return [
            Detection(
                pams_pin=t.pams_pin,
                signal="pool",
                present=True,
                confidence=0.9,
                image_ref=t.image_ref,
                capture_date=t.capture_date,
            )
            for t in batch
        ]


def test_a_killed_run_keeps_the_answers_it_already_paid_for(tmp_path):
    cache = Cache(tmp_path / "cache")
    batch = tiles(16)
    dying = CachedVisionProvider(ExplodingAfter(8), cache=cache, chunk_tiles=4)
    with pytest.raises(RuntimeError):
        dying.detect(batch)

    # The resumed run must not re-ask for the tiles the dead one banked.
    survivor = RecordingProvider()
    CachedVisionProvider(survivor, cache=cache, chunk_tiles=4).detect(batch)
    reasked = {t.image_ref for call in survivor.calls for t in call}
    assert not (reasked & {t.image_ref for t in batch[:8]})


def test_banking_in_chunks_still_returns_every_detection(tmp_path):
    batch = tiles(16)
    chunked = CachedVisionProvider(
        RecordingProvider(), cache=Cache(tmp_path / "a"), chunk_tiles=4
    ).detect(batch)
    whole = CachedVisionProvider(RecordingProvider(), cache=Cache(tmp_path / "b")).detect(batch)
    assert [d.image_ref for d in chunked] == [d.image_ref for d in whole]


def test_without_chunking_the_old_single_pass_still_holds(tmp_path):
    inner = RecordingProvider()
    batch = tiles(16)
    CachedVisionProvider(inner, cache=Cache(tmp_path / "cache")).detect(batch)
    assert len(inner.calls) == 1
