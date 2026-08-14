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

**Two denominators, and which one R3.2 grades.** The report carries both,
because the question "did the join work" and the question "did this door get its
permits" are not the same question:

* `permit_match_rate` — permits landed on a *door* over permits inside the
  rolling window whose **block** the territory occupies. Territory-scoped. A
  block holds many parcels the territory does not, so permits on those parcels
  sit in this denominator and can never match: the first live run read 0.62 on a
  join that was in fact fine.
* `municipal_match_rate` — in-window permits joining **any** municipal parcel by
  block/lot, over **all** in-window permits. Municipality-wide, and the rate
  R3.2's >=95% floor is graded against; the live run reads 1696/1741 = 0.9742.

Neither denominator may be made trivially 1.0. Exact block+lot containment as a
denominator would match by construction — a permit whose block+lot is a
territory parcel matches definitionally — so the territory rate's denominator is
block-level and the municipal rate's is every in-window permit.

The municipal join is block/lot only: it answers "did this permit resolve to a
*parcel*", where the address fallback exists to place a record on a *door* and
is reported by `matched_by_address`. The residual misses stay visible and stay
apart: a permit carrying a placeholder block or lot (`0000`/`00`) is counted in
`permits_placeholder_block_lot` — inside the denominator, never in the numerator
— and a permit naming a lot no parcel has is counted in
`permits_unmatched_municipal`. Lot-suffix variants (`4.2` vs `4.02`) are
*distinct* lots under NJ MOD-IV convention and are kept distinct here;
collapsing them would invent matches to flatter the rate.

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

from houseaccount.normalize import (
    normalize_address,
    parcel_key,
    parse_deed_date,
    sale_key,
    situs_display,
)
from houseaccount.scoring.engine import (
    SOURCE_ACS,
    SOURCE_MODIV,
    SOURCE_PERMITS,
    SOURCE_RENTAL,
    SOURCE_SR1A,
    ScoreInput,
)
from houseaccount.sources.acs import AcsResult, BlockGroupStats
from houseaccount.sources.parcels import DEFAULT_MUN, Parcel
from houseaccount.sources.permits import PERMIT_WINDOW_DAYS, permits_within
from houseaccount.sources.rental import RentalRegistrationProvider
from houseaccount.sources.sales import Sale, latest_by_parcel

#: Provenance keys — one per signal a door can carry.
SIGNAL_PARCEL = "parcel"
SIGNAL_PERMITS = "permits"
SIGNAL_ACS = "acs"
SIGNAL_RENTAL = "rental"
SIGNAL_SALES = "sales"

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

#: A block or lot component that carries no parcel identity. MOD-IV and the
#: permit feed both write "not recorded" as zeros, and `parcel_key` reduces
#: every spelling of that ("0000", "00", "0", "") to one of these two.
PLACEHOLDER_COMPONENTS = frozenset({"", "0"})

