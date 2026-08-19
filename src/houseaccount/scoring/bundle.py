"""Live evidence-bundle builder (ticket 102): real cached sources -> V2Bundle.

Contract: docs/plans/2026-08-18-2221-feat-door-score-v2-plan.md R8, R13, R14,
R22 (precedence table), R23, R24, R25. Additive to V1 — nothing here is called
by pipeline.py until ticket 103.

Two entry points:

``build_bundle(parcel_ctx, as_of)``
    Assemble one :class:`~houseaccount.scoring.v2.V2Bundle` from a per-door
    context mapping. ``parcel_ctx`` keys (all optional except ``parcel``):

    - ``parcel``: MOD-IV / territory parcel mapping — ``pams_pin``,
      ``prop_class``, ``net_value`` (assessed value), ``yr_constr``,
      ``calc_acre``, ``deed_date`` (raw MOD-IV YYMMDD or ISO), ``sale_price``,
      ``sales_code``, ``centroid`` (lon, lat).
    - ``sr1a_sales``: sequence of SR1A sale mappings
      (``date``/``deed_date``, ``price``, ``sales_code``).
    - ``sdl``: the SDL property-history record for this parcel (the shape of
      one ``properties[]`` entry in data/sdl_property_history_territory.json),
      or None.
    - ``sdl_available``: bool — False for the eight unavailable SDL pages
      (R23: absence of the page is never proof of absence of events).
    - ``sdl_match_exact_current``: bool — SDL assessed valuation may fill a
      missing MOD-IV assessed value only when this is True (R22 row 1).
    - ``statewide_permits``: sequence of statewide construction-permit
      mappings (``municipal_id``/``id``, ``description``, ``disposition``,
      ``issue_date``, ``completion_date``, ``last_activity_date``).
    - ``territory_parcels``: sequence of parcel mappings for every territory
      door (used for local comparables and the territory assessed-value list;
      the subject is excluded from its own comparables).
    - ``acs_block_group``, ``rental_registry``, ``imagery``: passed through.

    Output invariants the tests pin:

    - R22 precedence per fact row; lower-precedence sources fill gaps only and
      never overwrite a fresher valid higher-precedence fact.
    - Every entry in ``bundle.sales`` carries a ``source`` key
      (``"SR1A"`` / ``"MODIV"`` / ``"SDL"``).
    - Every entry in ``bundle.permits`` carries ``sources`` (tuple of
      ``"SDL"`` / ``"statewide"``) and a stable ``municipal_id``; duplicates
      coalesce per R8 with SDL detail retained.
    - Deed dates are normalized through ``normalize.parse_deed_date`` (YYMMDD
      century pivot derived from ``as_of``).
    - No owner-name, street-address, or city-state identity values (or fields
      derived from them) anywhere in the bundle (R25; the token names are
      pinned by tests/test_redaction.py's scan).

``build_bundles(as_of, *, data_dir=..., cache_dir=...)``
    Assemble the context for every territory parcel from the cached snapshots
    on disk (zero network) and return ``{pams_pin: V2Bundle}`` — exactly one
    bundle per territory door.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Mapping

from houseaccount.scoring.v2 import V2Bundle

__all__ = ["build_bundle", "build_bundles"]


def build_bundle(parcel_ctx: Mapping[str, Any], as_of: date) -> V2Bundle:
    """Assemble one normalized V2 evidence bundle for one door."""
    raise NotImplementedError("ticket 102: implemented by the implementation agent")


def build_bundles(
    as_of: date,
    *,
    data_dir: str | Path = "data",
    cache_dir: str | Path | None = None,
) -> dict[str, V2Bundle]:
    """Assemble bundles for all territory parcels from cached snapshots.

    Returns a mapping of ``pams_pin -> V2Bundle``, one entry per territory
    door. Reads only files already on disk; never touches the network.
    """
    raise NotImplementedError("ticket 102: implemented by the implementation agent")
