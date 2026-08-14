"""Census ACS5 block-group source (T005).

Ground truth (Phase 0): `https://api.census.gov/data/2022/acs/acs5`, block-group
level, New Jersey = state `34`, Bergen County = `003`. Tables B23007 (presence of
own children by parents' employment status — the dual-income proxy) and B19013
(median household income). The response is a header row followed by data rows.

A free API key is required and keyless calls are blocked upstream; `CENSUS_API_KEY`
is unset in this environment, so **the declination path is the one that actually
runs today**. It must return a result, not raise, and a caller with no ACS data
must still be able to score (the ACS component simply contributes 0).

Everything here is neighbourhood-level. PRD R6.2 forbids household-level claims
from ACS data anywhere in this system.

No network: scripted transports only.
"""

import json
import time
from datetime import date

import pytest

from houseaccount.cache import Cache
from houseaccount.http import Response
from houseaccount.scoring.engine import ScoreInput, score_door
from houseaccount.sources import acs as acs_module
from houseaccount.sources.acs import (
    ACS_YEAR,
    COUNTY_BERGEN,
    DUAL_INCOME_DENOMINATOR_VARIABLE,
    DUAL_INCOME_NUMERATOR_VARIABLES,
    MEDIAN_INCOME_VARIABLE,
    STATE_NJ,
    AcsSource,
)

API_KEY = "census-key-for-tests"

#: Census's "no estimate" sentinel, as it really appears in a response.
NULL_SENTINEL = "-666666666"

HEADER = [
    MEDIAN_INCOME_VARIABLE,
    DUAL_INCOME_DENOMINATOR_VARIABLE,
    *DUAL_INCOME_NUMERATOR_VARIABLES,
    "state",
    "county",
    "tract",
    "block group",
]

#: 210 + 90 + 110 = 410 of 1000 families -> 0.41 dual-income (the PRD's example).
BG_ONE = ["145673", "1000", "210", "90", "110", "34", "003", "011300", "1"]
BG_ONE_GEOID = "340030113001"

#: 100 + 60 + 40 = 200 of 800 -> 0.25.
BG_TWO = ["98250", "800", "100", "60", "40", "34", "003", "011400", "2"]
BG_TWO_GEOID = "340030114002"


def canned(*rows, header=None):
    return Response(
        status=200,
        body=json.dumps([list(header or HEADER), *[list(r) for r in rows]]).encode(),
        headers={},
    )


class ScriptedTransport:
    def __init__(self, *responses):
        self.queued = list(responses)
        self.calls = []

    def __call__(self, method, url, params, headers):
        self.calls.append({"method": method, "url": url, "params": dict(params or {})})
        return self.queued.pop(0) if len(self.queued) > 1 else self.queued[0]


class ExplodingTransport:
    def __call__(self, method, url, params, headers):
        raise AssertionError(f"transport was called for {url} — nothing should have been fetched")


@pytest.fixture(autouse=True)
def no_real_sleeping(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)


@pytest.fixture
def keyed_env(monkeypatch):
    """A machine that does have a Census key."""
    monkeypatch.setenv("CENSUS_API_KEY", API_KEY)
    return monkeypatch


@pytest.fixture
def keyless_env(monkeypatch):
    """The real state of this machine: no Census key at all."""
    monkeypatch.delenv("CENSUS_API_KEY", raising=False)
    return monkeypatch


@pytest.fixture
def cache(tmp_path):
    return Cache(tmp_path / "cache")


# --- the request ------------------------------------------------------------


def test_requests_the_acs5_endpoint_for_the_configured_year(keyed_env, cache):
    transport = ScriptedTransport(canned(BG_ONE))
    AcsSource(cache=cache, transport=transport).fetch()

    url = transport.calls[0]["url"]
    assert url.startswith("https://api.census.gov/data/")
    assert f"/{ACS_YEAR}/acs/acs5" in url


def test_requests_block_groups_in_the_configured_geography(keyed_env, cache):
    transport = ScriptedTransport(canned(BG_ONE))
    AcsSource(cache=cache, transport=transport).fetch(state=STATE_NJ, county=COUNTY_BERGEN)

    params = transport.calls[0]["params"]
    assert "block group" in str(params.get("for"))
    scope = str(params.get("in"))
    assert f"state:{STATE_NJ}" in scope
    assert f"county:{COUNTY_BERGEN}" in scope


