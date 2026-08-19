"""`make pipeline` — harvest -> resolve -> vision -> score -> publish (T009, R2.2).

R2.2 grades *autonomy*: a fresh clone plus the documented environment variables
produces the serving artifacts with zero manual steps. So this module is the one
place that knows the whole run, and everything it needs is either a shipped
default (the territory anchor, the ~540 target, the ACS threshold) or a seam
passed in.

**Every seam is injected.** `transport` is the T001 HTTP callable every source
fetches through and `client` is the OpenAI client the vision provider calls,
so the entire five-stage run is exercisable against scripted responses without
opening a socket. `run_pipeline` builds no clients of its own; `main` builds a
`Config.from_env()` and nothing else.

**One cache for the whole run.** It is constructed here, from `Config`, and
handed to every source — which is what makes R2.3 true across all five of them
at once: a second run reads parcels, permits, block groups, ACS rows, ortho
tiles *and* vision detections off disk, calls the transport zero times, and
spends $0.00.

**Only the parcel harvest is load-bearing.** Without parcels there is no
territory, and publishing an empty town over a good one is worse than failing,
so a `SourceError` from that stage propagates. Every other source failing is a
degradation: named in `PipelineResult.degradations`, logged once at WARNING,
copied into the manifest, and the run still publishes every door. That list is
what the Data & Ethics page reads, so the strings are written for an operator —
what was lost, and why.

**Declining is free.** With no `OPENAI_API_KEY` the vision stage sits out,
and it must do so *before* the tiles are fetched: downloading 1,080 ortho frames
for a stage that will not look at them is exactly the waste the declination
exists to avoid.

**And declining is not the same as answering badly.** The vision stage has three
outcomes — it ran, it ran and lost part of its answers, it never ran — and the
run measures which one happened rather than leaving it to be read off the
degradation sentence. A stage that answered 270 requests, lost 31 of them and
left imagery evidence on 119 doors recorded prose that pattern-matched as a
refusal, and the Data & Ethics page duly printed DECLINED beside evidence a
reviewer could click on the map. The counts go into the manifest's `vision`
block; the sentence stays in `degradations`, where an operator reads it.

**A stale extract is a degradation too.** The Mover group is the heaviest signal
in the model, and whether it can fire at all depends on how old the county's deed
data is, not on the rule. When the run measures that no door is inside the mover
window, that is named in `degradations` in the same voice as a provider that
refused — because a reviewer reading the map otherwise cannot tell "nobody moved
here recently" from "the mover rule is broken". The measurement itself is
published beside it, in the manifest's `deed_vintage` block. Neither reaches a
door: this is disclosure, and the score engine is not told about it.
"""

from __future__ import annotations

import argparse
import logging
import subprocess
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from houseaccount.cache import Cache
from houseaccount.config import Config
from houseaccount.cost import CostLedger
from houseaccount.http import SourceError, Transport, requests_transport
from houseaccount.normalize import parse_deed_date
from houseaccount.publish import (
    MOVER_WINDOW_DAYS,
    PublishResult,
    RunManifest,
    parcel_record_incomplete,
    publish,
)
from houseaccount.resolve import (
    SIGNAL_ACS,
    SIGNAL_PARCEL,
    SIGNAL_PERMITS,
    SIGNAL_SALES,
    DoorFacts,
    ResolveReport,
    resolve,
)
from houseaccount.scoring.bundle import (
    _parse_us_date,
    build_bundle,
    roof_permits_by_pin,
    sdl_by_pin,
)
from houseaccount.scoring.evidence import imagery_for
from houseaccount.scoring.v2 import score_door_v2
from houseaccount.sources.acs import AcsResult, AcsSource
from houseaccount.sources.parcels import DEFAULT_MUN, Parcel, ParcelSource
from houseaccount.sources.permits import PERMIT_WINDOW_DAYS, PermitRecord, PermitSource
from houseaccount.sources.rental import NullRentalProvider, RentalRegistrationProvider
from houseaccount.sources.sales import Sale, SalesExtract, SalesSource
from houseaccount.sources.tiger import BlockGroupIndex, TigerSource
from houseaccount.territory import select_territory, write_territory_geojson
from houseaccount.vision.run import run_vision
from houseaccount.vision.schema import Detection, condition_declined, to_score_vision
from houseaccount.vision.tiles import ORTHO_YEARS, Tile, fetch_tile, tile_for