#: Which municipal bucket an in-window permit fell into. One permit, one bucket.
MUNICIPAL_MATCHED = "permits_matched_municipal"
MUNICIPAL_PLACEHOLDER = "permits_placeholder_block_lot"
MUNICIPAL_UNMATCHED = "permits_unmatched_municipal"


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
    #: Which register `deed_date` came from — MOD-IV, or the SR1A sales file when
    #: it held a newer sale for this parcel.
    deed_source: str = SOURCE_MODIV
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
            deed_source=self.deed_source,
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
    """The match-rate and coverage numbers the rubric grades (R3.2).

    Two match rates, on two different denominators, and they are different
    numbers on purpose:

    * `permit_match_rate` — permits landed on a door / in-window permits whose
      *block* the territory occupies. Territory-scoped, and depressed by the
      many parcels on those blocks that the territory does not hold.
    * `municipal_match_rate` — in-window permits joining any municipal parcel by
      block/lot / *all* in-window permits. R3.2's >=95% floor grades this one.

    The municipal buckets partition the window exactly:
    `permits_matched_municipal + permits_placeholder_block_lot +
    permits_unmatched_municipal == permits_in_window`. Placeholder block/lot
    values stay in that denominator and out of the numerator, because a permit
    the county filed as `0000`/`00` is a real miss, not an absent record.
    """

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
    permits_in_window: int = 0
    permits_matched_municipal: int = 0
    permits_placeholder_block_lot: int = 0
    permits_unmatched_municipal: int = 0
    municipal_match_rate: float = 0.0
    unmatched: tuple[UnmatchedPermit, ...] = ()
    doors_with_block_group: int = 0
    acs_available: bool = False
    acs_reason: str | None = None
    rental_declination_reason: str | None = None
    #: SR1A sale records read for the municipality, the parcels they collapse to
    #: (a condominium contributes many sales and many parcels), and the doors
    #: whose deed the register actually superseded.
    sales_total: int = 0
    sales_parcels: int = 0
    sales_applied: int = 0


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
    sales: Sequence[Sale] = (),
    block_group_index: Any | None = None,
    municipal_parcels: Sequence[Parcel] | None = None,
    mun: str = DEFAULT_MUN,
    permit_window_days: int = PERMIT_WINDOW_DAYS,
    retrieved_at: Mapping[str, date] | None = None,
) -> ResolveResult:
    """Join every signal onto the territory parcels and report what happened.

    `doors` comes back in the order `parcels` were given — territory order is
    nearest-first and meaningful, so resolution must not reshuffle it.

    `parcels` is the territory: the doors this run publishes, and the only
    parcels a permit can land on. `municipal_parcels` is every parcel the
    municipality has, and is used for nothing but the R3.2 denominator — it
    changes no door and no territory-scoped number. Omitting it measures the
    join against the territory alone, which is a smaller universe and a smaller
    rate; the harvest already holds the full municipal extract, so the pipeline
    passes it.
    """
    parcels = list(parcels)
    retrieved = dict(retrieved_at or {})
    municipal = parcels if municipal_parcels is None else list(municipal_parcels)

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
        municipal_keys=frozenset(
            parcel_key(mun, parcel.pclblock, parcel.pcllot) for parcel in municipal
        ),
    )

    latest_sales = latest_by_parcel(sales, sale_key)
    sales_applied = 0

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

        deed = _latest_deed(
            parcel,
            latest_sales.get(sale_key(mun, parcel.pclblock, parcel.pcllot, parcel.qualifier)),
            as_of=as_of,
        )
        if deed.source == SOURCE_SR1A:
            sales_applied += 1

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
            deed_date=deed.deed_date,
            sale_price=deed.sale_price,
            sales_code=deed.sales_code,
            deed_source=deed.source,
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
                deed_source=deed.source,
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
            sales_total=len(list(sales)),
            sales_parcels=len(latest_sales),
            sales_applied=sales_applied,
        ),
    )


# --- the deed merge ----------------------------------------------------------


@dataclass(frozen=True)
class _Deed:
    """The deed facts one door was actually scored from, and where they came from."""

    deed_date: date | None
    sale_price: float
    sales_code: str
    source: str


def _latest_deed(parcel: Parcel, sale: Sale | None, *, as_of: date) -> _Deed:
    """MOD-IV's deed, or the SR1A sale when the register holds a newer one.

    Newer *strictly*: when both registers name the same transfer — the ordinary
    case once the county catches up — the parcel record stays authoritative, so
    a door's provenance does not flip between runs for a date that never moved.

    The whole record moves together. Taking SR1A's date but MOD-IV's price would
    describe two different transfers as one, and the engine's non-arm's-length
    rule reads exactly that pair; a fresh sale carrying a stale $1 would be
    zeroed as nominal. The NU code takes the `sales_code` slot because it means
    the same thing — the state's reason this sale was not usable — which is what
    lets the mover rules stay untouched by this ticket.
    """
    modiv = parse_deed_date(parcel.deed_date, as_of)
    if sale is None:
        return _Deed(modiv, parcel.sale_price, parcel.sales_code, SOURCE_MODIV)

    recorded = parse_deed_date(sale.deed_date, as_of)
    if recorded is None or (modiv is not None and recorded <= modiv):
        return _Deed(modiv, parcel.sale_price, parcel.sales_code, SOURCE_MODIV)

    return _Deed(recorded, sale.sale_price, sale.nu_code.strip(), SOURCE_SR1A)


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
    municipal_keys: frozenset[str],
) -> tuple[dict[str, list[Any]], dict[str, int], list[UnmatchedPermit]]:
    """Send each permit to a door, to a counter, or to the unmatched list.

    Every record leaves this function through exactly one of those three exits,
    which is what the accounting invariant in `ResolveReport` is asserting.

    Each in-window record is *also* filed into exactly one municipal bucket on
    the way past. That pass is deliberately independent of the territory routing
    above it — it asks whether the permit resolves to any parcel in the town, a
    question the door routing cannot answer — so the two accountings partition
    the same records twice without either disturbing the other.
    """
    records = list(permits)
    in_window = permits_within(records, as_of, window_days)

    landed: dict[str, list[Any]] = {}
    unmatched: list[UnmatchedPermit] = []
    counts = {
        "permits_total": len(records),
        # An undated permit falls out of the window rather than being guessed at.
        "permits_out_of_window": len(records) - len(in_window),
        "permits_in_window": len(in_window),
        "permits_out_of_territory": 0,
        "matched_by_block_lot": 0,
        "matched_by_address": 0,
        MUNICIPAL_MATCHED: 0,
        MUNICIPAL_PLACEHOLDER: 0,
        MUNICIPAL_UNMATCHED: 0,
    }

    for record in in_window:
        counts[_municipal_bucket(record, mun=mun, municipal_keys=municipal_keys)] += 1

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


