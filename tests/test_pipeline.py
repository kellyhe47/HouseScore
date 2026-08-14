"""The end-to-end orchestrator: `make pipeline` (T009, R2.2/R2.3).

R2.2 grades *autonomy*: a fresh clone plus the documented env vars runs
harvest -> resolve -> vision -> score -> publish with zero manual steps. So the
thing under test here is the whole run, and the tests are deliberately few and
whole rather than many and shallow.

**Every seam is injected, because no test may open a socket.**
`run_pipeline(*, config, transport, client, ...)` takes the T001 transport that
every source fetches through and the vision client the provider calls, so the
entire five-stage run executes against scripted responses. `main()` is the thin
`python -m houseaccount.pipeline` wrapper that builds a `Config.from_env()` and
calls it.

**The zero-network run is the heart of the ticket.** R2.3 says a re-run is free.
The proof is a second run over the same warm cache with an `ExplodingTransport`
and an exploding vision client: any call at all fails the test. That single
assertion covers the cache-first contract across all five sources at once, and
it is why the cache lives in `Config`, not in each stage.

**Degradation is a completed run, not a failure.** No `CENSUS_API_KEY`, no
`OPENAI_API_KEY`, no rental register, an upstream that 404s — each one is
named in the manifest's `degradations`, logged once, and the run still publishes
every door. Only the parcel harvest is load-bearing: a run with no parcels is
not a degraded run, it is no run, and it raises.

Every artifact is written under `tmp_path`; the repo's real `data/` and `cache/`
are never touched.
"""

import json
import logging
import re
import time
from datetime import date, timedelta
from pathlib import Path

import pytest

from houseaccount import pipeline as pipeline_module
from houseaccount.config import Config
from houseaccount.cost import CostLedger
from houseaccount.http import Response, SourceError
from houseaccount.pipeline import (
    ACS_DUAL_INCOME_THRESHOLD,
    TERRITORY_CENTER,
    TERRITORY_TARGET,
    PipelineResult,
    code_version,
    main,
    run_pipeline,
)
from houseaccount.publish import DOORS_GEOJSON_NAME, EXCLUSION_REASON, RUN_MANIFEST_NAME
from houseaccount.resolve import ResolveReport
from houseaccount.scoring.engine import SOURCE_ACS, SOURCE_IMAGERY, SOURCE_SR1A
from houseaccount.scoring.weights import THRESHOLDS
from houseaccount.sources import parcels as parcels_module
from houseaccount.sources import permits as permits_module
import test_sales
from houseaccount.sources import sales as sales_module
from houseaccount.sources import tiger as tiger_module
from houseaccount.sources.acs import NO_KEY_REASON as ACS_NO_KEY_REASON
from houseaccount.sources.rental import DECLINATION_REASON as RENTAL_DECLINATION_REASON
from houseaccount.vision.run import NO_KEY_REASON as VISION_NO_KEY_REASON

AS_OF = date(2026, 8, 14)

#: Ramsey Golf & Country Club, near enough. Tests pass it explicitly so the
#: shipped default can move without rewriting the fixtures.
LON, LAT = -74.1560, 41.0447
CENTER = (LON, LAT)

#: A quarter of a block; four parcels laid out along it, plus one across town.
STEP = 0.0005

BG_GEOID = "340030113001"

PNG = b"\x89PNG\r\n\x1a\n ortho tile bytes"

PIN_MOVER = "0248_01101_00003"
PIN_STEADY = "0248_01101_00005"
PIN_INCOMPLETE = "0248_01101_00007"
PIN_FAR = "0248_02200_00001"
PIN_COMMERCIAL = "0248_01101_00009"