logger = logging.getLogger(__name__)

#: Ramsey Golf & Country Club, (lon, lat) — the territory's anchor (R1.1).
TERRITORY_CENTER: tuple[float, float] = (-74.1560, 41.0447)

#: The ~540 doors the territory is defined as.
TERRITORY_TARGET = 540

#: PRD R6 capacity prior: a block group at or above this share of dual-income
#: families earns the +5 nudge.
ACS_DUAL_INCOME_THRESHOLD = 0.35

#: The committed territory artifact everything downstream reads (R1.1).
TERRITORY_GEOJSON_NAME = "territory.geojson"


@dataclass(frozen=True)
class PipelineResult:
    """One completed run: what resolved, what was published, what degraded."""

    report: ResolveReport
    manifest: RunManifest
    published: PublishResult
    ledger: CostLedger
    degradations: tuple[str, ...] = ()


@dataclass(frozen=True)
class VisionStage:
    """What the vision stage produced, and what it can say about its own run.

    Two things a caller needs and one it must not have to guess. `signals` is
    what the score reads. The rest is what the *manifest* reads: whether the
    stage ran at all, and how much of what it issued came back readable. A run
    that answered 270 requests and lost 31 of them is a partial loss, and the
    only place that is distinguishable from a refusal is here — the degradation
    sentence beside it reads like one to anything that pattern-matches prose.
    """

    signals: Mapping[str, dict[str, Any]]
    available: bool
    declination_reason: str | None
    answers_total: int
    answers_lost: int


@dataclass(frozen=True)
class DeedVintage:
    """How old this extract's deeds are, and what that leaves the Mover group.

    Two numbers with deliberately different scopes. `latest_deed_date` is a
    statement about the *feed*, so it is measured over every municipal parcel the
    harvest holds — a fresh deed across town still proves the extract is current.
    `doors_in_mover_window` is a statement about the *territory*, because that is
    the set the map draws and the set a rep would knock on.
    """

    latest_deed_date: date | None
    doors_in_mover_window: int
    #: The sales register's own newest deed, and the files it was read from.
    latest_sale_date: date | None = None
    source_files: tuple[str, ...] = ()


def code_version() -> str:
    """The git short SHA if this is a checkout, else the package version.

    Stamped into the manifest so a published artifact can be traced back to the
    code that produced it (R13). A tarball with neither is still a run worth
    publishing, so the last resort is a marker rather than an exception.
    """
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).resolve().parent,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        completed = None
    if completed is not None and completed.returncode == 0 and completed.stdout.strip():
        return completed.stdout.strip()

    try:
        from importlib.metadata import PackageNotFoundError, version

        return version("houseaccount")
    except (ImportError, PackageNotFoundError):
        return "unknown"


