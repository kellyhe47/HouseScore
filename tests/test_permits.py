"""NJ construction permits, Socrata SODA (T005).

Ground truth (verified live, Phase 0): `data.nj.gov/resource/w9se-dmra.json?comu=0248`
returns Ramsey's permits with `block`/`lot`/`permitdate`/`permittypedesc`/`constcost` —
and **no contractor field at all**. That absence is the interesting part of this
module: V1's churn rule once wanted a contractor field,
and from *this* source it is always None, deliberately, so the churn bonus is
structurally unearnable on real data (PRD R6: "permits lacking a contractor field
are excluded from churn, not from permit points").

The join keys are block/lot — there is no address on these records. T006 joins them
to parcel PCLBLOCK/PCLLOT.

No network: every test drives a scripted transport, exactly as tests/test_http.py does.
"""

import json
import time
from datetime import date, timedelta

import pytest

from houseaccount.cache import Cache
from houseaccount.http import Response
from houseaccount.sources import permits as permits_module
from houseaccount.sources.permits import (
    SOCRATA_PERMITS_URL,
    PermitRecord,
    PermitSource,
    permits_within,
)

AS_OF = date(2026, 8, 14)

#: A real record, copied verbatim from the live service.
REAL_RECORD = {
    "comu": "0248",
    "treasurycode": "0248",
    "muniname": "RAMSEY",
    "munitype": "BOROUGH",
    "county": "BERGEN",
    "recordid": "10000002",
    "block": "2702",
    "lot": "15",
    "permitno": "20220821",
    "status": "P",
    "permitstatusdesc": "Permit",
    "permitdate": "2022-11-29T00:00:00.000",
    "permittype": "06",
    "permittypedesc": "Alteration",
    "certcount": "0",
    "buildfee": "0",
    "dcafee": "9",
    "otherfee": "125",
    "totalfee": "134",
    "cubic": "0",
    "squarefeet": "0",
    "constcost": "4500",
    "usegroup": "R-5",
    "usegroupdesc": "International Residential Code",
    "source": "SDL",
    "pk": "024810000002",
}


def row(record_id, block="2702", lot="15", permitdate="2022-11-29T00:00:00.000", **extra):
    record = dict(REAL_RECORD, recordid=record_id, block=block, lot=lot, permitdate=permitdate)
    record.update(extra)
    return record


def ok(payload):
    return Response(status=200, body=json.dumps(payload).encode(), headers={})


class ScriptedTransport:
    """Returns each queued response in turn; records every call it received."""

    def __init__(self, *responses):
        self.queued = list(responses)
        self.calls = []

    def __call__(self, method, url, params, headers):
        self.calls.append({"method": method, "url": url, "params": dict(params or {})})
        return self.queued.pop(0) if len(self.queued) > 1 else self.queued[0]


class ExplodingTransport:
    """Any call at all is a failure of the cache-first contract."""

    def __call__(self, method, url, params, headers):
        raise AssertionError(f"transport was called for {url} — cache was not consulted")


@pytest.fixture(autouse=True)
def no_real_sleeping(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)


@pytest.fixture
def cache(tmp_path):
    return Cache(tmp_path / "cache")


# --- paging -----------------------------------------------------------------


def test_two_pages_yield_all_records(cache):
    transport = ScriptedTransport(
        ok([row("1"), row("2")]),
        ok([row("3")]),
    )
    source = PermitSource(cache=cache, transport=transport, page_size=2)
    records = source.fetch(comu="0248")

    assert [r.record_id for r in records] == ["1", "2", "3"]
    assert len(transport.calls) == 2, "a short page ends the walk"


def test_paging_uses_limit_and_offset(cache):
    transport = ScriptedTransport(ok([row("1"), row("2")]), ok([row("3")]))
    PermitSource(cache=cache, transport=transport, page_size=2).fetch(comu="0248")

    limits = [int(call["params"]["$limit"]) for call in transport.calls]
    offsets = [int(call["params"]["$offset"]) for call in transport.calls]
    assert limits == [2, 2]
    assert offsets == [0, 2]


