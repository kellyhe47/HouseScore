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

**Two denominators, and which one R3.2 grades.** The report carries both, and
they are different numbers on purpose:

* `permit_match_rate` = permits matched to a *door* / permits in the window
  whose **block** the territory occupies. Territory-scoped. A block holds many
  parcels the territory does not, so this rate under-reports how well permits
  resolve to parcels — the first live run read 0.62 on a join that was fine.
* `municipal_match_rate` = permits joining **any municipal parcel** by block/lot
  / **all** in-window permits. Municipality-wide, and the one R3.2's >=95% floor
  is graded against (the live run reads 0.9742 here).

Neither denominator may be made trivially 1.0: exact block+lot containment as a
denominator would match by construction. The territory rate's denominator is
therefore block-level, and the municipal rate's is every in-window permit.

Permits carrying placeholder block/lot (`0000`/`00`) get their own bucket, and
permits naming a lot no parcel has stay in the municipal unmatched count — both
are real residual misses that must stay visible instead of being normalized
away. Lot-suffix variants (`4.2` vs `4.02`) are *distinct* lots in NJ MOD-IV
convention and are pinned as distinct here.

Permits on other blocks are out of territory, and permits outside the window are
out of scope — both are *counted* in the report rather than dropped, which is
the real content of "never silently dropped".

