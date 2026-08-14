"""Deterministic House Score engine (R6).

The score is a plain sum of five groups, clamped to 0..100:

    score = clamp(mover + hires_out + capacity + need + modifier, 0, 100)

Additive rather than multiplicative on purpose. A rep reading a door on a phone
needs to see *which* facts made the number, and a product of factors cannot be
narrated one line at a time. The same choice makes the two invariants below
cheap to hold, and they are what makes the score auditable rather than merely
reproducible:

1. `raw_total` is the UNCLAMPED sum of `groups`, so a 108 that clamps to 100 is
   still visible as a 108 — clamping is presentation, not arithmetic.
2. The signed `points` on the evidence items sum to `raw_total`. Every point
   has a sentence, and no sentence invents points.

Degradation (R6.1) is a first-class path, not an error path: Ramsey's MOD-IV
extract genuinely contains null deed dates and `YR_CONSTR == 0`. A missing deed
skips the Mover group; an unknown year built skips only the age-*dependent*
components (a pool is still a pool). Either way the door still scores, the
result is flagged `confidence: "low"`, and a `data_gap` item says which signal
was unavailable — the alternative is a silent zero that reads like a cold door.

No I/O and no network: inputs arrive already normalised, which is what lets the
golden fixtures and the live pipeline share one entry point.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Mapping, Sequence

from houseaccount.normalize import parse_deed_date
from houseaccount.scoring.evidence import EvidenceItem, imagery_for
from houseaccount.scoring.weights import CONDITION_ORDER, THRESHOLDS, WEIGHTS

#: Attribution strings. Deliberately describe the dataset, never a person —
#: Daniel's Law (R11.1) means no identity-derived field reaches a rep's screen.
SOURCE_MODIV = "NJ MOD-IV parcel record"
SOURCE_SR1A = "NJ SR1A sales register"
SOURCE_PERMITS = "NJ DCA construction permits (Socrata)"
SOURCE_ACS = "US Census ACS 5-year estimates, block group"
SOURCE_RENTAL = "municipal rental registration list"
SOURCE_IMAGERY = "NJ aerial orthoimagery"


@dataclass(frozen=True)
class Permit:
    """One construction permit joined to this parcel. `contractor` is None when
    the Socrata record carries no contractor field (excluded from the churn
    test, still worth permit points)."""

    permit_date: date | None = None
    permit_type: str = ""
    contractor: str | None = None


@dataclass(frozen=True)
class ScoreInput:
    """Everything the score needs for one door, already normalised."""

    as_of: date
    territory_median_value: float
    acs_dual_income_threshold: float
    pams_pin: str = ""
    deed_date: date | None = None
    sale_price: float = 0.0
    sales_code: str = ""
    #: Which register `deed_date` came from. MOD-IV carries one deed per parcel
    #: and lags; the SR1A sales file supersedes it when it holds a newer sale.
    #: R7 requires every evidence line to name the source it actually used, so
    #: the mover lines read this rather than assuming the parcel record.
    deed_source: str = SOURCE_MODIV
    yr_constr: int = 0
    net_value: float = 0.0
    calc_acre: float = 0.0
    permits: Sequence[Permit] = ()
    dual_income_pct: float | None = None
    median_hh_income: float | None = None
    rental_registration_match: bool = False
    vision: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_fixture(cls, given: Mapping[str, Any]) -> "ScoreInput":
        """Build from a golden fixture's `given` dict (also the pipeline's entry
        point). DEED_DATE goes through `normalize.parse_deed_date`, so both ISO
        and raw YYMMDD strings are accepted."""
        config = given.get("config") or {}
        parcel = given.get("parcel") or {}
        acs = given.get("acs_block_group") or {}
        as_of = date.fromisoformat(given["as_of"])

        return cls(
            as_of=as_of,
            territory_median_value=float(config.get("territory_median_value") or 0.0),
            acs_dual_income_threshold=float(config.get("acs_dual_income_threshold") or 0.0),
            pams_pin=str(parcel.get("PAMS_PIN") or ""),
            deed_date=parse_deed_date(parcel.get("DEED_DATE"), as_of),
            sale_price=float(parcel.get("SALE_PRICE") or 0.0),
            sales_code=str(parcel.get("SALES_CODE") or "").strip(),
            yr_constr=int(parcel.get("YR_CONSTR") or 0),
            net_value=float(parcel.get("NET_VALUE") or 0.0),
            calc_acre=float(parcel.get("CALC_ACRE") or 0.0),
            permits=tuple(
                Permit(
                    # Permit dates arrive ISO, but they go through the same door
                    # as deed dates so a malformed one degrades identically.
                    permit_date=parse_deed_date(record.get("date"), as_of),
                    permit_type=str(record.get("type") or ""),
                    contractor=(record.get("contractor") or None),
                )
                for record in given.get("permits_2yr") or ()
            ),
            dual_income_pct=acs.get("dual_income_pct"),
            median_hh_income=acs.get("median_hh_income"),
            rental_registration_match=bool(given.get("rental_registration_match")),
            vision=dict(given.get("vision") or {}),
        )


@dataclass(frozen=True)
class ScoreResult:
    """The scored door. `raw_total` is the UNCLAMPED sum of `groups`."""

    score: int
    confidence: str
    evidence: Sequence[EvidenceItem]
    groups: Mapping[str, int]
    raw_total: int


def score_door(door: ScoreInput) -> ScoreResult:
    """Score one door and explain it. Deterministic and total: no input shape
    raises, because a run over a whole town cannot stop on one bad record."""
    evidence: list[EvidenceItem] = []
    recent_permits = _permits_in_window(door)

    groups = {
        "mover": _score_mover(door, evidence),
        "hires_out": _score_hires_out(door, recent_permits, evidence),
        "capacity": _score_capacity(door, evidence),
        "need": _score_need(door, recent_permits, evidence),
        "modifier": _score_modifier(door, evidence),
    }

    gaps = _data_gaps(door)
    if gaps:
        evidence.append(
            EvidenceItem(
                type="data_gap",
                points=0,
                sentence=(
                    f"Partial data: {_join_prose(gaps)} missing from the parcel record, "
                    "so the signals that rely on it were skipped. Read this score as a "
                    "floor rather than a verdict."
                ),
                source=SOURCE_MODIV,
                retrieved=door.as_of,
            )
        )

    raw_total = sum(groups.values())
    floor, ceiling = int(THRESHOLDS["score_floor"]), int(THRESHOLDS["score_ceiling"])
    return ScoreResult(
        score=max(floor, min(ceiling, raw_total)),
        confidence="low" if gaps else "normal",
        evidence=tuple(evidence),
        groups=groups,
        raw_total=raw_total,
    )


# --- groups -----------------------------------------------------------------


def _score_mover(door: ScoreInput, evidence: list[EvidenceItem]) -> int:
    """Recency of the recorded deed, minus the transfers that only look like moves.

    MOD-IV records trust transfers, inheritances and corrections as deeds with a
    $1 price or a sales code; audited Ramsey data has both. Scoring those as
    fresh movers would send a rep to a door where nobody moved, so the OR-rule
    zeroes the group and leaves a zero-point line explaining the miss.
    """
    if door.deed_date is None:
        return 0

    if _is_non_arms_length(door):
        evidence.append(
            EvidenceItem(
                type="non_arms_length_transfer",
                points=0,
                sentence=(
                    f"Deed recorded {door.deed_date.isoformat()}, but the transfer is "
                    f"{_nominal_reason(door)} — a paperwork transfer, not a household "
                    "move, so it earns no mover points."
                ),
                source=door.deed_source,
                retrieved=door.as_of,
            )
        )
        return 0

    days = (door.as_of - door.deed_date).days
    if days <= THRESHOLDS["mover_30d_days"]:
        points, band = WEIGHTS["mover_30d"], "the top band"
    elif days <= THRESHOLDS["mover_60d_days"]:
        points, band = WEIGHTS["mover_60d"], "the 31-60 day band"
    elif days <= THRESHOLDS["mover_90d_days"]:
        points, band = WEIGHTS["mover_90d"], "the 61-90 day band"
    else:
        evidence.append(
            EvidenceItem(
                type="tenure",
                points=0,
                sentence=_tenure_sentence(door.deed_date, days),
                source=door.deed_source,
                retrieved=door.as_of,
            )
        )
        return 0

    evidence.append(
        EvidenceItem(
            type="deed_recency",
            points=points,
            sentence=(
                f"Deed recorded {door.deed_date.isoformat()} — {days} days ago, "
                f"{band} of the 90-day mover window. New arrivals are still choosing "
                "who they use for the house."
            ),
            source=door.deed_source,
            retrieved=door.as_of,
        )
    )
    return points


def _score_hires_out(
    door: ScoreInput, recent_permits: Sequence[Permit], evidence: list[EvidenceItem]
) -> int:
    """Demonstrated willingness to pay someone else to do the work.

    Permit points cap at 40 so a single gut renovation cannot outrank a genuine
    mover. The churn bonus fires only when the work is spread across distinct
    contractors with no repeats: a household that keeps calling the same firm
    has an incumbent to displace, which is a materially harder door.
    """
    points = min(
        len(recent_permits) * WEIGHTS["permit_each"],
        WEIGHTS["permit_cap"],
    )
    if points:
        types = _join_prose(sorted({p.permit_type for p in recent_permits if p.permit_type}))
        detail = f" ({types})" if types else ""
        evidence.append(
            EvidenceItem(
                type="permit_history",
                points=points,
                sentence=(
                    f"{len(recent_permits)} permit{'s' if len(recent_permits) != 1 else ''} "
                    f"filed here in the last 24 months{detail} — work at this address gets "
                    "contracted out rather than done in-house."
                ),
                source=SOURCE_PERMITS,
                retrieved=door.as_of,
            )
        )

    # Permits with no contractor on the record say nothing either way about
    # loyalty, so they sit out the churn test while keeping their points.
    named = [p.contractor.strip().upper() for p in recent_permits if p.contractor and p.contractor.strip()]
    distinct = set(named)
    if len(distinct) >= THRESHOLDS["provider_churn_min_contractors"] and len(distinct) == len(named):
        points += WEIGHTS["provider_churn"]
        evidence.append(
            EvidenceItem(
                type="provider_churn",
                points=WEIGHTS["provider_churn"],
                sentence=(
                    f"{len(distinct)} different contractors across those permits, none of "
                    "them repeating — no incumbent relationship stands in the way."
                ),
                source=SOURCE_PERMITS,
                retrieved=door.as_of,
            )
        )
    return points


def _score_capacity(door: ScoreInput, evidence: list[EvidenceItem]) -> int:
    """Ability to pay, from assessed value plus a neighbourhood-level prior.

    The value bands are alternatives, not a sum. The ACS term is deliberately a
    small standalone nudge (R6.2): it describes a block group, so it can lift a
    door slightly but must never carry one on its own.
    """
    points = 0
    median = door.territory_median_value

    if median > 0 and door.net_value >= THRESHOLDS["capacity_1_5x_multiple"] * median:
        points += WEIGHTS["capacity_1_5x"]
        evidence.append(
            EvidenceItem(
                type="assessed_value",
                points=WEIGHTS["capacity_1_5x"],
                sentence=(
                    f"Assessed at ${door.net_value:,.0f}, more than 1.5x the "
                    f"${median:,.0f} territory median — top of the range you cover."
                ),
                source=SOURCE_MODIV,
                retrieved=door.as_of,
            )
        )
    elif median > 0 and door.net_value >= median:
        points += WEIGHTS["capacity_median"]
        evidence.append(
            EvidenceItem(
                type="assessed_value",
                points=WEIGHTS["capacity_median"],
                sentence=(
                    f"Assessed at ${door.net_value:,.0f}, at or above the "
                    f"${median:,.0f} territory median."
                ),
                source=SOURCE_MODIV,
                retrieved=door.as_of,
            )
        )

    if door.dual_income_pct is not None and door.dual_income_pct >= door.acs_dual_income_threshold:
        points += WEIGHTS["capacity_acs_prior"]
        evidence.append(
            EvidenceItem(
                type="acs_dual_income_prior",
                points=WEIGHTS["capacity_acs_prior"],
                sentence=(
                    f"Census block group: {door.dual_income_pct:.0%} dual-income, at or "
                    f"above the {door.acs_dual_income_threshold:.0%} threshold. "
                    "A neighbourhood-level pattern — nothing is known about who lives here."
                ),
                source=SOURCE_ACS,
                retrieved=door.as_of,
            )
        )
    return points


def _score_need(
    door: ScoreInput, recent_permits: Sequence[Permit], evidence: list[EvidenceItem]
) -> int:
    """What the house itself is likely to want doing.

    `YR_CONSTR == 0` means unknown, not "built in year zero", so the age term
    and the deferred-maintenance combo that depends on it are skipped while the
    pool, lot and condition terms score normally (R6.1).
    """
    points = 0
    vision = door.vision or {}

    age = door.as_of.year - door.yr_constr if door.yr_constr > 0 else None
    is_old = age is not None and age >= THRESHOLDS["need_home_age_years"]
    if is_old:
        points += WEIGHTS["need_home_age"]
        evidence.append(
            EvidenceItem(
                type="home_age",
                points=WEIGHTS["need_home_age"],
                sentence=(
                    f"Built {door.yr_constr}, so roughly {age} years old — old enough that "
                    "the original systems are at or past replacement age."
                ),
                source=SOURCE_MODIV,
                retrieved=door.as_of,
            )
        )

    if vision.get("pool"):
        points += WEIGHTS["need_pool"]
        evidence.append(
            EvidenceItem(
                type="pool",
                points=WEIGHTS["need_pool"],
                sentence="Pool visible from the air — a standing maintenance commitment.",
                source=SOURCE_IMAGERY,
                retrieved=door.as_of,
                imagery=imagery_for(vision, "pool"),
            )
        )

    if door.calc_acre >= THRESHOLDS["need_lot_acres"]:
        points += WEIGHTS["need_lot"]
        evidence.append(
            EvidenceItem(
                type="lot_size",
                points=WEIGHTS["need_lot"],
                sentence=(
                    f"{door.calc_acre:g}-acre lot — enough ground that outdoor work is a "
                    "recurring job rather than an afternoon."
                ),
                source=SOURCE_MODIV,
                retrieved=door.as_of,
            )
        )

    declined = _condition_declined(vision)
    if declined:
        points += WEIGHTS["need_condition_decline"]
        evidence.append(
            EvidenceItem(
                type="condition_trajectory",
                points=WEIGHTS["need_condition_decline"],
                sentence=(
                    f"Exterior condition read as {vision['condition_2015']} on the 2015 "
                    f"orthoimagery and {vision['condition_2020']} on the 2020 pass — the "
                    "trend is downward, not just low."
                ),
                source=SOURCE_IMAGERY,
                retrieved=door.as_of,
                imagery=imagery_for(vision, "condition"),
            )
        )

    if is_old and not recent_permits and declined:
        points += WEIGHTS["need_deferred_maintenance"]
        evidence.append(
            EvidenceItem(
                type="deferred_maintenance",
                points=WEIGHTS["need_deferred_maintenance"],
                sentence=(
                    f"At {age} years old, with no permits in two years and an exterior "
                    "that is slipping: the work here is being put off, not done elsewhere."
                ),
                source=SOURCE_IMAGERY,
                retrieved=door.as_of,
            )
        )
    return points


def _score_modifier(door: ScoreInput, evidence: list[EvidenceItem]) -> int:
    """The absentee demotion, detected only via the municipal rental register.

    -15 and no more, and it applies to everyone including a 30-day mover: a new
    owner of a registered rental is still a lead, just a different one. The
    signal is a ToS-clean municipal list, never a reconstruction of who lives
    where.
    """
    if not door.rental_registration_match:
        return 0

    evidence.append(
        EvidenceItem(
            type="absentee_likely",
            points=WEIGHTS["absentee_modifier"],
            sentence=(
                "This parcel appears on the municipal rental registration list, so the "
                "person who answers is more likely a tenant than the decision-maker. "
                "A mild demotion only — rentals still buy home services."
            ),
            source=SOURCE_RENTAL,
            retrieved=door.as_of,
        )
    )
    return WEIGHTS["absentee_modifier"]


# --- helpers ----------------------------------------------------------------


def _permits_in_window(door: ScoreInput) -> tuple[Permit, ...]:
    """Permits inside the rolling 730 days ending at `as_of`, newest first."""
    window = int(THRESHOLDS["permit_window_days"])
    inside = [
        permit
        for permit in door.permits
        if permit.permit_date is not None and 0 <= (door.as_of - permit.permit_date).days <= window
    ]
    return tuple(sorted(inside, key=lambda p: p.permit_date, reverse=True))


def _is_non_arms_length(door: ScoreInput) -> bool:
    """MOD-IV's two independent tells that a deed is not a market sale."""
    return door.sale_price <= THRESHOLDS["nominal_sale_price_usd"] or bool(door.sales_code.strip())