def _municipal_bucket(record: Any, *, mun: str, municipal_keys: frozenset[str]) -> str:
    """Which of the three municipal counters this in-window permit belongs to.

    Block/lot only — the address fallback places a record on a *door*, and this
    number answers whether the record resolves to a *parcel*. Placeholders are
    tested first so a town that happens to hold a `0`-blocked parcel could never
    turn "not recorded" into a match; a placeholder is a miss with a known cause,
    which is worth more to whoever fixes the feed than a flattering rate.
    """
    if _is_placeholder(record.block, record.lot):
        return MUNICIPAL_PLACEHOLDER
    key = parcel_key(mun, record.block, record.lot)
    return MUNICIPAL_MATCHED if key in municipal_keys else MUNICIPAL_UNMATCHED


def _is_placeholder(block: Any, lot: Any) -> bool:
    """True when block or lot carries no parcel identity once canonicalized.

    Routed through `parcel_key` rather than string-matched here, so "0000", "00",
    "0" and "" are recognized by the same normalizer that does the joining — a
    second spelling table would be a second thing to keep in step.
    """
    _, *components = parcel_key("", block, lot).split("/")
    return any(component in PLACEHOLDER_COMPONENTS for component in components)


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
    deed_source: str = SOURCE_MODIV,
) -> dict[str, Provenance]:
    """One entry per signal that actually contributed a fact to this door.

    A signal that contributed nothing gets no entry: a provenance line asserts
    that some fact came from somewhere, and there is no such fact to attribute.
    A rental answer of False is not a fact about this door either — it is the
    absence of the door from a list — so it earns no attribution.
    """
    sources = [(SIGNAL_PARCEL, SOURCE_MODIV)]
    # Only when the sales register actually supplied this door's deed. A parcel
    # SR1A has no newer sale for contributed no fact, and claiming it did would
    # attribute the county's own date to the wrong register.
    if deed_source == SOURCE_SR1A:
        sources.append((SIGNAL_SALES, SOURCE_SR1A))
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
    sales_total: int = 0,
    sales_parcels: int = 0,
    sales_applied: int = 0,
) -> ResolveReport:
    """The graded numbers, all of them derived from the resolved doors."""
    matched_by_block_lot = permit_counts["matched_by_block_lot"]
    matched_by_address = permit_counts["matched_by_address"]
    matched = matched_by_block_lot + matched_by_address
    in_territory = matched + len(unmatched)
    in_window = permit_counts["permits_in_window"]
    matched_municipal = permit_counts[MUNICIPAL_MATCHED]

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
        permits_in_window=in_window,
        permits_matched_municipal=matched_municipal,
        permits_placeholder_block_lot=permit_counts[MUNICIPAL_PLACEHOLDER],
        permits_unmatched_municipal=permit_counts[MUNICIPAL_UNMATCHED],
        municipal_match_rate=_rate(matched_municipal, in_window),
        unmatched=tuple(unmatched),
        doors_with_block_group=sum(1 for f in doors.values() if f.block_group is not None),
        acs_available=acs.available,
        acs_reason=acs.reason,
        rental_declination_reason=rental_provider.declination_reason(),
        sales_total=sales_total,
        sales_parcels=sales_parcels,
        sales_applied=sales_applied,
    )


def _rate(numerator: int, denominator: int) -> float:
    """A ratio that reports 0.0 rather than dividing by an empty territory."""
    return numerator / denominator if denominator else 0.0
