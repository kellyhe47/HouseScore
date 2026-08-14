"""The NJ SR1A sales harvest (T020, R2.1/R11.1).

The feed is a fixed-width COBOL extract, so *position is the schema*. These
tests build records from the 1-indexed START/END columns printed in
`SR1Afilelayout.pdf` — deliberately re-typed here from the published layout
rather than imported from `sources.sales`, because a test that shares the
module's offset table would pass just as happily if every offset were wrong
together. `LAYOUT` below is the spec; the module is the thing under test.

Three properties matter more than the rest:

* **A wrong-width record is refused.** One dropped character shifts every field
  after it, and the failure is silent: dates and prices still parse, they are
  just someone else's.
* **Identity columns are never read.** The layout reserves grantor and grantee
  name, street, city/state and postcode. Today the state publishes them blank in
  all 169,935 records; these tests fill them with obvious junk and assert none of
  it survives, so the guarantee holds for the file that ships tomorrow too.
* **A condominium's units stay apart.** Twenty real Ramsey sales share block
  4001 / lot 22 and differ only by qualification code. Collapsing them would
  mark a whole complex as freshly moved on one neighbour's closing.
"""

import io
import zipfile
from datetime import date

import pytest

from houseaccount.cache import Cache
from houseaccount.http import Response, SourceError
from houseaccount.normalize import parse_deed_date, sale_key
from houseaccount.sources.sales import (
    RECORD_LENGTH,
    Sale,
    SalesSource,
    latest_by_parcel,
    sales_within,
)

#: Field -> (start, end, picture), 1-indexed and inclusive, exactly as the
#: layout PDF prints them. The picture matters: a `9(n)` column is right-aligned
#: and zero-filled, an `X(n)` column left-aligned and space-filled. Getting that
#: backwards is precisely the drift these tests exist to catch — `PROPERTY-CLASS`
#: is `XXX`, so the real file writes class 2 as "2  ", never "002".
#:
#: The identity columns are listed *on purpose*: the tests need to populate them
#: to prove the parser never reads them.
LAYOUT = {
    "COUNTY-CODE": (1, 2, "9"),
    "DISTRICT-CODE": (3, 4, "9"),
    "SR-NU-CODE": (35, 37, "X"),
    "REPORTED-SALES-PRICE": (38, 46, "9"),
    "VERIFIED-SALES-PRICE": (47, 55, "9"),
    "GRANTOR-NAME": (110, 144, "X"),
    "GRANTOR-STREET": (145, 169, "X"),
    "GRANTOR-CITY": (170, 194, "X"),
    "GRANTOR-ZIP": (195, 203, "9"),
    "GRANTEE-NAME": (204, 238, "X"),
    "GRANTEE-STREET": (239, 263, "X"),
    "GRANTEE-CITY": (264, 288, "X"),
    "GRANTEE-ZIP": (289, 297, "9"),
    "PROPERTY-LOCATION": (298, 322, "X"),
    "DEED-BOOK": (329, 333, "X"),
    "DEED-PAGE": (334, 338, "X"),
    "DEED-DATE": (339, 344, "9"),
    "DATE-RECORDED": (345, 350, "9"),
    "BLOCK": (351, 355, "X"),
    "LOT": (360, 364, "X"),
    "LOT-SUFFIX": (365, 368, "X"),
    "QUALIFICATION-CODES": (620, 624, "X"),
    "PROPERTY-CLASS": (627, 629, "X"),
    "YEAR-BUILT": (653, 656, "9"),
}


#: Every identity column the layout reserves, and the junk the tests write into
#: them. If any of these strings ever reaches a `Sale`, R11.1 is broken.
#:
#: The tokens are deliberately unmistakable rather than realistic: a plausible
#: value like "RAMSEY NJ" also occurs legitimately in every published situs
#: string, so a leak test built on one would fail on the product working.
IDENTITY_COLUMNS = {
    "GRANTOR-NAME": "GRANTORNAMELEAKZZ",
    "GRANTOR-STREET": "GRANTORSTREETLEAKZZ",
    "GRANTOR-CITY": "GRANTORCITYLEAKZZ",
    "GRANTOR-ZIP": "099998888",
    "GRANTEE-NAME": "GRANTEENAMELEAKZZ",
    "GRANTEE-STREET": "GRANTEESTREETLEAKZZ",
    "GRANTEE-CITY": "GRANTEECITYLEAKZZ",
    "GRANTEE-ZIP": "099997777",
}


