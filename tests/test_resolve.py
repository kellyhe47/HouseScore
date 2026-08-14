"""Entity resolution + match-rate reporting (T006, R3.1/R3.2).

The canonical key is `PAMS_PIN`. Everything else is a join onto it.

**Why the primary join is block/lot.** Phase 0 found the Socrata permit dataset
carries `block`/`lot` and no situs address at all (see
`tests/test_permits.py::test_permit_record_carries_nothing_beyond_the_agreed_fields`).
So `normalize.parcel_key(mun, block, lot)` is the primary join, and the
normalized situs address is the *fallback* — usable only by a permit-like record
that does carry an address. `PermitRecord` never will; a future OPRA-obtained
municipal permit list might, so the fallback is duck-typed on an `address`
attribute and a plain `PermitRecord` simply skips it.

**What `permit_match_rate` divides by.** R3.2 grades "match rate on records whose
address falls in the territory". Taking the denominator as *exact* block+lot
containment would make the rate identically 1.0 — a permit whose block+lot is a
territory parcel matches by construction, so the metric would be ungradeable.
The denominator this suite pins is therefore the honest one: permits, inside the
2-year window, whose **block** is a block the territory occupies. Those are the
records that plausibly belong to us; the graded question is how many of them we
landed on a door. Permits on other blocks are out of territory, and permits
outside the window are out of scope — both are *counted* in the report rather
than dropped, which is the real content of "never silently dropped".

No network, no fixtures on disk: every parcel, permit and polygon below is
synthetic and built in-module.
"""

from dataclasses import dataclass
from datetime import date, timedelta

import pytest

from houseaccount.normalize import normalize_address, parcel_key, situs_display
from houseaccount.resolve import (
    SIGNAL_ACS,
    SIGNAL_PARCEL,
    SIGNAL_PERMITS,
    SIGNAL_RENTAL,
    DoorFacts,
    Provenance,
    ResolveReport,
    ResolveResult,
    UnmatchedPermit,
    resolve,
)
from houseaccount.scoring import engine
from houseaccount.scoring.engine import (
    SOURCE_ACS,
    SOURCE_MODIV,
    SOURCE_PERMITS,
    SOURCE_RENTAL,
    ScoreInput,
    score_door,
)
from houseaccount.sources.acs import AcsResult, BlockGroupStats
from houseaccount.sources.parcels import DEFAULT_MUN, Parcel
from houseaccount.sources.permits import PermitRecord
from houseaccount.sources.rental import (
    DECLINATION_REASON,
    FixtureRentalProvider,
    NullRentalProvider,
)
from houseaccount.sources.tiger import BlockGroupBoundary, BlockGroupIndex

AS_OF = date(2026, 8, 14)

#: Somewhere in Ramsey. Doors sit here unless a test needs them somewhere else.
LON, LAT = -74.1560, 41.0447

BG_ONE_GEOID = "340030113001"
BG_TWO_GEOID = "340030114002"

BG_ONE = BlockGroupStats(
    geoid=BG_ONE_GEOID,
    state="34",
    county="003",
    tract="011300",
    block_group="1",
    dual_income_pct=0.41,
    median_hh_income=145673.0,
)
BG_TWO = BlockGroupStats(
    geoid=BG_TWO_GEOID,
    state="34",
    county="003",
    tract="011400",
    block_group="2",
    dual_income_pct=0.25,
    median_hh_income=98250.0,
)

ACS_DECLINED = AcsResult(available=False, block_groups={}, reason="CENSUS_API_KEY is not set")
ACS_AVAILABLE = AcsResult(
    available=True, block_groups={BG_ONE_GEOID: BG_ONE, BG_TWO_GEOID: BG_TWO}, reason=None
)


# --- builders ------------------------------------------------------------------


def door(block, lot, *, street="MAPLE", lon=LON, lat=LAT, **overrides):
    """One territory parcel. `prop_loc` is unique per (block, lot) so the address
    fallback has something unambiguous to hit."""
    fields = dict(
        pams_pin=f"0248_{int(float(block)):05d}_{int(float(lot)):05d}",
        prop_class="2",
        prop_loc=f"{int(float(lot))} {street} ST",
        zip5="07446",
        pclblock=str(block),
        pcllot=str(lot),
        deed_date="200315",
        sale_price=640000.0,
        sales_code="",
        yr_constr=1962,
        net_value=725000.0,
        calc_acre=0.34,
        geometry={"type": "Polygon", "coordinates": [[[lon, lat], [lon, lat], [lon, lat]]]},
        centroid=(lon, lat),
    )
    fields.update(overrides)
    return Parcel(**fields)


def permit(record_id, block, lot, *, days_ago=30, kind="Alteration", **overrides):
    fields = dict(
        record_id=record_id,
        block=str(block),
        lot=str(lot),
        date=AS_OF - timedelta(days=days_ago) if days_ago is not None else None,
        type=kind,
        contractor=None,
        cost=4500.0,
    )
    fields.update(overrides)
    return PermitRecord(**fields)


