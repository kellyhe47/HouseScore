"""Route planner — the single implementation behind `plan_route` and the Map UI
(R10.1-R10.3).

*"I have 2 hours in Ramsey — which 20 doors do I knock, and what do I say?"*

**Why this module owns its own input type.** It is called from three places that
agree on nothing: the pipeline holds scored door facts, the published
`doors.geojson` holds GeoJSON features, the MCP tool holds JSON arguments. A
planner that imported the pipeline would drag the harvest/ACS/permits stack into
a web process whose entire job is sorting points on a map, so the planner defines
`RouteDoor` and everything else adapts into it. `route_door_from_facts` is
duck-typed for the same reason: it reads `pams_pin` / `situs` / `centroid` off
anything that has them. The only imports here are stdlib.

**Why a leg is measured on a street network.** A rep can only walk along
streets, so a leg is the shortest way along them: down the driveway, along the
road, up the next driveway. The network comes from `houseaccount.streets`, which
derives it from the parcels themselves (no paid routing API, R12), and reaches
the planner as an argument rather than an import — the planner never learns how
it was made, and a caller that has no network still gets a route.

**Why straight lines survive as the fallback.** Without a network — a caller
with bare points, a door on an island of parcels the centrelines never reach —
a leg is the haversine distance times a fixed detour factor, walked at 3 mph.
Both models are estimates and the route says which one produced it:
`estimate_disclosure` ships with the route rather than living in the UI, so both
surfaces disclose the same thing.

**Why greedy, not optimal.** The rep's question is "where do I go next", asked
again at every door. Greedy on score-per-walking-minute answers exactly that,
runs in well under a second over a whole town, and produces a route a human can
follow and reason about. An optimal orienteering solution would be neither.

**Why `exclude` re-plans.** The rep excludes a door from a route they are looking
at ("that house is vacant"). Deleting the stop would leave the rest of the walk
detouring around a house nobody is visiting, so `Route` remembers what it was
planned from and `exclude` plans again without those doors.

**Why the talk track is a plain string.** R7.2 makes the opener presentation-only:
generated from the door's top evidence sentence, never fed back into the score.
The caller has already scored the door and holds the evidence item, so it hands
over the sentence and the planner stays free of the scoring stack for a value it
only renders.
"""

from __future__ import annotations

import base64
import binascii
import math
import re
import zlib
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

#: Straight-line distance underestimates a walk along streets and driveways;
#: 1.3 is the usual planning multiplier for a residential street grid.
DETOUR_FACTOR = 1.3

#: A rep walking a block with a tablet, not a pedestrian crossing town.
WALKING_SPEED_MPH = 3.0

#: Mean Earth radius (metres), the same figure the fixtures are laid out with.
EARTH_RADIUS_M = 6371008.8

_METRES_PER_MINUTE = WALKING_SPEED_MPH * 1609.344 / 60.0

#: Shipped with every route planned without a network, empty ones included, so
#: the UI never has to invent its own wording for the same caveat (R10.2). A
#: route planned on one carries that network's own disclosure instead.
ESTIMATE_DISCLOSURE = (
    "Walking times are straight-line estimates (x1.3 detour at 3 mph), "
    "not turn-by-turn directions."
)

#: Marks a share link as ours, so a mangled fragment decodes to nothing instead
#: of being mistaken for a route.
_SHARE_PREFIX = "r1"

#: Conservative: everything a PAMS PIN can contain, nothing a URL would mind.
_PIN_PATTERN = re.compile(r"[A-Za-z0-9._-]+")

#: Keep a long evidence sentence from swallowing the opener — but only ever by
#: stopping where the sentence already stops, never by cutting it short.
_EVIDENCE_LIMIT = 110

#: The punctuation a source sentence can be halted in front of and still be a
#: whole thing said out loud. The comma and the hyphen are left out on purpose:
#: "(Alteration," is no more sayable at a door than "rather than".
_CLAUSE_BOUNDARIES = "—–;:."


@dataclass(frozen=True)
class RouteDoor:
    """One candidate door, in the planner's own vocabulary.

    `score` is `None` for a door that was never scored and `centroid` is `None`
    for a parcel with no geometry; either makes the door unroutable, and both
    are ordinary states in the published data rather than errors.
    """

    pams_pin: str
    address: str
    score: int | None
    centroid: tuple[float, float] | None
    top_evidence: str | None = None


@dataclass(frozen=True)
class Stop:
    """One door on the planned walk, with the leg that reached it.

    `path` is that leg as a line — where the rep actually walks, `(lon, lat)`
    from the previous position to this door. Planned on a street network it
    runs along the roads; planned without one it is the straight line the time
    was estimated from. Either way it is the planner's answer rather than the
    map's guess: a browser drawing its own line between two centroids would
    draw a walk through the middle of a block (R10.3).
    """

    pams_pin: str
    address: str
    score: int
    walk_minutes: float
    cumulative_minutes: float
    talk_track: str
    path: tuple[tuple[float, float], ...] = ()


