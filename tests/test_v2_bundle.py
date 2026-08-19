"""Ticket 102 — live evidence-bundle builder (bundle.py) contract tests.

Pins R22 (precedence table), R8 (project coalescing), R23 (SDL unavailability
is not evidence of absence), R13/R14 (comparables + territory list), the
YYMMDD deed-date pivot reuse, R25 redaction, and a zero-network integration
build over the real cached snapshots.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import date
from pathlib import Path

import pytest

from houseaccount.scoring.bundle import build_bundle, build_bundles
from houseaccount.scoring.v2 import V2Bundle

AS_OF = date(2026, 8, 15)
DATA_DIR = Path(__file__).resolve().parents[1] / "data"


# ---------------------------------------------------------------------------
# synthetic-context helpers
# ---------------------------------------------------------------------------


def make_parcel(**overrides):
    parcel = {
        "pams_pin": "0248_1001_1",
        "prop_class": "2",
        "net_value": 500000.0,
        "yr_constr": 1980,
        "calc_acre": 0.30,
        "deed_date": None,
        "sale_price": 0.0,
        "sales_code": "",
        "centroid": (-74.15, 41.04),
    }
    parcel.update(overrides)
    return parcel


def make_sdl(**overrides):
    record = {
        "pams_pin": "0248_1001_1",
        "collection_status": "collected",
        "assessed_valuation": {
            "land": "$200000",
            "improvements": "$400000",
            "total": "$600000",
        },
        "property_details": {"last_sale_date": "", "last_sale_price": "$"},
        "construction": {
            "permit_applications": [],
            "inspections": [],
            "violations": [],
        },
    }
    record.update(overrides)
    return record


def make_ctx(**overrides):
    ctx = {
        "parcel": make_parcel(),
        "sr1a_sales": (),
        "sdl": None,
        "sdl_available": True,
        "sdl_match_exact_current": False,
        "statewide_permits": (),
        "territory_parcels": (),
        "acs_block_group": {},
        "rental_registry": {},
        "imagery": {},
    }
    ctx.update(overrides)
    return ctx


def sdl_permit(control="C-23-00202", number="20230225", **overrides):
    app = {
        "control_number": control,
        "permit_number": number,
        "work_description": "Roof Replacement - full tear-off, GAF Timberline",
        "work_type": "Alteration",
        "status": "CA and Close Date Issued",
        "issue_date": "4/20/2023",
        "close_date": "6/26/2023",
        "certificates": "CA",
        "subcodes": "Building",
        "total_cost": "30910",
    }
    app.update(overrides)
    return app


def statewide_permit(municipal_id="20230225", **overrides):
    permit = {
        "municipal_id": municipal_id,
        "id": municipal_id,
        "description": "roof replacement",
        "disposition": "completed",
        "issue_date": "2023-04-20",
        "completion_date": "2023-06-26",
    }
    permit.update(overrides)
    return permit


def bundle_json(bundle: V2Bundle) -> str:
    return json.dumps(dataclasses.asdict(bundle), default=str)


def latest_sale(bundle: V2Bundle):
    assert bundle.sales, "expected at least one sale in the bundle"
    return max(bundle.sales, key=lambda s: str(s.get("date")))


# ---------------------------------------------------------------------------
# 1. R22 precedence — assessed value row
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("modiv_value", "exact_current", "expected"),
    [
        # MOD-IV present: SDL must never overwrite it, exact match or not.
        pytest.param(500000.0, True, 500000.0, id="modiv-primary-wins"),
        pytest.param(500000.0, False, 500000.0, id="modiv-wins-no-match"),
        # MOD-IV missing: SDL fills only on an exact+current parcel match.
        pytest.param(None, True, 600000.0, id="sdl-fills-gap-exact-current"),
        pytest.param(None, False, None, id="sdl-no-fill-without-match"),
    ],
)
def test_r22_assessed_value_precedence(modiv_value, exact_current, expected):
    ctx = make_ctx(
        parcel=make_parcel(net_value=modiv_value),
        sdl=make_sdl(),
        sdl_match_exact_current=exact_current,
    )
    bundle = build_bundle(ctx, AS_OF)
    assert bundle.parcel.get("assessed_value") == expected


# ---------------------------------------------------------------------------
# 1. R22 precedence — move-date row (SR1A > MOD-IV transfer > SDL displayed)
# ---------------------------------------------------------------------------

SR1A_SALE = {"date": "2026-01-10", "price": 750000.0, "sales_code": ""}


@pytest.mark.parametrize(
    ("ctx_kwargs", "expected_source", "expected_date"),
    [
        # SR1A present: primary, even when MOD-IV transfer and SDL displayed
        # sale both show a *later* date (lower precedence never overwrites a
        # fresher-precedence valid fact — the SR1A register is authoritative).
        pytest.param(
            dict(
                sr1a_sales=(SR1A_SALE,),
                parcel=make_parcel(
                    deed_date="2025-06-01", sale_price=700000.0, sales_code=""
                ),
                sdl=make_sdl(
                    property_details={
                        "last_sale_date": "03/01/2026",
                        "last_sale_price": "$800000",
                    }
                ),
                sdl_match_exact_current=True,
            ),
            "SR1A",
            "2026-01-10",
            id="sr1a-primary",
        ),
        # No SR1A: valid MOD-IV transfer is the fallback.
        pytest.param(
            dict(
                sr1a_sales=(),
                parcel=make_parcel(
                    deed_date="2025-06-01", sale_price=700000.0, sales_code=""
                ),
            ),
            "MODIV",
            "2025-06-01",
            id="modiv-fallback",
        ),
        # No SR1A, no MOD-IV transfer: SDL displayed sale fills the gap, but
        # only when arm's-length eligibility is establishable (real price).
        pytest.param(
            dict(
                sr1a_sales=(),
                parcel=make_parcel(deed_date=None, sale_price=0.0),
                sdl=make_sdl(
                    property_details={
                        "last_sale_date": "03/01/2026",
                        "last_sale_price": "$800000",
                    }
                ),
                sdl_match_exact_current=True,
            ),
            "SDL",
            "2026-03-01",
            id="sdl-fills-gap-arms-length",
        ),
    ],
)
def test_r22_move_date_precedence(ctx_kwargs, expected_source, expected_date):
    bundle = build_bundle(make_ctx(**ctx_kwargs), AS_OF)
    sale = latest_sale(bundle)
    assert sale.get("source") == expected_source
    assert str(sale.get("date")) == expected_date


def test_r22_sdl_displayed_sale_excluded_without_arms_length():
    """An SDL displayed sale with no establishable price never enters."""
    ctx = make_ctx(
        parcel=make_parcel(deed_date=None, sale_price=0.0),
        sdl=make_sdl(
            property_details={"last_sale_date": "03/01/2026", "last_sale_price": "$"}
        ),
        sdl_match_exact_current=True,
    )
    bundle = build_bundle(ctx, AS_OF)
    assert all(s.get("source") != "SDL" for s in bundle.sales)


# ---------------------------------------------------------------------------
# 1+2. R22 project-lifecycle row and R8 coalescing
# ---------------------------------------------------------------------------


def test_r22_statewide_fills_only_sdl_absent_records():
    """SDL is primary for lifecycle; statewide fills only SDL-absent records."""
    ctx = make_ctx(
        sdl=make_sdl(
            construction={
                "permit_applications": [sdl_permit(number="20230225")],
                "inspections": [],
                "violations": [],
            }
        ),
        statewide_permits=(
            statewide_permit("20230225", description="roof"),
            statewide_permit("20240111", description="deck addition"),
        ),
    )
    bundle = build_bundle(ctx, AS_OF)
    by_id = {str(p.get("municipal_id")): p for p in bundle.permits}
    assert set(by_id) == {"20230225", "20240111"}
    # statewide-only record filled in
    assert tuple(by_id["20240111"]["sources"]) == ("statewide",)


def test_sdl_terminal_status_maps_to_completed_disposition():
    """SDL never says "completed" — its finished permits display "CA and Close
    Date Issued" / "Closed with Date". Without the mapping, no SDL permit can
    earn completed-project points or establish a roof's installation age."""
    ctx = make_ctx(
        sdl=make_sdl(
            construction={
                "permit_applications": [
                    sdl_permit(status="CA and Close Date Issued", close_date="6/26/2023")
                ],
                "inspections": [],
                "violations": [],
            }
        ),
    )
    (permit,) = build_bundle(ctx, AS_OF).permits
    assert permit["disposition"] == "completed"
    assert permit["completion_date"] == "2023-06-26"


