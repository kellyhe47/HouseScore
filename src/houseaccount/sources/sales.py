"""NJ SR1A sales flat-file harvest — the fresh deed source (T020, R2.1).

MOD-IV carries one deed per parcel and the county extract behind it runs badly
stale: on the run that prompted this module the newest deed anywhere in Ramsey
was 2024-12-06, twenty months old, so the 100-point Mover group — the heaviest
signal in the model — scored zero on all 540 doors. SR1A is the same state's
sales register published continuously: the year-to-date file carried Ramsey
deeds through 2026-06-15 and had been refreshed two days before that run.

Three realities of this feed drive the shapes here.

*It is a fixed-width COBOL extract, not an API.* One 663-character record per
sale, no delimiters, published as a zip of a 113 MB text file covering all 21
counties. Position is the only thing identifying a field, so a record of the
wrong width is rejected outright rather than sliced into plausible nonsense —
a one-character drift would silently move every date and price.

*The layout reserves grantor and grantee identity columns* — name, street,
city/state and postcode for both parties, spanning offsets 110 through 297. In
every one of the 169,935 records published today those columns are blank; the
state redacts them before publication. That is an observation about this file,
not a promise about the next one, so this module reads an **allowlist** of
non-identity columns and never slices those offsets at all. It is the same
guarantee `parcels.py` gets from naming `outFields` instead of asking for `*`,
and it is why the raw download is never cached: only the distilled projection
below reaches disk (R11.1).

*A condominium's units share one block and lot.* Twenty Ramsey sales — 224
Cambridge Drive, 112 Surrey Court, 300 Coventry Court and seventeen more —
all report block 4001, lot 22, and are separated only by their qualification
code. The parcel layer agrees: those doors are `0248_4001_22_C0115` and so on.
So the qualifier is part of the join key, not decoration. Dropping it would
attach one unit's sale to all twenty-five doors in the complex and invent a
fresh mover for twenty-four households that never moved.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Iterable, Iterator, Mapping, Sequence

from houseaccount.cache import Cache
from houseaccount.http import SourceError, Transport, fetch_bytes, requests_transport

#: The year-to-date file, refreshed through the year — the fresh one.
YTD_URL_TEMPLATE = "https://www.nj.gov/treasury/taxation/lpt/statdata/YTDSR1A{year}.zip"

#: Closed prior years. Only reached when the mover window crosses 1 January.
ANNUAL_URL_TEMPLATE = "https://www.nj.gov/treasury/taxation/lpt/statdata/Sales{year}.zip"

#: Every record in the published file is exactly this wide (verified across all
#: 169,935 records of `YTDSR1A2026.txt`). The layout PDF declares the same.
RECORD_LENGTH = 663

#: Ramsey Borough: MOD-IV's `PCL_MUN` "0248" is county 02 + district 48, and
#: SR1A writes those as its first two fields.
DEFAULT_MUN = "0248"

#: The only columns this module is allowed to read, as (start, end) 0-indexed
#: half-open slices derived from the 1-indexed START/END of `SR1Afilelayout.pdf`.
#:
#: This is an allowlist, and its completeness is the point: the grantor and
#: grantee blocks simply have no entry here, so no code path can reach them.
#: `_distil` builds every record exclusively from these names.
FIELD_SLICES: Mapping[str, tuple[int, int]] = {
    "county": (0, 2),
    "district": (2, 4),
    "nu_code": (34, 37),
    "reported_price": (37, 46),
    "verified_price": (46, 55),
    "prop_loc": (297, 322),
    "deed_book": (328, 333),
    "deed_page": (333, 338),
    "deed_date": (338, 344),
    "date_recorded": (344, 350),
    "block": (350, 355),
    "block_suffix": (355, 359),
    "lot": (359, 364),
    "lot_suffix": (364, 368),
    "qualifier": (619, 624),
    "prop_class": (626, 629),
    "year_built": (652, 656),
}

#: Cache-key discriminator. Bumped whenever `FIELD_SLICES` changes, so a widened
#: projection can never be served the narrow projection's cached answer — the
#: discipline `parcels.py` gets for free by putting `outFields` in the params.
PROJECTION_VERSION = "sr1a-663-v1"


@dataclass(frozen=True)
class Sale:
    """One recorded sale, stripped to the non-identity columns the score needs.

    A closed frozen dataclass with no raw attribute bag: like `Parcel`, a feed
    that starts populating the grantor and grantee columns has nowhere to put
    them even if a future edit were careless enough to slice them.
    """

    county: str = ""
    district: str = ""
    block: str = ""
    lot: str = ""
    qualifier: str = ""
    #: Situs of the property that sold — the same kind of string MOD-IV's
    #: `PROP_LOC` carries and the UI already publishes. Not a party's address.
    prop_loc: str = ""
    #: Raw `YYMMDD`, parsed downstream by `normalize.parse_deed_date` — the one
    #: parser fixture 11 pins, so SR1A and MOD-IV cannot drift apart.
    deed_date: str = ""
    date_recorded: str = ""
    sale_price: float = 0.0
    #: SR1A's non-usable code. Blank means an arm's-length sale; any value is a
    #: reason the state deemed the sale unusable, which is exactly what MOD-IV's
    #: `SALES_CODE` means to the score engine.
    nu_code: str = ""
    prop_class: str = ""
    year_built: int = 0

    @property
    def mun(self) -> str:
        """The `PCL_MUN` form MOD-IV and the permit feed both use."""
        return f"{self.county}{self.district}"


@dataclass(frozen=True)
class SalesExtract:
    """What the harvest returned, plus what it could not get.

    `available` is False only when every requested year failed; a run that got
    the year-to-date file but not a closed prior year still scores, and says so.
    """

    sales: tuple[Sale, ...] = ()
    source_files: tuple[str, ...] = ()
    available: bool = False
    reason: str | None = None


class SalesSource:
    """Cache-first reader for the SR1A sales register.

    The raw zip is deliberately fetched **without** the cache: it is 11 MB
    compressed, 113 MB open, statewide, and carries the reserved identity
    columns. What gets cached is the distilled municipal projection — roughly
    240 rows for Ramsey, non-identity by construction — so a warm run still
    makes zero network calls (R2.3) without ever writing those bytes to disk.
    """

    def __init__(
        self,
        *,
        cache: Cache | None = None,
        transport: Transport = requests_transport,
    ) -> None:
        self.cache = cache
        self.transport = transport

    def fetch(
        self,
        mun: str = DEFAULT_MUN,
        *,
        as_of: date,
        window_days: int,
    ) -> SalesExtract:
        """Every sale this municipality recorded in the years the window touches.

        A failure on any one year is reported rather than raised: the deed date
        already on the parcel is a usable fallback, so a refused download must
        degrade the run, never end it.
        """
        sales: list[Sale] = []
        files: list[str] = []
        failures: list[str] = []

        for year in _years_for(as_of=as_of, window_days=window_days):
            url = _url_for(year, as_of=as_of)
            try:
                sales.extend(self._year(url, mun=mun))
            except (SourceError, zipfile.BadZipFile, ValueError) as error:
                failures.append(f"{url.rsplit('/', 1)[-1]} ({error})")
                continue
            files.append(url)

        if not files:
            return SalesExtract(
                available=False,
                reason=(
                    "the NJ SR1A sales register could not be read ("
                    + "; ".join(failures)
                    + "), so deed recency falls back to the MOD-IV extract's own deed date"
                ),
            )

        # Newest first, so a later "most recent sale wins" merge reads in order.
        sales.sort(key=_recency, reverse=True)
        return SalesExtract(
            sales=tuple(sales),
            source_files=tuple(files),
            available=True,
            reason=(
                "part of the SR1A register was unavailable: " + "; ".join(failures)
                if failures
                else None
            ),
        )

    # --- internals ----------------------------------------------------------

    def _year(self, url: str, *, mun: str) -> list[Sale]:
        """One year's sales for one municipality, from cache when warm."""
        key = None
        if self.cache is not None:
            key = self.cache.key(url, {"mun": mun, "projection": PROJECTION_VERSION})
            cached = self.cache.get(key)
            if cached is not None:
                return [Sale(**row) for row in cached]

        sales = list(self._download(url, mun=mun))
        if key is not None and self.cache is not None:
            self.cache.put(key, [vars(sale) for sale in sales])
        return sales

    def _download(self, url: str, *, mun: str) -> Iterator[Sale]:
        """Stream the zip and yield this municipality's distilled sales.

        The archive member is read line by line rather than into one string:
        the open file is 113 MB and only a few hundred of its records are ours.
        """
        body = fetch_bytes(url, cache=None, transport=self.transport)
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            name = _sole_member(archive)
            with archive.open(name) as member:
                for raw in io.TextIOWrapper(member, encoding="latin-1", newline=""):
                    sale = _distil(raw, mun=mun)
                    if sale is not None:
                        yield sale