@dataclass(frozen=True)
class Route:
    """The planned walk, plus enough of its own inputs to be planned again.

    The trailing fields are what make `exclude` a re-plan rather than a filter;
    callers only ever read `stops`, `total_minutes` and `estimate_disclosure`.
    """

    stops: tuple[Stop, ...]
    total_minutes: float
    estimate_disclosure: str = ESTIMATE_DISCLOSURE
    candidates: tuple[RouteDoor, ...] = ()
    hours: float = 0.0
    start_point: tuple[float, float] = (0.0, 0.0)
    max_doors: int | None = field(default=None)
    network: Any = None

    def exclude(self, pins: Iterable[str]) -> "Route":
        """Re-plan without `pins`, from the same start, budget, cap and network.

        Dropping a door changes where the rep stands for every later decision,
        so the remaining doors are planned from scratch — a filtered route would
        keep detours around a house that is no longer being visited.
        """
        dropped = set(pins)
        remaining = [door for door in self.candidates if door.pams_pin not in dropped]
        return plan_route(
            remaining,
            hours=self.hours,
            start_point=self.start_point,
            max_doors=self.max_doors,
            network=self.network,
        )


def plan_route(
    doors: Sequence[RouteDoor],
    hours: float,
    start_point: tuple[float, float],
    max_doors: int | None = None,
    network: Any = None,
) -> Route:
    """The best walk greedy can find from `start_point` within `hours`.

    At every step only the doors whose leg still fits the *remaining* budget are
    considered, and the winner is the one with the highest score per walking
    minute — a door at the rep's feet is free, so it wins outright. Ties break on
    PAMS PIN so the same inputs always produce the same walk, whatever order they
    arrived in.

    `network` is anything that can answer two questions about walking between
    points — `metres_from(origin, destinations)` and `path(origin, destination)`
    — which is what `houseaccount.streets.WalkNetwork` is. It is duck-typed for
    the same reason `route_door_from_facts` is: the planner sorts points on a
    map and has no business importing the geometry stack to do it. Passing
    `None` measures every leg as a straight line, which is what a caller holding
    nothing but coordinates has always got.
    """
    candidates = tuple(doors)
    budget_minutes = max(hours, 0.0) * 60.0
    cap = len(candidates) if max_doors is None else max(max_doors, 0)

    routable = [door for door in candidates if door.score is not None and door.centroid is not None]

    stops: list[Stop] = []
    position = start_point
    elapsed = 0.0

    while routable and len(stops) < cap:
        # One question per step rather than one per candidate: a network answers
        # for every door in a single sweep, and the straight-line fallback does
        # not care either way.
        legs = _leg_minutes(network, position, [door.centroid for door in routable])

        best: tuple[float, str] | None = None
        chosen: RouteDoor | None = None
        chosen_minutes = 0.0

        for door, minutes in zip(routable, legs):
            if elapsed + minutes > budget_minutes:
                continue
            ratio = math.inf if minutes == 0.0 else door.score / minutes
            key = (-ratio, door.pams_pin)
            if best is None or key < best:
                best, chosen, chosen_minutes = key, door, minutes

        if chosen is None:
            break

        elapsed += chosen_minutes
        stops.append(
            Stop(
                pams_pin=chosen.pams_pin,
                address=chosen.address,
                score=chosen.score,
                walk_minutes=chosen_minutes,
                cumulative_minutes=elapsed,
                talk_track=talk_track_for(chosen),
                path=_leg_path(network, position, chosen.centroid),
            )
        )
        position = chosen.centroid
        routable.remove(chosen)

    return Route(
        stops=tuple(stops),
        total_minutes=stops[-1].cumulative_minutes if stops else 0.0,
        estimate_disclosure=_disclosure(network),
        candidates=candidates,
        hours=hours,
        start_point=start_point,
        max_doors=max_doors,
        network=network,
    )


def route_door_from_facts(
    facts: Any, *, score: int | None, top_evidence: str | None = None
) -> RouteDoor:
    """Adapt anything carrying `pams_pin` / `situs` / `centroid` into a candidate.

    Duck-typed on purpose: the pipeline's door facts and a GeoJSON feature's
    properties both satisfy it, and neither has to be importable from here.
    """
    return RouteDoor(
        pams_pin=facts.pams_pin,
        address=facts.situs,
        score=score,
        centroid=facts.centroid,
        top_evidence=top_evidence,
    )


def talk_track_for(door: RouteDoor) -> str:
    """The one sentence the rep opens with at this door (R7.2).

    Built from the door's top evidence when there is one, from its street when
    there is not. Presentation only — it never touches the score or the order.
    """
    street = _street_of(door.address)
    evidence = (door.top_evidence or "").strip()

    if not evidence:
        return (
            f"Hi, I'm working {street} today and introducing myself to the block. "
            "Are you the homeowner here?"
        )

    return (
        f"Hi, I'm working {street} today. Quick reason I knocked: "
        f"{_as_clause(evidence)}. Is now a bad time?"
    )


