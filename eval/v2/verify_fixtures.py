#!/usr/bin/env python3
"""Verify Door Score V2 golden fixtures by re-deriving every expectation from `given`.

This is the Phase 4 domain verifier for the V2 contract in
docs/plans/2026-08-18-2221-feat-door-score-v2-plan.md (R1-R26). It implements the
contract arithmetic directly from the requirement text and deep-compares the derived
observation envelope against each fixture's expect.exact.result. It also asserts the
plan's Acceptance Example anchors (AE1-AE4) and the band tables at every boundary.

Envelope conventions (binding on the future product golden runner):
- Floats (mover.strength, mover_lift, pre_rounding, adjustment) are rounded to 3
  decimal places before comparison.
- result.evidence is projected to {type, points} and sorted by type; entries exist for
  every nonzero signal and for the consequential neutralizations defined in R26 (see
  derive() for the exact emission rules).
- result.data_gaps is a list of {type} sorted by type.
- state_changes / emitted_events / external_calls are exactly [] (scoring is pure).

This validates the SPEC, not the product. The implementation must supply its own
golden runner (test-golden) that drives the real scoring entry point.
"""

from __future__ import annotations

import json
import math
import sys
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

HERE = Path(__file__).resolve().parent
GOLDEN_DIR = HERE / "golden"
MANIFEST = HERE / "golden-manifest.json"

# --- contract constants (plan R2, R4, R5, R6) ---
PROJECT_CAP = 45
CAPACITY_CAP = 25
FIT_CAP = 48
MOVER_PEAK = 90.0
MOVER_FLAT_DAYS = 90
MOVER_ZERO_DAY = 365
RENTAL_MODIFIER = -25
NOMINAL_PRICE_MAX = 100  # R3: at or below $100 is non-arm's-length

ROOF_INSTALL_KEYWORDS = ("roof replacement", "reroof", "re-roof", "reshingle", "new roof")
EXTERIOR_KEYWORDS = ("siding", "roof", "window", "facade", "paint", "stucco")
TERMINAL_COMPLETED = "completed"
NON_QUALIFYING_DISPOSITIONS = {"voided", "abandoned", "denied", "expired", "administrative"}


def r3(x: float) -> float:
    return float(Decimal(str(x)).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP))