@dataclass(frozen=True)
class AddressedPermit(PermitRecord):
    """A permit-like record that *does* carry a situs address.

    The Socrata feed has no address column, so nothing from that source can ever
    use the address fallback. A municipal list obtained by OPRA would, and this
    is the shape it would arrive in — the fallback keys off the `address`
    attribute, not off the concrete type.
    """

    address: str = ""


def addressed(record_id, block, lot, address, *, days_ago=30):
    return AddressedPermit(
        record_id=record_id,
        block=str(block),
        lot=str(lot),
        date=AS_OF - timedelta(days=days_ago),
        type="Alteration",
        contractor=None,
        cost=4500.0,
        address=address,
    )


class StubIndex:
    """A hand-wired centroid -> GEOID map.

    Resolution's contract is "attach ACS via the GEOID the index returns", not
    "do point-in-polygon"; the real `BlockGroupIndex` is exercised in
    `tests/test_tiger.py` and in the integration test at the bottom of this file.
    """

    def __init__(self, mapping):
        self._mapping = {tuple(k): v for k, v in dict(mapping).items()}

    def geoid_for(self, centroid):
        if centroid is None:
            return None
        return self._mapping.get(tuple(centroid))


def resolved(
    parcels,
    permits=(),
    *,
    acs=ACS_DECLINED,
    rental=None,
    as_of=AS_OF,
    index=None,
    **kwargs,
):
    return resolve(
        parcels,
        permits,
        acs,
        rental if rental is not None else NullRentalProvider(),
        as_of,
        block_group_index=index,
        **kwargs,
    )


# --- the worked example (hand-checkable arithmetic) ---------------------------
#
# 20 doors on two blocks; 25 permits in.
#
#   18 permits join on block/lot        (blocks 101 and 102, lots 1..9)
#    1 permit joins on address only     (block 101, lot 999, "10 Maple Street")
#    1 permit joins on neither          (block 102, lot 999, no address)
#   ------------------------------------------------------------------
#   20 in-territory  ->  19 matched  ->  19 / 20 = 0.95   (R3.2's threshold)
#
#    3 permits on block 777             -> out of territory, not in the divisor
#    2 permits dated 900 days ago       -> out of window, not in the divisor
#   ------------------------------------------------------------------
#   25 accounted for, none dropped.

BLOCK_A, BLOCK_B, BLOCK_ELSEWHERE = "101", "102", "777"

#: The door the addressed permit is aimed at, and the door seeded as a rental.
ADDRESS_TARGET_PIN = "0248_00101_00010"
RENTAL_PIN = "0248_00102_00010"


def worked_example_doors():
    return [door(BLOCK_A, lot, street="MAPLE") for lot in range(1, 11)] + [
        door(BLOCK_B, lot, street="OAK") for lot in range(1, 11)
    ]


def worked_example_permits():
    joined = [permit(f"P-A-{lot:02d}", BLOCK_A, lot) for lot in range(1, 10)]
    joined += [permit(f"P-B-{lot:02d}", BLOCK_B, lot) for lot in range(1, 10)]
    by_address = [addressed("P-ADDR", BLOCK_A, "999", "10 Maple Street")]
    orphan = [permit("P-ORPHAN", BLOCK_B, "999")]
    elsewhere = [permit(f"P-OUT-{n}", BLOCK_ELSEWHERE, n) for n in (1, 2, 3)]
    stale = [permit(f"P-OLD-{n}", BLOCK_A, 1, days_ago=900) for n in (1, 2)]
    return joined + by_address + orphan + elsewhere + stale


@pytest.fixture
def worked_example():
    return resolved(
        worked_example_doors(),
        worked_example_permits(),
        rental=FixtureRentalProvider({RENTAL_PIN}),
    )


WORKED_EXAMPLE_NUMBERS = [
    ("doors_total", 20),
    ("doors_with_signal", 20),
    ("coverage", 1.0),
    ("permits_total", 25),
    ("permits_out_of_window", 2),
    ("permits_out_of_territory", 3),
    ("permits_in_territory", 20),
    ("permits_matched", 19),
    ("matched_by_block_lot", 18),
    ("matched_by_address", 1),
    ("permit_match_rate", 0.95),
    ("block_lot_match_rate", 0.90),
    ("address_match_rate", 0.05),
]


@pytest.mark.parametrize("field_name, expected", WORKED_EXAMPLE_NUMBERS)
def test_the_worked_example_report_is_exactly_this(worked_example, field_name, expected):
    assert getattr(worked_example.report, field_name) == pytest.approx(expected)


def test_the_worked_example_reports_the_rubric_threshold(worked_example):
    """R3.2 targets >=95%. The metric must be able to say so, and does."""
    assert worked_example.report.permit_match_rate >= 0.95