No network, no fixtures on disk: every parcel, permit and polygon below is
synthetic and built in-module.
"""

from dataclasses import dataclass
from datetime import date, timedelta

import pytest

from houseaccount import resolve as resolve_module
from houseaccount.normalize import normalize_address, parcel_key, situs_display
from houseaccount.resolve import (
    SIGNAL_ACS,
    SIGNAL_PARCEL,
    SIGNAL_PERMITS,
    SIGNAL_RENTAL,
    SIGNAL_SALES,
    DoorFacts,
    Provenance,
    ResolveReport,
    ResolveResult,
    UnmatchedPermit,
    resolve,
)
# The provenance vocabulary is resolve's own after the V1 engine's deletion
# (ticket 103 / plan R27): these names must import from houseaccount.resolve.
from houseaccount.resolve import (
    SOURCE_ACS,
    SOURCE_MODIV,
    SOURCE_PERMITS,
    SOURCE_RENTAL,
    SOURCE_SR1A,
)
from houseaccount.sources.acs import AcsResult, BlockGroupStats
from houseaccount.sources.parcels import DEFAULT_MUN, Parcel
from houseaccount.sources.permits import PermitRecord
from houseaccount.sources.rental import (
    DECLINATION_REASON,
    FixtureRentalProvider,
    NullRentalProvider,
)
from houseaccount.sources.sales import Sale
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


# --- the municipal denominator (the rate R3.2 grades) --------------------------
#
# The territory is block 101, lots 1-4. The municipality is nine parcels: those
# four, three more on the same block (lots 5-7), two on block 102, one on block
# 4203 (lot 3). Fourteen permits in.
#
#   IN WINDOW (12)                            territory        municipal
#     3 on doors            101/1,2,3         matched          matched
#     3 on the same block   101/5,6,7         IN, unmatched    matched
#     2 on block 102        102/1,2           out of terr.     matched
#     1 on block 4203       4203/3            out of terr.     matched
#     2 placeholders        0000/00           out of terr.     placeholder
#     1 naming no lot       4203/4            out of terr.     unmatched
#   OUT OF WINDOW (2)       101/1, 900 days ago
#   ---------------------------------------------------------------------------
#   territory:  3 matched / 6 in territory                     = 0.50
#   municipal:  9 matched / 12 in window                       = 0.75
#
# The two rates disagree, which is the whole point: 9 of the 12 permits resolve
# to a parcel, and the territory-scoped number cannot say so.

MUNICIPAL_BLOCK = "102"
GHOST_BLOCK = "4203"
PLACEHOLDER_BLOCK, PLACEHOLDER_LOT = "0000", "00"


def municipal_example_doors():
    """The territory: block 101, lots 1-4."""
    return [door(BLOCK_A, lot, street="MAPLE") for lot in range(1, 5)]


def municipal_example_parcels():
    """Every parcel the municipality has — the four doors plus five more."""
    return (
        municipal_example_doors()
        + [door(BLOCK_A, lot, street="MAPLE") for lot in (5, 6, 7)]
        + [door(MUNICIPAL_BLOCK, lot, street="ELM") for lot in (1, 2)]
        + [door(GHOST_BLOCK, 3, street="OAK")]
    )


def municipal_example_permits():
    on_doors = [permit(f"P-DOOR-{lot}", BLOCK_A, lot) for lot in (1, 2, 3)]
    same_block = [permit(f"P-BLOCK-{lot}", BLOCK_A, lot) for lot in (5, 6, 7)]
    other_blocks = [permit(f"P-ELM-{lot}", MUNICIPAL_BLOCK, lot) for lot in (1, 2)]
    other_blocks += [permit("P-OAK-3", GHOST_BLOCK, 3)]
    placeholders = [
        permit(f"P-PLACEHOLDER-{n}", PLACEHOLDER_BLOCK, PLACEHOLDER_LOT) for n in (1, 2)
    ]
    ghost = [permit("P-GHOST", GHOST_BLOCK, 4)]
    stale = [permit(f"P-OLD-{n}", BLOCK_A, 1, days_ago=900) for n in (1, 2)]
    return on_doors + same_block + other_blocks + placeholders + ghost + stale


@pytest.fixture
def municipal_example():
    return resolved(
        municipal_example_doors(),
        municipal_example_permits(),
        municipal_parcels=municipal_example_parcels(),
    )


MUNICIPAL_EXAMPLE_NUMBERS = [
    ("permits_total", 14),
    ("permits_out_of_window", 2),
    ("permits_in_window", 12),
    ("permits_matched_municipal", 9),
    ("permits_placeholder_block_lot", 2),
    ("permits_unmatched_municipal", 1),
    ("municipal_match_rate", 0.75),
]

#: The same run, read through the territory-scoped fields. Unchanged in meaning
#: by the municipal set being supplied — only newly accompanied.
MUNICIPAL_EXAMPLE_TERRITORY_NUMBERS = [
    ("permits_out_of_territory", 6),
    ("permits_in_territory", 6),
    ("permits_matched", 3),
    ("matched_by_block_lot", 3),
    ("matched_by_address", 0),
    ("permit_match_rate", 0.5),
]


@pytest.mark.parametrize(
    "field_name, expected", MUNICIPAL_EXAMPLE_NUMBERS + MUNICIPAL_EXAMPLE_TERRITORY_NUMBERS
)
def test_the_municipal_worked_example_report_is_exactly_this(
    municipal_example, field_name, expected
):
    assert getattr(municipal_example.report, field_name) == pytest.approx(expected)


def test_the_two_rates_are_different_numbers_on_the_same_run(municipal_example):
    """The whole ticket: one number is territory-scoped, the other is not."""
    report = municipal_example.report
    assert report.permit_match_rate == pytest.approx(0.5)
    assert report.municipal_match_rate == pytest.approx(0.75)
    assert report.municipal_match_rate != report.permit_match_rate


def test_every_in_window_permit_lands_in_exactly_one_municipal_bucket(municipal_example):
    report = municipal_example.report
    assert report.permits_in_window == report.permits_total - report.permits_out_of_window
    assert (
        report.permits_matched_municipal
        + report.permits_placeholder_block_lot
        + report.permits_unmatched_municipal
        == report.permits_in_window
    )


def test_the_municipal_rate_divides_by_every_in_window_permit(municipal_example):
    report = municipal_example.report
    assert report.municipal_match_rate == pytest.approx(
        report.permits_matched_municipal / report.permits_in_window
    )


def test_placeholder_permits_are_their_own_bucket_not_silent_unmatched(municipal_example):
    """Two placeholders and one absent lot: three misses, two distinct kinds."""
    report = municipal_example.report
    assert report.permits_placeholder_block_lot == 2
    assert report.permits_unmatched_municipal == 1  # P-GHOST alone


@pytest.mark.parametrize(
    "block, lot, placeholder",
    [
        ("0000", "00", True),
        ("0", "0", True),
        ("", "", True),
        ("0000", "4", True),
        ("4203", "00", True),
        ("4203", "4", False),
    ],
    ids=[
        "both-placeholders",
        "bare-zeros",
        "blank",
        "placeholder-block",
        "placeholder-lot",
        "a-real-lot-the-parcel-file-lacks",
    ],
)
def test_a_placeholder_block_or_lot_is_counted_apart_from_a_real_miss(block, lot, placeholder):
    doors = [door(BLOCK_A, 1)]
    report = resolved(
        doors, [permit("P-1", block, lot)], municipal_parcels=doors + [door(GHOST_BLOCK, 3)]
    ).report

    assert report.permits_in_window == 1
    assert report.permits_matched_municipal == 0
    assert report.permits_placeholder_block_lot == (1 if placeholder else 0)
    assert report.permits_unmatched_municipal == (0 if placeholder else 1)


@pytest.mark.parametrize(
    "parcel_lot, permit_lot, joins",
    [
        ("4.02", "4.02", True),
        ("4.2", "4.2", True),
        ("4.02", "4.2", False),
        ("4.2", "4.02", False),
    ],
    ids=["same-suffix", "same-other-suffix", "02-is-not-2", "2-is-not-02"],
)
def test_lot_suffix_variants_are_distinct_lots_municipality_wide(parcel_lot, permit_lot, joins):
    """NJ MOD-IV convention: `4.2` and `4.02` are different parcels. Collapsing
    them would invent matches to flatter the rate."""
    doors = [door(BLOCK_A, 1)]
    municipal = doors + [door(GHOST_BLOCK, parcel_lot)]
    report = resolved(
        doors, [permit("P-1", GHOST_BLOCK, permit_lot)], municipal_parcels=municipal
    ).report

    assert report.permits_matched_municipal == (1 if joins else 0)
    assert report.municipal_match_rate == pytest.approx(1.0 if joins else 0.0)


def test_the_municipal_join_is_block_lot_not_the_door_address_fallback():
    """The municipal rate answers "did this permit resolve to a parcel", which is
    the block/lot question — the address fallback exists to place a record on a
    *door*, and is reported by `matched_by_address`."""
    doors = [door(BLOCK_A, 12, street="MAPLE")]
    result = resolved(
        doors, [addressed("P-1", BLOCK_A, "999", "12 Maple St")], municipal_parcels=doors
    )

    assert result.report.matched_by_address == 1
    assert result.report.permits_matched_municipal == 0
    assert result.report.permits_unmatched_municipal == 1


def test_omitting_the_municipal_set_measures_against_the_territory_parcels():
    """The parameter defaults to the parcels given, so today's callers keep
    working — the denominator is still every in-window permit."""
    report = resolved(municipal_example_doors(), municipal_example_permits()).report

    assert report.permits_in_window == 12
    assert report.permits_matched_municipal == 3
    assert report.permits_placeholder_block_lot == 2
    assert report.permits_unmatched_municipal == 7
    assert report.municipal_match_rate == pytest.approx(0.25)


TERRITORY_SCOPED_FIELDS = (
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
)


@pytest.mark.parametrize("field_name", TERRITORY_SCOPED_FIELDS)
def test_supplying_a_municipal_set_changes_no_territory_scoped_number(field_name):
    doors, permits = municipal_example_doors(), municipal_example_permits()
    without = resolved(doors, permits).report
    with_municipal = resolved(doors, permits, municipal_parcels=municipal_example_parcels()).report

    assert getattr(with_municipal, field_name) == getattr(without, field_name)


def test_a_municipality_with_no_in_window_permits_reports_a_zero_rate_not_a_crash():
    doors = [door(BLOCK_A, 1)]
    report = resolved(
        doors, [permit("P-1", BLOCK_A, 1, days_ago=900)], municipal_parcels=doors
    ).report

    assert report.permits_in_window == 0
    assert report.municipal_match_rate == 0.0


@pytest.mark.parametrize("needle", ["R3.2", "municipal_match_rate", "permit_match_rate"])
def test_the_report_docstring_names_both_denominators(needle):
    """Shape, not prose: a reader of the dataclass must be told which rate the
    rubric grades without leaving the file."""
    assert needle in (ResolveReport.__doc__ or "")


@pytest.mark.parametrize("needle", ["R3.2", "municipal_match_rate"])
def test_the_module_docstring_explains_the_two_denominators(needle):
    assert needle in (resolve_module.__doc__ or "")


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
    "deed_source",
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


# --- the seam the pipeline consumes ------------------------------------------------
#
# V1's DoorFacts.to_score_input()/ScoreInput seam is deleted with the engine
# (ticket 103 / plan R27). The V2 pipeline seam is DoorFacts -> evidence bundle
# -> score_door_v2, pinned by the locked tests/test_v2_bundle.py; the DoorFacts
# field values themselves are pinned throughout this module.

# --- a door with nothing on it -------------------------------------------------------


def test_a_door_with_no_permits_no_acs_and_no_rental_still_resolves():
    (facts,) = resolved([door(BLOCK_A, 3)], []).doors.values()

    assert isinstance(facts, DoorFacts)
    assert facts.pams_pin == "0248_00101_00003"
    assert facts.permits_2yr == ()
    assert facts.block_group is None
    assert facts.rental_registration_match is False


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
    # The municipal denominator (R3.2's floor is graded on `municipal_match_rate`).
    "permits_in_window",
    "permits_matched_municipal",
    "permits_placeholder_block_lot",
    "permits_unmatched_municipal",
    "municipal_match_rate",
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


# --- the SR1A merge (T020) ----------------------------------------------------
#
# MOD-IV carries one deed per parcel and the county extract runs badly stale —
# on the run that prompted this source, twenty months. The sales register is the
# same state's continuously-published record of the same transfers. Where both
# describe a parcel, the newer one is the truth about who lives there now.
#
# The merge is deliberately narrow: it swaps a *deed*, not a score. Whether the
# resulting transfer counts as a move is still decided by the engine's
# non-arm's-length rule, which fixtures 03 and 12 pin and this ticket leaves
# untouched.


def sale(block, lot, deed_date, *, qualifier="", price=890000.0, nu_code="", recorded="260612"):
    return Sale(
        county="02",
        district="48",
        block=str(block),
        lot=str(lot),
        qualifier=qualifier,
        prop_loc="41 RAMSEY AVE",
        deed_date=deed_date,
        date_recorded=recorded,
        sale_price=price,
        nu_code=nu_code,
        prop_class="2",
        year_built=2007,
    )


def only_door(result):
    (facts,) = result.doors.values()
    return facts


def test_a_fresher_sale_supersedes_the_stale_parcel_deed():
    """The whole point of the source: MOD-IV says 2020, the register says this
    June, and the door is scored on this June."""
    result = resolved([door(101, 3, deed_date="200916")], sales=[sale(101, 3, "260601")])

    facts = only_door(result)
    assert facts.deed_date == date(2026, 6, 1)
    assert facts.deed_source == SOURCE_SR1A
    assert facts.provenance[SIGNAL_SALES] == Provenance(source=SOURCE_SR1A, retrieved=AS_OF)


def test_the_superseding_record_moves_as_one_piece():
    """Taking the register's date but the parcel's price would describe two
    different transfers as one — and the engine's nominal rule reads that pair."""
    result = resolved(
        [door(101, 3, deed_date="200916", sale_price=1.0, sales_code="26")],
        sales=[sale(101, 3, "260601", price=890000.0, nu_code="")],
    )

    facts = only_door(result)
    assert (facts.sale_price, facts.sales_code) == (890000.0, "")