def record(**fields):
    """One 663-character SR1A record with `fields` written at their columns.

    Alignment follows the column's PICTURE, not the look of the value, so a
    numeric-looking code in a text column is written the way the state writes it.
    """
    buffer = [" "] * RECORD_LENGTH
    for name, value in fields.items():
        start, end, picture = LAYOUT[name]
        width = end - start + 1
        text = str(value)
        if len(text) > width:
            raise AssertionError(f"{name} takes {width} chars, got {text!r}")
        padded = text.rjust(width, "0") if picture == "9" and text else text.ljust(width)
        buffer[start - 1 : end] = padded
    return "".join(buffer)


def ramsey(**overrides):
    """A complete, plausible Ramsey sale — identity columns filled with junk."""
    fields = {
        "COUNTY-CODE": "02",
        "DISTRICT-CODE": "48",
        "SR-NU-CODE": "",
        "REPORTED-SALES-PRICE": "890000",
        "PROPERTY-LOCATION": "41 RAMSEY AVE",
        "DEED-DATE": "260601",
        "DATE-RECORDED": "260612",
        "BLOCK": "03201",
        "LOT": "00022",
        "PROPERTY-CLASS": "2",
        "YEAR-BUILT": "2007",
        **IDENTITY_COLUMNS,
    }
    fields.update(overrides)
    return record(**fields)


def zipped(*records, name="YTDSR1A2026.txt"):
    """The records as the published archive delivers them: one zipped text file."""
    payload = "\r\n".join(records).encode("latin-1")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, payload)
    return buffer.getvalue()


def transport_for(body, status=200):
    calls = []

    def transport(method, url, params, headers):
        calls.append(url)
        return Response(status=status, body=body, headers={})

    transport.calls = calls
    return transport


AS_OF = date(2026, 8, 14)
WINDOW = 90


def fetch(*records, cache=None, body=None):
    source = SalesSource(cache=cache, transport=transport_for(body or zipped(*records)))
    return source.fetch("0248", as_of=AS_OF, window_days=WINDOW)


# --- the layout --------------------------------------------------------------


def test_the_builder_writes_the_width_the_layout_declares():
    """A guard on the tests themselves: every record below must be 663 chars."""
    assert len(ramsey()) == RECORD_LENGTH == 663


def test_a_record_is_parsed_at_the_columns_the_layout_prints():
    extract = fetch(ramsey())
    assert extract.available is True
    (sale,) = extract.sales
    assert sale.county == "02"
    assert sale.district == "48"
    assert sale.mun == "0248"
    assert sale.block == "3201"
    assert sale.lot == "22"
    assert sale.deed_date == "260601"
    assert sale.date_recorded == "260612"
    assert sale.sale_price == 890000.0
    assert sale.prop_class == "2"
    assert sale.year_built == 2007
    assert sale.prop_loc == "41 RAMSEY AVE"


def test_the_deed_date_goes_through_the_one_shared_parser():
    """SR1A writes YYMMDD exactly as MOD-IV does, so fixture 11's parser applies."""
    (sale,) = fetch(ramsey(**{"DEED-DATE": "260601"})).sales
    assert parse_deed_date(sale.deed_date, AS_OF) == date(2026, 6, 1)


@pytest.mark.parametrize("width", [662, 664])
def test_a_record_of_the_wrong_width_is_refused_not_mis_sliced(width):
    """The failure mode this guards is silent: every field after the drift still
    parses, it just belongs to a different column."""
    drifted = ramsey()
    drifted = drifted[:width] if width < RECORD_LENGTH else drifted + " "
    assert fetch(drifted).sales == ()


def test_a_record_from_another_municipality_is_dropped():
    assert fetch(ramsey(**{"DISTRICT-CODE": "49"})).sales == ()
    assert fetch(ramsey(**{"COUNTY-CODE": "13"})).sales == ()


# --- the identity guarantee (R11.1) ------------------------------------------


