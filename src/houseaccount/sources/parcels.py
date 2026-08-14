"""NJ statewide parcel / MOD-IV harvest (T004, R1.2/R2.1).

STUB — shapes only, so `tests/test_parcels.py` and `tests/test_territory.py`
import. No behaviour lives here yet; the tests are the specification.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from houseaccount.cache import Cache
from houseaccount.http import Transport, requests_transport


@dataclass(frozen=True)
class Parcel:
    """One parcel, stripped to the non-identity fields the score needs."""

    pams_pin: str = ""
    prop_class: str = ""
    prop_loc: str = ""
    zip5: str = ""
    pclblock: str = ""
    pcllot: str = ""
    deed_date: str | None = None
    sale_price: float = 0.0
    sales_code: str = ""
    yr_constr: int = 0
    net_value: float = 0.0
    calc_acre: float = 0.0
    geometry: Mapping[str, Any] | None = None
    centroid: tuple[float, float] | None = None


class ParcelSource:
    """Paginated, cache-first reader for the NJ parcel FeatureServer."""

    def __init__(
        self,
        *,
        cache: Cache | None = None,
        transport: Transport = requests_transport,
        page_size: int | None = None,
    ) -> None:
        raise NotImplementedError("T004: ParcelSource is not implemented yet")

    def fetch(self, mun: str = "0248") -> Sequence[Parcel]:
        raise NotImplementedError("T004: ParcelSource.fetch is not implemented yet")