@pytest.fixture(autouse=True)
def no_ambient_credentials(monkeypatch):
    """`AcsSource` falls back to the environment, so the environment is cleared:
    a developer's real key must not change what this suite asserts."""
    for name in ("OPENAI_API_KEY", "CENSUS_API_KEY", "GOOGLE_MAPS_KEY"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def no_real_sleeping(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)


# --- the scripted sources -----------------------------------------------------


def square(lon, lat, step=0.0002):
    return {
        "type": "Polygon",
        "coordinates": [
            [[lon, lat], [lon + step, lat], [lon + step, lat + step], [lon, lat + step], [lon, lat]]
        ],
    }


def parcel_feature(
    pin,
    *,
    block="1101",
    lot="3",
    prop_class="2",
    loc="3 MAPLE ST",
    deed="2026-06-15",
    sale_price=1150000.0,
    yr_constr=1962,
    net_value=980000.0,
    acre=0.61,
    lon=LON,
    lat=LAT,
):
    return {
        "type": "Feature",
        "geometry": square(lon, lat),
        "properties": {
            "PAMS_PIN": pin,
            "PCL_MUN": "0248",
            "PCLBLOCK": block,
            "PCLLOT": lot,
            "PROP_CLASS": prop_class,
            "PROP_LOC": loc,
            "ZIP5": "07446",
            "DEED_DATE": deed,
            "SALE_PRICE": sale_price,
            "SALES_CODE": "",
            "YR_CONSTR": yr_constr,
            "NET_VALUE": net_value,
            "CALC_ACRE": acre,
        },
    }


#: Five parcels: three residential doors on the club's block, one residential
#: parcel across town (outside a 3-door territory), one commercial parcel that
#: is not ours to sell to at all.
PARCEL_FEATURES = [
    parcel_feature(PIN_MOVER, lot="3", loc="3 MAPLE ST", lon=LON, lat=LAT),
    parcel_feature(
        PIN_STEADY,
        lot="5",
        loc="5 MAPLE ST",
        deed="2004-03-15",
        net_value=620000.0,
        yr_constr=1958,
        lon=LON + STEP,
        lat=LAT,
    ),
    # The R9.4 door: the county record carries no deed, no year built and no
    # assessed value, so there is nothing to score it from.
    parcel_feature(
        PIN_INCOMPLETE,
        lot="7",
        loc="7 MAPLE ST",
        deed=None,
        sale_price=0.0,
        yr_constr=0,
        net_value=0.0,
        acre=0.0,
        lon=LON + 2 * STEP,
        lat=LAT,
    ),
    parcel_feature(
        PIN_FAR, block="2200", lot="1", loc="1 ELM ST", net_value=5000000.0, lon=LON + 0.03, lat=LAT
    ),
    parcel_feature(
        PIN_COMMERCIAL, lot="9", loc="9 MAPLE ST", prop_class="4A", net_value=4000000.0
    ),
]

PERMIT_ROWS = [
    {
        "recordid": "RAM-1",
        "block": "1101",
        "lot": "3",
        "permitdate": "2025-11-20T00:00:00.000",
        "permittypedesc": "ROOFING",
        "constcost": "21000",
    },
    {
        "recordid": "RAM-2",
        "block": "9999",
        "lot": "1",
        "permitdate": "2025-11-21T00:00:00.000",
        "permittypedesc": "ELECTRIC",
        "constcost": "800",
    },
]

#: 41 of 100 families with both parents in the labour force — above the 0.35
#: threshold, so the capacity prior fires and proves ACS reached the score.
ACS_ROWS = [
    ["B19013_001E", "B23007_001E", "B23007_004E", "B23007_015E", "B23007_026E", "state", "county", "tract", "block group"],
    ["145673", "100", "20", "11", "10", "34", "003", "011300", "1"],
]

BLOCK_GROUP_FEATURES = [
    {
        "type": "Feature",
        "geometry": square(LON - 0.01, LAT - 0.01, step=0.02),
        "properties": {
            "GEOID": BG_GEOID,
            "STATE": "34",
            "COUNTY": "003",
            "TRACT": "011300",
            "BLKGRP": "1",
        },
    }
]

ROUTES = {
    "parcels": parcels_module.QUERY_URL,
    "sales": "https://www.nj.gov/treasury/",
    "permits": permits_module.SOCRATA_PERMITS_URL,
    "tiger": tiger_module.TIGERWEB_BLOCK_GROUPS_URL,
    "acs": "https://api.census.gov/data/",
    "orthos": "https://maps.nj.gov/",
}


def ok(payload):
    return Response(status=200, body=json.dumps(payload).encode(), headers={})


def raw(body):
    return Response(status=200, body=body, headers={})


def refused(status=404):
    return Response(status=status, body=b"upstream said no", headers={})


def collection(features):
    return ok({"type": "FeatureCollection", "features": list(features)})


def sr1a_zip(*sales):
    """The SR1A archive, built from the layout-derived helpers in `test_sales`.

    Shared rather than re-implemented so there is exactly one place in the suite
    that knows what a 663-character record looks like.
    """
    return test_sales.zipped(*sales)


def sr1a_sale(**overrides):
    """One Ramsey sale record, defaulted onto the territory's mover parcel."""
    fields = {"BLOCK": "01101", "LOT": "00003", "PROPERTY-LOCATION": "3 MAPLE ST"}
    fields.update(overrides)
    return test_sales.ramsey(**fields)


class RoutingTransport:
    """Answers each source at its own URL, and records every call.

    An unrouted URL is a bug in the test, not a 404 — a source quietly reaching
    somewhere unexpected is exactly what this fake exists to catch.
    """

    def __init__(self, **overrides):
        self.answers = {
            "parcels": collection(PARCEL_FEATURES),
            "permits": ok(PERMIT_ROWS),
            "tiger": collection(BLOCK_GROUP_FEATURES),
            "acs": ok(ACS_ROWS),
            "orthos": raw(PNG),
            # A valid, empty archive by default: the SR1A register exists and is
            # readable, but holds no sale for this town, so every existing test
            # keeps scoring off the MOD-IV deed it was written against.
            "sales": raw(sr1a_zip()),
        }
        self.answers.update(overrides)
        self.calls = []

    def __call__(self, method, url, params, headers):
        self.calls.append(url)
        for name, prefix in ROUTES.items():
            if url.startswith(prefix):
                return self.answers[name]
        raise AssertionError(f"pipeline reached an unrouted url: {url}")

    def calls_to(self, name):
        return [url for url in self.calls if url.startswith(ROUTES[name])]


class ExplodingTransport:
    """Any call at all is a failure of the warm-cache contract."""

    def __call__(self, method, url, params, headers):
        raise AssertionError(f"transport was called for {url} — the cache was not consulted")


# --- the scripted vision client -----------------------------------------------


class _Message:
    def __init__(self, text):
        self.role = "assistant"
        self.content = text


class _Choice:
    def __init__(self, text):
        self.message = _Message(text)
        self.finish_reason = "stop"


class _Usage:
    prompt_tokens = 1200
    completion_tokens = 180


class _Reply:
    def __init__(self, text):
        self.choices = [_Choice(text)]
        self.usage = _Usage()


class _Completions:
    def __init__(self, client):
        self._client = client

    def create(self, **kwargs):
        self._client.calls.append(kwargs)
        label = "Tile image_ref: "
        refs = [
            block["text"][len(label) :]
            for message in kwargs["messages"]
            # The system message carries a plain string; only the user turn is
            # a block list, and only its text blocks label a tile.
            for block in (
                message["content"] if isinstance(message["content"], list) else ()
            )
            if block.get("type") == "text" and block.get("text", "").startswith(label)
        ]
        return _Reply(
            json.dumps(
                {
                    "detections": [
                        {"image_ref": ref, "signal": "pool", "present": True, "confidence": 0.93}
                        for ref in refs
                    ]
                }
            )
        )


class _Chat:
    def __init__(self, client):
        self.completions = _Completions(client)


class FakeVisionClient:
    """`client.chat.completions.create(**kw)` — the whole surface the provider
    uses.

    Claims a pool on every tile it is shown, which is only possible because the
    provider labels each image with its `image_ref`.
    """

    def __init__(self):
        self.calls = []
        self.chat = _Chat(self)


class _ExplodingCompletions:
    def create(self, **kwargs):
        raise AssertionError("the model was called — cached detections were not consulted")


class _ExplodingChat:
    completions = _ExplodingCompletions()


class ExplodingVisionClient:
    chat = _ExplodingChat()


# --- running the pipeline -----------------------------------------------------


def config_for(tmp_path, *, openai="sk-test", census="census-test"):
    return Config(
        openai_api_key=openai,
        census_api_key=census,
        google_maps_key=None,
        repo_root=tmp_path,
        cache_dir=tmp_path / "cache",
        data_dir=tmp_path / "data",
    )


def go(config, transport, *, client=None, target=3, **overrides):
    return run_pipeline(
        config=config,
        transport=transport,
        client=client if client is not None else FakeVisionClient(),
        as_of=AS_OF,
        center=CENTER,
        target=target,
        **overrides,
    )


def geojson(config):
    return json.loads((config.data_dir / DOORS_GEOJSON_NAME).read_text(encoding="utf-8"))


def manifest(config):
    return json.loads((config.data_dir / RUN_MANIFEST_NAME).read_text(encoding="utf-8"))


def by_pin(config):
    return {
        feature["properties"]["PAMS_PIN"]: feature["properties"] for feature in geojson(config)["features"]
    }


# --- the whole run ------------------------------------------------------------


def test_the_run_carries_every_stage_through_to_the_artifacts(tmp_path):
    """One assertion per stage, on the published artifact rather than on a call
    count: harvest, resolve, vision, score, publish."""
    config = config_for(tmp_path)

    result = go(config, RoutingTransport())

    doors = by_pin(config)
    assert isinstance(result, PipelineResult)
    # harvest + territory: the three nearest residential parcels, and only those
    assert set(doors) == {PIN_MOVER, PIN_STEADY, PIN_INCOMPLETE}
    # resolve: the permit on block 1101 lot 3 landed on its door
    assert result.report.permits_matched == 1
    assert isinstance(result.report, ResolveReport)
    # vision: a pool claim became a scored, re-openable evidence line
    assert any(
        item["source"] == SOURCE_IMAGERY and item["imagery"] is not None
        for item in doors[PIN_MOVER]["evidence"]
    )
    # ACS reached the score as a block-group prior
    assert any(item["source"] == SOURCE_ACS for item in doors[PIN_MOVER]["evidence"])
    # score + publish
    assert isinstance(doors[PIN_MOVER]["score"], int)
    assert result.published.geojson_path.is_file()
    assert result.published.sqlite_path.is_file()
    assert result.published.manifest_path.is_file()


def test_a_warm_cache_run_makes_no_network_calls_and_spends_nothing(tmp_path):
    """R2.3, the whole point of the cache: the second run is free."""
    config = config_for(tmp_path)
    go(config, RoutingTransport())
    first = (config.data_dir / DOORS_GEOJSON_NAME).read_bytes()

    result = go(config, ExplodingTransport(), client=ExplodingVisionClient())

    assert (config.data_dir / DOORS_GEOJSON_NAME).read_bytes() == first
    assert result.ledger.total_usd() == 0.0
    assert manifest(config)["cost_usd"] == 0.0


def test_two_runs_produce_identical_doors_geojson(tmp_path):
    """Idempotence is asserted on the bytes, so key order and float formatting
    are part of the contract rather than an accident (R13)."""
    config = config_for(tmp_path)

    go(config, RoutingTransport())
    first = (config.data_dir / DOORS_GEOJSON_NAME).read_bytes()
    first_manifest = manifest(config)

    go(config, RoutingTransport())
    second_manifest = manifest(config)

    assert (config.data_dir / DOORS_GEOJSON_NAME).read_bytes() == first
    assert second_manifest["code_version"] == first_manifest["code_version"]
    assert second_manifest["doors_total"] == first_manifest["doors_total"]


def test_the_manifest_records_the_inputs_the_run_was_computed_against(tmp_path):
    config = config_for(tmp_path)

    result = go(config, RoutingTransport())

    payload = manifest(config)
    # Median over the *territory's* class-2 parcels with a value on record —
    # the 620k and 980k doors. The 5M parcel across town is not in the territory
    # and the zero-value record is not an assessment.
    assert payload["territory_median_value"] == 800000.0
    assert payload["acs_dual_income_threshold"] == ACS_DUAL_INCOME_THRESHOLD
    assert payload["as_of"] == AS_OF.isoformat()
    assert {"parcel", "permits", "acs"} <= set(payload["retrieved"])
    assert all(
        re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) for value in payload["retrieved"].values()
    )
    assert payload["cost_usd"] == pytest.approx(result.ledger.total_usd())
    assert isinstance(result.ledger, CostLedger)
    assert payload["code_version"] == code_version()