def test_sdl_voided_status_maps_to_nonqualifying_disposition():
    ctx = make_ctx(
        sdl=make_sdl(
            construction={
                "permit_applications": [sdl_permit(status="Voided")],
                "inspections": [],
                "violations": [],
            }
        ),
    )
    (permit,) = build_bundle(ctx, AS_OF).permits
    assert permit["disposition"] == "voided"


def test_sdl_open_status_stays_active_eligible():
    ctx = make_ctx(
        sdl=make_sdl(
            construction={
                "permit_applications": [sdl_permit(status="Open", close_date="")],
                "inspections": [],
                "violations": [],
            }
        ),
    )
    (permit,) = build_bundle(ctx, AS_OF).permits
    assert permit["disposition"] is None


def test_roof_collection_fills_the_blank_description_of_the_same_record():
    """Pre-2015 property-history rows display blank descriptions; the roof
    collection's detail page carries the actual scope for the same municipal
    record, keyed by the same permit number."""
    ctx = make_ctx(
        sdl=make_sdl(
            construction={
                "permit_applications": [
                    sdl_permit(
                        number="20090980",
                        work_description="",
                        status="CA and Close Date Issued",
                        issue_date="11/16/2009",
                        close_date="",
                    )
                ],
                "inspections": [],
                "violations": [],
            }
        ),
        sdl_roof_permits=(
            {
                "municipal_id": "20090980",
                "sources": ("SDL",),
                "description": "RE-ROOF/ICE SHIELD",
                "status": "CA and Close Date Issued",
                "disposition": "completed",
                "issue_date": "2009-11-16",
            },
        ),
    )
    (permit,) = build_bundle(ctx, AS_OF).permits
    assert permit["description"] == "RE-ROOF/ICE SHIELD"
    assert permit["disposition"] == "completed"