def test_requests_the_socrata_endpoint_filtered_to_the_municipality(cache):
    transport = ScriptedTransport(ok([]))
    PermitSource(cache=cache, transport=transport, page_size=2).fetch(comu="0248")

    call = transport.calls[0]
    assert call["url"] == SOCRATA_PERMITS_URL
    assert call["params"]["comu"] == "0248"


def test_a_full_final_page_is_followed_by_one_empty_page(cache):
    """Exactly-full page can't be assumed final — the walk needs the empty page."""
    transport = ScriptedTransport(ok([row("1"), row("2")]), ok([row("3"), row("4")]), ok([]))
    records = PermitSource(cache=cache, transport=transport, page_size=2).fetch(comu="0248")

    assert [r.record_id for r in records] == ["1", "2", "3", "4"]
    assert len(transport.calls) == 3


def test_empty_first_page_yields_nothing_and_stops(cache):
    transport = ScriptedTransport(ok([]))
    assert list(PermitSource(cache=cache, transport=transport, page_size=2).fetch()) == []
    assert len(transport.calls) == 1


def test_warm_cache_performs_zero_transport_calls(cache):
    cold = ScriptedTransport(ok([row("1"), row("2")]), ok([row("3")]))
    first = PermitSource(cache=cache, transport=cold, page_size=2).fetch(comu="0248")

    warm = PermitSource(cache=cache, transport=ExplodingTransport(), page_size=2)
    assert [r.record_id for r in warm.fetch(comu="0248")] == [r.record_id for r in first]


def test_since_narrows_the_query_by_permit_date(cache):
    transport = ScriptedTransport(ok([]))
    since = date(2024, 8, 14)
    PermitSource(cache=cache, transport=transport, page_size=2).fetch(comu="0248", since=since)

    where = str(transport.calls[0]["params"].get("$where") or "")
    assert "permitdate" in where
    assert since.isoformat() in where


def test_without_since_no_date_filter_is_sent(cache):
    transport = ScriptedTransport(ok([]))
    PermitSource(cache=cache, transport=transport, page_size=2).fetch(comu="0248")
    assert not transport.calls[0]["params"].get("$where")


# --- record mapping ---------------------------------------------------------


def test_a_real_record_maps_field_for_field(cache):
    transport = ScriptedTransport(ok([REAL_RECORD]), ok([]))
    (record,) = PermitSource(cache=cache, transport=transport, page_size=100).fetch()

    assert record.record_id == "10000002"
    assert record.block == "2702"
    assert record.lot == "15"
    assert record.date == date(2022, 11, 29)
    assert record.type == "Alteration"
    assert record.cost == pytest.approx(4500)


PERMIT_FIELDS = ("record_id", "block", "lot", "date", "type", "contractor", "cost")


@pytest.mark.parametrize("field_name", PERMIT_FIELDS)
def test_permit_record_carries_the_agreed_fields(field_name):
    assert field_name in PermitRecord.__dataclass_fields__


def test_permit_record_carries_nothing_beyond_the_agreed_fields():
    assert set(PermitRecord.__dataclass_fields__) == set(PERMIT_FIELDS)


@pytest.mark.parametrize(
    "record,expected_cost",
    [
        (row("1", constcost="4500"), 4500.0),
        (row("2", constcost=""), 0.0),
        (row("3", constcost="not a number"), 0.0),
    ],
    ids=["numeric", "blank", "garbage"],
)
def test_cost_degrades_to_zero_rather_than_raising(cache, record, expected_cost):
    transport = ScriptedTransport(ok([record]), ok([]))
    (mapped,) = PermitSource(cache=cache, transport=transport, page_size=100).fetch()
    assert mapped.cost == pytest.approx(expected_cost)


@pytest.mark.parametrize(
    "record,expected_type",
    [
        (row("1"), "Alteration"),
        (row("2", permittypedesc=""), "06"),
    ],
    ids=["description", "code-only-fallback"],
)
def test_type_prefers_the_human_readable_description(cache, record, expected_type):
    transport = ScriptedTransport(ok([record]), ok([]))
    (mapped,) = PermitSource(cache=cache, transport=transport, page_size=100).fetch()
    assert mapped.type == expected_type


