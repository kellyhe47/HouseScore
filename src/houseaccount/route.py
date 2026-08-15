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

**Why the talk track is authored, not quoted.** R7.2 makes the opener
presentation-only, and the caller hands over the top evidence item's **type** —
not its sentence. An evidence sentence is written for the audit panel: it recites
what we worked out about a stranger ("4 permits filed here in the last 24
months"), which is the one thing a rep must never say out loud at a door. So the
type selects an *angle* from `ANGLES` — a short authored opener ending in one
open question, plus the lines to say after the homeowner answers — and the
sentence itself stays on the panel where it belongs.

Two properties fall out of authoring rather than quoting. Nothing ever needs
truncating, so the boundary-cutting that ticket 021 fixed is gone rather than
improved. And an evidence type the rep could not say without revealing the file
(assessed value, the census prior, a declining exterior) simply maps to an angle
that never mentions it — the signal still picks the door and the words, it just
never reaches the doorstep.
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

#: How the opener names where the rep is standing when the address carries no
#: readable street. "This block" is a thing said out loud; a blank is not.
_UNNAMED_BLOCK = "this block"


@dataclass(frozen=True)
class Branch:
    """One thing the homeowner might answer, and what the rep says back.

    `trigger` is a label the rep scans on a tablet mid-conversation, not a
    sentence anyone reads aloud; `line` is.
    """

    trigger: str
    line: str


@dataclass(frozen=True)
class Angle:
    """One door's script: the question, then the answers to it.

    `hook` is the last thing the rep says before the homeowner speaks, and it is
    always an *open* question. A tag question ("You're pretty new in, right?")
    asks for confirmation, which tells the homeowner the rep already knew — the
    same leak as reciting the evidence, one grammatical step removed.

    There is no scripted acknowledgement between the two. Every branch opens by
    reacting to what was actually said, because a fixed token in that slot
    ("Figured.") reads as a rep running a script, and admits the answer was
    never in doubt.
    """

    hook: str
    branches: tuple[Branch, ...]


#: What HouseAccount is, in the one sentence a homeowner needs. The cheap end of
#: the range is load-bearing: "mounting a TV" is what makes a stranger at the
#: door feel low-risk to try, and the expensive end is what makes them remember
#: the card. Each angle picks the pair that fits the door.
_TENURE_ANGLE = Angle(
    hook="Have you been here long?",
    branches=(
        Branch(
            trigger="Just moved in",
            line=(
                "Oh nice, congrats. HouseAccount's basically one number for the whole "
                "house — we handle everything from mounting a TV to fixing the roof. "
                "First year in a place, most people are still working out who to call "
                "for what. Want me to leave a card?"
            ),
        ),
        Branch(
            trigger="A couple of years",
            line=(
                "Oh okay. HouseAccount's one number for the whole house — a running "
                "toilet up to a furnace, same call. Want me to leave a card?"
            ),
        ),
        Branch(
            trigger="A long time",
            line=(
                "Wow, okay — so you've seen the whole street change. HouseAccount's one "
                "number for the whole house, a running toilet up to a furnace. Mostly "
                "we end up doing the stuff people have been meaning to get to. Want me "
                "to leave a card?"
            ),
        ),
    ),
)

_CHURN_ANGLE = Angle(
    hook="Who do you usually call when something on the house needs doing?",
    branches=(
        Branch(
            trigger="Names one person",
            line=(
                "Oh, is he good? That's the thing though — most people have someone for "
                "one thing and then they're googling for everything else. HouseAccount's "
                "one number for all of it, mounting a TV up to fixing the roof. Want me "
                "to leave a card for the stuff he doesn't do?"
            ),
        ),
        Branch(
            trigger='"Depends what it is"',
            line=(
                "Right, that's the annoying part. HouseAccount's one number for all of "
                "it — a TV mount up to roofing. Want me to leave a card?"
            ),
        ),
        Branch(
            trigger='"I do it myself"',
            line=(
                "Respect. We're one number for the ones that aren't worth your Saturday "
                "— furnaces, roofs, that end of it. Want me to leave a card?"
            ),
        ),
    ),
)

_POOL_ANGLE = Angle(
    hook="Do you have a pool or anything out back?",
    branches=(
        Branch(
            trigger="Yes",
            line=(
                "Oh nice. Who's opening it for you? HouseAccount's one number for the "
                "whole house — pool openings and filter swaps right up through roofing. "
                "Most people are paying three separate people for that. Want me to "
                "leave a card?"
            ),
        ),
        Branch(
            trigger="No",
            line=(
                "No worries. HouseAccount's one number for the whole house anyway — "
                "mounting a TV up to fixing the roof. Want me to leave a card?"
            ),
        ),
    ),
)

_HOUSE_ANGLE = Angle(
    hook="How old's the house, do you know?",
    branches=(
        Branch(
            trigger="Gives a year",
            line=(
                "Yeah, that tracks for this block. HouseAccount's one number for the "
                "whole house — a running toilet up to a furnace, same call. At that age "
                "it's never one big thing, it's six small ones. Anything on your list "
                "that's been sitting a while?"
            ),
        ),
        Branch(
            trigger='"No idea"',
            line=(
                "Ha, fair enough. HouseAccount's one number for the whole house — a "
                "running toilet up to a furnace, same call. Anything on your list that's "
                "been sitting a while?"
            ),
        ),
    ),
)

#: Where nothing about the door can be said out loud, the hook qualifies the
#: decision-maker instead — which is also the only honest way to reach the
#: rental branch. "Are you the homeowner?" asks the same thing and sounds like a
#: cold call; this asks it the way a person would.
_DEFAULT_ANGLE = Angle(
    hook="Are you the one who deals with the house stuff, or is that somebody else?",
    branches=(
        Branch(
            trigger='"That\'s me"',
            line=(
                "Then you're the one I should be bugging, sorry. HouseAccount's one "
                "number for the whole house — mounting a TV up to fixing the roof. Want "
                "me to leave a card?"
            ),
        ),
        Branch(
            trigger='"My partner"',
            line="Fair enough — want me to leave a card for them?",
        ),
        Branch(
            trigger='"I rent"',
            line=(
                "Ah, got it. We do work for landlords too. Want to pass the card along, "
                "or is there a better number for the owner?"
            ),
        ),
    ),
)

#: Evidence type -> the angle it opens. The types absent from this table are
#: absent on purpose: `assessed_value`, `acs_dual_income_prior` and
#: `absentee_likely` are things a rep cannot say without revealing the file, and
#: `condition_trajectory` / `deferred_maintenance` are things nobody says to
#: someone's face. They score the door, they sort the route, and they fall
#: through to an angle that never mentions them.
ANGLES: dict[str, Angle] = {
    "deed_recency": _TENURE_ANGLE,
    "tenure": _TENURE_ANGLE,
    "non_arms_length_transfer": _TENURE_ANGLE,
    "condition_trajectory": _TENURE_ANGLE,
    "deferred_maintenance": _TENURE_ANGLE,
    "permit_history": _CHURN_ANGLE,
    "provider_churn": _CHURN_ANGLE,
    "pool": _POOL_ANGLE,
    "home_age": _HOUSE_ANGLE,
    "lot_size": _HOUSE_ANGLE,
}

#: The angle for a door whose top evidence names no angle — an unscored trail, a
#: `data_gap` line, a type added to the engine and not yet to `ANGLES`. It says
#: nothing about the door, so it is always safe to fall through to.
DEFAULT_ANGLE = _DEFAULT_ANGLE


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
    evidence_type: str | None = None


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
    talk_track_branches: tuple[Branch, ...] = ()
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
                talk_track_branches=talk_track_branches_for(chosen),
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
    facts: Any, *, score: int | None, evidence_type: str | None = None
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
        evidence_type=evidence_type,
    )