def test_requests_every_variable_the_formula_needs(keyed_env, cache):
    transport = ScriptedTransport(canned(BG_ONE))
    AcsSource(cache=cache, transport=transport).fetch()

    requested = {v.strip() for v in str(transport.calls[0]["params"]["get"]).split(",")}
    assert requested >= {
        MEDIAN_INCOME_VARIABLE,
        DUAL_INCOME_DENOMINATOR_VARIABLE,
        *DUAL_INCOME_NUMERATOR_VARIABLES,
    }


def test_the_key_is_sent_and_comes_from_the_environment(keyed_env, cache):
    transport = ScriptedTransport(canned(BG_ONE))
    AcsSource(cache=cache, transport=transport).fetch()
    assert API_KEY in {str(v) for v in transport.calls[0]["params"].values()}


def test_an_explicit_key_overrides_the_environment(keyless_env, cache):
    transport = ScriptedTransport(canned(BG_ONE))
    AcsSource(cache=cache, transport=transport, api_key="explicit-key").fetch()
    assert "explicit-key" in {str(v) for v in transport.calls[0]["params"].values()}


def test_warm_cache_performs_zero_transport_calls(keyed_env, cache):
    cold = ScriptedTransport(canned(BG_ONE, BG_TWO))
    first = AcsSource(cache=cache, transport=cold).fetch()

    warm = AcsSource(cache=cache, transport=ExplodingTransport()).fetch()
    assert set(warm.block_groups) == set(first.block_groups)


# --- the documented formula -------------------------------------------------


def test_dual_income_pct_is_both_parents_in_labour_force_over_families(keyed_env, cache):
    transport = ScriptedTransport(canned(BG_ONE))
    result = AcsSource(cache=cache, transport=transport).fetch()

    stats = result.block_groups[BG_ONE_GEOID]
    assert stats.dual_income_pct == pytest.approx((210 + 90 + 110) / 1000)


def test_median_household_income_comes_straight_from_b19013(keyed_env, cache):
    transport = ScriptedTransport(canned(BG_ONE))
    result = AcsSource(cache=cache, transport=transport).fetch()
    assert result.block_groups[BG_ONE_GEOID].median_hh_income == pytest.approx(145673)


def test_every_row_becomes_a_block_group_keyed_by_geoid(keyed_env, cache):
    transport = ScriptedTransport(canned(BG_ONE, BG_TWO))
    result = AcsSource(cache=cache, transport=transport).fetch()

    assert set(result.block_groups) == {BG_ONE_GEOID, BG_TWO_GEOID}
    assert result.block_groups[BG_TWO_GEOID].dual_income_pct == pytest.approx(0.25)


def test_geography_columns_are_kept_alongside_the_geoid(keyed_env, cache):
    transport = ScriptedTransport(canned(BG_ONE))
    stats = AcsSource(cache=cache, transport=transport).fetch().block_groups[BG_ONE_GEOID]

    assert (stats.state, stats.county, stats.tract, stats.block_group) == (
        "34",
        "003",
        "011300",
        "1",
    )


def test_columns_are_read_by_name_not_by_position(keyed_env, cache):
    """Census does not promise column order; a reordered response must parse the same."""
    order = [8, 0, 3, 5, 1, 6, 2, 7, 4]
    shuffled_header = [HEADER[i] for i in order]
    shuffled_row = [BG_ONE[i] for i in order]

    transport = ScriptedTransport(canned(shuffled_row, header=shuffled_header))
    stats = AcsSource(cache=cache, transport=transport).fetch().block_groups[BG_ONE_GEOID]

    assert stats.dual_income_pct == pytest.approx(0.41)
    assert stats.median_hh_income == pytest.approx(145673)


def test_the_formula_is_documented_in_the_module():
    doc = acs_module.__doc__ or ""
    assert "B23007" in doc
    for variable in (*DUAL_INCOME_NUMERATOR_VARIABLES, DUAL_INCOME_DENOMINATOR_VARIABLE):
        assert variable in doc, f"{variable} must be named in the documented formula"
    assert "block group" in doc.lower(), "R6.2: this is a neighbourhood statistic, say so"