def test_roof_collection_adds_a_record_the_history_page_lacks():
    ctx = make_ctx(
        sdl_roof_permits=(
            {
                "municipal_id": "20020455",
                "sources": ("SDL",),
                "description": "RE-ROOF/ICE SHIELD",
                "status": "CA and Close Date Issued",
                "disposition": "completed",
                "issue_date": "2002-04-15",
            },
        ),
    )
    (permit,) = build_bundle(ctx, AS_OF).permits
    assert permit["municipal_id"] == "20020455"
    assert permit["disposition"] == "completed"


def test_sdl_permit_dates_are_normalized_to_iso():
    """SDL displays US-format dates; the engine's date reader is ISO-only, so
    the bundle must convert or every SDL permit silently earns nothing."""
    ctx = make_ctx(
        sdl=make_sdl(
            construction={
                "permit_applications": [
                    sdl_permit(issue_date="4/20/2023", close_date="6/26/2023")
                ],
                "inspections": [],
                "violations": [],
            }
        ),
    )
    bundle = build_bundle(ctx, AS_OF)
    (permit,) = bundle.permits
    assert permit["issue_date"] == "2023-04-20"
    assert permit["close_date"] == "2023-06-26"


def test_r8_same_record_in_both_sources_coalesces_with_sdl_detail():
    ctx = make_ctx(
        sdl=make_sdl(
            construction={
                "permit_applications": [sdl_permit(number="20230225")],
                "inspections": [],
                "violations": [],
            }
        ),
        statewide_permits=(statewide_permit("20230225", description="roof"),),
    )
    bundle = build_bundle(ctx, AS_OF)
    assert len(bundle.permits) == 1
    project = bundle.permits[0]
    assert set(project["sources"]) == {"SDL", "statewide"}
    # richer SDL detail retained, not the statewide stub description
    assert "GAF Timberline" in str(project.get("description"))