def test_an_unparseable_permit_date_becomes_none_not_an_exception(cache):
    transport = ScriptedTransport(ok([row("1", permitdate="")]), ok([]))
    (mapped,) = PermitSource(cache=cache, transport=transport, page_size=100).fetch()
    assert mapped.date is None


# --- the contractor absence, on purpose -------------------------------------


def test_every_record_from_this_source_has_contractor_none(cache):
    transport = ScriptedTransport(ok([REAL_RECORD, row("2"), row("3")]), ok([]))
    records = PermitSource(cache=cache, transport=transport, page_size=100).fetch()
    assert records
    assert all(r.contractor is None for r in records)


def test_a_stray_contractor_key_is_still_not_trusted(cache):
    """This dataset has no contractor field; a lookalike key must not invent one."""
    transport = ScriptedTransport(ok([row("1", contractor="ACME PLUMBING")]), ok([]))
    (mapped,) = PermitSource(cache=cache, transport=transport, page_size=100).fetch()
    assert mapped.contractor is None


def test_the_absence_is_documented_at_module_level():
    note = (permits_module.__doc__ or "").lower()
    assert "contractor" in note, "the module must say why contractor is always None"


# --- relationship to the score engine --------------------------------------
#
# V1's engine.Permit / to_score_permit seam and the provider-churn rule are
# deleted with the V1 engine (ticket 103 / plan R27; V2 scope boundary: no
# contractor/provider-churn scoring). V2 consumes permit records as plain
# mappings through the evidence bundle, pinned by the locked
# tests/test_v2_bundle.py; the contractor-None guarantee above is still the
# source contract this module owns.


# --- the 730-day window -----------------------------------------------------


WINDOW_TABLE = [
    (0, True),
    (1, True),
    (729, True),
    (730, True),
    (731, False),
    (1000, False),
]


@pytest.mark.parametrize("days_ago,inside", WINDOW_TABLE)
def test_the_rolling_window_boundary(days_ago, inside):
    record = PermitRecord(record_id="x", date=AS_OF - timedelta(days=days_ago))
    kept = permits_within([record], AS_OF)
    assert bool(kept) is inside


def test_the_default_window_is_two_years():
    boundary = PermitRecord(record_id="in", date=AS_OF - timedelta(days=730))
    outside = PermitRecord(record_id="out", date=AS_OF - timedelta(days=731))
    assert {r.record_id for r in permits_within([boundary, outside], AS_OF, days=730)} == {"in"}
    assert {r.record_id for r in permits_within([boundary, outside], AS_OF)} == {"in"}


def test_a_permit_dated_after_as_of_is_excluded():
    future = PermitRecord(record_id="future", date=AS_OF + timedelta(days=1))
    assert permits_within([future], AS_OF) == ()


def test_a_permit_with_no_date_is_excluded_not_crashed_on():
    undated = PermitRecord(record_id="undated", date=None)
    dated = PermitRecord(record_id="dated", date=AS_OF)
    assert {r.record_id for r in permits_within([undated, dated], AS_OF)} == {"dated"}


def test_an_empty_input_yields_an_empty_window():
    assert permits_within([], AS_OF) == ()


def test_a_shorter_window_can_be_requested():
    recent = PermitRecord(record_id="recent", date=AS_OF - timedelta(days=10))
    older = PermitRecord(record_id="older", date=AS_OF - timedelta(days=100))
    assert {r.record_id for r in permits_within([recent, older], AS_OF, days=30)} == {"recent"}


def test_the_window_is_deterministic():
    records = [
        PermitRecord(record_id=str(n), date=AS_OF - timedelta(days=n * 30)) for n in range(6)
    ]
    first = permits_within(records, AS_OF)
    assert [r.record_id for r in permits_within(records, AS_OF)] == [r.record_id for r in first]
