"""Census ACS5 block-group source. STUB — T005 tests define the behaviour."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from houseaccount.cache import Cache
from houseaccount.http import Transport, requests_transport

#: Vintage of the 5-year estimates this project reads.
ACS_YEAR = 2022

#: The ACS5 endpoint, without the query string.
ACS_BASE_URL = f"https://api.census.gov/data/{ACS_YEAR}/acs/acs5"

#: New Jersey / Bergen County — the only geography this MVP fetches.
STATE_NJ = "34"
COUNTY_BERGEN = "003"

#: Median household income in the past 12 months.
MEDIAN_INCOME_VARIABLE = "B19013_001E"

#: B23007 "both parents in labor force" lines, one per age-of-own-children branch.
DUAL_INCOME_NUMERATOR_VARIABLES = ("B23007_004E", "B23007_015E", "B23007_026E")

#: B23007 universe: families with own children under 18.
DUAL_INCOME_DENOMINATOR_VARIABLE = "B23007_001E"

#: Census jams large negative values in where an estimate is unavailable.
NULL_SENTINEL_CEILING = -1_000_000


@dataclass(frozen=True)
class BlockGroupStats:
    """Neighbourhood-level statistics for one block group. Never a household."""

    geoid: str
    state: str
    county: str
    tract: str
    block_group: str
    dual_income_pct: float | None = None
    median_hh_income: float | None = None


@dataclass(frozen=True)
class AcsResult:
    """What a fetch produced, or why it declined."""

    available: bool
    block_groups: Mapping[str, BlockGroupStats] = field(default_factory=dict)
    reason: str | None = None


class AcsSource:
    """Cache-first ACS5 reader that declines rather than raising."""

    def __init__(
        self,
        *,
        cache: Cache | None = None,
        transport: Transport = requests_transport,
        api_key: str | None = None,
        year: int = ACS_YEAR,
    ) -> None:
        self.cache = cache
        self.transport = transport
        self.api_key = api_key
        self.year = year

    def fetch(self, state: str = STATE_NJ, county: str = COUNTY_BERGEN) -> AcsResult:
        raise NotImplementedError