def test_no_identity_column_survives_into_a_sale():
    """The layout reserves eight identity columns. Populate every one of them and
    assert not one byte reaches the parsed record."""
    (sale,) = fetch(ramsey()).sales
    haystack = repr(vars(sale)).upper()
    for column, junk in IDENTITY_COLUMNS.items():
        assert junk.upper() not in haystack, f"{column} leaked into the Sale"


def test_the_identity_junk_really_was_in_the_record():
    """A guard on the guard: a test that asserted against an empty record would
    pass forever."""
    raw = ramsey()
    for junk in IDENTITY_COLUMNS.values():
        assert junk in raw


def test_the_cache_stores_only_the_distilled_projection(tmp_path):
    """The raw file is 113 MB and statewide. What lands on disk is ~240 non-identity
    rows for one town — never the download."""
    cache = Cache(tmp_path / "cache")
    fetch(ramsey(), cache=cache)

    on_disk = "\n".join(path.read_text() for path in cache.root.rglob("*")).upper()
    assert on_disk, "the distilled projection should have been cached"
    for junk in IDENTITY_COLUMNS.values():
        assert junk.upper() not in on_disk


def test_a_warm_cache_makes_no_second_request(tmp_path):
    cache = Cache(tmp_path / "cache")
    body = zipped(ramsey())

    first = SalesSource(cache=cache, transport=transport_for(body))
    first.fetch("0248", as_of=AS_OF, window_days=WINDOW)

    def explode(method, url, params, headers):
        raise AssertionError(f"warm run reached the network: {url}")

    warm = SalesSource(cache=cache, transport=explode).fetch(
        "0248", as_of=AS_OF, window_days=WINDOW
    )
    assert [sale.prop_loc for sale in warm.sales] == ["41 RAMSEY AVE"]


# --- condominiums ------------------------------------------------------------


def test_units_sharing_a_block_and_lot_stay_distinct():
    """Twenty real Ramsey sales report block 4001 / lot 22. Only the qualification
    code tells 224 Cambridge Drive from 112 Surrey Court."""
    extract = fetch(
        ramsey(
            **{
                "BLOCK": "04001",
                "LOT": "00022",
                "QUALIFICATION-CODES": "C0224",
                "PROPERTY-LOCATION": "224 CAMBRIDGE DRIVE",
                "DEED-DATE": "260604",
            }
        ),
        ramsey(
            **{
                "BLOCK": "04001",
                "LOT": "00022",
                "QUALIFICATION-CODES": "C0112",
                "PROPERTY-LOCATION": "112 SURREY COURT",
                "DEED-DATE": "251015",
            }
        ),
    )
    latest = latest_by_parcel(extract.sales, sale_key)

    assert len(latest) == 2, "the two units collapsed onto one parcel"
    assert latest["248/4001/22/C0224"].prop_loc == "224 CAMBRIDGE DRIVE"
    assert latest["248/4001/22/C0112"].prop_loc == "112 SURREY COURT"


def test_a_detached_house_keys_exactly_as_the_three_part_key():
    """A blank qualifier must reduce to the key the parcel feed already uses,
    or every ordinary door would stop joining."""
    (sale,) = fetch(ramsey()).sales
    assert sale_key(sale.mun, sale.block, sale.lot, sale.qualifier) == "248/3201/22"


def test_a_lot_suffix_is_rendered_the_way_mod_iv_writes_it():
    """SR1A keeps the suffix in its own column; MOD-IV writes the same parcel as
    "22.01". One spelling has to reach the join."""
    (sale,) = fetch(ramsey(**{"LOT": "00022", "LOT-SUFFIX": "01"})).sales
    assert sale.lot == "22.01"
    assert sale_key(sale.mun, sale.block, sale.lot, sale.qualifier) == "248/3201/22.01"


# --- selection ---------------------------------------------------------------


def test_the_most_recent_sale_wins_for_a_parcel():
    extract = fetch(
        ramsey(**{"DEED-DATE": "260601", "REPORTED-SALES-PRICE": "890000"}),
        ramsey(**{"DEED-DATE": "250110", "REPORTED-SALES-PRICE": "700000"}),
    )
    latest = latest_by_parcel(extract.sales, sale_key)
    assert latest["248/3201/22"].sale_price == 890000.0