def test_an_older_sale_leaves_the_parcel_record_authoritative():
    result = resolved([door(101, 3, deed_date="250110")], sales=[sale(101, 3, "240101")])

    facts = only_door(result)
    assert facts.deed_date == date(2025, 1, 10)
    assert facts.deed_source == SOURCE_MODIV
    assert SIGNAL_SALES not in facts.provenance


def test_the_same_transfer_in_both_registers_stays_attributed_to_the_parcel():
    """Once the county catches up both feeds name the same deed. Flipping
    provenance on a date that never moved would make two identical runs disagree
    about where the fact came from."""
    result = resolved([door(101, 3, deed_date="260601")], sales=[sale(101, 3, "260601")])

    assert only_door(result).deed_source == SOURCE_MODIV


def test_a_parcel_with_no_deed_at_all_takes_the_register_s():
    """98 of 540 real doors carry a null deed. A sale is strictly better than the
    R6.1 degraded path, and must not be withheld because there was nothing to
    compare against."""
    result = resolved([door(101, 3, deed_date=None)], sales=[sale(101, 3, "260601")])

    facts = only_door(result)
    assert facts.deed_date == date(2026, 6, 1)
    assert facts.deed_source == SOURCE_SR1A


def test_a_sale_for_a_parcel_the_territory_does_not_hold_changes_nothing():
    result = resolved([door(101, 3, deed_date="200916")], sales=[sale(999, 99, "260601")])

    assert only_door(result).deed_source == SOURCE_MODIV