def run_pipeline(
    *,
    config: Config,
    transport: Transport = requests_transport,
    client: Any | None = None,
    rental_provider: RentalRegistrationProvider | None = None,
    as_of: date | None = None,
    now: datetime | None = None,
    center: tuple[float, float] = TERRITORY_CENTER,
    target: int = TERRITORY_TARGET,
    mun: str = DEFAULT_MUN,
) -> PipelineResult:
    """Run all five stages in order and publish the artifacts.

    `as_of` is the date the score is computed against and defaults to the run's
    own date; `now` is the wall clock stamped on the manifest. Both are
    injectable so a run can be reproduced against a past date without moving the
    machine's clock.
    """
    moment = now if now is not None else datetime.now(timezone.utc)
    as_of = as_of if as_of is not None else moment.date()
    cache = Cache(config.cache_dir)
    ledger = CostLedger()
    degradations: list[str] = []

    # --- harvest ------------------------------------------------------------
    # Load-bearing: a failure here raises rather than publishing an empty town.
    parcels: Sequence[Parcel] = ParcelSource(cache=cache, transport=transport).fetch(mun)
    territory = select_territory(parcels, center, target)
    write_territory_geojson(territory, config.data_dir / TERRITORY_GEOJSON_NAME)

    permits = _harvest_permits(
        cache=cache, transport=transport, mun=mun, as_of=as_of, degradations=degradations
    )
    block_groups = _harvest_block_groups(
        cache=cache, transport=transport, degradations=degradations
    )
    acs = _harvest_acs(cache=cache, transport=transport, config=config, degradations=degradations)
    sales = _harvest_sales(
        cache=cache, transport=transport, mun=mun, as_of=as_of, degradations=degradations
    )

    # --- resolve ------------------------------------------------------------
    retrieved = {
        signal: moment.date()
        for signal in (SIGNAL_PARCEL, SIGNAL_PERMITS, SIGNAL_ACS, SIGNAL_SALES)
    }
    resolved = resolve(
        territory,
        permits,
        acs,
        rental_provider if rental_provider is not None else NullRentalProvider(),
        as_of,
        sales=sales.sales,
        block_group_index=block_groups,
        # R3.2 is a municipality-wide question and the harvest already holds
        # every municipal parcel, so the graded denominator is measured against
        # all of them rather than only the doors the territory selected. This
        # changes no door and no territory-scoped number.
        municipal_parcels=parcels,
        mun=mun,
        retrieved_at=retrieved,
    )
    if resolved.report.rental_declination_reason:
        _degrade(degradations, resolved.report.rental_declination_reason)

    doors = list(resolved.doors.values())
    vintage = _deed_vintage(
        parcels, doors, as_of=as_of, degradations=degradations, sales=sales
    )

    # --- vision -------------------------------------------------------------
    vision = _run_vision(
        doors,
        config=config,
        cache=cache,
        ledger=ledger,
        transport=transport,
        client=client,
        degradations=degradations,
    )

    # --- score (V2: bundle -> score_door_v2, R27) -----------------------------
    scored = _score_doors(doors, as_of=as_of, vision=vision, data_dir=config.data_dir)

    # --- publish ------------------------------------------------------------
    manifest = RunManifest(
        run_at=moment,
        as_of=as_of,
        code_version=code_version(),
        acs_dual_income_threshold=ACS_DUAL_INCOME_THRESHOLD,
        retrieved=retrieved,
        cost_usd=ledger.total_usd(),
        degradations=tuple(degradations),
        latest_deed_date=vintage.latest_deed_date,
        doors_in_mover_window=vintage.doors_in_mover_window,
        latest_sale_date=vintage.latest_sale_date,
        sales_source_files=vintage.source_files,
        doors_with_sales_deed=resolved.report.sales_applied,
        vision_available=vision.available,
        vision_declination_reason=vision.declination_reason,
        vision_answers_total=vision.answers_total,
        vision_answers_lost=vision.answers_lost,
    )
    published = publish(
        scored, report=resolved.report, manifest=manifest, data_dir=config.data_dir
    )
    logger.info(
        "published %s of %s doors to %s",
        published.doors_scored,
        published.doors_total,
        config.data_dir,
    )

    return PipelineResult(
        report=resolved.report,
        manifest=manifest,
        published=published,
        ledger=ledger,
        degradations=tuple(degradations),
    )


def main(argv: Sequence[str] | None = None) -> int:
    """`python -m houseaccount.pipeline` — the whole run, no arguments."""
    parser = argparse.ArgumentParser(
        prog="houseaccount.pipeline",
        description="Harvest, resolve, see, score and publish the Ramsey territory.",
    )
    parser.parse_args(list(argv) if argv is not None else None)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    config = Config.from_env()
    run_pipeline(config=config)
    return 0


# --- the optional sources -----------------------------------------------------


def _harvest_permits(
    *,
    cache: Cache,
    transport: Transport,
    mun: str,
    as_of: date,
    degradations: list[str],
) -> Sequence[PermitRecord]:
    """Permits inside the rolling window, or none and a named degradation.

    The window is applied server-side so a two-year run does not page through a
    decade; `resolve` applies it again, which is what makes the report's
    out-of-window count meaningful whatever the feed returns.
    """
    since = as_of - timedelta(days=PERMIT_WINDOW_DAYS)
    try:
        return PermitSource(cache=cache, transport=transport).fetch(comu=mun, since=since)
    except SourceError as error:
        _degrade(
            degradations,
            f"the NJ construction-permit feed was unavailable ({error}); no door carries "
            "permit points on this run, so the hires-out signal is missing everywhere",
        )
        return ()