def test_every_input_permit_is_accounted_for(worked_example):
    """Nothing silently dropped: in-territory + elsewhere + out-of-window = all."""
    report = worked_example.report
    assert (
        report.permits_in_territory + report.permits_out_of_territory + report.permits_out_of_window
        == report.permits_total
    )


def test_in_territory_permits_are_either_matched_or_reported_unmatched(worked_example):
    report = worked_example.report
    assert report.permits_matched + len(report.unmatched) == report.permits_in_territory


def test_matched_permits_split_cleanly_between_the_two_join_routes(worked_example):
    report = worked_example.report
    assert report.matched_by_block_lot + report.matched_by_address == report.permits_matched


def test_the_one_permit_that_joined_nowhere_is_named(worked_example):
    assert [u.record_id for u in worked_example.report.unmatched] == ["P-ORPHAN"]


def test_the_addressed_permit_landed_on_the_door_its_address_names(worked_example):
    facts = worked_example.doors[ADDRESS_TARGET_PIN]
    assert [p.record_id for p in facts.permits_2yr] == ["P-ADDR"]


def test_an_out_of_territory_permit_reaches_no_door(worked_example):
    landed = {p.record_id for facts in worked_example.doors.values() for p in facts.permits_2yr}
    assert not {"P-OUT-1", "P-OUT-2", "P-OUT-3"} & landed


def test_an_out_of_window_permit_reaches_no_door(worked_example):
    landed = {p.record_id for facts in worked_example.doors.values() for p in facts.permits_2yr}
    assert not {"P-OLD-1", "P-OLD-2"} & landed


# --- the match-rate arithmetic, at other ratios --------------------------------


@pytest.mark.parametrize(
    "matched_lots, expected_rate",
    [
        ((1, 2, 3, 4), 1.0),
        ((1, 2, 3), 0.75),
        ((1, 2), 0.5),
        ((), 0.0),
    ],
    ids=["4-of-4", "3-of-4", "2-of-4", "0-of-4"],
)
def test_the_rate_is_matched_over_in_territory(matched_lots, expected_rate):
    doors = [door(BLOCK_A, lot) for lot in range(1, 5)]
    permits = [permit(f"P-{lot}", BLOCK_A, lot if lot in matched_lots else 900 + lot) for lot in range(1, 5)]

    report = resolved(doors, permits).report
    assert report.permits_in_territory == 4
    assert report.permit_match_rate == pytest.approx(expected_rate)


def test_no_in_territory_permits_reports_a_zero_rate_not_a_crash():
    report = resolved([door(BLOCK_A, 1)], [permit("P-1", BLOCK_ELSEWHERE, 1)]).report

    assert report.permits_in_territory == 0
    assert report.permit_match_rate == 0.0


def test_no_permits_at_all_reports_a_zero_rate():
    report = resolved([door(BLOCK_A, 1)], []).report

    assert report.permits_total == 0
    assert report.permit_match_rate == 0.0


# --- the block/lot join --------------------------------------------------------


@pytest.mark.parametrize(
    "parcel_block, parcel_lot, permit_block, permit_lot, joins",
    [
        ("101", "3", "101", "3", True),
        ("101", "3", "0101", "003", True),
        ("101", "3", "101", "3.0", True),
        ("0101", "3.0", "101", "3", True),
        ("101", "3", "101", "3.5", False),
        ("101", "3", "102", "3", False),
        ("101", "3", "101", "4", False),
        ("101", "3", "", "", False),
    ],
    ids=[
        "exact",
        "leading-zeros",
        "float-lot",
        "float-parcel-lot",
        "fractional-lot-is-a-different-parcel",
        "other-block",
        "other-lot",
        "blank-keys",
    ],
)
def test_block_lot_normalization_decides_the_join(
    parcel_block, parcel_lot, permit_block, permit_lot, joins
):
    parcels = [door(parcel_block, parcel_lot)]
    result = resolved(parcels, [permit("P-1", permit_block, permit_lot)])

    (facts,) = result.doors.values()
    assert bool(facts.permits_2yr) is joins


def test_the_block_lot_route_is_tried_before_the_address_route():
    """An addressed permit whose block/lot is good must not need its address."""
    doors = [door(BLOCK_A, 3), door(BLOCK_A, 4)]
    # Block/lot says lot 3; the address says lot 4. Block/lot wins.
    record = addressed("P-1", BLOCK_A, 3, "4 Maple Street")

    result = resolved(doors, [record])
    assert [p.record_id for p in result.doors["0248_00101_00003"].permits_2yr] == ["P-1"]
    assert result.doors["0248_00101_00004"].permits_2yr == ()
    assert result.report.matched_by_block_lot == 1
    assert result.report.matched_by_address == 0


def test_two_permits_on_one_door_both_land():
    result = resolved(
        [door(BLOCK_A, 3)],
        [permit("P-1", BLOCK_A, 3), permit("P-2", BLOCK_A, 3, days_ago=100)],
    )
    (facts,) = result.doors.values()
    assert {p.record_id for p in facts.permits_2yr} == {"P-1", "P-2"}