def test_r8_amendments_and_supplements_collapse_to_one_project():
    ctx = make_ctx(
        sdl=make_sdl(
            construction={
                "permit_applications": [
                    sdl_permit(control="C-23-00202", number="20230225"),
                    sdl_permit(
                        control="C-23-00202-A1",
                        number="20230225",
                        work_type="Amendment",
                        work_description="Amendment - add gutter work",
                    ),
                    sdl_permit(
                        control="C-23-00202-S1",
                        number="20230225",
                        work_type="Supplement",
                        work_description="Supplemental electrical",
                    ),
                ],
                "inspections": [],
                "violations": [],
            }
        ),
    )
    bundle = build_bundle(ctx, AS_OF)
    assert len(bundle.permits) == 1


# ---------------------------------------------------------------------------
# 3. R23 — unavailable SDL page
# ---------------------------------------------------------------------------


def test_r23_unavailable_sdl_page_flags_gap_and_stays_complete():
    ctx = make_ctx(
        sdl=None,
        sdl_available=False,
        statewide_permits=(statewide_permit("20240111"),),
        acs_block_group={"dual_income_pct": 0.4},
    )
    bundle = build_bundle(ctx, AS_OF)
    # the engine turns this flag into the sdl_page_unavailable data gap
    assert bundle.parcel.get("sdl_page_available") is False
    # bundle stays complete and scoreable from other sources
    assert bundle.parcel.get("assessed_value") == 500000.0
    assert len(bundle.permits) == 1
    assert bundle.acs_block_group == {"dual_income_pct": 0.4}


def test_r23_available_page_does_not_flag():
    bundle = build_bundle(make_ctx(sdl=make_sdl(), sdl_available=True), AS_OF)
    assert bundle.parcel.get("sdl_page_available") is not False


# ---------------------------------------------------------------------------
# 4+5. Comparables and territory assessed-value list
# ---------------------------------------------------------------------------


def territory_of(n, *, subject_pin="0248_1001_1"):
    """n synthetic single-family parcels on a west->east line from subject."""
    out = []
    for i in range(n):
        out.append(
            make_parcel(
                pams_pin=f"0248_2000_{i + 1}",
                net_value=400000.0 + 1000 * i,
                centroid=(-74.15 + 0.0001 * (i + 1), 41.04),
            )
        )
    # subject itself is present in the territory listing
    out.append(make_parcel(pams_pin=subject_pin))
    return tuple(out)


def test_comparables_nearest_20_subject_excluded():
    ctx = make_ctx(territory_parcels=territory_of(30))
    bundle = build_bundle(ctx, AS_OF)
    comps = list(bundle.local_comparables)
    assert len(comps) == 20
    pins = [c["pams_pin"] for c in comps]
    assert "0248_1001_1" not in pins
    # nearest 20 of the 30 by centroid distance
    assert set(pins) == {f"0248_2000_{i + 1}" for i in range(20)}


def test_comparables_exclude_non_single_family_and_invalid():
    territory = list(territory_of(12))
    territory[0] = dict(territory[0], prop_class="4A")  # commercial
    territory[1] = dict(territory[1], net_value=None)  # invalid assessed value
    ctx = make_ctx(territory_parcels=tuple(territory))
    bundle = build_bundle(ctx, AS_OF)
    pins = {c["pams_pin"] for c in bundle.local_comparables}
    assert "0248_2000_1" not in pins
    assert "0248_2000_2" not in pins
    assert len(bundle.local_comparables) == 10


def test_comparables_deterministic_tie_break():
    """Equidistant comparables order deterministically (stable across runs)."""
    tied = tuple(
        make_parcel(
            pams_pin=f"0248_3000_{i}",
            net_value=450000.0,
            centroid=(-74.15, 41.04 + 0.0005),  # all identical -> same distance
        )
        for i in (3, 1, 2)
    )
    ctx = make_ctx(territory_parcels=tied)
    first = build_bundle(ctx, AS_OF)
    second = build_bundle(make_ctx(territory_parcels=tuple(reversed(tied))), AS_OF)
    order1 = [c["pams_pin"] for c in first.local_comparables]
    order2 = [c["pams_pin"] for c in second.local_comparables]
    assert order1 == order2 == sorted(order1)


