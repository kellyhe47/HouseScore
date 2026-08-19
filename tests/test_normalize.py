"""Normalizers (T002) — the parse/join seam every other module sits on.

Pinned surface (`src/houseaccount/normalize.py`, pure functions, no I/O):

    parse_deed_date(raw: str | None, as_of: date) -> date | None
    normalize_address(s: str | None) -> str
    parcel_key(mun, block, lot) -> str
    situs_display(prop_loc: str | None, zip5: str | None) -> str

The YYMMDD deed-parse cases were originally pinned by V1 golden fixture 11
(`eval/golden/11_deed_date_yymmdd_parse.json`). That directory is deleted with
the V1 contract (ticket 103 / plan R27), so the cases are inlined here verbatim:
the parser contract survives the cutover unchanged. The century pivot is derived
from `as_of` (`YY <= as_of.year % 100 + 1` -> 2000s, else 1900s), which a second
as_of in 2030 proves by shifting a boundary case pinned at 2026.

Both input shapes matter: fixtures feed the scorer ISO strings, live MOD-IV
feeds it YYMMDD, and `parse_deed_date` is the single door both go through.

`normalize_address` canonicalises to the SHORT street type ("12 OAK ST"), which
is the form R9.1's copy-address string shows.
"""

from datetime import date

import pytest

from houseaccount.normalize import (
    normalize_address,
    parcel_key,
    parse_deed_date,
    situs_display,
)

#: Inlined from V1 golden fixture 11 before eval/golden/ was deleted (R27):
#: raw DEED_DATE is a YYMMDD 2-digit-year string ('080122' = 2008-01-22); a
#: silent century bug ('26' -> 1926) would zero every mover.
AS_OF = date(2026, 8, 1)
CASES = [
    {"raw": "260712", "expect_iso": "2026-07-12"},
    {"raw": "080122", "expect_iso": "2008-01-22"},
    {"raw": "990315", "expect_iso": "1999-03-15"},
    {"raw": "270101", "expect_iso": "2027-01-01"},
    {"raw": "280101", "expect_iso": "1928-01-01"},
    {"raw": "", "expect_iso": None},
    {"raw": "9903", "expect_iso": None},
]


def _expected(case):
    return date.fromisoformat(case["expect_iso"]) if case["expect_iso"] else None


# --- deed date: the (inlined) fixture-11 contract ---------------------------


@pytest.mark.parametrize("case", CASES, ids=[c["raw"] or "<empty>" for c in CASES])
def test_fixture_11_deed_parse_cases(case):
    assert parse_deed_date(case["raw"], AS_OF) == _expected(case)


# --- century pivot is derived from as_of, not hardcoded to 2026 -------------


@pytest.mark.parametrize(
    "raw, as_of, expected",
    [
        # as_of 2026 -> pivot 27: "28" is last century (fixture 11 pins this).
        ("280101", date(2026, 8, 1), date(1928, 1, 1)),
        # as_of 2030 -> pivot 31: the SAME string now lands in this century.
        ("280101", date(2030, 1, 1), date(2028, 1, 1)),
        ("310101", date(2030, 1, 1), date(2031, 1, 1)),
        ("320101", date(2030, 1, 1), date(1932, 1, 1)),
    ],
)
def test_century_pivot_follows_as_of(raw, as_of, expected):
    assert parse_deed_date(raw, as_of) == expected


# --- unparseable input degrades to None, never raises ----------------------


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "   ",
        "9903",  # too short
        "2607121",  # too long
        "abcdef",  # non-digit
        "26071a",  # partly non-digit
        "261332",  # structurally valid, impossible month
        "260230",  # structurally valid, impossible day
        "999999",
        "2026-13-45",  # ISO-shaped but impossible
    ],
)
def test_unparseable_deed_dates_are_none(raw):
    assert parse_deed_date(raw, AS_OF) is None


# --- an already-ISO string passes through -----------------------------------


