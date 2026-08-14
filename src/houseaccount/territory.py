"""Territory bootstrap: the ~540 doors that are ours (T004, R1.1).

STUB — signatures only, so `tests/test_territory.py` imports. No behaviour lives
here yet; the tests are the specification.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from houseaccount.sources.parcels import Parcel


def select_territory(
    parcels: Sequence[Parcel],
    center: tuple[float, float],
    target: int = 540,
) -> Sequence[Parcel]:
    raise NotImplementedError("T004: select_territory is not implemented yet")


def write_territory_geojson(parcels: Sequence[Parcel], path: Path) -> Path:
    raise NotImplementedError("T004: write_territory_geojson is not implemented yet")


def territory_median_value(parcels: Sequence[Parcel]) -> float:
    raise NotImplementedError("T004: territory_median_value is not implemented yet")