def test_a_doors_permits_are_ordered_newest_first():
    result = resolved(
        [door(BLOCK_A, 3)],
        [
            permit("P-mid", BLOCK_A, 3, days_ago=100),
            permit("P-old", BLOCK_A, 3, days_ago=700),
            permit("P-new", BLOCK_A, 3, days_ago=5),
        ],
    )
    (facts,) = result.doors.values()
    assert [p.record_id for p in facts.permits_2yr] == ["P-new", "P-mid", "P-old"]


# --- the address fallback ------------------------------------------------------


@pytest.mark.parametrize(
    "address, joins",
    [
        ("12 MAPLE ST", True),
        ("12 Maple Street", True),
        ("12 maple st.", True),
        ("12 Maple St, Apt 3B", True),
        ("14 MAPLE ST", False),
        ("12 OAK ST", False),
        ("", False),
    ],
    ids=["canonical", "long-form", "punctuated", "unit-suffix", "other-number", "other-street", "blank"],
)
def test_the_address_fallback_uses_the_normalizer(address, joins):
    doors = [door(BLOCK_A, 12, street="MAPLE")]
    record = addressed("P-1", BLOCK_A, "999", address)

    result = resolved(doors, [record])
    (facts,) = result.doors.values()
    assert bool(facts.permits_2yr) is joins
    assert result.report.matched_by_address == (1 if joins else 0)


def test_a_permit_record_with_no_address_attribute_skips_the_fallback():
    """`PermitRecord` has no address column; it must not crash the fallback."""
    result = resolved([door(BLOCK_A, 12)], [permit("P-1", BLOCK_A, "999")])

    assert result.report.matched_by_address == 0
    assert [u.record_id for u in result.report.unmatched] == ["P-1"]


def test_an_ambiguous_address_matches_nothing_rather_than_guessing():
    """Two doors normalize to one address. Picking either would be a fabrication."""
    doors = [door(BLOCK_A, 12, street="MAPLE"), door(BLOCK_B, 12, street="MAPLE")]
    result = resolved(doors, [addressed("P-1", BLOCK_A, "999", "12 Maple St")])

    assert all(facts.permits_2yr == () for facts in result.doors.values())
    assert [u.record_id for u in result.report.unmatched] == ["P-1"]


# --- unmatched permits are reported, never dropped -----------------------------


def test_an_unmatched_permit_carries_its_identifiers_and_a_reason():
    result = resolved([door(BLOCK_A, 3)], [permit("P-1", BLOCK_A, "999")])
    (unmatched,) = result.report.unmatched

    assert isinstance(unmatched, UnmatchedPermit)
    assert unmatched.record_id == "P-1"
    assert unmatched.block == BLOCK_A
    assert unmatched.lot == "999"
    assert unmatched.parcel_key == parcel_key(DEFAULT_MUN, BLOCK_A, "999")
    assert isinstance(unmatched.reason, str) and unmatched.reason.strip()


@pytest.mark.parametrize("field_name", ("record_id", "block", "lot", "parcel_key", "reason"))
def test_unmatched_permit_carries_the_agreed_fields(field_name):
    assert field_name in UnmatchedPermit.__dataclass_fields__


def test_unmatched_is_ordered_by_record_id_so_input_order_cannot_move_it():
    doors = [door(BLOCK_A, 3)]
    permits = [permit(rid, BLOCK_A, "999") for rid in ("P-3", "P-1", "P-2")]

    report = resolved(doors, permits).report
    assert [u.record_id for u in report.unmatched] == ["P-1", "P-2", "P-3"]


def test_an_undated_permit_is_counted_out_of_window_not_unmatched():
    result = resolved([door(BLOCK_A, 3)], [permit("P-1", BLOCK_A, 3, days_ago=None)])

    assert result.report.permits_out_of_window == 1
    assert result.report.unmatched == ()
    assert result.doors["0248_00101_00003"].permits_2yr == ()


# --- ACS attaches through the block-group index --------------------------------


def test_acs_stats_attach_to_a_door_via_its_geoid():
    parcels = [door(BLOCK_A, 3, lon=-74.10, lat=41.00)]
    index = StubIndex({(-74.10, 41.00): BG_ONE_GEOID})

    (facts,) = resolved(parcels, acs=ACS_AVAILABLE, index=index).doors.values()
    assert facts.block_group_geoid == BG_ONE_GEOID
    assert facts.block_group == BG_ONE


def test_two_doors_in_different_block_groups_get_different_stats():
    parcels = [door(BLOCK_A, 3, lon=-74.10, lat=41.00), door(BLOCK_A, 4, lon=-74.20, lat=41.00)]
    index = StubIndex({(-74.10, 41.00): BG_ONE_GEOID, (-74.20, 41.00): BG_TWO_GEOID})

    doors = resolved(parcels, acs=ACS_AVAILABLE, index=index).doors
    assert doors["0248_00101_00003"].block_group == BG_ONE
    assert doors["0248_00101_00004"].block_group == BG_TWO