def test_the_run_measures_the_permit_join_against_the_whole_municipality(tmp_path):
    """R3.2's rate is a municipality-wide question, and the harvest already has
    every municipal parcel in hand — so the pipeline hands that full set to
    `resolve` rather than only the three doors the territory selected.

    Two permits are in the window. RAM-1 (block 1101, lot 3) is a door and a
    municipal parcel; RAM-2 (block 9999) is neither. So the territory-scoped
    rate reads 1/1 = 1.0 and the municipal rate reads 1/2 = 0.5 — the two
    denominators, on one run, disagreeing.
    """
    config = config_for(tmp_path)

    result = go(config, RoutingTransport())

    assert result.report.permits_in_window == 2
    assert result.report.permits_matched_municipal == 1
    assert result.report.municipal_match_rate == pytest.approx(0.5)
    assert result.report.permit_match_rate == pytest.approx(1.0)


def test_the_manifest_publishes_both_match_rates(tmp_path):
    """A reader of the manifest must be able to tell the two apart."""
    config = config_for(tmp_path)

    go(config, RoutingTransport())

    block = manifest(config)["resolve"]
    assert {"permit_match_rate", "municipal_match_rate"} <= set(block)
    assert block["permit_match_rate"] == pytest.approx(1.0)
    assert block["municipal_match_rate"] == pytest.approx(0.5)
    assert block["municipal_match_rate"] != block["permit_match_rate"]