# --- record -> Sale -----------------------------------------------------------


def _distil(raw: str, *, mun: str) -> Sale | None:
    """One flat record -> a `Sale`, or None when it is not ours to keep.

    Returns None for a record from another municipality and for any record whose
    width is not the layout's: at that point the field positions are unknown, and
    a best-effort slice would produce a confident, wrong date.
    """
    line = raw.rstrip("\r\n")
    if len(line) != RECORD_LENGTH:
        return None

    field = {name: line[start:end].strip() for name, (start, end) in FIELD_SLICES.items()}
    if f"{field['county']}{field['district']}" != mun:
        return None

    return Sale(
        county=field["county"],
        district=field["district"],
        block=_component(field["block"], field["block_suffix"]),
        lot=_component(field["lot"], field["lot_suffix"]),
        qualifier=field["qualifier"],
        prop_loc=field["prop_loc"],
        deed_date=field["deed_date"],
        date_recorded=field["date_recorded"],
        sale_price=_price(field["reported_price"], field["verified_price"]),
        nu_code=field["nu_code"],
        prop_class=field["prop_class"],
        year_built=int(field["year_built"]) if field["year_built"].isdigit() else 0,
    )


def _component(value: str, suffix: str) -> str:
    """Block or lot plus its suffix, in the `22.01` form MOD-IV publishes.

    SR1A stores the suffix in its own column; MOD-IV writes the same parcel's lot
    as "22.01". Rendering it here means `normalize` sees one spelling from both
    feeds and the join needs no special case.
    """
    base = value.lstrip("0") or ("0" if value else "")
    return f"{base}.{suffix}" if suffix else base