def test_one_units_sale_does_not_move_its_neighbours_in_the_complex():
    """Twenty real Ramsey sales share block 4001 / lot 22. Without the qualifier
    in the key, one closing would mark the whole complex freshly moved — the
    single worst failure this source could introduce."""
    sold = door(4001, 22, qualifier="C0224", deed_date="200916")
    neighbour = door(4001, 22, qualifier="C0112", deed_date="200916")
    # Distinct PINs, exactly as the parcel layer publishes them.
    sold = Parcel(**{**vars(sold), "pams_pin": "0248_4001_22_C0224"})
    neighbour = Parcel(**{**vars(neighbour), "pams_pin": "0248_4001_22_C0112"})

    result = resolved([sold, neighbour], sales=[sale(4001, 22, "260604", qualifier="C0224")])

    assert result.doors["0248_4001_22_C0224"].deed_date == date(2026, 6, 4)
    assert result.doors["0248_4001_22_C0224"].deed_source == SOURCE_SR1A
    assert result.doors["0248_4001_22_C0112"].deed_date == date(2020, 9, 16)
    assert result.doors["0248_4001_22_C0112"].deed_source == SOURCE_MODIV


def test_a_nominal_sale_still_supersedes_the_recorded_date():
    """The register makes the transfer visible even when it is not a move: the
    resolver supersedes the date and hands the price/NU code through, and the
    V2 engine's refusal to treat it as a mover (R3) is pinned by the locked
    tests/test_v2_engine.py / tests/test_v2_golden.py."""
    result = resolved(
        [door(101, 3, deed_date="200916")],
        sales=[sale(101, 3, "260601", price=1.0, nu_code="10")],
    )
    facts = only_door(result)
    assert facts.deed_source == SOURCE_SR1A
    assert facts.deed_date == date(2026, 6, 1)
    assert facts.sale_price == 1.0
    assert facts.sales_code.strip() == "10"