def round_half_up(x: float) -> int:
    return int(Decimal(str(x)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def parse_date(s: str) -> date:
    return date.fromisoformat(s[:10])


def years_between(start: date, end: date) -> int:
    y = end.year - start.year
    if (end.month, end.day) < (start.month, start.day):
        y -= 1
    return y


def mover_strength(d: int) -> float:
    """R4: flat 90 through day 90, normalized exponential to 0 at day 365."""
    if d <= MOVER_FLAT_DAYS:
        return MOVER_PEAK
    if d >= MOVER_ZERO_DAY:
        return 0.0
    t = (d - MOVER_FLAT_DAYS) / 275.0
    return MOVER_PEAK * (math.exp(-2.0 * t) - math.exp(-2.0)) / (1.0 - math.exp(-2.0))


def dedupe_permits(permits: list[dict]) -> list[dict]:
    """R8: same municipal record across sources counts once."""
    seen: dict[str, dict] = {}
    for p in permits:
        key = p.get("municipal_id") or p["id"]
        if key not in seen:
            seen[key] = p
    return list(seen.values())


def derive(given: dict) -> dict:
    inputs = given["inputs"]
    as_of = parse_date(given["clock"]["as_of"])
    parcel = inputs["parcel"]
    permits = dedupe_permits(inputs.get("permits", []))
    evidence: list[dict] = []
    gaps: set[str] = set()

    # --- Project and Hiring Behavior (R8-R12) ---
    project_raw = 0
    window_24m = as_of - timedelta(days=730)
    window_12m = as_of - timedelta(days=365)

    def is_qualifying(p: dict) -> bool:
        return (p.get("disposition") or "") not in NON_QUALIFYING_DISPOSITIONS

    active = [
        p
        for p in permits
        if is_qualifying(p)
        and p.get("disposition") != TERMINAL_COMPLETED
        and parse_date(p.get("last_activity_date") or p.get("issue_date")) >= window_12m
    ]
    def completion_effective_date(p: dict) -> str | None:
        """R10/R17: completion date preferred; issue date only for an explicitly
        terminal record displaying no later lifecycle date."""
        if p.get("disposition") != TERMINAL_COMPLETED:
            return None
        if p.get("completion_date"):
            return p["completion_date"]
        if p.get("issue_date") and not p.get("last_activity_date"):
            return p["issue_date"]
        return None

    completed_recent = [
        p
        for p in permits
        if completion_effective_date(p)
        and parse_date(completion_effective_date(p)) >= window_24m
    ]
    # One flat rule (user-directed 2026-08-19): every distinct qualifying
    # permit — active now, or completed within 24 months — earns 15; three
    # saturate the 45 cap. A permit cannot be both: completed is terminal.
    for p in active:
        project_raw += 15
        evidence.append({"type": "project_active", "points": 15})
    for p in completed_recent:
        project_raw += 15
        evidence.append({"type": "project_completed", "points": 15})
    if permits and project_raw == 0:
        evidence.append({"type": "project_neutralized", "points": 0})
    project = min(PROJECT_CAP, project_raw)
    if project_raw > PROJECT_CAP:
        evidence.append({"type": "project_cap_adjustment", "points": PROJECT_CAP - project_raw})

    # --- Capacity (R13-R16) ---
    capacity_raw = 0
    # R22: parcel facts primary; SDL assessed valuation fills a gap only when the
    # match is exact and current, and never overwrites a present primary value.
    subject_value = parcel.get("assessed_value")
    if subject_value is None and parcel.get("sdl_match_exact_current") and parcel.get("sdl_assessed_value") is not None:
        subject_value = parcel["sdl_assessed_value"]
    comps = sorted(inputs.get("local_comparables", []), key=lambda c: c["distance_m"])[:20]
    if subject_value is None:
        gaps.add("assessed_value_missing")
    if len(comps) < 10 or subject_value is None:
        if subject_value is not None:
            gaps.add("local_comparables_insufficient")
    else:
        values = sorted(c["assessed_value"] for c in comps)
        n = len(values)
        median = (
            values[n // 2] if n % 2 == 1 else (values[n // 2 - 1] + values[n // 2]) / 2.0
        )
        ratio = subject_value / median
        pts = 0 if ratio <= 1.0 else 3 if ratio < 1.2 else 7 if ratio < 1.5 else 10
        capacity_raw += pts
        if pts:
            evidence.append({"type": "capacity_local_relative_value", "points": pts})

    universe = inputs.get("territory_assessed_values", [])
    if universe and subject_value is not None:
        below = sum(1 for v in universe if v < subject_value)
        equal = sum(1 for v in universe if v == subject_value)
        pct = 100.0 * (below + 0.5 * equal) / len(universe)  # R14 midrank
        pts = 0 if pct < 50 else 3 if pct < 75 else 7 if pct < 90 else 10
        capacity_raw += pts
        if pts:
            evidence.append({"type": "capacity_territory_percentile", "points": pts})

    acs = inputs.get("acs_block_group") or {}
    if "dual_income_pct" not in acs:
        gaps.add("acs_missing")
    elif acs["dual_income_pct"] >= 0.35:
        capacity_raw += 5
        evidence.append({"type": "capacity_acs_dual_income_prior", "points": 5})
    capacity = min(CAPACITY_CAP, capacity_raw)
    if capacity_raw > CAPACITY_CAP:
        evidence.append({"type": "capacity_cap_adjustment", "points": CAPACITY_CAP - capacity_raw})

    # --- Property Service Fit (R17-R21) ---
    fit_raw = 0
    roof_candidates = [
        p for p in permits if any(k in p.get("description", "").lower() for k in ROOF_INSTALL_KEYWORDS)
    ]
    roof_installs = [p for p in roof_candidates if completion_effective_date(p)]
    generic_roof = [
        p
        for p in permits
        if "roof" in p.get("description", "").lower() and p not in roof_candidates
    ]
    if roof_installs:
        latest = max(roof_installs, key=completion_effective_date)
        age = years_between(parse_date(completion_effective_date(latest)), as_of)
        pts = 0 if age < 10 else 4 if age < 15 else 8 if age < 20 else 12
        fit_raw += pts
        evidence.append({"type": "fit_roof_age", "points": pts})
    elif roof_candidates or generic_roof:
        evidence.append({"type": "fit_roof_age", "points": 0})  # R17/R26 neutralization

    yr = parcel.get("construction_year")
    if yr:
        age = as_of.year - yr
        pts = 0 if age < 30 else 5 if age < 50 else 8 if age < 75 else 10
        fit_raw += pts
        if pts:
            evidence.append({"type": "fit_home_age", "points": pts})

    imagery = inputs.get("imagery", {})
    obs = imagery.get("observations", []) if imagery.get("available") else []
    if not imagery.get("available", False):
        gaps.add("imagery_missing")
    decline = [
        o for o in obs if o["kind"] == "condition_decline" and o["confidence"] >= 0.60
    ]
    if decline:
        superseding = [
            p
            for p in permits
            if p.get("disposition") == TERMINAL_COMPLETED
            and p.get("completion_date")
            and parse_date(p["completion_date"]) > date(2020, 12, 31)
            and any(k in p.get("description", "").lower() for k in EXTERIOR_KEYWORDS)
        ]
        if superseding:
            evidence.append({"type": "fit_condition_superseded", "points": 0})
        else:
            fit_raw += 8
            evidence.append({"type": "fit_condition_decline", "points": 8})

    def feature_established(name: str) -> bool:
        by_permit = any(
            name in p.get("description", "").lower()
            and p.get("disposition") == TERMINAL_COMPLETED
            for p in permits
        )
        by_imagery = any(o["kind"] == name and o["confidence"] >= 0.60 for o in obs)
        return by_permit or by_imagery

    # Pool 8 (user-directed 2026-08-19: a standing maintenance commitment),
    # solar 5.
    for feature, pts in (("pool", 8), ("solar", 5)):
        if feature_established(feature):
            fit_raw += pts
            evidence.append({"type": f"fit_{feature}", "points": pts})

    if parcel.get("lot_acres") is not None and parcel["lot_acres"] >= 0.5:
        fit_raw += 5
        evidence.append({"type": "fit_lot", "points": 5})
    fit = min(FIT_CAP, fit_raw)
    if fit_raw > FIT_CAP:
        evidence.append({"type": "fit_cap_adjustment", "points": FIT_CAP - fit_raw})

    if not parcel.get("sdl_page_available", True):
        gaps.add("sdl_page_unavailable")

    base = project + capacity + fit

    # --- Mover (R3-R5) ---
    valid_sales = []
    invalid_present = False
    for s in inputs.get("sales", []):
        try:
            sd = parse_date(s["date"])
        except (ValueError, KeyError):
            invalid_present = True
            continue
        if s.get("price", 0) <= NOMINAL_PRICE_MAX or s.get("sales_code", "") or sd > as_of:
            invalid_present = True
            continue
        valid_sales.append(sd)
    eligible = bool(valid_sales)
    days = (as_of - max(valid_sales)).days if eligible else None
    strength = mover_strength(days) if eligible else 0.0
    if strength > 0:
        blended = base + (strength / MOVER_PEAK) * ((MOVER_PEAK + base / 8.0) - base)
    else:
        blended = float(base)
    lift = blended - base
    if strength > 0:
        evidence.append({"type": "mover_recency", "points": r3(lift)})
    if invalid_present:
        evidence.append({"type": "mover_invalid_sale", "points": 0})

    # --- Rental (R6) ---
    rental = inputs.get("rental_registry", {})
    rental_modifier = 0
    if not rental.get("available", False):
        gaps.add("rental_data_missing")
    else:
        regs = [parse_date(r["date"]) for r in rental.get("registrations", [])]
        if regs:
            latest_reg = max(regs)
            latest_sale = max(valid_sales) if valid_sales else None
            # R6: current = dated on/after latest valid sale; with no valid sale,
            # current only within a provisional 24-month window before as_of.
            if (latest_sale is not None and latest_reg >= latest_sale) or (
                latest_sale is None and (as_of - latest_reg).days <= 730
            ):
                rental_modifier = RENTAL_MODIFIER
                evidence.append({"type": "rental_registration", "points": RENTAL_MODIFIER})
            else:
                evidence.append({"type": "rental_stale", "points": 0})

    # --- Final (R7) ---
    pre = blended + rental_modifier
    score = round_half_up(min(100.0, max(0.0, pre)))

    return {
        "score_contract_version": "v2",
        "confidence": "low" if len(gaps) >= 2 else "normal",  # R37
        "categories": {"project": project, "capacity": capacity, "fit": fit},
        "base": base,
        "mover": {
            "eligible": eligible,
            "days_since_move": days,
            "strength": r3(strength),
        },
        "mover_lift": r3(lift),
        "rental_modifier": rental_modifier,
        "pre_rounding": r3(pre),
        "adjustment": r3(score - pre),
        "score": score,
        "evidence": sorted(evidence, key=lambda e: e["type"]),
        "data_gaps": sorted(({"type": g} for g in gaps), key=lambda g: g["type"]),
    }


def check_anchors() -> list[str]:
    """Assert the plan's Acceptance Example arithmetic and band boundaries."""
    errors = []

    def expect(cond, msg):
        if not cond:
            errors.append(msg)

    # AE1-AE4 mover arithmetic
    expect(mover_strength(47) == 90.0, "AE1/AE2: M(47) must be 90")
    expect(mover_strength(90) == 90.0, "R4: M(90) must be 90")
    expect(0 < mover_strength(91) < 90, "R4: M(91) must be inside (0, 90)")
    expect(abs(mover_strength(180) - 40.005) < 0.001, "AE3: M(180) ~ 40.005")
    expect(mover_strength(364) > 0, "R4: M(364) must be > 0")
    expect(mover_strength(365) == 0.0, "AE4: M(365) must be 0")
    b = 40
    p = b + (mover_strength(47) / 90.0) * ((90 + b / 8.0) - b)
    expect(round_half_up(p) == 95, "AE2: B=40 fresh mover must score 95")
    p180 = b + (mover_strength(180) / 90.0) * ((90 + b / 8.0) - b)
    expect(abs(p180 - 64.448) < 0.001 and round_half_up(p180) == 64, "AE3: blend ~64.448 -> 64")
    expect(round_half_up(0.5) == 1 and round_half_up(1.5) == 2, "R7: round half up")

    # Band boundary tables (R13, R14, R17, R18)
    def local_pts(ratio):
        return 0 if ratio <= 1.0 else 3 if ratio < 1.2 else 7 if ratio < 1.5 else 10

    expect([local_pts(r) for r in (1.0, 1.01, 1.2, 1.5)] == [0, 3, 7, 10], "R13 bands")

    def pct_pts(pct):
        return 0 if pct < 50 else 3 if pct < 75 else 7 if pct < 90 else 10

    expect([pct_pts(p_) for p_ in (49.9, 50, 74.9, 75, 89.9, 90)] == [0, 3, 3, 7, 7, 10], "R14 bands")

    def roof_pts(age):
        return 0 if age < 10 else 4 if age < 15 else 8 if age < 20 else 12

    expect([roof_pts(a) for a in (9, 10, 14, 15, 19, 20)] == [0, 4, 4, 8, 8, 12], "R17 bands")

    def home_pts(age):
        return 0 if age < 30 else 5 if age < 50 else 8 if age < 75 else 10

    expect([home_pts(a) for a in (29, 30, 49, 50, 74, 75)] == [0, 5, 5, 8, 8, 10], "R18 bands")
    return errors


def main() -> int:
    errors = check_anchors()
    fixtures = sorted(GOLDEN_DIR.glob("*.json"))
    if not fixtures:
        errors.append(f"no fixtures found in {GOLDEN_DIR}")
    for path in fixtures:
        fx = json.loads(path.read_text())
        derived = derive(fx["given"])
        expected = fx["expect"]["exact"]["result"]
        if derived != expected:
            errors.append(f"{path.name} ({fx['id']}): derived result != expect.exact.result")
            for key in expected:
                if derived.get(key) != expected[key]:
                    errors.append(f"    {key}: derived={derived.get(key)!r} expected={expected[key]!r}")
        for surface in ("state_changes", "emitted_events", "external_calls"):
            if fx["expect"]["exact"][surface] != []:
                errors.append(f"{path.name}: {surface} must be [] for pure scoring")
    if MANIFEST.exists():
        manifest = json.loads(MANIFEST.read_text())
        behavior_ids = {b["id"] for b in manifest["behaviors"]}
        covered = set()
        for path in fixtures:
            covered.update(json.loads(path.read_text())["covers"])
        missing = behavior_ids - covered
        if missing:
            errors.append(f"behaviors with no fixture coverage: {sorted(missing)}")
    else:
        errors.append(f"missing manifest {MANIFEST}")

    if errors:
        print(f"FAIL — {len(errors)} problem(s):")
        for e in errors:
            print(f"  - {e}")
        return 1
    print(f"OK — {len(fixtures)} fixtures re-derived from given; anchors AE1-AE4 and band tables hold.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
