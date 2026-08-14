"""Normalizers: deed dates, situs addresses, parcel/permit join keys (T002).

STUB — the surface below is pinned by tests/test_normalize.py; there is no
behaviour yet. Pure functions only: no I/O, no network.
"""

from __future__ import annotations

from datetime import date


def parse_deed_date(raw: str | None, as_of: date) -> date | None:
    """Raw MOD-IV `DEED_DATE` (YYMMDD) or an ISO string -> a date, or None."""
    raise NotImplementedError


def normalize_address(s: str | None) -> str:
    """Situs address -> a stable join key (upper, de-punctuated, abbreviated)."""
    raise NotImplementedError


def parcel_key(mun: str | None, block: str | None, lot: str | None) -> str:
    """Municipality + block + lot -> a key that joins Socrata permits to ArcGIS parcels."""
    raise NotImplementedError


def situs_display(prop_loc: str | None, zip5: str | None) -> str:
    """The copy-address string shown in the UI (R9.1)."""
    raise NotImplementedError
