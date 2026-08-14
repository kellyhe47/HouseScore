"""Normalizers: deed dates, situs addresses, parcel/permit join keys (T002).

Every downstream module joins records that three different agencies typed by
hand, so the messiness has to be absorbed in exactly one place rather than
re-guessed at each call site.

Three specific realities drive the shapes here:

* MOD-IV writes `DEED_DATE` as a 2-digit-year `YYMMDD` string, while the golden
  fixtures (and anything already through a pipeline) carry ISO. Both go through
  `parse_deed_date`, which never raises — a bad deed date degrades the Mover
  group (R6.1) instead of taking down a whole run.
* The century pivot is derived from `as_of`, never from a literal year: a
  hardcoded pivot silently misparses every recent deed as 1926 the moment the
  clock rolls forward, and every ISO-fed test would still pass.
* Socrata permits carry `block`/`lot` as free text while ArcGIS carries
  `PCLBLOCK`/`PCLLOT`; the same parcel shows up as "0248"/"15" and "248"/"15.0".
  `parcel_key` collapses that, but a fractional lot ("15.5") is a *different*
  parcel and must never collide with "15".

Pure functions only: no I/O, no network.
"""

from __future__ import annotations

import re
from datetime import date

#: The one municipality this MVP covers; the situs string is written for a rep
#: pasting it into a phone's map app, so it always carries town and state.
MUNICIPALITY = "Ramsey"
STATE = "NJ"

#: Long street types -> the short form R9.1's copy-address string shows.
#: Applied to the LAST token only, so a street *named* "Court" survives.
STREET_TYPES = {
    "STREET": "ST",
    "ST": "ST",
    "AVENUE": "AVE",
    "AVE": "AVE",
    "AV": "AVE",
    "ROAD": "RD",
    "RD": "RD",
    "DRIVE": "DR",
    "DR": "DR",
    "LANE": "LN",
    "LN": "LN",
    "COURT": "CT",
    "CT": "CT",
    "PLACE": "PL",
    "PL": "PL",
    "TERRACE": "TER",
    "TERR": "TER",
    "TER": "TER",
    "BOULEVARD": "BLVD",
    "BLVD": "BLVD",
    "CIRCLE": "CIR",
    "CIR": "CIR",
    "WAY": "WAY",
    "HIGHWAY": "HWY",
    "HWY": "HWY",
    "PARKWAY": "PKWY",
    "PKWY": "PKWY",
    "TRAIL": "TRL",
    "TRL": "TRL",
    "SQUARE": "SQ",
    "SQ": "SQ",
    "HEIGHTS": "HTS",
    "HTS": "HTS",
    "EXTENSION": "EXT",
    "EXT": "EXT",
}

#: A unit designator and everything after it is dropped: the parcel is the
#: join target, and "12 Oak St Apt 3B" and "12 Oak St Unit 2" are one door on a
#: rep's route sheet.
UNIT_DESIGNATORS = frozenset(
    {
        "UNIT",
        "APT",
        "APARTMENT",
        "STE",
        "SUITE",
        "FL",
        "FLOOR",
        "RM",
        "ROOM",
        "BLDG",
        "BUILDING",
        "TRLR",
    }
)

_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_FLOATISH = re.compile(r"\d+\.\d*")
_NON_ALNUM = re.compile(r"[^A-Z0-9]+")


def parse_deed_date(raw: str | None, as_of: date) -> date | None:
    """Raw MOD-IV `DEED_DATE` (YYMMDD) or an ISO string -> a date, or None.

    `as_of` supplies the century pivot: `YY <= as_of.year % 100 + 1` lands in
    the 2000s (a deed may legitimately be dated slightly in the future when an
    extract is stale), anything higher in the 1900s. Anything unparseable —
    empty, wrong length, non-digit, or an impossible calendar date — returns
    None rather than raising, because the caller's job is to skip the Mover
    group and flag low confidence, not to crash the run.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None

    if len(text) == 6 and text.isdigit():
        yy, mm, dd = int(text[:2]), int(text[2:4]), int(text[4:6])
        pivot = as_of.year % 100 + 1
        year = 2000 + yy if yy <= pivot else 1900 + yy
        try:
            return date(year, mm, dd)
        except ValueError:
            return None

    if _ISO_DATE.fullmatch(text):
        try:
            return date.fromisoformat(text)
        except ValueError:
            return None

    return None


def normalize_address(s: str | None) -> str:
    """Situs address -> a stable join key (upper, de-punctuated, abbreviated).

    "12 Oak St., Apt 3B", "12 OAK STREET" and "12 oak st  " are one door, and
    the canonical form is the short one ("12 OAK ST") because that is what a
    rep copies out of the UI.
    """
    if s is None:
        return ""
    text = str(s).upper()
    # "#2" carries the same meaning as "Unit 2", but punctuation-stripping would
    # erase the marker and leave a bare "2" glued to the street.
    text = text.replace("#", " UNIT ")
    text = _NON_ALNUM.sub(" ", text)

    tokens: list[str] = []
    for token in text.split():
        if token in UNIT_DESIGNATORS:
            break
        tokens.append(token)
    if not tokens:
        return ""

    tokens[-1] = STREET_TYPES.get(tokens[-1], tokens[-1])
    return " ".join(tokens)


def parcel_key(mun: str | None, block: str | None, lot: str | None) -> str:
    """Municipality + block + lot -> a key that joins Socrata permits to ArcGIS parcels.

    Tolerates the three differences observed between the two feeds — leading
    zeros, stray whitespace, and lots typed as floats ("15.0") — while keeping
    a genuinely fractional lot ("15.5") distinct from its whole-number sibling.
    """
    return "/".join(_join_component(part) for part in (mun, block, lot))


def situs_display(prop_loc: str | None, zip5: str | None) -> str:
    """The copy-address string shown in the UI (R9.1).

    Degrades to "12 OAK ST, Ramsey NJ" when the parcel carries no ZIP, which is
    still a mappable address — better than showing nothing or a bare street.
    """
    loc = (prop_loc or "").strip()
    zip_part = (zip5 or "").strip()
    base = f"{loc}, {MUNICIPALITY} {STATE}" if loc else f"{MUNICIPALITY} {STATE}"
    return f"{base} {zip_part}" if zip_part else base


def _join_component(raw: str | None) -> str:
    """One block/lot/municipality component reduced to its canonical digits."""
    text = re.sub(r"\s+", "", str(raw or "").upper())
    if not text:
        return ""

    if _FLOATISH.fullmatch(text):
        whole, _, frac = text.partition(".")
        frac = frac.rstrip("0")
        text = f"{whole}.{frac}" if frac else whole

    head, dot, tail = text.partition(".")
    head = head.lstrip("0") or "0"
    return f"{head}{dot}{tail}"
