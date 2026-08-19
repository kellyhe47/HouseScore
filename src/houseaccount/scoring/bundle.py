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

import json
import math
import re
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from houseaccount.normalize import parse_deed_date
from houseaccount.scoring.v2 import V2Bundle

__all__ = ["build_bundle", "build_bundles", "sdl_by_pin"]

#: Single-family residential MOD-IV property class.
_SINGLE_FAMILY = "2"

#: SDL work types that amend or supplement an existing municipal record
#: rather than open a new one (R8: they collapse into the base project).
_AMENDMENT_TYPES = frozenset({"amendment", "supplement", "supplemental"})

_MONEY = re.compile(r"[-+]?\d[\d,]*\.?\d*")
_US_DATE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")


# ---------------------------------------------------------------------------
# small parsers
# ---------------------------------------------------------------------------


def _parse_money(raw: Any) -> float | None:
    """"$600000" / "30910" -> float; "$" / "" / None -> None."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    match = _MONEY.search(str(raw))
    if not match:
        return None
    try:
        return float(match.group().replace(",", ""))
    except ValueError:
        return None


def _parse_us_date(raw: Any) -> date | None:
    """"03/01/2026" -> date(2026, 3, 1); anything unparseable -> None."""
    if not raw:
        return None
    match = _US_DATE.match(str(raw).strip())
    if not match:
        return None
    mm, dd, yyyy = int(match.group(1)), int(match.group(2)), int(match.group(3))
    try:
        return date(yyyy, mm, dd)
    except ValueError:
        return None


def _distance_m(a: Sequence[float], b: Sequence[float]) -> float:
    """Equirectangular metres between two (lon, lat) points — monotone in
    true distance at territory scale, which is all ranking needs."""
    lon1, lat1 = float(a[0]), float(a[1])
    lon2, lat2 = float(b[0]), float(b[1])
    mean_lat = math.radians((lat1 + lat2) / 2.0)
    dx = math.radians(lon2 - lon1) * math.cos(mean_lat)
    dy = math.radians(lat2 - lat1)
    return math.hypot(dx, dy) * 6_371_000.0


def _is_valid_single_family(p: Mapping[str, Any]) -> bool:
    value = p.get("net_value")
    return (
        str(p.get("prop_class") or "").strip() == _SINGLE_FAMILY
        and isinstance(value, (int, float))
        and float(value) > 0
        and p.get("centroid") is not None
    )


# ---------------------------------------------------------------------------
# per-fact assembly (R22 rows)
# ---------------------------------------------------------------------------


def _assessed_value(ctx: Mapping[str, Any]) -> float | None:
    """R22 row: MOD-IV primary; SDL fills only on exact+current match."""
    parcel = ctx.get("parcel") or {}
    modiv = parcel.get("net_value")
    if isinstance(modiv, (int, float)) and float(modiv) > 0:
        return float(modiv)
    sdl = ctx.get("sdl")
    if sdl and ctx.get("sdl_match_exact_current"):
        return _parse_money((sdl.get("assessed_valuation") or {}).get("total"))
    return None


def _sales(ctx: Mapping[str, Any], as_of: date) -> tuple[dict[str, Any], ...]:
    """R22 row: SR1A primary > MOD-IV transfer fallback > SDL displayed sale.

    A lower-precedence source only fills the row when every higher-precedence
    source has nothing valid — it never overwrites, however fresh it looks.
    """
    parcel = ctx.get("parcel") or {}

    sr1a = []
    for sale in ctx.get("sr1a_sales") or ():
        raw = sale.get("date") or sale.get("deed_date")
        parsed = parse_deed_date(str(raw), as_of) if raw is not None else None
        if parsed is None:
            continue
        sr1a.append(
            {
                "source": "SR1A",
                "date": parsed.isoformat(),
                "price": sale.get("price"),
                "sales_code": sale.get("sales_code", ""),
            }
        )
    if sr1a:
        return tuple(sr1a)

    deed = parse_deed_date(parcel.get("deed_date"), as_of)
    price = parcel.get("sale_price")
    if deed is not None and isinstance(price, (int, float)) and float(price) > 0:
        return (
            {
                "source": "MODIV",
                "date": deed.isoformat(),
                "price": float(price),
                "sales_code": parcel.get("sales_code", ""),
            },
        )

    sdl = ctx.get("sdl")
    if sdl and ctx.get("sdl_match_exact_current"):
        details = sdl.get("property_details") or {}
        sdl_date = _parse_us_date(details.get("last_sale_date"))
        sdl_price = _parse_money(details.get("last_sale_price"))
        # Arm's-length eligibility must be establishable: a displayed sale
        # with no real price ("$") never enters the bundle.
        if sdl_date is not None and sdl_price is not None and sdl_price > 0:
            return (
                {
                    "source": "SDL",
                    "date": sdl_date.isoformat(),
                    "price": sdl_price,
                    "sales_code": "",
                },
            )
    return ()


def _sdl_projects(sdl: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    """SDL permit applications grouped by municipal record identity (R8).

    Amendments/supplements collapse into the base record; the richest
    work description (the base record's) is retained.
    """
    projects: dict[str, dict[str, Any]] = {}
    if not sdl:
        return projects
    construction = sdl.get("construction") or {}
    for app in construction.get("permit_applications") or ():
        municipal_id = str(app.get("permit_number") or app.get("control_number") or "")
        if not municipal_id:
            continue
        is_amendment = str(app.get("work_type") or "").strip().lower() in _AMENDMENT_TYPES
        # SDL displays US-format dates ("4/20/2023"); the engine's date reader
        # is ISO-only, so an unconverted issue date silently disqualifies the
        # permit from ever earning project points.
        issue = _parse_us_date(app.get("issue_date"))
        close = _parse_us_date(app.get("close_date"))
        record = {
            "municipal_id": municipal_id,
            "sources": ("SDL",),
            "description": app.get("work_description"),
            "work_type": app.get("work_type"),
            "status": app.get("status"),
            "issue_date": issue.isoformat() if issue else None,
            "close_date": close.isoformat() if close else None,
            "certificates": app.get("certificates"),
            "subcodes": app.get("subcodes"),
            "total_cost": _parse_money(app.get("total_cost")),
            "control_number": app.get("control_number"),
        }
        existing = projects.get(municipal_id)
        if existing is None:
            record["amendments"] = 1 if is_amendment else 0
            projects[municipal_id] = record
        elif is_amendment:
            existing["amendments"] = existing.get("amendments", 0) + 1
        else:
            # A later base record replaces an amendment that arrived first.
            record["amendments"] = existing.get("amendments", 0)
            projects[municipal_id] = record
    return projects


def _permits(ctx: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    """R22 row: SDL primary for lifecycle; statewide fills SDL-absent records
    and coalesces (R8) when both carry the same municipal record."""
    projects = _sdl_projects(ctx.get("sdl"))
    for permit in ctx.get("statewide_permits") or ():
        municipal_id = str(permit.get("municipal_id") or permit.get("id") or "")
        if not municipal_id:
            continue
        existing = projects.get(municipal_id)
        statewide_fields = {
            "disposition": permit.get("disposition"),
            "statewide_issue_date": permit.get("issue_date"),
            "completion_date": permit.get("completion_date"),
            "last_activity_date": permit.get("last_activity_date"),
        }
        if existing is None:
            record = {
                "municipal_id": municipal_id,
                "sources": ("statewide",),
                "description": permit.get("description"),
            }
            record.update(statewide_fields)
            projects[municipal_id] = record
        else:
            # Coalesce: statewide adds lifecycle fields it alone carries but
            # never overwrites the richer SDL detail.
            existing["sources"] = tuple(existing["sources"]) + ("statewide",)
            for key, value in statewide_fields.items():
                if existing.get(key) is None:
                    existing[key] = value
    return tuple(projects.values())


def _comparables(
    ctx: Mapping[str, Any],
) -> tuple[tuple[dict[str, Any], ...], tuple[float, ...]]:
    """R13/R14: nearest-<=20 valid single-family by centroid distance with a
    deterministic tie-break, subject excluded; plus the territory
    assessed-value list for the midrank percentile."""
    parcel = ctx.get("parcel") or {}
    subject_pin = parcel.get("pams_pin")
    subject_centroid = parcel.get("centroid")

    valid = [p for p in ctx.get("territory_parcels") or () if _is_valid_single_family(p)]
    values = tuple(float(p["net_value"]) for p in valid)

    candidates = [p for p in valid if p.get("pams_pin") != subject_pin]
    if subject_centroid is None:
        return (), values

    ranked = sorted(
        candidates,
        key=lambda p: (
            _distance_m(p["centroid"], subject_centroid),
            str(p.get("pams_pin")),
        ),
    )
    comps = tuple(
        {
            "pams_pin": p.get("pams_pin"),
            "pin": p.get("pams_pin"),
            "assessed_value": float(p["net_value"]),
            "distance_m": _distance_m(p["centroid"], subject_centroid),
        }
        for p in ranked[:20]
    )
    return comps, values


def build_bundle(parcel_ctx: Mapping[str, Any], as_of: date) -> V2Bundle:
    """Assemble one normalized V2 evidence bundle for one door."""
    ctx = parcel_ctx
    parcel = ctx.get("parcel") or {}
    comps, territory_values = _comparables(ctx)

    # Whitelisted output parcel — identity values never cross this boundary
    # (R25): only the facts the engine consumes are copied through.
    out_parcel: dict[str, Any] = {
        "pams_pin": parcel.get("pams_pin"),
        "pin": parcel.get("pams_pin"),
        "prop_class": parcel.get("prop_class"),
        "assessed_value": _assessed_value(ctx),
        "construction_year": parcel.get("yr_constr"),
        "lot_acres": parcel.get("calc_acre"),
        "sdl_page_available": bool(ctx.get("sdl_available", True)),
    }

    return V2Bundle(
        parcel=out_parcel,
        sales=_sales(ctx, as_of),
        permits=_permits(ctx),
        local_comparables=comps,
        territory_assessed_values=territory_values,
        acs_block_group=dict(ctx.get("acs_block_group") or {}),
        rental_registry=dict(ctx.get("rental_registry") or {}),
        imagery=dict(ctx.get("imagery") or {}),
    )


# ---------------------------------------------------------------------------
# live assembly from cached snapshots (zero network)
# ---------------------------------------------------------------------------


def _load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _territory_parcels(data_dir: Path) -> list[dict[str, Any]]:
    """data/territory.geojson features -> the parcel_ctx parcel shape."""
    from houseaccount.sources.parcels import _centroid

    collection = _load_json(data_dir / "territory.geojson")
    parcels: list[dict[str, Any]] = []
    for feature in collection.get("features") or ():
        props = feature.get("properties") or {}
        raw_value = props.get("NET_VALUE")
        try:
            net_value = float(raw_value) if raw_value is not None else None
        except (TypeError, ValueError):
            net_value = None
        try:
            yr_constr = int(props.get("YR_CONSTR")) if props.get("YR_CONSTR") else None
        except (TypeError, ValueError):
            yr_constr = None
        parcels.append(
            {
                "pams_pin": str(props.get("PAMS_PIN") or ""),
                "prop_class": str(props.get("PROP_CLASS") or "").strip(),
                "net_value": net_value,
                "yr_constr": yr_constr,
                "calc_acre": props.get("CALC_ACRE"),
                "deed_date": props.get("DEED_DATE"),
                "sale_price": props.get("SALE_PRICE"),
                "sales_code": props.get("SALES_CODE"),
                "centroid": _centroid(feature.get("geometry")),
            }
        )
    return parcels


def sdl_by_pin(data_dir: Path) -> dict[str, Mapping[str, Any]]:
    path = data_dir / "sdl_property_history_territory.json"
    if not path.is_file():
        # No snapshot collected (e.g. a fresh checkout or a test tmpdir): SDL
        # is unknown for every door, which callers treat as "keys stay out of
        # the ctx" rather than "every page is unavailable".
        return {}
    snapshot = _load_json(path)
    records: Iterable[Mapping[str, Any]] = snapshot.get("properties") or ()
    return {str(r.get("pams_pin")): r for r in records}


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
    root = Path(data_dir)
    parcels = _territory_parcels(root)
    territory = tuple(parcels)
    by_pin = sdl_by_pin(root)

    bundles: dict[str, V2Bundle] = {}
    for parcel in parcels:
        pin = parcel["pams_pin"]
        sdl = by_pin.get(pin)
        collected = bool(sdl) and sdl.get("collection_status") == "collected"
        ctx = {
            "parcel": parcel,
            "sr1a_sales": (),
            "sdl": sdl if collected else None,
            # R23: an uncollected/unavailable SDL page is a data gap, never
            # proof of absence — flagged so the engine emits the gap.
            "sdl_available": collected,
            "sdl_match_exact_current": collected,
            "statewide_permits": (),
            "territory_parcels": territory,
            "acs_block_group": {},
            "rental_registry": {},
            "imagery": {},
        }
        bundles[pin] = build_bundle(ctx, as_of)
    return bundles