def test_the_incomplete_record_is_published_unscored_and_counted(tmp_path):
    """R9.4: "537 of 540" — the gap is explained and still counted."""
    config = config_for(tmp_path)

    result = go(config, RoutingTransport())

    doors = by_pin(config)
    assert doors[PIN_INCOMPLETE]["score"] is None
    assert doors[PIN_INCOMPLETE]["exclusion_reason"] == EXCLUSION_REASON
    assert doors[PIN_MOVER]["exclusion_reason"] is None
    assert (result.published.doors_total, result.published.doors_scored) == (3, 2)
    assert manifest(config)["doors_unscored"] == 1


def test_the_defaults_are_the_ramsey_territory():
    """Shipped defaults, so `make pipeline` needs no arguments (R2.2)."""
    lon, lat = TERRITORY_CENTER
    assert TERRITORY_TARGET == 540
    assert -74.25 < lon < -74.05 and 40.95 < lat < 41.15
    assert ACS_DUAL_INCOME_THRESHOLD == 0.35
    assert isinstance(code_version(), str) and code_version().strip()


# --- degradation --------------------------------------------------------------


def test_a_missing_census_key_degrades_the_acs_term_and_the_run_completes(tmp_path):
    config = config_for(tmp_path, census=None)

    result = go(config, RoutingTransport())

    assert result.report.acs_available is False
    assert ACS_NO_KEY_REASON in result.degradations
    assert ACS_NO_KEY_REASON in manifest(config)["degradations"]
    assert by_pin(config)[PIN_MOVER]["score"] is not None
    assert not any(
        item["source"] == SOURCE_ACS for item in by_pin(config)[PIN_MOVER]["evidence"]
    )


