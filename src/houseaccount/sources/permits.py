"""NJ construction permits (Socrata). STUB — T005 tests define the behaviour."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as Date
from typing import Any, Iterable, Mapping, Sequence

from houseaccount.http import Transport, requests_transport
from houseaccount.cache import Cache
from houseaccount.scoring.engine import Permit as Permit  # re-export; the score engine owns it

#: Socrata SODA endpoint for the NJ statewide construction-permit dataset.
SOCRATA_PERMITS_URL = "https://data.nj.gov/resource/w9se-dmra.json"

#: Socrata's own page ceiling for an unauthenticated app-token-less caller.
DEFAULT_PAGE_SIZE = 1000

#: Ramsey Borough's municipality code in this dataset.
RAMSEY_COMU = "0248"

#: The rolling window R6 scores permits over.
PERMIT_WINDOW_DAYS = 730


@dataclass(frozen=True)
class PermitRecord:
    """One Socrata permit row, before it is joined to a parcel."""

    record_id: str = ""
    block: str = ""
    lot: str = ""
    date: Date | None = None
    type: str = ""
    contractor: str | None = None
    cost: float = 0.0

    @classmethod
    def from_socrata(cls, row: Mapping[str, Any]) -> "PermitRecord":
        raise NotImplementedError

    def to_score_permit(self) -> Permit:
        raise NotImplementedError


def permits_within(
    permits: Iterable[PermitRecord],
    as_of: Date,
    days: int = PERMIT_WINDOW_DAYS,
) -> tuple[PermitRecord, ...]:
    raise NotImplementedError


class PermitSource:
    """Paged, cache-first reader for the Socrata permit dataset."""

    def __init__(
        self,
        *,
        cache: Cache | None = None,
        transport: Transport = requests_transport,
        page_size: int = DEFAULT_PAGE_SIZE,
        url: str = SOCRATA_PERMITS_URL,
    ) -> None:
        self.cache = cache
        self.transport = transport
        self.page_size = page_size
        self.url = url

    def fetch(self, comu: str = RAMSEY_COMU, since: Date | None = None) -> Sequence[PermitRecord]:
        raise NotImplementedError