def _harvest_sales(
    *,
    cache: Cache,
    transport: Transport,
    mun: str,
    as_of: date,
    degradations: list[str],
) -> SalesExtract:
    """The SR1A sales register, or an empty extract and a named degradation.

    Not load-bearing. MOD-IV still carries a deed date for every parcel, so a
    refused download costs the run its *freshness*, not its Mover group — the
    score falls back to the county's own date exactly as it did before this
    source existed. That is a materially different failure from the parcel
    harvest going down, and it is reported rather than raised.
    """
    try:
        extract = SalesSource(cache=cache, transport=transport).fetch(
            mun, as_of=as_of, window_days=MOVER_WINDOW_DAYS
        )
    except SourceError as error:
        _degrade(
            degradations,
            f"the NJ SR1A sales register declined ({error}), so deed recency falls back to "
            "the MOD-IV extract's own deed date",
        )
        return SalesExtract()

    if extract.reason:
        _degrade(degradations, extract.reason)
    return extract


def _harvest_block_groups(
    *, cache: Cache, transport: Transport, degradations: list[str]
) -> BlockGroupIndex:
    """The point-in-polygon index, or an empty one and a named degradation.

    An empty index is not an error to the doors: every one of them resolves to
    no block group, so the ACS prior simply never fires.
    """
    try:
        return BlockGroupIndex(TigerSource(cache=cache, transport=transport).fetch())
    except SourceError as error:
        _degrade(
            degradations,
            f"TIGERweb block-group boundaries were unavailable ({error}); no door could be "
            "placed in a block group, so the ACS neighbourhood prior was skipped",
        )
        return BlockGroupIndex(())


def _harvest_acs(
    *, cache: Cache, transport: Transport, config: Config, degradations: list[str]
) -> AcsResult:
    """Block-group statistics. This source declines rather than raising, so the
    only thing to do here is publish the reason it gave."""
    result = AcsSource(
        cache=cache, transport=transport, api_key=config.census_api_key
    ).fetch()
    if not result.available and result.reason:
        _degrade(degradations, result.reason)
    return result


# --- the vision stage ---------------------------------------------------------


def _run_vision(
    doors: Sequence[DoorFacts],
    *,
    config: Config,
    cache: Cache,
    ledger: CostLedger,
    transport: Transport,
    client: Any | None,
    degradations: list[str],
) -> VisionStage:
    """The R4 imagery signals per PAMS_PIN, and what the stage says about itself.

    Tiles are only fetched when there is a key to read them with: `run_vision`
    declines without one, and a declination that first downloaded two frames per
    door would cost bandwidth for an answer nobody asked for.

    The stage's own state is returned alongside the signals rather than left to
    be inferred from the degradation sentence below. Both are recorded — the
    sentence is what an operator reads, the counts are what the artifact states —
    and only the counts can tell a stage that answered and lost 31 of 270 from
    one that never ran at all.
    """
    tiles = (
        _fetch_tiles(doors, cache=cache, transport=transport, degradations=degradations)
        if config.openai_api_key
        else ()
    )
    result = run_vision(tiles, config=config, ledger=ledger, cache=cache, client=client)

    if result.declined and result.reason:
        # `run_vision` has already logged this one; recording it here is what
        # puts it in the manifest.
        degradations.append(result.reason)
    if result.parse_failures:
        _degrade(
            degradations,
            f"{result.answers_lost} vision answers could not be read as detections; "
            "the doors they covered scored without their imagery signals",
        )

    by_pin: dict[str, list[Detection]] = {}
    for detection in result.detections:
        by_pin.setdefault(detection.pams_pin, []).append(detection)
    return VisionStage(
        signals={pin: to_score_vision(found) for pin, found in by_pin.items()},
        available=not result.declined,
        declination_reason=result.reason or None if result.declined else None,
        answers_total=result.answers_total,
        answers_lost=result.answers_lost,
    )


def _fetch_tiles(
    doors: Sequence[DoorFacts],
    *,
    cache: Cache,
    transport: Transport,
    degradations: list[str],
) -> tuple[Tile, ...]:
    """Both ortho vintages for every door that has a centroid to centre on.

    A tile the MapServer refuses is one missing frame, not a failed run: the
    door keeps every other signal and the count of what was lost is reported
    once rather than per tile.
    """
    tiles: list[Tile] = []
    missing = 0
    for door in doors:
        if door.centroid is None:
            continue
        for year in ORTHO_YEARS:
            try:
                tiles.append(
                    fetch_tile(
                        tile_for(door.pams_pin, door.centroid, year),
                        cache=cache,
                        transport=transport,
                    )
                )
            except SourceError:
                missing += 1

    if missing:
        _degrade(
            degradations,
            f"{missing} NJ orthoimagery tiles could not be fetched; the pool, solar and "
            "exterior-condition signals are missing for the doors they cover",
        )
    return tuple(tiles)