def test_a_centroid_outside_every_block_group_leaves_the_door_without_stats():
    parcels = [door(BLOCK_A, 3, lon=-70.0, lat=30.0)]
    index = StubIndex({(-74.10, 41.00): BG_ONE_GEOID})

    (facts,) = resolved(parcels, acs=ACS_AVAILABLE, index=index).doors.values()
    assert facts.block_group_geoid is None
    assert facts.block_group is None


def test_a_parcel_with_no_centroid_resolves_without_stats():
    parcels = [door(BLOCK_A, 3, centroid=None, geometry=None)]
    index = StubIndex({(-74.10, 41.00): BG_ONE_GEOID})

    (facts,) = resolved(parcels, acs=ACS_AVAILABLE, index=index).doors.values()
    assert facts.block_group is None


def test_a_geoid_the_acs_response_never_carried_leaves_the_door_without_stats():
    """The index knows every block group; ACS may have dropped a row."""
    parcels = [door(BLOCK_A, 3, lon=-74.10, lat=41.00)]
    index = StubIndex({(-74.10, 41.00): "349990000009"})

    (facts,) = resolved(parcels, acs=ACS_AVAILABLE, index=index).doors.values()
    assert facts.block_group_geoid == "349990000009"
    assert facts.block_group is None


def test_no_index_at_all_still_resolves_every_door():
    parcels = [door(BLOCK_A, 3)]
    (facts,) = resolved(parcels, acs=ACS_AVAILABLE, index=None).doors.values()

    assert facts.block_group is None
    assert facts.pams_pin == "0248_00101_00003"


# --- ACS declined ---------------------------------------------------------------


def test_a_declined_acs_leaves_no_block_group_stats_and_says_why():
    parcels = [door(BLOCK_A, 3, lon=-74.10, lat=41.00)]
    index = StubIndex({(-74.10, 41.00): BG_ONE_GEOID})

    result = resolved(parcels, acs=ACS_DECLINED, index=index)
    (facts,) = result.doors.values()

    assert facts.block_group is None
    assert result.report.acs_available is False
    assert result.report.acs_reason == ACS_DECLINED.reason


def test_an_available_acs_reports_no_reason():
    result = resolved([door(BLOCK_A, 3)], acs=ACS_AVAILABLE)

    assert result.report.acs_available is True
    assert result.report.acs_reason is None


def test_a_door_with_declined_acs_still_scores():
    parcels = [door(BLOCK_A, 3)]
    (facts,) = resolved(parcels, acs=ACS_DECLINED).doors.values()

    scored = score_door(
        facts.to_score_input(
            as_of=AS_OF, territory_median_value=500000.0, acs_dual_income_threshold=0.35
        )
    )
    assert 0 <= scored.score <= 100
    assert "acs_dual_income_prior" not in {item.type for item in scored.evidence}


def test_the_number_of_doors_carrying_block_group_stats_is_reported():
    parcels = [door(BLOCK_A, 3, lon=-74.10, lat=41.00), door(BLOCK_A, 4, lon=-70.0, lat=30.0)]
    index = StubIndex({(-74.10, 41.00): BG_ONE_GEOID})

    report = resolved(parcels, acs=ACS_AVAILABLE, index=index).report
    assert report.doors_with_block_group == 1


# --- rental registration ---------------------------------------------------------


def test_a_seeded_pin_carries_the_rental_match():
    parcels = [door(BLOCK_A, 3), door(BLOCK_A, 4)]
    result = resolved(parcels, rental=FixtureRentalProvider({"0248_00101_00003"}))

    assert result.doors["0248_00101_00003"].rental_registration_match is True
    assert result.doors["0248_00101_00004"].rental_registration_match is False
    assert result.report.rental_declination_reason is None


def test_the_null_provider_matches_nothing_and_says_why():
    result = resolved([door(BLOCK_A, 3)], rental=NullRentalProvider())

    (facts,) = result.doors.values()
    assert facts.rental_registration_match is False
    assert result.report.rental_declination_reason == DECLINATION_REASON


# --- coverage ---------------------------------------------------------------------


def test_coverage_is_doors_with_any_non_parcel_source_over_all_doors():
    """Four doors: one permit, one ACS, one rental, one nothing -> 3/4."""
    parcels = [
        door(BLOCK_A, 1),
        door(BLOCK_A, 2, lon=-74.10, lat=41.00),
        door(BLOCK_A, 3),
        door(BLOCK_A, 4),
    ]
    result = resolved(
        parcels,
        [permit("P-1", BLOCK_A, 1)],
        acs=ACS_AVAILABLE,
        index=StubIndex({(-74.10, 41.00): BG_ONE_GEOID}),
        rental=FixtureRentalProvider({"0248_00101_00003"}),
    )

    assert result.report.doors_total == 4
    assert result.report.doors_with_signal == 3
    assert result.report.coverage == pytest.approx(0.75)