def encode_share(stops: Sequence[Stop]) -> str:
    """Pack a route's PINs into a URL-fragment-safe token.

    Deflate then base64url: the PINs of one territory share a long prefix, so a
    twenty-door route compresses to well under a third of the naive list, and the
    alphabet is URL-unreserved so the fragment needs no escaping.
    """
    payload = ",".join(stop.pams_pin for stop in stops).encode("ascii", "ignore")
    packed = zlib.compress(payload, 9)
    return _SHARE_PREFIX + base64.urlsafe_b64encode(packed).rstrip(b"=").decode("ascii")


def decode_share(text: str) -> tuple[str, ...]:
    """The PINs a share token carries, in order — `()` for anything unreadable.

    A mangled URL fragment should open an empty route, not return a 500, so every
    failure mode (wrong prefix, bad base64, corrupt deflate stream, contents that
    are not PINs) lands on the same empty answer.
    """
    token = (text or "").strip()
    if not token.startswith(_SHARE_PREFIX):
        return ()

    body = token[len(_SHARE_PREFIX) :]
    padded = body + "=" * (-len(body) % 4)
    try:
        packed = base64.urlsafe_b64decode(padded.encode("ascii"))
        payload = zlib.decompress(packed).decode("ascii")
    except (binascii.Error, zlib.error, UnicodeDecodeError, ValueError):
        return ()

    pins = payload.split(",") if payload else []
    if not all(_PIN_PATTERN.fullmatch(pin) for pin in pins):
        return ()
    return tuple(pins)


# --- internals ----------------------------------------------------------------


def _disclosure(network: Any) -> str:
    """What the route says about the model its minutes came from.

    A network describes its own derivation — the planner would be guessing —
    so it is asked, and only a planner working without one speaks for itself.
    """
    stated = getattr(network, "disclosure", None)
    return stated if isinstance(stated, str) and stated else ESTIMATE_DISCLOSURE


def _leg_minutes(
    network: Any,
    origin: tuple[float, float],
    destinations: Sequence[tuple[float, float]],
) -> tuple[float, ...]:
    """Walking minutes from `origin` to each destination.

    A destination the network cannot reach falls back to the straight-line
    estimate rather than becoming unroutable: an unreachable door is a gap in
    the derived streets, not a house that stopped existing (R10.1).
    """
    walked: Sequence[float | None] = (None,) * len(destinations)
    if network is not None:
        walked = network.metres_from(origin, list(destinations))

    return tuple(
        (metres if metres is not None else _straight_metres(origin, destination))
        / _METRES_PER_MINUTE
        for destination, metres in zip(destinations, walked)
    )


def _leg_path(
    network: Any, origin: tuple[float, float], destination: tuple[float, float]
) -> tuple[tuple[float, float], ...]:
    """The line the rep walks for this leg — the network's, or the straight one
    the fallback estimate measured."""
    if network is not None:
        drawn = tuple(tuple(point) for point in network.path(origin, destination))
        if drawn:
            return drawn
    return (tuple(origin), tuple(destination))


def _straight_metres(origin: tuple[float, float], destination: tuple[float, float]) -> float:
    """The straight-line estimate of a walk: haversine metres, detoured."""
    return _haversine_metres(origin, destination) * DETOUR_FACTOR


def _haversine_metres(origin: tuple[float, float], destination: tuple[float, float]) -> float:
    """Great-circle distance in metres between two (lon, lat) points."""
    lon1, lat1 = origin
    lon2, lat2 = destination
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)

    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def _street_of(address: str) -> str:
    """The street out of a display address — "1 FAWN HILL RD, Ramsey NJ 07446"
    is a thing to read off a form, not a thing to say out loud."""
    street = (address or "").split(",")[0].strip()
    head, _, tail = street.partition(" ")
    if tail and any(character.isdigit() for character in head):
        street = tail.strip()
    return street or "the neighbourhood"


def _as_clause(evidence: str) -> str:
    """An evidence sentence folded into the middle of the opener."""
    clause = evidence.strip().rstrip(".!?").strip()
    if len(clause) > _EVIDENCE_LIMIT:
        clause = _stopped_at_a_boundary(clause)

    # Fold the leading capital into the sentence, but leave "SKYLIGHT" and other
    # shouted source values alone — they are what makes the opener specific.
    head, separator, tail = clause.partition(" ")
    if head and not head[1:].isupper():
        clause = head.lower() + separator + tail
    return clause


def _stopped_at_a_boundary(clause: str) -> str:
    """The leading run of `clause` that fits, ended where the sentence itself ends
    a thought — the whole sentence when it offers nowhere to stop.

    A rep says this out loud at a stranger's door, so stopping on a word boundary
    strands the homeowner on "...gets contracted out rather than" (ticket 021).
    Only the source's own punctuation marks a place a listener hears as finished,
    and the substance a permit-led door was knocked on for sits ahead of the first
    of them. Where there is no such mark inside the limit, the sentence goes to
    the door long rather than half-said.
    """
    stop = max(clause.rfind(mark, 0, _EVIDENCE_LIMIT) for mark in _CLAUSE_BOUNDARIES)
    head = clause[:stop].rstrip() if stop > 0 else ""
    return head or clause