def test_a_nominal_transfer_still_wins_when_it_is_the_newest():
    """`latest_by_parcel` reports the latest deed, exactly as MOD-IV's own field
    does. Deciding that a $1 quitclaim is not a move is the score engine's job,
    and fixtures 03 and 12 pin it there."""
    extract = fetch(
        ramsey(**{"DEED-DATE": "260701", "REPORTED-SALES-PRICE": "1", "SR-NU-CODE": "10"}),
        ramsey(**{"DEED-DATE": "260601", "REPORTED-SALES-PRICE": "890000"}),
    )
    latest = latest_by_parcel(extract.sales, sale_key)
    assert latest["248/3201/22"].sale_price == 1.0
    assert latest["248/3201/22"].nu_code == "10"


def test_a_record_with_no_deed_date_is_not_selectable():
    extract = fetch(ramsey(**{"DEED-DATE": ""}))
    assert latest_by_parcel(extract.sales, sale_key) == {}


def test_the_verified_price_is_preferred_over_the_reported_one():
    (sale,) = fetch(
        ramsey(**{"REPORTED-SALES-PRICE": "890000", "VERIFIED-SALES-PRICE": "875000"})
    ).sales
    assert sale.sale_price == 875000.0


def test_a_nominal_price_is_preserved_exactly():
    """$1 is the signal the engine's non-arm's-length rule reads."""
    (sale,) = fetch(ramsey(**{"REPORTED-SALES-PRICE": "1"})).sales
    assert sale.sale_price == 1.0


def test_sales_within_uses_the_shared_parser_and_an_inclusive_edge():
    inside = fetch(ramsey(**{"DEED-DATE": "260516"})).sales  # exactly 90 days
    outside = fetch(ramsey(**{"DEED-DATE": "260515"})).sales  # 91
    assert len(sales_within(inside, AS_OF, WINDOW, parse_deed_date)) == 1
    assert sales_within(outside, AS_OF, WINDOW, parse_deed_date) == []


# --- failure is a degradation, never an exception ----------------------------


def test_a_refused_download_reports_rather_than_raises():
    source = SalesSource(transport=transport_for(b"", status=404))
    extract = source.fetch("0248", as_of=AS_OF, window_days=WINDOW)
    assert extract.available is False
    assert extract.sales == ()
    assert "MOD-IV" in (extract.reason or ""), "the fallback must be named"


def test_a_corrupt_archive_reports_rather_than_raises():
    extract = fetch(body=b"this is not a zip")
    assert extract.available is False
    assert extract.reason


def test_an_archive_with_more_than_one_member_is_refused():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("a.txt", ramsey())
        archive.writestr("b.txt", ramsey())
    extract = fetch(body=buffer.getvalue())
    assert extract.available is False


def test_the_window_reaches_into_the_prior_year_only_when_it_crosses_january():
    """In early January the year-to-date file is nearly empty and the 90-day
    window sits mostly in the closed prior year — exactly when missing it would
    silently zero the Mover group again."""
    seen = []

    def transport(method, url, params, headers):
        seen.append(url)
        return Response(status=200, body=zipped(ramsey()), headers={})

    SalesSource(transport=transport).fetch(
        "0248", as_of=date(2026, 2, 1), window_days=WINDOW
    )
    assert any("YTDSR1A2026.zip" in url for url in seen)
    assert any("Sales2025.zip" in url for url in seen)

    seen.clear()
    SalesSource(transport=transport).fetch("0248", as_of=AS_OF, window_days=WINDOW)
    assert seen == [url for url in seen if "YTDSR1A2026.zip" in url]


def test_one_year_failing_still_returns_the_other_and_says_so():
    def transport(method, url, params, headers):
        if "Sales2025" in url:
            return Response(status=500, body=b"", headers={})
        return Response(status=200, body=zipped(ramsey()), headers={})

    extract = SalesSource(transport=transport).fetch(
        "0248", as_of=date(2026, 2, 1), window_days=WINDOW
    )
    assert extract.available is True
    assert len(extract.sales) == 1
    assert "Sales2025.zip" in (extract.reason or "")


def test_the_extract_names_the_files_it_actually_read():
    extract = fetch(ramsey())
    assert extract.source_files == (
        "https://www.nj.gov/treasury/taxation/lpt/statdata/YTDSR1A2026.zip",
    )