# --- the deed vintage ---------------------------------------------------------


def _deed_vintage(
    parcels: Sequence[Parcel],
    doors: Sequence[DoorFacts],
    *,
    as_of: date,
    degradations: list[str],
    sales: SalesExtract | None = None,
) -> DeedVintage:
    """Measure the extract's deed vintage (R28: numbers only, no V1 prose).

    The deed strings are read through `parse_deed_date` — the same normalizer
    `resolve` already ran over them — so the vintage cannot disagree with the
    dates the doors were scored from. Territory doors are counted off the parsed
    `DoorFacts.deed_date` for the same reason: a second parse here would be a
    second answer waiting to drift.

    Nothing measured here is returned to the score engine, and nothing here is
    recorded as a degradation: V2's mover strength decays to 365 days, so an
    empty 90-day window no longer means the signal cannot fire — the measured
    numbers are the whole disclosure now.
    """
    parsed = [parse_deed_date(parcel.deed_date, as_of) for parcel in parcels]
    modiv_latest = max((day for day in parsed if day is not None), default=None)

    sold = [parse_deed_date(sale.deed_date, as_of) for sale in (sales.sales if sales else ())]
    sale_latest = max((day for day in sold if day is not None), default=None)

    # The feed's vintage is the freshest date either register can show.
    latest = max((day for day in (modiv_latest, sale_latest) if day is not None), default=None)

    in_window = sum(
        1
        for door in doors
        if door.deed_date is not None and (as_of - door.deed_date).days <= MOVER_WINDOW_DAYS
    )

    return DeedVintage(
        latest_deed_date=latest,
        doors_in_mover_window=in_window,
        latest_sale_date=sale_latest,
        source_files=tuple(sales.source_files) if sales else (),
    )


# --- scoring (V2, R27: bundle -> score_door_v2, no side-by-side paths) ---------


