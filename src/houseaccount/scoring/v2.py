"""Door Score V2 engine (ticket 101) — STUB.

Contract: docs/plans/2026-08-18-2221-feat-door-score-v2-plan.md (R1-R26, R37)
and eval/v2/provenance.md (binding envelope conventions).

Pure scoring: no I/O, no network. Input is a normalized evidence bundle
mirroring the golden fixtures' ``given.inputs`` shape plus ``as_of``; output
is the fixture ``result`` envelope dict.

Everything except bundle construction raises NotImplementedError until the
implementation ticket lands. V1 (engine.py/weights.py) is untouched.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
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


def mover_strength(days_since_move: int) -> float:
    """M(d) per R4: 90 for d 0..90; normalized exponential 91..364; 0 at >=365.

    No intermediate rounding.
    """
    raise NotImplementedError("ticket 101: V2 engine not implemented")


def blend(base: float, strength: float) -> float:
    """P = B + (M/90) * ((90 + B/8) - B) per R5."""
    raise NotImplementedError("ticket 101: V2 engine not implemented")


def round_half_up(value: float) -> int:
    """round_half_up per R7: 0.5 fractions round up (away from lower int)."""
    raise NotImplementedError("ticket 101: V2 engine not implemented")


def score_door_v2(bundle: V2Bundle, as_of: date) -> Mapping[str, Any]:
    """Score one door as of ``as_of``; returns the V2 result envelope dict.

    Envelope keys: score_contract_version, confidence, categories, base,
    mover {eligible, days_since_move, strength}, mover_lift, rental_modifier,
    pre_rounding, adjustment, score, evidence, data_gaps.
    """
    raise NotImplementedError("ticket 101: V2 engine not implemented")