def test_iso_string_passes_through():
    assert parse_deed_date("2026-07-12", AS_OF) == date(2026, 7, 12)


def test_iso_and_yymmdd_agree():
    assert parse_deed_date("2026-07-12", AS_OF) == parse_deed_date("260712", AS_OF)


# --- address normalization --------------------------------------------------

#: Every variant in a group must normalise to the same key; keys of different
#: groups must stay distinct.
ADDRESS_GROUPS = [
    (
        "12  Oak  St.",
        "12 OAK STREET",
        "12 Oak St, Unit 2",
        "  12 oak st  ",
        "12 Oak St., Apt 3B",
        "12 OAK ST #2",
        "12 Oak Street, Ste 4",
    ),
    ("7 Maple Ave", "7 MAPLE AVENUE", "7 maple ave."),
    ("21 Birch Rd", "21 BIRCH ROAD"),
    ("3 Cedar Dr", "3 CEDAR DRIVE"),
    ("9 Elm Ln", "9 ELM LANE"),
    ("5 Fir Ct", "5 FIR COURT"),
    ("8 Grove Pl", "8 GROVE PLACE"),
    ("2 Hill Ter", "2 HILL TERRACE"),
    ("400 Island Blvd", "400 ISLAND BOULEVARD"),
    ("14 Juniper Way", "14 JUNIPER WAY"),
    ("6 Knoll Cir", "6 KNOLL CIRCLE"),
]


@pytest.mark.parametrize("group", ADDRESS_GROUPS, ids=[g[0] for g in ADDRESS_GROUPS])
def test_address_variants_collapse_to_one_key(group):
    keys = {normalize_address(variant) for variant in group}
    assert len(keys) == 1, f"variants normalised to {sorted(keys)}"


def test_different_addresses_keep_different_keys():
    keys = {normalize_address(group[0]) for group in ADDRESS_GROUPS}
    assert len(keys) == len(ADDRESS_GROUPS)


def test_canonical_key_is_upper_and_abbreviated():
    assert normalize_address("12 Oak Street") == "12 OAK ST"


@pytest.mark.parametrize("blank", [None, "", "   "])
def test_blank_address_normalises_to_empty_string(blank):
    assert normalize_address(blank) == ""


# --- parcel / permit join key ----------------------------------------------


def test_parcel_key_tolerates_zeros_whitespace_and_float_lots():
    assert parcel_key("0248", " 2702 ", "15") == parcel_key("248", "2702", "15.0")


@pytest.mark.parametrize(
    "left, right",
    [
        (("0248", "02702", "015"), ("248", "2702", "15")),
        (("0248", "2702.0", "15.00"), ("248", "2702", "15")),
        (("0248", " 2702", "15 "), ("0248", "2702", "15")),
    ],
)
def test_parcel_keys_that_must_match(left, right):
    assert parcel_key(*left) == parcel_key(*right)


@pytest.mark.parametrize(
    "left, right",
    [
        (("0248", "2702", "15"), ("0248", "2702", "151")),
        (("0248", "2702", "15"), ("0248", "2703", "15")),
        (("0248", "2702", "15"), ("0249", "2702", "15")),
        # a fractional lot is a different lot, not a truncated 15
        (("0248", "2702", "15"), ("0248", "2702", "15.5")),
    ],
)
def test_parcel_keys_that_must_differ(left, right):
    assert parcel_key(*left) != parcel_key(*right)


def test_parcel_key_is_a_string():
    assert isinstance(parcel_key("0248", "2702", "15"), str)


# --- situs display string (R9.1 copy-address) -------------------------------


def test_situs_display_with_zip():
    assert situs_display("12 OAK ST", "07446") == "12 OAK ST, Ramsey NJ 07446"


@pytest.mark.parametrize("missing", [None, "", "   "])
def test_situs_display_degrades_without_zip(missing):
    assert situs_display("12 OAK ST", missing) == "12 OAK ST, Ramsey NJ"
