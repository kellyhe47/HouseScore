"""Entity resolution: every signal joined onto a territory door (T006, R3).

`PAMS_PIN` is the canonical key. Parcels, permits, block-group statistics and
the rental register arrive from four agencies that agree on nothing else, so
this module is where they are made to meet — and where the honest accounting of
what failed to meet lives.

**Why block/lot is the primary join.** The Socrata permit dataset carries
`block`/`lot` and no situs address at all (Phase 0, and pinned closed by
`tests/test_permits.py`). So `normalize.parcel_key(mun, block, lot)` is the
primary join and the normalized situs address is a *fallback*, usable only by a
permit-like record that does carry an address — a municipal list obtained by
OPRA would, a `PermitRecord` never will. The fallback is therefore duck-typed on
an `address` attribute rather than on a type, and a record without one simply
skips it. An address shared by two doors matches neither: picking one would be a
fabrication dressed as a join.

**What the match rate divides by.** R3.2 grades the match rate on records whose
address falls in the territory. Taking that denominator as exact block+lot
containment would make the number identically 1.0 — a permit whose block+lot is
a territory parcel matches by construction — and an ungradeable metric is worse
than none. The denominator here is the honest one: permits inside the rolling
window whose *block* is a block the territory occupies. Those are the records
that plausibly belong to us, and the graded question is how many of them landed
on a door.

**Nothing is silently dropped.** An in-territory permit that joins nowhere is
named in `report.unmatched` with the reason; permits on other blocks and permits
outside the window are counted rather than discarded, so
`permits_in_territory + permits_out_of_territory + permits_out_of_window`
always reconstructs `permits_total`.

Every number reached through `block_group` describes a *block group*, never a
household (R6.2): it is a neighbourhood prior attached to a door by geography,
and says nothing about whoever lives behind it.

No I/O and no network — the sources have already been read by the time anything
here runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable, Mapping, Sequence

from houseaccount.normalize import normalize_address, parcel_key, parse_deed_date, situs_display
from houseaccount.scoring.engine import (
    SOURCE_ACS,
    SOURCE_MODIV,
    SOURCE_PERMITS,
    SOURCE_RENTAL,
    ScoreInput,
)
from houseaccount.sources.acs import AcsResult, BlockGroupStats
from houseaccount.sources.parcels import DEFAULT_MUN, Parcel
from houseaccount.sources.permits import PERMIT_WINDOW_DAYS, permits_within
from houseaccount.sources.rental import RentalRegistrationProvider

#: Provenance keys — one per signal a door can carry.
SIGNAL_PARCEL = "parcel"
SIGNAL_PERMITS = "permits"
SIGNAL_ACS = "acs"
SIGNAL_RENTAL = "rental"

#: Why an in-territory permit reached no door. Each is a distinct failure a
#: human can act on: fix the parcel extract, obtain an addressed permit list, or
#: disambiguate two doors that normalize to one address.
REASON_NO_PARCEL = (
    "block/lot matched no territory parcel, and the record carries no address to fall back to"
)
REASON_ADDRESS_UNKNOWN = "block/lot matched no territory parcel, and its address matched no door"
REASON_ADDRESS_AMBIGUOUS = (
    "block/lot matched no territory parcel, and its address matches more than one door"
)


@dataclass(frozen=True)
class Provenance:
    """Where one signal on one door came from, and when it was retrieved."""

    source: str
    retrieved: date


@dataclass(frozen=True)
class UnmatchedPermit:
    """An in-territory permit that joined to no door, and why."""

    record_id: str
    block: str
    lot: str
    parcel_key: str
    reason: str


@dataclass(frozen=True)
class DoorFacts:
    """Everything known about one door, ready to become a `ScoreInput`.

    The join keys and the parsed deed date are computed once, here. Anything
    that re-derived them downstream could disagree with what the report counted.
    """

    pams_pin: str
    prop_class: str
    prop_loc: str
    zip5: str
    pclblock: str
    pcllot: str
    parcel_key: str
    address_key: str
    situs: str
    centroid: tuple[float, float] | None
    geometry: Mapping[str, Any] | None
    deed_date: date | None
    sale_price: float
    sales_code: str
    yr_constr: int
    net_value: float
    calc_acre: float
    permits_2yr: tuple[Any, ...] = ()
    block_group_geoid: str | None = None
    block_group: BlockGroupStats | None = None
    rental_registration_match: bool = False
    provenance: Mapping[str, Provenance] = field(default_factory=dict)

    def to_score_input(
        self,
        *,
        as_of: date,
        territory_median_value: float,
        acs_dual_income_threshold: float,
        vision: Mapping[str, Any] | None = None,
    ) -> ScoreInput:
        """Hand this door to the score engine, deriving nothing new.

        The ACS numbers are the block group's, passed through as the
        neighbourhood prior R6.2 permits them to be and nothing more.
        """
        return ScoreInput(
            as_of=as_of,
            territory_median_value=territory_median_value,
            acs_dual_income_threshold=acs_dual_income_threshold,
            pams_pin=self.pams_pin,
            deed_date=self.deed_date,
            sale_price=self.sale_price,
            sales_code=self.sales_code,
            yr_constr=self.yr_constr,
            net_value=self.net_value,
            calc_acre=self.calc_acre,
            permits=tuple(record.to_score_permit() for record in self.permits_2yr),
            dual_income_pct=self.block_group.dual_income_pct if self.block_group else None,
            median_hh_income=self.block_group.median_hh_income if self.block_group else None,
            rental_registration_match=self.rental_registration_match,
            vision=dict(vision or {}),
        )


@dataclass(frozen=True)
class ResolveReport:
    """The match-rate and coverage numbers the rubric grades (R3.2)."""

    as_of: date
    doors_total: int = 0
    doors_with_signal: int = 0
    coverage: float = 0.0
    permits_total: int = 0
    permits_out_of_window: int = 0
    permits_out_of_territory: int = 0
    permits_in_territory: int = 0
    permits_matched: int = 0
    matched_by_block_lot: int = 0
    matched_by_address: int = 0
    permit_match_rate: float = 0.0
    block_lot_match_rate: float = 0.0
    address_match_rate: float = 0.0
    unmatched: tuple[UnmatchedPermit, ...] = ()
    doors_with_block_group: int = 0
    acs_available: bool = False
    acs_reason: str | None = None
    rental_declination_reason: str | None = None


@dataclass(frozen=True)
class ResolveResult:
    """Resolved doors plus the report explaining what joined and what did not."""

    doors: Mapping[str, DoorFacts]
    report: ResolveReport


def resolve(
    parcels: Sequence[Parcel],
    permits: Iterable[Any],
    acs: AcsResult,
    rental_provider: RentalRegistrationProvider,
    as_of: date,
    *,
    block_group_index: Any | None = None,
    mun: str = DEFAULT_MUN,
    permit_window_days: int = PERMIT_WINDOW_DAYS,
    retrieved_at: Mapping[str, date] | None = None,
) -> ResolveResult:
    """Join every signal onto the territory parcels and report what happened.

    `doors` comes back in the order `parcels` were given — territory order is
    nearest-first and meaningful, so resolution must not reshuffle it.
    """
    parcels = list(parcels)
    retrieved = dict(retrieved_at or {})

    keys = [parcel_key(mun, parcel.pclblock, parcel.pcllot) for parcel in parcels]
    addresses = [normalize_address(parcel.prop_loc) for parcel in parcels]

    landed, permit_counts, unmatched = _route_permits(
        permits,
        as_of=as_of,
        mun=mun,
        window_days=permit_window_days,
        pin_by_parcel_key=_first_wins(keys, parcels),
        pins_by_address=_all_pins(addresses, parcels),
        territory_blocks={parcel_key(mun, parcel.pclblock, "") for parcel in parcels},
    )

    doors: dict[str, DoorFacts] = {}
    for parcel, key, address in zip(parcels, keys, addresses):
        geoid = (
            block_group_index.geoid_for(parcel.centroid) if block_group_index is not None else None
        )
        stats = acs.block_groups.get(geoid) if acs.available and geoid else None
        door_permits = tuple(
            sorted(landed.get(parcel.pams_pin, ()), key=_newest_first, reverse=True)
        )
        is_rental = bool(rental_provider.is_registered_rental(parcel.pams_pin))

        doors[parcel.pams_pin] = DoorFacts(
            pams_pin=parcel.pams_pin,
            prop_class=parcel.prop_class,
            prop_loc=parcel.prop_loc,
            zip5=parcel.zip5,
            pclblock=parcel.pclblock,
            pcllot=parcel.pcllot,
            parcel_key=key,
            address_key=address,
            situs=situs_display(parcel.prop_loc, parcel.zip5),
            centroid=parcel.centroid,
            geometry=parcel.geometry,
            deed_date=parse_deed_date(parcel.deed_date, as_of),
            sale_price=parcel.sale_price,
            sales_code=parcel.sales_code,
            yr_constr=parcel.yr_constr,
            net_value=parcel.net_value,
            calc_acre=parcel.calc_acre,
            permits_2yr=door_permits,
            block_group_geoid=geoid,
            block_group=stats,
            rental_registration_match=is_rental,
            provenance=_provenance(
                as_of=as_of,
                retrieved=retrieved,
                has_permits=bool(door_permits),
                has_block_group=stats is not None,
                is_rental=is_rental,
            ),
        )

    return ResolveResult(
        doors=doors,
        report=_report(
            as_of=as_of,
            doors=doors,
            permit_counts=permit_counts,
            unmatched=unmatched,
            acs=acs,
            rental_provider=rental_provider,
        ),
    )


# --- the permit join ---------------------------------------------------------


def _route_permits(
    permits: Iterable[Any],
    *,
    as_of: date,
    mun: str,
    window_days: int,
    pin_by_parcel_key: Mapping[str, str],
    pins_by_address: Mapping[str, list[str]],
    territory_blocks: frozenset[str] | set[str],
) -> tuple[dict[str, list[Any]], dict[str, int], list[UnmatchedPermit]]:
    """Send each permit to a door, to a counter, or to the unmatched list.

    Every record leaves this function through exactly one of those three exits,
    which is what the accounting invariant in `ResolveReport` is asserting.
    """
    records = list(permits)
    in_window = permits_within(records, as_of, window_days)

    landed: dict[str, list[Any]] = {}
    unmatched: list[UnmatchedPermit] = []
    counts = {
        "permits_total": len(records),
        # An undated permit falls out of the window rather than being guessed at.
        "permits_out_of_window": len(records) - len(in_window),
        "permits_out_of_territory": 0,
        "matched_by_block_lot": 0,
        "matched_by_address": 0,
    }

    for record in in_window:
        if parcel_key(mun, record.block, "") not in territory_blocks:
            counts["permits_out_of_territory"] += 1
            continue

        key = parcel_key(mun, record.block, record.lot)
        pin = pin_by_parcel_key.get(key)
        if pin is not None:
            counts["matched_by_block_lot"] += 1
            landed.setdefault(pin, []).append(record)
            continue

        pin, reason = _by_address(record, pins_by_address)
        if pin is not None:
            counts["matched_by_address"] += 1
            landed.setdefault(pin, []).append(record)
            continue

        unmatched.append(
            UnmatchedPermit(
                record_id=str(record.record_id),
                block=str(record.block),
                lot=str(record.lot),
                parcel_key=key,
                reason=reason,
            )
        )

    # Sorted by record_id so the order of the input feed cannot move the report.
    unmatched.sort(key=lambda item: item.record_id)
    return landed, counts, unmatched


def _by_address(record: Any, pins_by_address: Mapping[str, list[str]]) -> tuple[str | None, str]:
    """The address fallback: a door's PIN, or None and the reason it failed.

    Duck-typed on `address` because `PermitRecord`'s field set is closed and
    carries none — the fallback exists for a municipal list this project may
    obtain later, and must be a no-op for the source that ships today.
    """
    raw = getattr(record, "address", None)
    if not isinstance(raw, str):
        return None, REASON_NO_PARCEL

    key = normalize_address(raw)
    if not key:
        return None, REASON_NO_PARCEL

    pins = pins_by_address.get(key) or []
    if len(pins) == 1:
        return pins[0], ""
    return None, REASON_ADDRESS_AMBIGUOUS if pins else REASON_ADDRESS_UNKNOWN


def _newest_first(record: Any) -> tuple[date, str]:
    """Sort key for a door's permits: by date, `record_id` breaking ties.

    Applied with `reverse=True`, so two permits filed the same day still land in
    one fixed order rather than in whatever order the feed happened to page in.
    """
    return (record.date, str(record.record_id))


# --- assembling the door -----------------------------------------------------


def _first_wins(keys: Sequence[str], parcels: Sequence[Parcel]) -> dict[str, str]:
    """parcel_key -> PAMS_PIN. A duplicated key keeps the first parcel seen, so
    territory order decides rather than dict-insertion luck."""
    lookup: dict[str, str] = {}
    for key, parcel in zip(keys, parcels):
        if key:
            lookup.setdefault(key, parcel.pams_pin)
    return lookup


def _all_pins(addresses: Sequence[str], parcels: Sequence[Parcel]) -> dict[str, list[str]]:
    """address_key -> every PAMS_PIN normalizing to it. Every one of them, so an
    ambiguous address is visibly ambiguous instead of quietly resolving."""
    lookup: dict[str, list[str]] = {}
    for address, parcel in zip(addresses, parcels):
        if address:
            lookup.setdefault(address, []).append(parcel.pams_pin)
    return lookup


def _provenance(
    *,
    as_of: date,
    retrieved: Mapping[str, date],
    has_permits: bool,
    has_block_group: bool,
    is_rental: bool,
) -> dict[str, Provenance]:
    """One entry per signal that actually contributed a fact to this door.

    A signal that contributed nothing gets no entry: a provenance line asserts
    that some fact came from somewhere, and there is no such fact to attribute.
    A rental answer of False is not a fact about this door either — it is the
    absence of the door from a list — so it earns no attribution.
    """
    sources = [(SIGNAL_PARCEL, SOURCE_MODIV)]
    if has_permits:
        sources.append((SIGNAL_PERMITS, SOURCE_PERMITS))
    if has_block_group:
        sources.append((SIGNAL_ACS, SOURCE_ACS))
    if is_rental:
        sources.append((SIGNAL_RENTAL, SOURCE_RENTAL))
    return {
        signal: Provenance(source=source, retrieved=retrieved.get(signal, as_of))
        for signal, source in sources
    }


def _report(
    *,
    as_of: date,
    doors: Mapping[str, DoorFacts],
    permit_counts: Mapping[str, int],
    unmatched: Sequence[UnmatchedPermit],
    acs: AcsResult,
    rental_provider: RentalRegistrationProvider,
) -> ResolveReport:
    """The graded numbers, all of them derived from the resolved doors."""
    matched_by_block_lot = permit_counts["matched_by_block_lot"]
    matched_by_address = permit_counts["matched_by_address"]
    matched = matched_by_block_lot + matched_by_address
    in_territory = matched + len(unmatched)

    with_signal = sum(
        1
        for facts in doors.values()
        if facts.permits_2yr or facts.block_group is not None or facts.rental_registration_match
    )

    return ResolveReport(
        as_of=as_of,
        doors_total=len(doors),
        doors_with_signal=with_signal,
        coverage=_rate(with_signal, len(doors)),
        permits_total=permit_counts["permits_total"],
        permits_out_of_window=permit_counts["permits_out_of_window"],
        permits_out_of_territory=permit_counts["permits_out_of_territory"],
        permits_in_territory=in_territory,
        permits_matched=matched,
        matched_by_block_lot=matched_by_block_lot,
        matched_by_address=matched_by_address,
        permit_match_rate=_rate(matched, in_territory),
        block_lot_match_rate=_rate(matched_by_block_lot, in_territory),
        address_match_rate=_rate(matched_by_address, in_territory),
        unmatched=tuple(unmatched),
        doors_with_block_group=sum(1 for f in doors.values() if f.block_group is not None),
        acs_available=acs.available,
        acs_reason=acs.reason,
        rental_declination_reason=rental_provider.declination_reason(),
    )


def _rate(numerator: int, denominator: int) -> float:
    """A ratio that reports 0.0 rather than dividing by an empty territory."""
    return numerator / denominator if denominator else 0.0
