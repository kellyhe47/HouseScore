"""NJ statewide construction permits, read from Socrata SODA (T005).

Endpoint: `https://data.nj.gov/resource/w9se-dmra.json?comu=0248` (0248 is Ramsey
Borough). Free, keyless, and paged with `$limit`/`$offset`.

Two facts about this dataset drive the whole module.

*There is no contractor field.* Verified against the live service in Phase 0: a
record carries `block`, `lot`, `permitdate`, `permittypedesc`, `constcost` and
fee columns, and nothing naming who did the work. `PermitRecord.contractor`
therefore exists — the score engine's churn rule reads it — but from this source
it is always `None`, deliberately, and a lookalike key on a row is not trusted
either. The consequence is spelled out in PRD R6: permits lacking a contractor
field are excluded from the provider-churn bonus, not from permit points. The
churn bonus is structurally unearnable on real NJ permit data, and that is the
honest answer rather than a gap to paper over.

*The join keys are `block`/`lot`.* There is no address on a permit record, so a
permit reaches a door only via the parcel's block/lot (T006 owns that join).

The record type here is `PermitRecord`. The V2 engine consumes permit records
as plain mappings through the evidence bundle (`scoring.bundle`), so this
module owns only the source contract — including the contractor-None fact.

Paging is walked until a short page arrives, because an exactly-full page cannot
be assumed final. Everything goes through `http.fetch_json`, so a warm cache
replays a whole walk without a single request.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as Date, timedelta
from typing import Any, Iterable, Mapping, Sequence

from houseaccount.cache import Cache
from houseaccount.http import Transport, requests_transport, fetch_json

#: Socrata SODA endpoint for the NJ statewide construction-permit dataset.
SOCRATA_PERMITS_URL = "https://data.nj.gov/resource/w9se-dmra.json"

#: Socrata's own page ceiling for an unauthenticated app-token-less caller.
DEFAULT_PAGE_SIZE = 1000

#: Ramsey Borough's municipality code in this dataset.
RAMSEY_COMU = "0248"

#: The rolling window R6 scores permits over.
PERMIT_WINDOW_DAYS = 730

#: Socrata's internal row identifier. Ordering by it makes an offset walk stable;
#: without an explicit order the server may re-shuffle rows between pages and a
#: record can be served twice or skipped entirely.
_STABLE_ORDER = ":id"


def _parse_permit_date(raw: Any) -> Date | None:
    """`"2022-11-29T00:00:00.000"` -> a date, anything else -> None.

    Socrata floating timestamps are ISO with a time part that is always
    midnight here, so the date half is the whole signal. A blank or malformed
    value degrades to None: the record still exists, it just falls out of the
    rolling window rather than taking down the run.
    """
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return Date.fromisoformat(text[:10])
    except ValueError:
        return None


def _parse_cost(raw: Any) -> float:
    """`constcost` -> a float, with blanks and free text reading as 0.

    Cost is a decoration on the permit line, never a gate, so an unparseable
    one must not cost the door its permit points.
    """
    try:
        return float(str(raw or "").strip())
    except ValueError:
        return 0.0


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
        """Map one raw SODA row. `contractor` is hardcoded to None: this dataset
        has no such column, and inventing one from a lookalike key would let an
        unearnable churn bonus fire on a coincidence."""
        return cls(
            record_id=str(row.get("recordid") or ""),
            block=str(row.get("block") or ""),
            lot=str(row.get("lot") or ""),
            date=_parse_permit_date(row.get("permitdate")),
            # The human-readable description is what a rep reads on the card;
            # the numeric code is the fallback when the description is blank.
            type=str(row.get("permittypedesc") or "").strip()
            or str(row.get("permittype") or "").strip(),
            contractor=None,
            cost=_parse_cost(row.get("constcost")),
        )


def permits_within(
    permits: Iterable[PermitRecord],
    as_of: Date,
    days: int = PERMIT_WINDOW_DAYS,
) -> tuple[PermitRecord, ...]:
    """The permits inside the rolling window ending at `as_of`, input order kept.

    The boundary is inclusive on both ends: `days` days ago is inside, one day
    more is outside, and a permit dated after `as_of` is outside too (a stale
    extract can carry one). An undated permit is dropped rather than guessed at.
    """
    cutoff = as_of - timedelta(days=days)
    return tuple(
        record
        for record in permits
        if record.date is not None and cutoff <= record.date <= as_of
    )


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
        """Walk every page for one municipality and map the rows.

        `since` narrows the query server-side rather than filtering after the
        fact, so a two-year run does not page through a decade of history.
        """
        records: list[PermitRecord] = []
        offset = 0
        while True:
            page = self._page(comu=comu, since=since, offset=offset)
            records.extend(PermitRecord.from_socrata(row) for row in page)
            # A short page is the end of the data. An exactly-full one is not,
            # so the walk pays for one empty page rather than risk truncation.
            if len(page) < self.page_size:
                return tuple(records)
            offset += self.page_size

    # --- internals ----------------------------------------------------------

    def _page(self, *, comu: str, since: Date | None, offset: int) -> list[Mapping[str, Any]]:
        payload = fetch_json(
            self.url,
            params=self._params(comu=comu, since=since, offset=offset),
            cache=self.cache,
            transport=self.transport,
        )
        return [row for row in payload if isinstance(row, Mapping)] if isinstance(payload, list) else []

    def _params(self, *, comu: str, since: Date | None, offset: int) -> dict[str, Any]:
        params: dict[str, Any] = {
            "$limit": self.page_size,
            "$offset": offset,
            "$order": _STABLE_ORDER,
        }
        if comu:
            params["comu"] = comu
        if since is not None:
            # SODA compares floating timestamps, so the bound is written as one.
            params["$where"] = f"permitdate >= '{since.isoformat()}T00:00:00.000'"
        return params