def _price(reported: str, verified: str) -> float:
    """The sale price, preferring the state's verified figure when it carries one.

    A nominal $1 transfer stays $1: the score engine's non-arm's-length rule
    reads this number, and rounding it away would turn a quitclaim into a move.
    """
    for candidate in (verified, reported):
        digits = candidate.lstrip("0")
        if digits.isdigit():
            return float(digits)
    return 0.0


def _sole_member(archive: zipfile.ZipFile) -> str:
    """The archive's single data member.

    The published zips carry exactly one text file. Picking blindly would make a
    future multi-file archive fail somewhere far from the cause.
    """
    names = [name for name in archive.namelist() if not name.endswith("/")]
    if len(names) != 1:
        raise ValueError(f"expected one member in the SR1A archive, found {len(names)}")
    return names[0]


def _recency(sale: Sale) -> tuple[str, str]:
    """Sort key: deed date, then the recording date as a tiebreak.

    Both are `YYMMDD` strings from the same decade-spanning feed, so they sort
    lexically in date order for every year this product will see.
    """
    return (sale.deed_date, sale.date_recorded)


def _years_for(*, as_of: date, window_days: int) -> list[int]:
    """Which yearly files the mover window touches, newest first.

    Almost always just the current year. In the first weeks of January the
    90-day window reaches back over the boundary, and the year-to-date file is
    nearly empty — exactly when a missed prior year would silently zero the
    Mover group again.
    """
    years = {as_of.year, (as_of - timedelta(days=window_days)).year}
    return sorted(years, reverse=True)


def _url_for(year: int, *, as_of: date) -> str:
    """The year-to-date file for the current year, the annual file for any past one."""
    template = YTD_URL_TEMPLATE if year == as_of.year else ANNUAL_URL_TEMPLATE
    return template.format(year=year)


# --- selection ----------------------------------------------------------------


def latest_by_parcel(sales: Iterable[Sale], key: Any) -> dict[str, Sale]:
    """Collapse sales to the most recent one per parcel.

    `key` is the join-key function, injected so this module never has to know how
    `normalize` spells a parcel. The most recent *recorded* sale wins outright,
    arm's-length or not: MOD-IV's own `DEED_DATE` means "the latest deed", and
    the score engine — not this module — decides that a nominal transfer earns no
    mover points. Choosing the newest arm's-length sale instead would quietly
    disagree with the field it is replacing.
    """
    latest: dict[str, Sale] = {}
    for sale in sales:
        if not sale.deed_date:
            continue
        parcel = key(sale.mun, sale.block, sale.lot, sale.qualifier)
        current = latest.get(parcel)
        if current is None or _recency(sale) > _recency(current):
            latest[parcel] = sale
    return latest


def sales_within(sales: Sequence[Sale], as_of: date, window_days: int, parse: Any) -> list[Sale]:
    """Every sale whose deed date falls inside the window ending at `as_of`.

    `parse` is `normalize.parse_deed_date`, injected for the same reason as
    `key` above — one parser, shared with the parcel feed and pinned by fixture 11.
    """
    inside = []
    for sale in sales:
        parsed = parse(sale.deed_date, as_of)
        if parsed is not None and 0 <= (as_of - parsed).days <= window_days:
            inside.append(sale)
    return inside
