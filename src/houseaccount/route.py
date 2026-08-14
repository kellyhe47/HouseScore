"""Route planner — STUB ONLY (T010, R10.1–R10.3).

`tests/test_route.py` is the contract; every function below raises. The
dataclasses are declared so the tests can build inputs, and the implementing
agent is free to ADD fields (`Route` will need to remember its own planning
inputs to satisfy `Route.exclude`) — the tests only read the fields declared
here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Sequence


@dataclass(frozen=True)
class RouteDoor:
    """One candidate door, in the planner's own vocabulary."""

    pams_pin: str
    address: str
    score: int | None
    centroid: tuple[float, float] | None
    top_evidence: str | None = None


@dataclass(frozen=True)
class Stop:
    """One door on the planned walk."""

    pams_pin: str
    address: str
    score: int
    walk_minutes: float
    cumulative_minutes: float
    talk_track: str


@dataclass(frozen=True)
class Route:
    """The planned walk plus the estimate disclosure the UI renders."""

    stops: tuple[Stop, ...]
    total_minutes: float
    estimate_disclosure: str

    def exclude(self, pins: Iterable[str]) -> "Route":
        raise NotImplementedError


def plan_route(
    doors: Sequence[RouteDoor],
    hours: float,
    start_point: tuple[float, float],
    max_doors: int | None = None,
) -> Route:
    raise NotImplementedError


def route_door_from_facts(facts: Any, *, score: int | None, top_evidence: str | None = None) -> RouteDoor:
    raise NotImplementedError


def talk_track_for(door: RouteDoor) -> str:
    raise NotImplementedError


def encode_share(stops: Sequence[Stop]) -> str:
    raise NotImplementedError


def decode_share(text: str) -> tuple[str, ...]:
    raise NotImplementedError