def _nominal_reason(door: ScoreInput) -> str:
    if door.sales_code.strip():
        return f"flagged with MOD-IV sales code {door.sales_code.strip()}"
    return f"recorded at a nominal ${door.sale_price:,.0f}"


def _tenure_sentence(deed_date: date, days: int) -> str:
    if days <= 365:
        return (
            f"Deed recorded {deed_date.isoformat()}, {days} days ago — just outside the "
            "90-day mover window, so the move itself is no longer the reason to knock."
        )
    years = days // 365
    return (
        f"Settled at this address since {deed_date.year} — about {years} "
        f"year{'s' if years != 1 else ''}. Long tenure means no move-in moment to work with."
    )


def _condition_declined(vision: Mapping[str, Any]) -> bool:
    """True when both ortho vintages are readable and 2020 is a step worse.

    One vintage alone is a condition, not a trajectory, and only a trajectory
    tells you the house is getting away from whoever maintains it.
    """
    before, after = vision.get("condition_2015"), vision.get("condition_2020")
    if before not in CONDITION_ORDER or after not in CONDITION_ORDER:
        return False
    return CONDITION_ORDER.index(after) < CONDITION_ORDER.index(before)


def _data_gaps(door: ScoreInput) -> tuple[str, ...]:
    """Which R6.1 signals were unavailable — the reason confidence drops."""
    gaps = []
    if door.deed_date is None:
        gaps.append("deed date")
    if door.yr_constr <= 0:
        gaps.append("year built")
    return tuple(gaps)


def _join_prose(items: Sequence[str]) -> str:
    items = list(items)
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return f"{', '.join(items[:-1])} and {items[-1]}"