def test_the_report_counts_what_the_register_actually_supplied():
    result = resolved(
        [door(101, 3, deed_date="200916"), door(101, 5, deed_date="260701")],
        sales=[sale(101, 3, "260601"), sale(101, 5, "240101"), sale(999, 1, "260601")],
    )

    report = result.report
    assert report.sales_total == 3
    assert report.sales_parcels == 3
    # Only door 3 was actually superseded: door 5's own deed is newer than its
    # sale, and the third sale belongs to no territory parcel.
    assert report.sales_applied == 1


def test_no_sales_at_all_leaves_every_door_exactly_as_before():
    """The source is not load-bearing: with the register absent the run is
    byte-identical to the one before this ticket existed."""
    parcels = [door(101, 3, deed_date="200916"), door(101, 5, deed_date="260701")]
    without = resolved(parcels)
    with_empty = resolved(parcels, sales=[])

    assert {pin: f.deed_date for pin, f in without.doors.items()} == {
        pin: f.deed_date for pin, f in with_empty.doors.items()
    }
    assert all(f.deed_source == SOURCE_MODIV for f in without.doors.values())


# The V1 golden-fixture-13 replay tests are deleted with eval/golden/ and the
# V1 engine (ticket 103). SR1A-supersedes-MOD-IV resolution stays pinned above;
# the scoring consequences of a superseding sale are pinned by eval/v2/golden
# through the locked tests/test_v2_golden.py.