def _score_doors(
    doors: Sequence[DoorFacts],
    *,
    as_of: date,
    vision: VisionStage,
    data_dir: Path | None = None,
) -> list[tuple[DoorFacts, dict[str, Any] | None]]:
    """Every door through the V2 seam: build its evidence bundle, score it,
    and enrich imagery-derived evidence with its re-openable frame.

    A door whose county record carries no scoreable fact at all is `None`
    (published as an exclusion); anything less scores through V2's typed
    data-gap path.
    """
    territory_parcels = tuple(
        {
            "pams_pin": door.pams_pin,
            "prop_class": door.prop_class,
            "net_value": door.net_value if door.net_value > 0 else None,
            "yr_constr": door.yr_constr if door.yr_constr > 0 else None,
            "calc_acre": door.calc_acre,
            "centroid": door.centroid,
        }
        for door in doors
    )

    # The SDL property-history snapshot (permits, displayed sales, assessed
    # valuations) is R22's primary lifecycle source. Without it, no door can
    # ever earn project points from a municipal permit: the statewide feed
    # below carries no dispositions and lags the portal.
    sdl_pages = sdl_by_pin(data_dir) if data_dir is not None else {}
    roof_pages = roof_permits_by_pin(data_dir) if data_dir is not None else {}

    scored: list[tuple[DoorFacts, dict[str, Any] | None]] = []
    for door in doors:
        if parcel_record_incomplete(door):
            scored.append((door, None))
            continue
        signals = vision.signals.get(door.pams_pin)
        sdl = sdl_pages.get(door.pams_pin)
        sdl_collected = bool(sdl) and sdl.get("collection_status") == "collected"
        # AE10: one municipal permit present in both the SDL page and the
        # statewide feed must count once. Socrata rows carry no municipal
        # permit number (only their own recordid), so the id-based coalesce in
        # the bundle can never match them — the join key here is the issue
        # date, and the richer SDL record wins.
        roof_permits = roof_pages.get(door.pams_pin, ())
        sdl_issue_dates = set()
        if sdl_collected:
            for app in (sdl.get("construction") or {}).get("permit_applications") or ():
                issued = _parse_us_date(app.get("issue_date"))
                if issued is not None:
                    sdl_issue_dates.add(issued)
        for permit in roof_permits:
            raw = permit.get("issue_date")
            if raw:
                sdl_issue_dates.add(date.fromisoformat(raw))
        ctx = {
            "parcel": {
                "pams_pin": door.pams_pin,
                "prop_class": door.prop_class,
                "net_value": door.net_value if door.net_value > 0 else None,
                "yr_constr": door.yr_constr if door.yr_constr > 0 else None,
                "calc_acre": door.calc_acre,
                "deed_date": door.deed_date.isoformat() if door.deed_date else None,
                "sale_price": door.sale_price,
                "sales_code": door.sales_code,
                "centroid": door.centroid,
            },
            "territory_parcels": territory_parcels,
            "statewide_permits": tuple(
                {
                    "id": f"{door.pams_pin}:{getattr(record, 'record_id', '') or index}",
                    "description": getattr(record, "type", ""),
                    "issue_date": record.date.isoformat() if record.date else None,
                }
                for index, record in enumerate(door.permits_2yr)
                if record.date not in sdl_issue_dates
            ),
            "acs_block_group": (
                {"dual_income_pct": door.block_group.dual_income_pct}
                if door.block_group is not None
                and door.block_group.dual_income_pct is not None
                else {}
            ),
            # The municipal rental registry is OPRA-only and absent from every
            # live run (R6/R23): the engine records the typed gap.
            "rental_registry": {},
            "imagery": _imagery_inputs(signals, available=vision.available),
        }
        if sdl_pages:
            # R23: with the snapshot on disk, an uncollected page is a data
            # gap, never proof of absence — flagged so the engine emits it.
            # With no snapshot at all the keys stay out, and the bundle treats
            # SDL as unknown rather than flooding every door with the gap.
            ctx["sdl"] = sdl if sdl_collected else None
            ctx["sdl_available"] = sdl_collected
            ctx["sdl_match_exact_current"] = sdl_collected
        if roof_permits:
            ctx["sdl_roof_permits"] = roof_permits
        envelope = dict(score_door_v2(build_bundle(ctx, as_of), as_of))
        envelope["evidence"] = _attach_frames(envelope["evidence"], signals)
        scored.append((door, envelope))
    return scored


def _imagery_inputs(
    signals: Mapping[str, Any] | None, *, available: bool
) -> dict[str, Any]:
    """The vision stage's answers in the V2 bundle's imagery vocabulary."""
    if not available:
        return {"available": False}
    observations: list[dict[str, Any]] = []
    if signals:
        for kind in ("pool", "solar"):
            if signals.get(kind):
                frame = imagery_for(signals, kind)
                observations.append(
                    {"kind": kind, "confidence": frame["model_confidence"] if frame else 1.0}
                )
        if condition_declined(signals.get("condition_2015"), signals.get("condition_2020")):
            frame = imagery_for(signals, "condition")
            observations.append(
                {
                    "kind": "condition_decline",
                    "confidence": frame["model_confidence"] if frame else 1.0,
                }
            )
    return {"available": True, "observations": tuple(observations)}


#: Which vision attachment re-opens each imagery-derived evidence type.
_FRAME_KEYS = {
    "fit_pool": "pool",
    "fit_solar": "solar",
    "fit_condition_decline": "condition",
}


def _attach_frames(
    evidence: Sequence[Mapping[str, Any]], signals: Mapping[str, Any] | None
) -> list[dict[str, Any]]:
    """Enrich imagery-derived evidence entries with their re-openable frames."""
    enriched = []
    for item in evidence:
        entry = dict(item)
        key = _FRAME_KEYS.get(entry["type"])
        if key and signals:
            frame = imagery_for(signals, key)
            if frame:
                entry["imagery"] = dict(frame)
        enriched.append(entry)
    return enriched


def _degrade(degradations: list[str], reason: str) -> None:
    """Record one lost signal and say so once, where an operator will see it."""
    logger.warning(reason)
    degradations.append(reason)


if __name__ == "__main__":  # pragma: no cover - exercised by `make pipeline`
    raise SystemExit(main())