def test_a_missing_openai_key_skips_vision_without_fetching_a_single_tile(tmp_path):
    """Declining costs nothing, so it must not download 1,080 ortho tiles first."""
    config = config_for(tmp_path, openai=None)
    transport = RoutingTransport()

    result = go(config, transport, client=ExplodingVisionClient())

    assert transport.calls_to("orthos") == []
    assert VISION_NO_KEY_REASON in result.degradations
    assert VISION_NO_KEY_REASON in manifest(config)["degradations"]
    assert by_pin(config)[PIN_MOVER]["score"] is not None


def test_the_absent_rental_register_is_declared_on_every_run(tmp_path):
    config = config_for(tmp_path)

    result = go(config, RoutingTransport())

    assert RENTAL_DECLINATION_REASON in result.degradations
    assert RENTAL_DECLINATION_REASON in manifest(config)["degradations"]


@pytest.mark.parametrize("source", ["permits", "tiger", "acs", "orthos"])
def test_an_optional_source_refusing_is_logged_and_the_run_still_publishes(
    tmp_path, caplog, source
):
    config = config_for(tmp_path)

    with caplog.at_level(logging.WARNING):
        result = go(config, RoutingTransport(**{source: refused()}))

    assert result.published.doors_total == 3
    assert by_pin(config)[PIN_MOVER]["score"] is not None
    assert result.degradations, f"a refused {source} must be named in the manifest"
    assert manifest(config)["degradations"] == list(result.degradations)
    assert caplog.records, "a degraded run must say so once, at WARNING"


def test_an_empty_permit_feed_still_publishes_every_door(tmp_path):
    config = config_for(tmp_path)

    result = go(config, RoutingTransport(permits=ok([])))

    assert result.report.permits_total == 0
    assert result.report.permits_matched == 0
    assert len(geojson(config)["features"]) == 3


def test_a_territory_with_no_parcels_publishes_an_empty_collection(tmp_path):
    """Nothing to score is a result to publish, not a crash."""
    config = config_for(tmp_path)

    result = go(config, RoutingTransport(parcels=collection([])))

    assert geojson(config) == {"type": "FeatureCollection", "features": []}
    assert result.published.doors_total == 0
    assert manifest(config)["coverage"] == 0.0


def test_the_parcel_harvest_failing_stops_the_run(tmp_path):
    """Every other source is optional. Without parcels there is no territory,
    and publishing an empty town over a good one would be worse than failing."""
    config = config_for(tmp_path)

    with pytest.raises(SourceError):
        go(config, RoutingTransport(parcels=refused()))


# --- the MOD-IV deed vintage (T019) -------------------------------------------
#
# On the live Ramsey extract the newest deed is roughly 20 months old, so no door
# is inside the 90-day mover window and the model's highest-weighted group cannot
# fire at all. A reviewer reading the map cannot otherwise tell "no movers here
# right now" from "the mover rule is broken", so the run says which.
#
# Everything below is measured from the scripted harvest. Nothing is asserted
# against a constant the pipeline could have hardcoded: each test that pins a
# vintage also changes the deeds the transport serves, so a hardcoded answer
# fails at least one of them.

#: Read, never re-typed — the disclosure describes the rule the engine scores on.
MOVER_WINDOW_DAYS = THRESHOLDS["mover_90d_days"]

#: 90 days before AS_OF, and one day older: the inclusive edge of the window.
DEED_AT_THE_EDGE = "2026-05-16"
DEED_ONE_DAY_PAST = "2026-05-15"

#: The vintage the live extract actually has — every deed months out of window.
STALE_DEED = "2024-12-06"

