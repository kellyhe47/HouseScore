"""Door Score V2 engine (ticket 101).

Contract: docs/plans/2026-08-18-2221-feat-door-score-v2-plan.md (R1-R26, R37)
and eval/v2/provenance.md (binding envelope conventions).

Pure scoring: no I/O, no network. Input is a normalized evidence bundle
mirroring the golden fixtures' ``given.inputs`` shape plus ``as_of``; output
is the fixture ``result`` envelope dict. V1 (engine.py/weights.py) untouched.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from statistics import median
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class V2Bundle:
    """Normalized evidence bundle for one door, mirroring given.inputs."""

    parcel: Mapping[str, Any] = field(default_factory=dict)
    sales: Sequence[Mapping[str, Any]] = ()
    permits: Sequence[Mapping[str, Any]] = ()
    local_comparables: Sequence[Mapping[str, Any]] = ()
    territory_assessed_values: Sequence[float] = ()
    acs_block_group: Mapping[str, Any] = field(default_factory=dict)
    rental_registry: Mapping[str, Any] = field(default_factory=dict)
    imagery: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_fixture(cls, given: Mapping[str, Any]) -> "V2Bundle":
        """Build the production bundle from a golden fixture's ``given``.

        Accepts either the full ``given`` object (with an ``inputs`` key) or
        the ``inputs`` mapping directly. Unknown keys inside ``parcel`` (e.g.
        owner_name / mailing_address in fixture 39) are carried opaquely in
        the parcel mapping; the engine must never consume identity fields
        (R25) — the bundle type exposes no such attributes.
        """
        inputs = given.get("inputs", given)
        return cls(
            parcel=inputs.get("parcel", {}),
            sales=tuple(inputs.get("sales", ())),
            permits=tuple(inputs.get("permits", ())),
            local_comparables=tuple(inputs.get("local_comparables", ())),
            territory_assessed_values=tuple(
                inputs.get("territory_assessed_values", ())
            ),
            acs_block_group=inputs.get("acs_block_group", {}) or {},
            rental_registry=inputs.get("rental_registry", {}) or {},
            imagery=inputs.get("imagery", {}) or {},
        )


# ---------------------------------------------------------------------------
# evidence source attributions (the panel's "<source> · fetched <date>" line)
# ---------------------------------------------------------------------------

SOURCE_MODIV = "NJ MOD-IV parcel record"
SOURCE_SR1A = "NJ SR1A sales register"
SOURCE_PERMITS_STATEWIDE = "NJ DCA construction permits (Socrata)"
SOURCE_PERMITS_SDL = "Ramsey municipal permits (SDL portal)"
SOURCE_SDL_SALE = "Ramsey municipal property page (SDL portal)"
SOURCE_ACS = "US Census ACS 5-year estimates, block group"
SOURCE_RENTAL = "municipal rental registration list"
SOURCE_IMAGERY = "NJ aerial orthoimagery"

_SALE_SOURCES = {"SR1A": SOURCE_SR1A, "MODIV": SOURCE_MODIV, "SDL": SOURCE_SDL_SALE}


def _permit_source(permits: Sequence[Mapping[str, Any]]) -> str:
    """The attribution line for evidence earned from these permit records."""
    names = {s for p in permits for s in (p.get("sources") or ())}
    parts = [
        label
        for key, label in (
            ("SDL", SOURCE_PERMITS_SDL),
            ("statewide", SOURCE_PERMITS_STATEWIDE),
        )
        if key in names
    ]
    return " + ".join(parts) or SOURCE_PERMITS_SDL


def _sale_source(sales: Sequence[Mapping[str, Any]]) -> str:
    names = {str(s.get("source")) for s in sales}
    parts = [label for key, label in _SALE_SOURCES.items() if key in names]
    return " + ".join(parts) or SOURCE_MODIV


# ---------------------------------------------------------------------------
# R4 / R5 / R7 formula seams
# ---------------------------------------------------------------------------

_EXP_NEG2 = math.exp(-2.0)


def mover_strength(days_since_move: int) -> float:
    """M(d) per R4: 90 for d 0..90; normalized exponential 91..364; 0 at >=365."""
    d = days_since_move
    if d < 0:
        return 0.0
    if d <= 90:
        return 90.0
    if d >= 365:
        return 0.0
    t = (d - 90) / 275.0
    return 90.0 * (math.exp(-2.0 * t) - _EXP_NEG2) / (1.0 - _EXP_NEG2)


def blend(base: float, strength: float) -> float:
    """P = B + (M/90) * ((90 + B/8) - B) per R5."""
    return base + (strength / 90.0) * ((90.0 + base / 8.0) - base)


def round_half_up(value: float) -> int:
    """round_half_up per R7: 0.5 fractions round up (away from lower int)."""
    return int(math.floor(value + 0.5))


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _parse_date(raw: Any) -> date | None:
    if isinstance(raw, date):
        return raw
    if not isinstance(raw, str):
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _years_between(start: date, end: date) -> int:
    """Whole truncated calendar years from start to end (R4/R17/R18 rule)."""
    years = end.year - start.year
    if (end.month, end.day) < (start.month, start.day):
        years -= 1
    return years


def _shift_years(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year - years)
    except ValueError:  # Feb 29
        return d.replace(year=d.year - years, day=28)


_TERMINAL_COMPLETED = {"completed"}
_TERMINAL_NONQUALIFYING = {
    "voided",
    "abandoned",
    "denied",
    "expired",
    "administrative",
}
_ROOF_KEYWORDS = ("roof", "reroof", "reshingle")
_ROOF_INSTALL_KEYWORDS = ("replacement", "reroof", "reshingle")
_ROOF_DISQUALIFIERS = (
    "repair",
    "partial",
    "porch",
    "deck",
    "addition",
    "accessory",
    "solar",
)
_EXTERIOR_KEYWORDS = ("siding", "roof", "window", "facade", "paint", "stucco")


def _desc(permit: Mapping[str, Any]) -> str:
    return str(permit.get("description") or "").lower()


def _disposition(permit: Mapping[str, Any]) -> str | None:
    d = permit.get("disposition")
    return str(d).lower() if d else None


def _completion_date(permit: Mapping[str, Any]) -> date | None:
    """Effective completion date per the shared R10/R17 fallback rule.

    Completion date preferred; issue date only when the record is explicitly
    terminal-completed and displays no later lifecycle date.
    """
    if _disposition(permit) not in _TERMINAL_COMPLETED:
        return None
    completion = _parse_date(permit.get("completion_date"))
    if completion is not None:
        return completion
    return _parse_date(permit.get("issue_date"))


def _dedupe_permits(
    permits: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    """Coalesce cross-source duplicates by municipal id, keeping the richer
    municipal (SDL) record (R8)."""
    by_key: dict[str, Mapping[str, Any]] = {}
    for permit in permits:
        key = str(permit.get("municipal_id") or permit.get("id"))
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = permit
        elif "SDL" in tuple(permit.get("sources") or ()) and "SDL" not in tuple(
            existing.get("sources") or ()
        ):
            by_key[key] = permit
    return list(by_key.values())


def _is_active(permit: Mapping[str, Any], as_of: date) -> bool:
    """R9: non-terminal with a qualifying lifecycle event within 12 months."""
    if _disposition(permit) is not None:
        return False
    cutoff = _shift_years(as_of, 1)
    events = [
        _parse_date(permit.get(k))
        for k in ("last_activity_date", "issue_date")
    ]
    return any(e is not None and cutoff <= e <= as_of for e in events)


def _mover_valid_sale(sale: Mapping[str, Any], as_of: date) -> bool:
    price = sale.get("price")
    if not isinstance(price, (int, float)) or price <= 100:
        return False
    if str(sale.get("sales_code") or "").strip():
        return False
    sale_date = _parse_date(sale.get("date"))
    return sale_date is not None and sale_date <= as_of


# ---------------------------------------------------------------------------
# main entry point
# ---------------------------------------------------------------------------


def _permit_brief(p: Mapping[str, Any]) -> str:
    """One permit as doorstep prose: its displayed scope and issue date."""
    desc = str(p.get("description") or p.get("work_type") or "permit").strip()
    issued = _parse_date(p.get("issue_date"))
    return f"{desc}, issued {issued.isoformat()}" if issued else desc


def _briefs(permits: Sequence[Mapping[str, Any]], limit: int = 3) -> str:
    listed = "; ".join(_permit_brief(p) for p in permits[:limit])
    extra = len(permits) - limit
    return f"{listed}; and {extra} more" if extra > 0 else listed


def score_door_v2(bundle: V2Bundle, as_of: date) -> Mapping[str, Any]:
    """Score one door as of ``as_of``; returns the V2 result envelope dict."""
    evidence: list[dict[str, Any]] = []
    data_gaps: list[dict[str, str]] = []

    def add(etype: str, points: float, reason: str, source: str | None = None) -> None:
        # `source`/`retrieved` are the panel's attribution line. Derived
        # entries (category caps) carry no source: they cite no record, only
        # arithmetic over the entries above them.
        evidence.append(
            {
                "type": etype,
                "points": points,
                "reason": reason,
                "source": source,
                "retrieved": as_of.isoformat() if source else None,
            }
        )

    parcel = bundle.parcel

    if parcel.get("sdl_page_available") is False:
        data_gaps.append({"type": "sdl_page_unavailable"})

    imagery_available = bundle.imagery.get("available") is not False
    observations = tuple(bundle.imagery.get("observations") or ())
    if not imagery_available:
        data_gaps.append({"type": "imagery_missing"})
        observations = ()

    # --- Project and Hiring Behavior (R8-R11, cap 45) ---
    #
    # One flat rule (user-directed 2026-08-19, replacing the earlier
    # active/completed/multi/major sub-rules): every distinct qualifying
    # permit earns 15, where qualifying means active now (R9) or completed
    # within 24 months (R10). Three permits saturate the 45 cap.
    permits = _dedupe_permits(bundle.permits)
    window_24mo = _shift_years(as_of, 2)
    project_raw = 0

    scoring_permits = [
        p for p in permits if _disposition(p) not in _TERMINAL_NONQUALIFYING
    ]
    for permit in scoring_permits:
        if _is_active(permit, as_of):
            project_raw += 15
            add(
                "project_active",
                15,
                f"open municipal project with activity in the last 12 months: "
                f"{_permit_brief(permit)}",
                _permit_source([permit]),
            )
        elif (
            completed := _completion_date(permit)
        ) is not None and window_24mo <= completed <= as_of:
            project_raw += 15
            add(
                "project_completed",
                15,
                f"project completed {completed.isoformat()}, within 24 months: "
                f"{_permit_brief(permit)}",
                _permit_source([permit]),
            )

    project_points = min(project_raw, 45)
    if project_raw > 45:
        add("project_cap_adjustment", project_points - project_raw, "project category capped at 45")
    if permits and project_points == 0:
        add(
            "project_neutralized",
            0,
            f"{len(permits)} permit record{'s' if len(permits) != 1 else ''} on "
            "file, but none active within 12 months or completed within 24 — no "
            "project points under conservative rules",
            _permit_source(permits),
        )

    # --- Capacity (R13-R16, cap 25) ---
    capacity_raw = 0

    assessed = parcel.get("assessed_value")
    if assessed is None and parcel.get("sdl_match_exact_current"):
        assessed = parcel.get("sdl_assessed_value")
    if assessed is None:
        data_gaps.append({"type": "assessed_value_missing"})
    else:
        valid_comps = [
            c
            for c in bundle.local_comparables
            if isinstance(c.get("assessed_value"), (int, float))
        ]
        valid_comps.sort(key=lambda c: c.get("distance_m", 0))
        valid_comps = valid_comps[:20]
        if len(valid_comps) < 10:
            data_gaps.append({"type": "local_comparables_insufficient"})
        else:
            local_median = median(c["assessed_value"] for c in valid_comps)
            ratio = assessed / local_median if local_median else 0.0
            if ratio >= 1.5:
                local_pts = 10
            elif ratio >= 1.2:
                local_pts = 7
            elif ratio > 1.0:
                local_pts = 3
            else:
                local_pts = 0
            if local_pts:
                capacity_raw += local_pts
                add(
                    "capacity_local_relative_value",
                    local_pts,
                    f"assessed at ${assessed:,.0f}, {ratio:.2f}× the "
                    f"${local_median:,.0f} median of the nearest "
                    f"{len(valid_comps)} single-family comparables",
                    SOURCE_MODIV,
                )

        territory = [
            v
            for v in bundle.territory_assessed_values
            if isinstance(v, (int, float))
        ]
        if territory:
            below = sum(1 for v in territory if v < assessed)
            equal = sum(1 for v in territory if v == assessed)
            percentile = (below + 0.5 * equal) / len(territory) * 100.0
            if percentile >= 90:
                pct_pts = 10
            elif percentile >= 75:
                pct_pts = 7
            elif percentile >= 50:
                pct_pts = 3
            else:
                pct_pts = 0
            if pct_pts:
                capacity_raw += pct_pts
                add(
                    "capacity_territory_percentile",
                    pct_pts,
                    f"assessed value sits at the {percentile:.0f}th percentile "
                    f"of the {len(territory)} territory single-family properties",
                    SOURCE_MODIV,
                )

    dual_income = bundle.acs_block_group.get("dual_income_pct")
    if dual_income is None:
        data_gaps.append({"type": "acs_missing"})
    elif dual_income >= 0.35:
        capacity_raw += 5
        add(
            "capacity_acs_dual_income_prior",
            5,
            f"{dual_income:.0%} of this block group's households are "
            "dual-income (at or above the 35% prior; a neighborhood-level "
            "prior, not a household claim)",
            SOURCE_ACS,
        )

    capacity_points = min(capacity_raw, 25)
    if capacity_raw > 25:
        add("capacity_cap_adjustment", capacity_points - capacity_raw, "capacity category capped at 25")

    # --- Property Service Fit / Need (R17-R21, cap 48 = the sum of every signal) ---
    fit_raw = 0

    roof_records = [p for p in permits if any(k in _desc(p) for k in _ROOF_KEYWORDS)]
    roof_installs = [
        p
        for p in roof_records
        if any(k in _desc(p) for k in _ROOF_INSTALL_KEYWORDS)
        and not any(k in _desc(p) for k in _ROOF_DISQUALIFIERS)
    ]
    roof_dates = [
        c for p in roof_installs if (c := _completion_date(p)) is not None and c <= as_of
    ]
    if roof_dates:
        roof_when = max(roof_dates)
        roof_age = _years_between(roof_when, as_of)
        if roof_age >= 20:
            roof_pts = 12
        elif roof_age >= 15:
            roof_pts = 8
        elif roof_age >= 10:
            roof_pts = 4
        else:
            roof_pts = 0
        if roof_pts:
            fit_raw += roof_pts
            add(
                "fit_roof_age",
                roof_pts,
                f"latest completed roof installation was {roof_when.isoformat()} "
                f"— {roof_age} years old, old enough to need service",
                _permit_source(roof_installs),
            )
        else:
            add(
                "fit_roof_age",
                0,
                f"roof installed {roof_when.isoformat()} — {roof_age} years old, "
                "younger than 10 years",
                _permit_source(roof_installs),
            )
    elif roof_records:
        add(
            "fit_roof_age",
            0,
            f"{len(roof_records)} roof-related permit record"
            f"{'s' if len(roof_records) != 1 else ''} on file "
            f"({_briefs(roof_records)}), but none establish a completed "
            "installation age",
            _permit_source(roof_records),
        )

    year = parcel.get("construction_year")
    if isinstance(year, int) and 0 < year <= as_of.year:
        home_age = as_of.year - year
        if home_age >= 75:
            home_pts = 10
        elif home_age >= 50:
            home_pts = 8
        elif home_age >= 30:
            home_pts = 5
        else:
            home_pts = 0
        if home_pts:
            fit_raw += home_pts
            add(
                "fit_home_age",
                home_pts,
                f"built {year} — a {home_age}-year-old home, an age that "
                "supports service fit",
                SOURCE_MODIV,
            )

    decline = any(
        o.get("kind") == "condition_decline" and (o.get("confidence") or 0) >= 0.60
        for o in observations
    )
    if decline:
        superseding = [
            p
            for p in permits
            if any(k in _desc(p) for k in _EXTERIOR_KEYWORDS)
            and (c := _completion_date(p)) is not None
            and c > date(2020, 12, 31)
        ]
        if superseding:
            add(
                "fit_condition_superseded",
                0,
                "exterior-condition decline observation superseded by a later "
                f"completed exterior permit ({_briefs(superseding)})",
                f"{SOURCE_IMAGERY} + {_permit_source(superseding)}",
            )
        else:
            fit_raw += 8
            add(
                "fit_condition_decline",
                8,
                "verified historical exterior-condition decline between the "
                "2015 and 2020 imagery vintages",
                SOURCE_IMAGERY,
            )

    def _feature(keyword: str, label: str, suffix: str = "") -> tuple[str, str] | None:
        """(reason, source) when the feature is established, else None."""
        by_permit = [
            p for p in permits if keyword in _desc(p) and _completion_date(p) is not None
        ]
        by_imagery = [
            o
            for o in observations
            if o.get("kind") == keyword and (o.get("confidence") or 0) >= 0.60
        ]
        if by_permit and by_imagery:
            return (
                f"{label} established by a completed permit ({_briefs(by_permit, limit=1)}) "
                f"and confirmed in aerial imagery{suffix}",
                f"{_permit_source(by_permit)} + {SOURCE_IMAGERY}",
            )
        if by_permit:
            return (
                f"{label} established by a completed permit "
                f"({_briefs(by_permit, limit=1)}){suffix}",
                _permit_source(by_permit),
            )
        if by_imagery:
            return (f"{label} visible in aerial imagery{suffix}", SOURCE_IMAGERY)
        return None

    if (pool := _feature("pool", "pool", " — a standing maintenance commitment")) is not None:
        fit_raw += 8
        add("fit_pool", 8, *pool)
    if (solar := _feature("solar", "solar panels")) is not None:
        fit_raw += 5
        add("fit_solar", 5, *solar)

    lot = parcel.get("lot_acres")
    if isinstance(lot, (int, float)) and lot >= 0.5:
        fit_raw += 5
        add(
            "fit_lot",
            5,
            f"{float(lot):.2f}-acre lot — at least half an acre",
            SOURCE_MODIV,
        )

    fit_points = min(fit_raw, 48)
    if fit_raw > 48:
        add("fit_cap_adjustment", fit_points - fit_raw, "property-fit category capped at 48")

    base = project_points + capacity_points + fit_points

    # --- Mover (R3-R5) ---
    valid_sales = []
    invalid_sales = []
    for sale in bundle.sales:
        if _mover_valid_sale(sale, as_of):
            valid_sales.append(sale)
        else:
            invalid_sales.append(sale)
    if invalid_sales:
        add(
            "mover_invalid_sale",
            0,
            "a transfer was disqualified from mover influence (nominal price, "
            "disqualifying code, or invalid/future date)",
            _sale_source(invalid_sales),
        )

    latest_sale_date: date | None = None
    if valid_sales:
        latest_sale_date = max(_parse_date(s.get("date")) for s in valid_sales)

    if latest_sale_date is not None:
        days = (as_of - latest_sale_date).days
        strength = mover_strength(days)
        mover = {"eligible": True, "days_since_move": days, "strength": strength}
    else:
        strength = 0.0
        mover = {"eligible": False, "days_since_move": None, "strength": 0.0}

    pre_rental = blend(float(base), strength)
    mover_lift = pre_rental - base
    if mover_lift > 0:
        latest = max(valid_sales, key=lambda s: str(s.get("date")))
        add(
            "mover_recency",
            mover_lift,
            f"home changed hands {mover['days_since_move']} days ago "
            f"({latest_sale_date.isoformat()}) in a market sale — new owners "
            "are the likeliest to start projects, so the score gets a large "
            "lift that fades out over the first year",
            _sale_source([latest]),
        )

    # --- Rental (R6) ---
    rental_modifier = 0
    registry = bundle.rental_registry
    if registry.get("available") is not True:
        data_gaps.append({"type": "rental_data_missing"})
    else:
        reg_dates = [
            d
            for r in (registry.get("registrations") or ())
            if (d := _parse_date(r.get("date"))) is not None
        ]
        if reg_dates:
            latest_reg = max(reg_dates)
            if latest_sale_date is not None:
                current = latest_reg >= latest_sale_date
            else:
                current = latest_reg >= _shift_years(as_of, 2)
            if current:
                rental_modifier = -25
                add(
                    "rental_registration",
                    -25,
                    f"current verified rental registration "
                    f"({latest_reg.isoformat()}) demotes the door",
                    SOURCE_RENTAL,
                )
            else:
                add(
                    "rental_stale",
                    0,
                    f"rental registration ({latest_reg.isoformat()}) is stale "
                    "and neutral until reverified",
                    SOURCE_RENTAL,
                )

    pre_rounding = pre_rental + rental_modifier
    score = round_half_up(min(max(pre_rounding, 0.0), 100.0))
    adjustment = float(score - pre_rounding)

    confidence = "low" if len(data_gaps) >= 2 else "normal"

    return {
        "score_contract_version": "v2",
        "confidence": confidence,
        "categories": {
            "project": project_points,
            "capacity": capacity_points,
            "fit": fit_points,
        },
        "base": base,
        "mover": mover,
        "mover_lift": float(mover_lift),
        "rental_modifier": rental_modifier,
        "pre_rounding": float(pre_rounding),
        "adjustment": adjustment,
        "evidence": evidence,
        "data_gaps": sorted(data_gaps, key=lambda g: g["type"]),
        "score": score,
    }