def test_a_door_the_rental_provider_answered_no_for_is_not_covered():
    """False is 'not known to be a rental', not a signal about this door."""
    result = resolved([door(BLOCK_A, 1)], rental=NullRentalProvider())

    assert result.report.doors_with_signal == 0
    assert result.report.coverage == 0.0


def test_no_doors_at_all_reports_zero_coverage_not_a_crash():
    result = resolved([], [])

    assert result.doors == {}
    assert result.report.doors_total == 0
    assert result.report.coverage == 0.0
    assert result.report.unmatched == ()


# --- DoorFacts shape --------------------------------------------------------------


DOOR_FACT_FIELDS = (
    "pams_pin",
    "prop_class",
    "prop_loc",
    "zip5",
    "pclblock",
    "pcllot",
    "parcel_key",
    "address_key",
    "situs",
    "centroid",
    "geometry",
    "deed_date",
    "sale_price",
    "sales_code",
    "yr_constr",
    "net_value",
    "calc_acre",
    "permits_2yr",
    "block_group_geoid",
    "block_group",
    "rental_registration_match",
    "provenance",
)


@pytest.mark.parametrize("field_name", DOOR_FACT_FIELDS)
def test_door_facts_carries_the_agreed_fields(field_name):
    assert field_name in DoorFacts.__dataclass_fields__


def test_door_facts_carries_nothing_beyond_the_agreed_fields():
    assert set(DoorFacts.__dataclass_fields__) == set(DOOR_FACT_FIELDS)


@pytest.mark.parametrize(
    "attribute, expected",
    [
        ("pams_pin", "0248_00101_00003"),
        ("prop_class", "2"),
        ("prop_loc", "3 MAPLE ST"),
        ("zip5", "07446"),
        ("pclblock", "0101"),
        ("pcllot", "3.0"),
        ("sale_price", 640000.0),
        ("sales_code", ""),
        ("yr_constr", 1962),
        ("net_value", 725000.0),
        ("calc_acre", 0.34),
    ],
)
def test_parcel_fields_are_carried_straight_through(attribute, expected):
    (facts,) = resolved([door("0101", "3.0")]).doors.values()
    assert getattr(facts, attribute) == expected


def test_the_join_keys_are_precomputed_not_left_to_the_caller():
    (facts,) = resolved([door("0101", "3.0")]).doors.values()

    assert facts.parcel_key == parcel_key(DEFAULT_MUN, "0101", "3.0") == "248/101/3"
    assert facts.address_key == normalize_address("3 MAPLE ST") == "3 MAPLE ST"
    assert facts.situs == situs_display("3 MAPLE ST", "07446")


def test_the_municipality_used_for_the_key_is_configurable():
    (facts,) = resolved([door("0101", "3.0")], mun="0299").doors.values()
    assert facts.parcel_key == parcel_key("0299", "0101", "3.0")


def test_the_deed_date_is_parsed_once_here_not_re_derived_downstream():
    (facts,) = resolved([door(BLOCK_A, 3, deed_date="260615")]).doors.values()
    assert facts.deed_date == date(2026, 6, 15)


@pytest.mark.parametrize("raw", [None, "", "not-a-date", "999999"])
def test_an_unparseable_deed_date_becomes_none_and_the_door_still_resolves(raw):
    (facts,) = resolved([door(BLOCK_A, 3, deed_date=raw)]).doors.values()

    assert facts.deed_date is None
    assert facts.pams_pin == "0248_00101_00003"


def test_the_geometry_survives_resolution_for_publishing():
    parcels = [door(BLOCK_A, 3)]
    (facts,) = resolved(parcels).doors.values()
    assert facts.geometry == parcels[0].geometry
    assert facts.centroid == parcels[0].centroid


# --- provenance (R7.1) -------------------------------------------------------------


@pytest.mark.parametrize("field_name", ("source", "retrieved"))
def test_provenance_carries_a_source_and_a_retrieval_date(field_name):
    assert field_name in Provenance.__dataclass_fields__


def test_every_door_carries_parcel_provenance():
    (facts,) = resolved([door(BLOCK_A, 3)]).doors.values()

    assert facts.provenance[SIGNAL_PARCEL].source == SOURCE_MODIV
    assert facts.provenance[SIGNAL_PARCEL].retrieved == AS_OF


def test_a_signal_that_contributed_nothing_carries_no_provenance():
    """A provenance entry claims a fact came from somewhere. With no permits,
    no ACS and no rental match there is nothing to attribute."""
    (facts,) = resolved([door(BLOCK_A, 3)]).doors.values()

    assert set(facts.provenance) == {SIGNAL_PARCEL}