# --- sad paths --------------------------------------------------------------


def test_the_null_sentinel_becomes_none_not_a_negative_income(keyed_env, cache):
    sentinel_row = [NULL_SENTINEL, *BG_ONE[1:]]
    transport = ScriptedTransport(canned(sentinel_row))
    stats = AcsSource(cache=cache, transport=transport).fetch().block_groups[BG_ONE_GEOID]

    assert stats.median_hh_income is None
    assert stats.dual_income_pct == pytest.approx(0.41), "one bad column, one lost statistic"


@pytest.mark.parametrize("denominator", [NULL_SENTINEL, "0"], ids=["sentinel", "zero"])
def test_an_unusable_denominator_yields_no_percentage(keyed_env, cache, denominator):
    bad_row = [BG_ONE[0], denominator, *BG_ONE[2:]]
    transport = ScriptedTransport(canned(bad_row))
    stats = AcsSource(cache=cache, transport=transport).fetch().block_groups[BG_ONE_GEOID]

    assert stats.dual_income_pct is None
    assert stats.median_hh_income == pytest.approx(145673)


def test_a_non_numeric_measure_yields_none_and_keeps_the_block_group(keyed_env, cache):
    garbled = [BG_ONE[0], BG_ONE[1], "N/A", *BG_ONE[3:]]
    transport = ScriptedTransport(canned(garbled))
    result = AcsSource(cache=cache, transport=transport).fetch()

    assert result.available is True
    assert result.block_groups[BG_ONE_GEOID].dual_income_pct is None


def test_a_row_of_the_wrong_width_is_skipped_and_the_rest_survive(keyed_env, cache):
    transport = ScriptedTransport(canned(BG_ONE[:4], BG_TWO))
    result = AcsSource(cache=cache, transport=transport).fetch()

    assert set(result.block_groups) == {BG_TWO_GEOID}


def test_a_header_only_response_is_available_but_empty(keyed_env, cache):
    transport = ScriptedTransport(canned())
    result = AcsSource(cache=cache, transport=transport).fetch()

    assert result.available is True
    assert dict(result.block_groups) == {}


def test_a_successful_fetch_reports_no_declination(keyed_env, cache):
    transport = ScriptedTransport(canned(BG_ONE))
    assert AcsSource(cache=cache, transport=transport).fetch().reason is None


# --- declination: the path that actually runs here --------------------------


def test_without_a_key_the_source_declines_instead_of_raising(keyless_env, cache):
    result = AcsSource(cache=cache, transport=ExplodingTransport()).fetch()

    assert result.available is False
    assert isinstance(result.reason, str) and result.reason.strip()
    assert dict(result.block_groups) == {}


@pytest.mark.parametrize("blank", ["", "   "], ids=["empty", "whitespace"])
def test_a_blank_key_counts_as_no_key(monkeypatch, cache, blank):
    monkeypatch.setenv("CENSUS_API_KEY", blank)
    result = AcsSource(cache=cache, transport=ExplodingTransport()).fetch()
    assert result.available is False


def test_declining_never_touches_the_network(keyless_env, cache):
    """ExplodingTransport is the assertion: a keyless fetch must not call out."""
    AcsSource(cache=cache, transport=ExplodingTransport()).fetch()


def test_an_upstream_refusal_degrades_rather_than_crashing(keyed_env, cache):
    refused = ScriptedTransport(Response(status=403, body=b"key required", headers={}))
    result = AcsSource(cache=cache, transport=refused).fetch()

    assert result.available is False
    assert isinstance(result.reason, str) and result.reason.strip()


def test_a_caller_with_no_acs_data_can_still_score(keyless_env, cache):
    """The ACS component contributes 0; the door still scores and still explains."""
    result = AcsSource(cache=cache, transport=ExplodingTransport()).fetch()
    stats = result.block_groups.get("340030113001")
    assert stats is None

    scored = score_door(
        ScoreInput(
            as_of=date(2026, 8, 14),
            territory_median_value=500_000.0,
            acs_dual_income_threshold=0.35,
            net_value=600_000.0,
            yr_constr=1965,
            dual_income_pct=None,
            median_hh_income=None,
        )
    )
    assert scored.score > 0
    assert "acs_dual_income_prior" not in {item.type for item in scored.evidence}
