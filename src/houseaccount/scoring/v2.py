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
_MAJOR_KEYWORDS = ("addition", "renovation", "alteration", "new construction")
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


def score_door_v2(bundle: V2Bundle, as_of: date) -> Mapping[str, Any]:
    """Score one door as of ``as_of``; returns the V2 result envelope dict."""
    evidence: list[dict[str, Any]] = []
    data_gaps: list[dict[str, str]] = []

    def add(etype: str, points: float, reason: str) -> None:
        evidence.append({"type": etype, "points": points, "reason": reason})

    parcel = bundle.parcel

    if parcel.get("sdl_page_available") is False:
        data_gaps.append({"type": "sdl_page_unavailable"})

    imagery_available = bundle.imagery.get("available") is not False
    observations = tuple(bundle.imagery.get("observations") or ())
    if not imagery_available:
        data_gaps.append({"type": "imagery_missing"})
        observations = ()

    # --- Project and Hiring Behavior (R8-R12, cap 25) ---
    permits = _dedupe_permits(bundle.permits)
    window_24mo = _shift_years(as_of, 2)
    project_raw = 0

    scoring_permits = [
        p for p in permits if _disposition(p) not in _TERMINAL_NONQUALIFYING
    ]
    active = [p for p in scoring_permits if _is_active(p, as_of)]
    if active:
        project_raw += 15
        add("project_active", 15, "active qualifying project with recent lifecycle activity")

    completed_recent = [
        p
        for p in scoring_permits
        if (c := _completion_date(p)) is not None and window_24mo <= c <= as_of
    ]
    if completed_recent:
        project_raw += 8
        add("project_completed", 8, "qualifying project completed within 24 months")

    def _issued_within_24mo(p: Mapping[str, Any]) -> bool:
        issued = _parse_date(p.get("issue_date"))
        return issued is not None and window_24mo <= issued <= as_of

    qualifying_recent = [
        p
        for p in scoring_permits
        if _issued_within_24mo(p)
        and (_completion_date(p) is not None or _is_active(p, as_of))
    ]
    if len(qualifying_recent) >= 2:
        project_raw += 5
        add("project_multi_permit", 5, "two or more distinct qualifying permits issued within 24 months")
    if any(k in _desc(p) for p in qualifying_recent for k in _MAJOR_KEYWORDS):
        project_raw += 5
        add("project_major", 5, "qualifying major renovation or addition within 24 months")

    project_points = min(project_raw, 25)
    if project_raw > 25:
        add("project_cap_adjustment", project_points - project_raw, "project category capped at 25")
    if permits and project_points == 0:
        add("project_neutralized", 0, "permit records present but none earn project points under conservative rules")

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
                add("capacity_local_relative_value", local_pts, "assessed value above the median of the nearest comparables")

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
                add("capacity_territory_percentile", pct_pts, "assessed value ranks high among territory single-family properties")

    dual_income = bundle.acs_block_group.get("dual_income_pct")
    if dual_income is None:
        data_gaps.append({"type": "acs_missing"})
    elif dual_income >= 0.35:
        capacity_raw += 5
        add("capacity_acs_dual_income_prior", 5, "neighborhood-level ACS dual-income prior at or above 35% (block-group prior, not a household claim)")

    capacity_points = min(capacity_raw, 25)
    if capacity_raw > 25:
        add("capacity_cap_adjustment", capacity_points - capacity_raw, "capacity category capped at 25")

    # --- Property Service Fit (R17-R21, cap 30) ---
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
        roof_age = _years_between(max(roof_dates), as_of)
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
            add("fit_roof_age", roof_pts, "latest explicit completed roof installation is old enough to need service")
        else:
            add("fit_roof_age", 0, "known roof installation younger than 10 years")
    elif roof_records:
        add("fit_roof_age", 0, "roof records present but none establish a completed installation age")

    year = parcel.get("construction_year")
    if isinstance(year, int) and 0 < year <= as_of.year:
        home_age = as_of.year - year
        if home_age >= 75:
            home_pts = 8
        elif home_age >= 50:
            home_pts = 5
        elif home_age >= 30:
            home_pts = 2
        else:
            home_pts = 0
        if home_pts:
            fit_raw += home_pts
            add("fit_home_age", home_pts, "home age supports service fit")

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
            add("fit_condition_superseded", 0, "exterior-condition decline observation superseded by a later completed exterior permit")
        else:
            fit_raw += 8
            add("fit_condition_decline", 8, "verified historical exterior-condition decline between the 2015 and 2020 imagery vintages")

    def _feature(keyword: str) -> bool:
        by_permit = any(
            keyword in _desc(p) and _completion_date(p) is not None for p in permits
        )
        by_imagery = any(
            o.get("kind") == keyword and (o.get("confidence") or 0) >= 0.60
            for o in observations
        )
        return by_permit or by_imagery

    if _feature("pool"):
        fit_raw += 5
        add("fit_pool", 5, "pool established by completed permit or qualifying imagery")
    if _feature("solar"):
        fit_raw += 5
        add("fit_solar", 5, "solar panels established by completed permit or qualifying imagery")

    lot = parcel.get("lot_acres")
    if isinstance(lot, (int, float)) and lot >= 0.5:
        fit_raw += 5
        add("fit_lot", 5, "lot of at least half an acre")

    fit_points = min(fit_raw, 30)
    if fit_raw > 30:
        add("fit_cap_adjustment", fit_points - fit_raw, "property-fit category capped at 30")

    base = project_points + capacity_points + fit_points

    # --- Mover (R3-R5) ---
    valid_sales = []
    any_invalid = False
    for sale in bundle.sales:
        if _mover_valid_sale(sale, as_of):
            valid_sales.append(sale)
        else:
            any_invalid = True
    if any_invalid:
        add("mover_invalid_sale", 0, "a transfer was disqualified from mover influence (nominal price, disqualifying code, or invalid/future date)")

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
        add("mover_recency", mover_lift, "recent valid arm's-length move blends the score toward the mover priority band")

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
                add("rental_registration", -25, "current verified rental registration demotes the door")
            else:
                add("rental_stale", 0, "rental registration is stale and neutral until reverified")

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
