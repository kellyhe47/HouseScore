"""The one place the House Score's numbers live (T003).

The Data & Ethics page (T014) publishes this table verbatim, so a weight that
is re-typed inline in the engine is a weight the public page can silently
contradict. Everything the score adds, subtracts or compares against is here,
and the engine reads it rather than restating it.

Split in three because they are different kinds of number: `WEIGHTS` are signed
point values (all int — the score is an integer), `THRESHOLDS` are the cut-offs
those points hang on, and `CONDITION_ORDER` is an ordinal scale, worst first.
"""

from __future__ import annotations

#: Signed point values. Every one of these has a matching evidence sentence in
#: the engine — no points move without a line a rep can read at the door.
WEIGHTS: dict[str, int] = {
    # Mover (group max 100) — the strongest single signal in the model.
    "mover_30d": 100,
    "mover_60d": 85,
    "mover_90d": 70,
    # Hires-out (group max 60).
    "permit_each": 20,
    "permit_cap": 40,
    "provider_churn": 20,
    # Capacity (group max 30). The two value bands are alternatives, not a sum.
    "capacity_median": 15,
    "capacity_1_5x": 25,
    "capacity_acs_prior": 5,
    # Need (group max 32).
    "need_home_age": 8,
    "need_pool": 8,
    "need_lot": 4,
    "need_condition_decline": 8,
    "need_deferred_maintenance": 4,
    # Modifier — mild by design: a registered rental is a demotion, never an
    # exclusion, because tenants and landlords both buy home services.
    "absentee_modifier": -15,
}

#: The cut-offs the weights hang on. Floats, so they live apart from WEIGHTS.
THRESHOLDS: dict[str, float] = {
    "mover_30d_days": 30,
    "mover_60d_days": 60,
    "mover_90d_days": 90,
    "permit_window_days": 730,
    "provider_churn_min_contractors": 2,
    "nominal_sale_price_usd": 100,
    "capacity_1_5x_multiple": 1.5,
    "need_home_age_years": 30,
    "need_lot_acres": 0.5,
    "score_floor": 0,
    "score_ceiling": 100,
}

#: Exterior condition as an ordinal scale, worst first. A decline is any move
#: toward the front of this tuple between two ortho vintages.
CONDITION_ORDER: tuple[str, ...] = ("poor", "fair", "good", "excellent")