def angle_for(door: RouteDoor) -> Angle:
    """The script this door's top evidence opens — `DEFAULT_ANGLE` for anything
    unmapped, so a new evidence type degrades to a safe opener rather than none."""
    return ANGLES.get(door.evidence_type or "", DEFAULT_ANGLE)


def talk_track_for(door: RouteDoor) -> str:
    """What the rep says before the homeowner has said anything (R7.2).

    Three beats and a stop: who I am, why I'm on this street, one open question.
    Everything after it depends on the answer and lives in
    `talk_track_branches_for`, because a rep reading a wall of text talks over
    the person they knocked for.

    Presentation only — it never touches the score or the order.
    """
    return (
        "Hey, I'm with HouseAccount — we're doing work for a few of your neighbors "
        f"here on {_street_of(door.address)} this week. {angle_for(door).hook}"
    )


def talk_track_branches_for(door: RouteDoor) -> tuple[Branch, ...]:
    """What the rep says *after* the homeowner answers the hook.

    Separate from the opener rather than concatenated onto it: these are
    alternatives, only one of which gets said, and a rep scanning a tablet
    mid-conversation needs them as a list to pick from rather than a paragraph
    to read out.
    """
    return angle_for(door).branches


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
    """The street out of a display address, as a rep would say it aloud.

    "1 FAWN HILL RD, Ramsey NJ 07446" is a thing to read off a form; the opener
    needs "Fawn Hill Rd". The house number goes because the homeowner knows
    which house they are standing in, and the shouting goes because the rep is
    speaking, not filing.
    """
    street = (address or "").split(",")[0].strip()
    head, _, tail = street.partition(" ")
    if tail and any(character.isdigit() for character in head):
        street = tail.strip()
    return street.title() if street else _UNNAMED_BLOCK
