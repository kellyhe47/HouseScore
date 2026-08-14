"""Census TIGERweb block-group boundaries + point-in-polygon index (T006).

STUB — written by the test-writer so `tests/test_tiger.py` can import. Every
name below is an empty shell; the implementation is ticket 006's job.

Ground truth (verified live, Phase 0):
`https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/tigerWMS_ACS2022/MapServer/8/query`
(layer 8 = Census Block Groups), `where=STATE='34' AND COUNTY='003'`,
`outFields=GEOID,STATE,COUNTY,TRACT,BLKGRP`, `f=geojson`. Free, no key.

It exists because ACS statistics are block-group-level while a parcel carries
only a lon/lat centroid: without a boundary polygon nothing can join the two.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from houseaccount.cache import Cache
from houseaccount.http import Transport, requests_transport
from houseaccount.sources.acs import COUNTY_BERGEN, STATE_NJ

#: The TIGERweb block-group query endpoint. Unset in the stub.
TIGERWEB_BLOCK_GROUPS_URL = ""

#: MapServer layer number for "Census Block Groups".
BLOCK_GROUP_LAYER = 0

#: The fields the request is allowed to ask for. Unset in the stub.
BLOCK_GROUP_OUT_FIELDS: tuple[str, ...] = ()


@dataclass(frozen=True)
class BlockGroupBoundary:
    """One block group's GEOID, geography parts and polygon."""


class TigerSource:
    """Cache-first reader for TIGERweb block-group boundaries."""

    def __init__(
        self,
        *,
        cache: Cache | None = None,
        transport: Transport = requests_transport,
    ) -> None:
        raise NotImplementedError("T006: TigerSource")

    def fetch(
        self, state: str = STATE_NJ, county: str = COUNTY_BERGEN
    ) -> Sequence[BlockGroupBoundary]:
        raise NotImplementedError("T006: TigerSource.fetch")


class BlockGroupIndex:
    """Point-in-polygon lookup: a parcel centroid -> a block-group GEOID."""

    def __init__(self, boundaries: Iterable[BlockGroupBoundary] = ()) -> None:
        raise NotImplementedError("T006: BlockGroupIndex")

    def geoid_for(self, centroid: tuple[float, float] | None) -> str | None:
        raise NotImplementedError("T006: BlockGroupIndex.geoid_for")

    def __len__(self) -> int:
        raise NotImplementedError("T006: BlockGroupIndex.__len__")