#: The same three MAPLE doors as `PARCEL_FEATURES`, with nothing recent on them.
STALE_PARCEL_FEATURES = [
    parcel_feature(PIN_MOVER, lot="3", loc="3 MAPLE ST", deed=STALE_DEED, lon=LON, lat=LAT),
    parcel_feature(
        PIN_STEADY,
        lot="5",
        loc="5 MAPLE ST",
        deed="2004-03-15",
        net_value=620000.0,
        yr_constr=1958,
        lon=LON + STEP,
        lat=LAT,
    ),
    parcel_feature(
        PIN_INCOMPLETE,
        lot="7",
        loc="7 MAPLE ST",
        deed=None,
        sale_price=0.0,
        yr_constr=0,
        net_value=0.0,
        acre=0.0,
        lon=LON + 2 * STEP,
        lat=LAT,
    ),
]


def deed_vintage(config):
    return manifest(config)["deed_vintage"]


def mover_notes(reasons):
    """Every recorded degradation that is about the mover group."""
    return [note for note in reasons if re.search(r"mover", note, re.I)]


def evidence_types(config):
    return {item["type"] for props in by_pin(config).values() for item in props["evidence"]}


def test_the_manifest_discloses_the_deed_vintage_the_harvest_actually_saw(tmp_path):
    """The default extract has one 60-day-old deed among the three doors, so the
    Mover group can fire and there is nothing to disclaim."""
    config = config_for(tmp_path)

    result = go(config, RoutingTransport())

    block = deed_vintage(config)
    assert block["latest_deed_date"] == "2026-06-15"
    assert block["doors_in_mover_window"] == 1
    assert block["mover_window_days"] == MOVER_WINDOW_DAYS
    assert mover_notes(result.degradations) == []
    assert mover_notes(manifest(config)["degradations"]) == []


def test_the_counted_doors_are_the_doors_the_engine_scored_as_movers(tmp_path):
    """The disclosure and the score have to be talking about the same doors: a
    count that disagreed with the evidence trail would be its own defect."""
    config = config_for(tmp_path)

    go(config, RoutingTransport())

    movers = [
        pin
        for pin, props in by_pin(config).items()
        if any(item["type"] == "deed_recency" for item in props["evidence"])
    ]
    assert movers == [PIN_MOVER]
    assert deed_vintage(config)["doors_in_mover_window"] == len(movers)


def test_a_different_extract_reports_a_different_vintage_and_says_the_mover_group_could_not_fire(
    tmp_path,
):
    """The load-bearing "never hardcoded" test: same code, older deeds, a
    different published date — and the note the live run needs."""
    config = config_for(tmp_path)

    result = go(config, RoutingTransport(parcels=collection(STALE_PARCEL_FEATURES)))

    block = deed_vintage(config)
    assert block["latest_deed_date"] == STALE_DEED
    assert block["doors_in_mover_window"] == 0
    assert "deed_recency" not in evidence_types(config), "the group is unearnable here"

    notes = mover_notes(result.degradations)
    assert len(notes) == 1, notes
    note = notes[0]
    assert isinstance(note, str)
    assert str(int(MOVER_WINDOW_DAYS)) in note, note
    assert STALE_DEED in note, note
    # Recorded in the same voice, and the same list, as a declined provider.
    assert manifest(config)["degradations"] == list(result.degradations)
    assert note in manifest(config)["degradations"]


def test_the_latest_deed_is_measured_across_the_municipality_not_the_territory(tmp_path):
    """The vintage is a statement about the extract, so it is measured over every
    municipal parcel the harvest holds — while the count that decides the note
    stays territory-scoped."""
    config = config_for(tmp_path)
    fresh_but_far = parcel_feature(
        PIN_FAR,
        block="2200",
        lot="1",
        loc="1 ELM ST",
        deed="2026-07-01",
        net_value=5000000.0,
        lon=LON + 0.03,
        lat=LAT,
    )

    result = go(
        config, RoutingTransport(parcels=collection([*STALE_PARCEL_FEATURES, fresh_but_far]))
    )

    assert set(by_pin(config)) == {PIN_MOVER, PIN_STEADY, PIN_INCOMPLETE}
    block = deed_vintage(config)
    assert block["latest_deed_date"] == "2026-07-01"
    assert block["doors_in_mover_window"] == 0
    assert len(mover_notes(result.degradations)) == 1


def test_the_counted_window_is_inclusive_at_its_edge(tmp_path):
    """A deed exactly `mover_90d_days` old is inside the window the engine scores
    on, so it is inside the window the manifest counts."""
    config = config_for(tmp_path)
    edge = [
        parcel_feature("0248_01101_00011", lot="11", loc="11 MAPLE ST", deed=DEED_AT_THE_EDGE),
        parcel_feature(
            "0248_01101_00013",
            lot="13",
            loc="13 MAPLE ST",
            deed=DEED_ONE_DAY_PAST,
            lon=LON + STEP,
            lat=LAT,
        ),
    ]

    result = go(config, RoutingTransport(parcels=collection(edge)), target=2)

    block = deed_vintage(config)
    assert block["latest_deed_date"] == DEED_AT_THE_EDGE
    assert block["doors_in_mover_window"] == 1
    assert mover_notes(result.degradations) == []