def test_each_contributing_signal_names_the_source_the_evidence_will_cite():
    parcels = [door(BLOCK_A, 3, lon=-74.10, lat=41.00)]
    result = resolved(
        parcels,
        [permit("P-1", BLOCK_A, 3)],
        acs=ACS_AVAILABLE,
        index=StubIndex({(-74.10, 41.00): BG_ONE_GEOID}),
        rental=FixtureRentalProvider({"0248_00101_00003"}),
    )
    (facts,) = result.doors.values()

    assert {key: p.source for key, p in facts.provenance.items()} == {
        SIGNAL_PARCEL: SOURCE_MODIV,
        SIGNAL_PERMITS: SOURCE_PERMITS,
        SIGNAL_ACS: SOURCE_ACS,
        SIGNAL_RENTAL: SOURCE_RENTAL,
    }


def test_per_source_retrieval_dates_are_carried_when_supplied():
    """R2.3/R13: the run manifest records when each source was actually read,
    which is not necessarily `as_of`."""
    fetched = {SIGNAL_PARCEL: date(2026, 8, 1), SIGNAL_PERMITS: date(2026, 8, 10)}
    result = resolved([door(BLOCK_A, 3)], [permit("P-1", BLOCK_A, 3)], retrieved_at=fetched)
    (facts,) = result.doors.values()

    assert facts.provenance[SIGNAL_PARCEL].retrieved == date(2026, 8, 1)
    assert facts.provenance[SIGNAL_PERMITS].retrieved == date(2026, 8, 10)


def test_retrieval_dates_default_to_as_of():
    result = resolved([door(BLOCK_A, 3)], [permit("P-1", BLOCK_A, 3)])
    (facts,) = result.doors.values()

    assert {p.retrieved for p in facts.provenance.values()} == {AS_OF}


# --- the seam ticket 009 consumes: DoorFacts -> ScoreInput --------------------------


RUN_CONFIG = dict(territory_median_value=500000.0, acs_dual_income_threshold=0.35)


def full_door_facts():
    parcels = [door(BLOCK_A, 3, lon=-74.10, lat=41.00, deed_date="260615", sale_price=850000.0)]
    result = resolved(
        parcels,
        [permit("P-1", BLOCK_A, 3, kind="Roofing")],
        acs=ACS_AVAILABLE,
        index=StubIndex({(-74.10, 41.00): BG_ONE_GEOID}),
        rental=FixtureRentalProvider({"0248_00101_00003"}),
    )
    (facts,) = result.doors.values()
    return facts


@pytest.mark.parametrize(
    "attribute, expected",
    [
        ("pams_pin", "0248_00101_00003"),
        ("deed_date", date(2026, 6, 15)),
        ("sale_price", 850000.0),
        ("sales_code", ""),
        ("yr_constr", 1962),
        ("net_value", 725000.0),
        ("calc_acre", 0.34),
        ("dual_income_pct", 0.41),
        ("median_hh_income", 145673.0),
        ("rental_registration_match", True),
        ("territory_median_value", 500000.0),
        ("acs_dual_income_threshold", 0.35),
        ("as_of", AS_OF),
    ],
)
def test_to_score_input_fills_every_field_without_re_deriving_anything(attribute, expected):
    score_input = full_door_facts().to_score_input(as_of=AS_OF, **RUN_CONFIG)

    assert isinstance(score_input, ScoreInput)
    assert getattr(score_input, attribute) == expected


def test_to_score_input_hands_over_engine_permits():
    score_input = full_door_facts().to_score_input(as_of=AS_OF, **RUN_CONFIG)

    assert all(isinstance(p, engine.Permit) for p in score_input.permits)
    assert [p.permit_type for p in score_input.permits] == ["Roofing"]
    assert [p.contractor for p in score_input.permits] == [None]


def test_to_score_input_passes_the_vision_payload_through():
    vision = {"pool": True, "condition_2015": "fair", "condition_2020": "poor"}
    score_input = full_door_facts().to_score_input(as_of=AS_OF, vision=vision, **RUN_CONFIG)

    assert dict(score_input.vision) == vision


def test_to_score_input_defaults_vision_to_empty():
    score_input = full_door_facts().to_score_input(as_of=AS_OF, **RUN_CONFIG)
    assert dict(score_input.vision) == {}


def test_a_door_with_no_block_group_hands_over_no_acs_numbers():
    (facts,) = resolved([door(BLOCK_A, 3)], acs=ACS_DECLINED).doors.values()
    score_input = facts.to_score_input(as_of=AS_OF, **RUN_CONFIG)

    assert score_input.dual_income_pct is None
    assert score_input.median_hh_income is None


def test_the_resolved_door_scores_end_to_end():
    scored = score_door(full_door_facts().to_score_input(as_of=AS_OF, **RUN_CONFIG))

    types = {item.type for item in scored.evidence}
    assert "permit_history" in types
    assert "acs_dual_income_prior" in types
    assert "absentee_likely" in types
    assert scored.raw_total == sum(item.points for item in scored.evidence)