def test_comparables_fewer_than_10_truncated_list_passes_through():
    """<10 valid comparables: builder passes the short list; engine handles
    neutrality + the data gap."""
    ctx = make_ctx(territory_parcels=territory_of(4))
    bundle = build_bundle(ctx, AS_OF)
    assert len(bundle.local_comparables) == 4


def test_territory_assessed_value_list_for_midrank_percentile():
    ctx = make_ctx(territory_parcels=territory_of(15))
    bundle = build_bundle(ctx, AS_OF)
    expected = sorted([400000.0 + 1000 * i for i in range(15)] + [500000.0])
    assert sorted(bundle.territory_assessed_values) == expected
    # non-single-family / invalid values excluded from the list too
    territory = list(territory_of(15))
    territory[0] = dict(territory[0], prop_class="4A")
    bundle2 = build_bundle(make_ctx(territory_parcels=tuple(territory)), AS_OF)
    assert 400000.0 not in bundle2.territory_assessed_values


# ---------------------------------------------------------------------------
# 6. Deed-date YYMMDD pivot reuse (normalize.parse_deed_date, through builder)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("240310", "2024-03-10"),  # recent deed: pivot lands in 2000s
        ("990615", "1999-06-15"),  # pre-pivot: 1900s
    ],
)
def test_deed_date_yymmdd_pivot_through_builder(raw, expected):
    ctx = make_ctx(
        parcel=make_parcel(deed_date=raw, sale_price=650000.0, sales_code="")
    )
    bundle = build_bundle(ctx, AS_OF)
    sale = latest_sale(bundle)
    assert str(sale.get("date")) == expected


# ---------------------------------------------------------------------------
# 7. Redaction (R25)
# ---------------------------------------------------------------------------


def test_redaction_no_identity_fields_in_bundle():
    dirty_parcel = make_parcel()
    dirty_parcel.update(
        {
            "OWNER_NAME": "PAT EXAMPLE",
            "ST_ADDRESS": "12 SECRET LANE",
            "CITY_STATE": "RAMSEY NJ",
        }
    )
    dirty_sdl = make_sdl(owner="PAT EXAMPLE", mailing_address="12 SECRET LANE")
    ctx = make_ctx(parcel=dirty_parcel, sdl=dirty_sdl, sdl_match_exact_current=True)
    bundle = build_bundle(ctx, AS_OF)
    blob = bundle_json(bundle)
    for token in ("OWNER_NAME", "ST_ADDRESS", "CITY_STATE"):
        assert token not in blob
    for value in ("PAT EXAMPLE", "12 SECRET LANE", "RAMSEY NJ"):
        assert value not in blob


# ---------------------------------------------------------------------------
# 8. Integration: real cached data, zero network
# ---------------------------------------------------------------------------

REQUIRED_DATA = [
    DATA_DIR / "sdl_property_history_territory.json",
    DATA_DIR / "sdl_roof_permits_territory.json",
    DATA_DIR / "territory.geojson",
    DATA_DIR / "run_manifest.json",
]


@pytest.mark.skipif(
    not all(p.exists() for p in REQUIRED_DATA),
    reason="real cached data files missing",
)
def test_integration_builds_540_bundles_from_disk_no_network(monkeypatch):
    import houseaccount.http as http_mod

    def _no_network(*args, **kwargs):  # pragma: no cover - must never fire
        raise AssertionError("network access attempted during bundle build")

    monkeypatch.setattr(http_mod, "requests_transport", _no_network)
    monkeypatch.setattr(http_mod, "fetch_json", _no_network, raising=False)

    bundles = build_bundles(AS_OF, data_dir=DATA_DIR)
    assert len(bundles) == 540
    for pin, bundle in bundles.items():
        assert isinstance(bundle, V2Bundle)
        assert bundle.parcel.get("pams_pin") == pin
        assert bundle.territory_assessed_values  # percentile list assembled