def test_an_extract_with_no_readable_deed_reports_no_vintage_at_all(tmp_path):
    """Sad path: every deed unparseable. There is no latest date, and the note
    must not print a placeholder where a date would go."""
    config = config_for(tmp_path)
    unreadable = [
        parcel_feature(PIN_MOVER, lot="3", loc="3 MAPLE ST", deed="not a date"),
        parcel_feature(
            PIN_STEADY, lot="5", loc="5 MAPLE ST", deed="", lon=LON + STEP, lat=LAT
        ),
    ]

    result = go(config, RoutingTransport(parcels=collection(unreadable)), target=2)

    block = deed_vintage(config)
    assert block["latest_deed_date"] is None
    assert block["doors_in_mover_window"] == 0

    notes = mover_notes(result.degradations)
    assert len(notes) == 1, notes
    assert not re.search(r"\bNone\b|\bnull\b|\bundefined\b", notes[0]), notes[0]


def test_an_empty_territory_discloses_the_gap_without_crashing(tmp_path):
    """No parcels is a publishable run (see above), so it is also a disclosable
    one: nothing measured, and the note that nothing could fire."""
    config = config_for(tmp_path)

    result = go(config, RoutingTransport(parcels=collection([])))

    block = deed_vintage(config)
    assert block["latest_deed_date"] is None
    assert block["doors_in_mover_window"] == 0
    assert block["mover_window_days"] == MOVER_WINDOW_DAYS
    assert result.published.doors_total == 0
    assert len(mover_notes(manifest(config)["degradations"])) == 1


def test_the_disclosure_never_reaches_a_door(tmp_path):
    """This ticket is disclosure, not rescoring. Nothing about the vintage may
    become an evidence line or move a point — the twelve golden fixtures still
    own the mover rule."""
    config = config_for(tmp_path)

    go(config, RoutingTransport(parcels=collection(STALE_PARCEL_FEATURES)))

    for props in by_pin(config).values():
        if props["score"] is None:
            continue
        points = sum(item["points"] for item in props["evidence"])
        assert props["score"] == max(0, min(100, points))
        for item in props["evidence"]:
            assert not re.search(r"vintage|extract|could not fire", item["sentence"], re.I), item


# --- the command line ---------------------------------------------------------


def test_main_runs_the_pipeline_against_the_environment_and_returns_zero(tmp_path, monkeypatch):
    seen = {}

    def recorder(*, config, **kwargs):
        seen["config"] = config
        seen["kwargs"] = kwargs
        return "ran"

    monkeypatch.setattr(pipeline_module, "run_pipeline", recorder)

    assert main([]) == 0
    assert isinstance(seen["config"], Config)


def test_the_module_is_runnable_as_python_dash_m(tmp_path):
    """`make pipeline` is literally `python -m houseaccount.pipeline` (R2.2)."""
    source = Path(pipeline_module.__file__).read_text(encoding="utf-8")

    assert re.search(r'if __name__ == "__main__":', source)
    assert re.search(r"main\(\)", source)


# --- the SR1A sales register (T020) -------------------------------------------
#
# Ticket 019 disclosed that the county extract was twenty months stale and the
# heaviest group in the model could not fire. This source is the fix, and these
# tests are about the seam between it and the run: the register supersedes a
# stale deed, its absence costs freshness rather than the run, and whatever the
# register cannot reach is still disclosed rather than quietly dropped.


def sales_register(config):
    return deed_vintage(config)["sales_register"]


def test_a_fresh_sale_makes_the_mover_group_fire_on_a_stale_extract(tmp_path):
    """The whole ticket, end to end: the same stale extract that scored zero
    movers in T019, plus the register, scores a mover."""
    config = config_for(tmp_path)

    result = go(
        config,
        RoutingTransport(
            parcels=collection(STALE_PARCEL_FEATURES),
            sales=raw(sr1a_zip(sr1a_sale(**{"DEED-DATE": "260601"}))),
        ),
    )

    movers = [
        pin
        for pin, props in by_pin(config).items()
        if any(item["type"] == "deed_recency" for item in props["evidence"])
    ]
    assert movers == [PIN_MOVER], "the register should have moved exactly the sold door"
    assert deed_vintage(config)["doors_in_mover_window"] == 1
    assert result.report.sales_applied == 1


def test_the_mover_evidence_names_the_register_not_the_parcel_record(tmp_path):
    config = config_for(tmp_path)

    go(
        config,
        RoutingTransport(
            parcels=collection(STALE_PARCEL_FEATURES),
            sales=raw(sr1a_zip(sr1a_sale(**{"DEED-DATE": "260601"}))),
        ),
    )

    (recency,) = [
        item for item in by_pin(config)[PIN_MOVER]["evidence"] if item["type"] == "deed_recency"
    ]
    assert recency["source"] == SOURCE_SR1A