# --- a door with nothing on it -------------------------------------------------------


def test_a_door_with_no_permits_no_acs_and_no_rental_still_resolves():
    (facts,) = resolved([door(BLOCK_A, 3)], []).doors.values()

    assert isinstance(facts, DoorFacts)
    assert facts.pams_pin == "0248_00101_00003"
    assert facts.permits_2yr == ()
    assert facts.block_group is None
    assert facts.rental_registration_match is False


def test_a_bare_door_still_scores_rather_than_being_dropped():
    (facts,) = resolved([door(BLOCK_A, 3, deed_date=None, yr_constr=0)], []).doors.values()
    scored = score_door(facts.to_score_input(as_of=AS_OF, **RUN_CONFIG))

    assert 0 <= scored.score <= 100
    assert scored.confidence == "low"


def test_resolution_never_drops_a_territory_door():
    parcels = [door(BLOCK_A, lot) for lot in range(1, 11)]
    result = resolved(parcels, [permit("P-1", BLOCK_A, 1)])

    assert set(result.doors) == {p.pams_pin for p in parcels}
    assert result.report.doors_total == 10


# --- determinism -------------------------------------------------------------------


def test_doors_are_ordered_exactly_as_the_territory_gave_them():
    """Territory order is nearest-first and meaningful; resolution preserves it."""
    parcels = [door(BLOCK_A, lot) for lot in (7, 2, 9, 1)]
    result = resolved(parcels, [])

    assert list(result.doors) == [p.pams_pin for p in parcels]


def test_the_same_inputs_produce_the_same_doors_and_the_same_report():
    parcels, permits = worked_example_doors(), worked_example_permits()
    first = resolved(parcels, permits, rental=FixtureRentalProvider({RENTAL_PIN}))
    second = resolved(parcels, permits, rental=FixtureRentalProvider({RENTAL_PIN}))

    assert list(first.doors) == list(second.doors)
    assert first.doors == second.doors
    assert first.report == second.report


def test_reordering_the_permits_changes_nothing():
    parcels = worked_example_doors()
    permits = worked_example_permits()
    forward = resolved(parcels, permits, rental=FixtureRentalProvider({RENTAL_PIN}))
    backward = resolved(
        parcels, list(reversed(permits)), rental=FixtureRentalProvider({RENTAL_PIN})
    )

    assert forward.report == backward.report
    assert list(forward.doors) == list(backward.doors)


def test_the_report_records_the_as_of_it_was_run_for():
    assert resolved([door(BLOCK_A, 3)], []).report.as_of == AS_OF


REPORT_FIELDS = (
    "as_of",
    "doors_total",
    "doors_with_signal",
    "coverage",
    "permits_total",
    "permits_out_of_window",
    "permits_out_of_territory",
    "permits_in_territory",
    "permits_matched",
    "matched_by_block_lot",
    "matched_by_address",
    "permit_match_rate",
    "block_lot_match_rate",
    "address_match_rate",
    "unmatched",
    "doors_with_block_group",
    "acs_available",
    "acs_reason",
    "rental_declination_reason",
)


@pytest.mark.parametrize("field_name", REPORT_FIELDS)
def test_the_report_carries_the_agreed_fields(field_name):
    assert field_name in ResolveReport.__dataclass_fields__


def test_the_result_carries_doors_and_a_report():
    result = resolved([door(BLOCK_A, 3)], [])

    assert isinstance(result, ResolveResult)
    assert isinstance(result.report, ResolveReport)
    assert set(ResolveResult.__dataclass_fields__) == {"doors", "report"}


# --- composed with the real block-group index (still no network) ---------------------


def test_resolution_composes_with_the_real_point_in_polygon_index():
    """StubIndex stands in everywhere above; once, end to end, with the real one."""
    inside = (-74.1555, 41.0447)
    outside = (-74.0000, 41.0447)
    ring = [
        [
            [LON - 0.01, LAT - 0.01],
            [LON + 0.01, LAT - 0.01],
            [LON + 0.01, LAT + 0.01],
            [LON - 0.01, LAT + 0.01],
            [LON - 0.01, LAT - 0.01],
        ]
    ]
    index = BlockGroupIndex(
        [
            BlockGroupBoundary(
                geoid=BG_ONE_GEOID,
                state="34",
                county="003",
                tract="011300",
                block_group="1",
                geometry={"type": "Polygon", "coordinates": ring},
            )
        ]
    )

    parcels = [
        door(BLOCK_A, 3, lon=inside[0], lat=inside[1]),
        door(BLOCK_A, 4, lon=outside[0], lat=outside[1]),
    ]
    doors = resolved(parcels, acs=ACS_AVAILABLE, index=index).doors

    assert doors["0248_00101_00003"].block_group == BG_ONE
    assert doors["0248_00101_00004"].block_group is None
