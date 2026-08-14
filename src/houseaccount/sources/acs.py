"""Census ACS 5-year estimates at block-group level (T005).

Endpoint: `https://api.census.gov/data/2022/acs/acs5`, geography `block group:*`
in state 34 (New Jersey) / county 003 (Bergen). Two tables are read:

* **B19013** — median household income in the past 12 months. Variable
  `B19013_001E`, used as reported.
* **B23007** — presence of own children by parents' employment status. This is
  the dual-income proxy, computed as::

      dual_income_pct = (B23007_004E + B23007_015E + B23007_026E) / B23007_001E

  The three numerators are B23007's "both parents in labour force" lines, one
  per age-of-own-children branch (under 6; under 6 and 6-17; 6-17 only), so
  summing them counts each family exactly once. The denominator `B23007_001E`
  is the table universe: families with own children under 18.

**Every number here describes a block group, never a household.** PRD R6.2
forbids a household-level claim from ACS data anywhere in this system: a block
group at 41% dual-income says nothing whatsoever about the family behind any
particular door, and the score engine only ever uses it as a small
neighbourhood prior.

Two failure shapes are ordinary rather than exceptional, so this source returns
an `AcsResult` and never raises:

* *No key.* The API requires a free key and blocks keyless callers; with
  `CENSUS_API_KEY` unset this is the path that actually runs today. Declining
  costs one request nobody can make, and a door with no ACS data still scores —
  the ACS term simply contributes 0.
* *A null estimate.* Census writes a large negative sentinel (-666666666) where
  it has no estimate. That is one lost statistic, not a negative income and not
  a lost block group, so it is read as None and the row survives.

The response is a header row followed by data rows. Columns are resolved by
name because Census does not promise their order.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from houseaccount.cache import Cache
from houseaccount.config import Config
from houseaccount.http import SourceError, Transport, requests_transport, fetch_json

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

#: Geography columns, in the order they concatenate into a block-group GEOID.
_GEOGRAPHY_COLUMNS = ("state", "county", "tract", "block group")

#: Said when there is no key to send.
NO_KEY_REASON = (
    "CENSUS_API_KEY is not set; the ACS API refuses keyless callers, so "
    "block-group statistics were not fetched"
)


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
        """Every block group in one county, or a declination explaining the gap."""
        key = self._resolve_key()
        if key is None:
            return AcsResult(available=False, block_groups={}, reason=NO_KEY_REASON)

        try:
            payload = fetch_json(
                self._url(),
                params=self._params(state=state, county=county, key=key),
                cache=self.cache,
                transport=self.transport,
            )
        except (SourceError, ValueError) as error:
            # An upstream refusal (a revoked key, a retired vintage) degrades the
            # ACS component of every door rather than stopping the run.
            return AcsResult(available=False, block_groups={}, reason=str(error))

        return AcsResult(available=True, block_groups=_parse(payload), reason=None)

    # --- internals ----------------------------------------------------------

    def _resolve_key(self) -> str | None:
        """An explicit key wins; otherwise the environment, read at call time.

        Blank counts as absent — an empty `CENSUS_API_KEY` in a shell profile
        would otherwise buy a guaranteed 403 instead of an honest declination.
        """
        explicit = (self.api_key or "").strip()
        if explicit:
            return explicit
        return Config.from_env().census_api_key

    def _url(self) -> str:
        return f"https://api.census.gov/data/{self.year}/acs/acs5"

    def _params(self, *, state: str, county: str, key: str) -> dict[str, Any]:
        variables = (
            MEDIAN_INCOME_VARIABLE,
            DUAL_INCOME_DENOMINATOR_VARIABLE,
            *DUAL_INCOME_NUMERATOR_VARIABLES,
        )
        return {
            "get": ",".join(variables),
            "for": "block group:*",
            "in": f"state:{state} county:{county}",
            "key": key,
        }


# --- parsing ----------------------------------------------------------------


def _parse(payload: Any) -> dict[str, BlockGroupStats]:
    """Header row + data rows -> block groups keyed by GEOID.

    A row of the wrong width cannot be zipped to the header without silently
    misassigning columns, so it is dropped and the rest of the county survives.
    """
    if not isinstance(payload, list) or not payload:
        return {}
    header = [str(name) for name in payload[0]]

    block_groups: dict[str, BlockGroupStats] = {}
    for raw in payload[1:]:
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
            continue
        if len(raw) != len(header):
            continue
        stats = _block_group(dict(zip(header, raw)))
        block_groups[stats.geoid] = stats
    return block_groups


def _block_group(row: Mapping[str, Any]) -> BlockGroupStats:
    geography = {name: str(row.get(name) or "") for name in _GEOGRAPHY_COLUMNS}
    return BlockGroupStats(
        geoid="".join(geography[name] for name in _GEOGRAPHY_COLUMNS),
        state=geography["state"],
        county=geography["county"],
        tract=geography["tract"],
        block_group=geography["block group"],
        dual_income_pct=_dual_income_pct(row),
        median_hh_income=_estimate(row.get(MEDIAN_INCOME_VARIABLE)),
    )


def _dual_income_pct(row: Mapping[str, Any]) -> float | None:
    """The documented B23007 ratio, or None when any input is unusable.

    A partial numerator would understate the neighbourhood rather than admit it
    does not know, so one missing line loses the whole statistic — the other
    columns on the row are unaffected.
    """
    denominator = _estimate(row.get(DUAL_INCOME_DENOMINATOR_VARIABLE))
    if denominator is None or denominator <= 0:
        return None

    total = 0.0
    for variable in DUAL_INCOME_NUMERATOR_VARIABLES:
        value = _estimate(row.get(variable))
        if value is None:
            return None
        total += value
    return total / denominator


def _estimate(raw: Any) -> float | None:
    """One ACS cell -> a number, or None for a blank, a sentinel, or free text."""
    text = str(raw if raw is not None else "").strip()
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    return None if value <= NULL_SENTINEL_CEILING else value