def test_the_manifest_reports_the_register_it_actually_read(tmp_path):
    config = config_for(tmp_path)

    go(
        config,
        RoutingTransport(
            parcels=collection(STALE_PARCEL_FEATURES),
            sales=raw(sr1a_zip(sr1a_sale(**{"DEED-DATE": "260601"}))),
        ),
    )

    block = sales_register(config)
    assert block["latest_sale_date"] == "2026-06-01"
    assert block["doors_superseding_modiv"] == 1
    assert block["source_files"] == [
        "https://www.nj.gov/treasury/taxation/lpt/statdata/YTDSR1A2026.zip"
    ]
    # The feed's vintage is now the register's date, not the county's.
    assert deed_vintage(config)["latest_deed_date"] == "2026-06-01"


def test_the_unreachable_top_band_is_disclosed_rather_than_left_looking_broken(tmp_path):
    """The residual limit T019 must not lose. A deed reaches the published
    register only after county recording and the state's next release, so the
    freshest sale a run can see is already weeks old and the 100-point tier
    cannot be earned — a property of the source's cadence, not of the rule."""
    config = config_for(tmp_path)

    result = go(
        config,
        RoutingTransport(
            parcels=collection(STALE_PARCEL_FEATURES),
            sales=raw(sr1a_zip(sr1a_sale(**{"DEED-DATE": "260601"}))),
        ),
    )

    block = deed_vintage(config)
    assert block["doors_in_mover_window"] == 1
    assert block["doors_in_top_band"] == 0
    assert block["top_band_days"] == int(THRESHOLDS["mover_30d_days"])

    (note,) = mover_notes(result.degradations)
    assert "2026-06-01" in note
    assert "recording" in note.lower()
    assert note in manifest(config)["degradations"]


def test_a_sale_inside_the_top_band_earns_the_top_band_and_says_nothing(tmp_path):
    """The disclosure above is a measurement, not a fixed string: give the run a
    sale from last week and it disappears."""
    config = config_for(tmp_path)
    last_week = (AS_OF - timedelta(days=7)).strftime("%y%m%d")

    result = go(
        config,
        RoutingTransport(
            parcels=collection(STALE_PARCEL_FEATURES),
            sales=raw(sr1a_zip(sr1a_sale(**{"DEED-DATE": last_week}))),
        ),
    )

    assert deed_vintage(config)["doors_in_top_band"] == 1
    assert mover_notes(result.degradations) == []
    assert by_pin(config)[PIN_MOVER]["score"] == 100


def test_the_register_refusing_degrades_the_run_without_ending_it(tmp_path):
    """Not load-bearing: MOD-IV still carries a deed for every parcel, so a
    refused download costs the run freshness, not its Mover group."""
    config = config_for(tmp_path)

    result = go(config, RoutingTransport(sales=refused(503)))

    # Every door the extract can support still publishes: 3 doors, the third
    # being the R9.4 record the county cannot support a score for at all.
    assert (result.published.doors_total, result.published.doors_scored) == (3, 2)
    (note,) = [n for n in result.degradations if "SR1A" in n]
    assert "MOD-IV" in note, "the note must name what the run fell back to"
    assert sales_register(config) == {
        "latest_sale_date": None,
        "source_files": [],
        "doors_superseding_modiv": 0,
    }


def test_the_register_is_read_once_and_never_again_on_a_warm_cache(tmp_path):
    """R2.3 covers this source too — and the 113 MB download is the one it
    matters most for."""
    config = config_for(tmp_path)
    transport = RoutingTransport()
    go(config, transport)
    assert transport.calls_to("sales")

    go(config, ExplodingTransport(), client=ExplodingVisionClient())


def test_no_identity_byte_from_the_register_reaches_the_cache_or_the_artifacts(tmp_path):
    """The register is statewide and its layout reserves grantor/grantee identity
    columns. R11.1 says none of it may land on disk — and the raw download is
    deliberately never cached, only the distilled projection."""
    config = config_for(tmp_path)

    go(
        config,
        RoutingTransport(
            parcels=collection(STALE_PARCEL_FEATURES),
            sales=raw(sr1a_zip(sr1a_sale(**{"DEED-DATE": "260601"}))),
        ),
    )

    written = []
    for root in (config.cache_dir, config.data_dir):
        for path in root.rglob("*"):
            if path.is_file():
                written.append(path.read_bytes().decode("utf-8", "replace").upper())
    haystack = "\n".join(written)
    assert haystack, "the run should have written something"
    for junk in test_sales.IDENTITY_COLUMNS.values():
        assert junk.upper() not in haystack
