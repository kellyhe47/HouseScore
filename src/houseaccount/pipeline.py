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
from typing import Any, Sequence

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
    DoorFacts,
    ResolveReport,
    resolve,
)
from houseaccount.scoring.engine import ScoreResult, score_door
from houseaccount.sources.acs import AcsResult, AcsSource
from houseaccount.sources.parcels import DEFAULT_MUN, Parcel, ParcelSource
from houseaccount.sources.permits import PERMIT_WINDOW_DAYS, PermitRecord, PermitSource
from houseaccount.sources.rental import NullRentalProvider, RentalRegistrationProvider
from houseaccount.sources.tiger import BlockGroupIndex, TigerSource
from houseaccount.territory import (
    select_territory,
    territory_median_value,
    write_territory_geojson,
)
from houseaccount.vision.run import run_vision
from houseaccount.vision.schema import Detection, to_score_vision
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

    # --- resolve ------------------------------------------------------------
    retrieved = {signal: moment.date() for signal in (SIGNAL_PARCEL, SIGNAL_PERMITS, SIGNAL_ACS)}
    resolved = resolve(
        territory,
        permits,
        acs,
        rental_provider if rental_provider is not None else NullRentalProvider(),
        as_of,
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
    vintage = _deed_vintage(parcels, doors, as_of=as_of, degradations=degradations)

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

    # --- score --------------------------------------------------------------
    median = territory_median_value(territory)
    scored = [
        (door, _score(door, as_of=as_of, median=median, vision=vision.get(door.pams_pin)))
        for door in doors
    ]

    # --- publish ------------------------------------------------------------
    manifest = RunManifest(
        run_at=moment,
        as_of=as_of,
        code_version=code_version(),
        territory_median_value=median,
        acs_dual_income_threshold=ACS_DUAL_INCOME_THRESHOLD,
        retrieved=retrieved,
        cost_usd=ledger.total_usd(),
        degradations=tuple(degradations),
        latest_deed_date=vintage.latest_deed_date,
        doors_in_mover_window=vintage.doors_in_mover_window,
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
) -> dict[str, dict[str, Any]]:
    """The R4 imagery signals per PAMS_PIN, or an empty mapping.

    Tiles are only fetched when there is a key to read them with: `run_vision`
    declines without one, and a declination that first downloaded two frames per
    door would cost bandwidth for an answer nobody asked for.
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
            f"{len(result.parse_failures)} vision answers could not be read as detections; "
            "the doors they covered scored without their imagery signals",
        )

    by_pin: dict[str, list[Detection]] = {}
    for detection in result.detections:
        by_pin.setdefault(detection.pams_pin, []).append(detection)
    return {pin: to_score_vision(found) for pin, found in by_pin.items()}


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
) -> DeedVintage:
    """Measure the extract's deed vintage, and say so when it costs the Mover group.

    The deed strings are read through `parse_deed_date` — the same normalizer
    `resolve` already ran over them — so the vintage cannot disagree with the
    dates the doors were scored from. Territory doors are counted off the parsed
    `DoorFacts.deed_date` for the same reason: a second parse here would be a
    second answer waiting to drift.

    Nothing measured here is returned to the score engine. The count exists to
    answer one question — could the model's heaviest group fire on this data? —
    and when the answer is no, that is recorded once, as a degradation, in the
    same list an operator already reads for a refused provider.
    """
    parsed = [parse_deed_date(parcel.deed_date, as_of) for parcel in parcels]
    latest = max((day for day in parsed if day is not None), default=None)

    in_window = sum(
        1
        for door in doors
        if door.deed_date is not None and (as_of - door.deed_date).days <= MOVER_WINDOW_DAYS
    )
    if in_window == 0:
        _degrade(degradations, _mover_unearnable_note(latest))
    return DeedVintage(latest_deed_date=latest, doors_in_mover_window=in_window)


def _mover_unearnable_note(latest: date | None) -> str:
    """Why no door earned a mover point, written for whoever is doubting the map.

    Two shapes, because they are two different findings. A dated extract that is
    simply old is the ordinary case and the date is the whole evidence, so it is
    quoted. An extract with no readable deed at all has no date to quote — and a
    placeholder standing in for one would read as a bug in this sentence rather
    than as the absence it describes.
    """
    if latest is not None:
        return (
            f"no door in this territory has a deed dated inside the {MOVER_WINDOW_DAYS}-day "
            f"mover window: the newest deed anywhere in the MOD-IV extract is "
            f"{latest.isoformat()}. The Mover group — the heaviest signal in the model — "
            "therefore scored zero everywhere on this run. That is the vintage of the county "
            "extract, not a rule that failed to fire."
        )
    return (
        "the MOD-IV extract carries no readable deed date, so no door could be placed inside "
        f"the {MOVER_WINDOW_DAYS}-day mover window and the Mover group — the heaviest signal "
        "in the model — scored zero everywhere on this run. That is the state of the county "
        "extract, not a rule that failed to fire."
    )


# --- scoring ------------------------------------------------------------------


def _score(
    door: DoorFacts,
    *,
    as_of: date,
    median: float,
    vision: dict[str, Any] | None,
) -> ScoreResult | None:
    """This door's score, or None when the county record cannot support one.

    R6.1's degraded path handles a record missing *some* of its parcel facts;
    `parcel_record_incomplete` catches the record missing all of them, where any
    number produced would be invented rather than degraded.
    """
    if parcel_record_incomplete(door):
        return None
    return score_door(
        door.to_score_input(
            as_of=as_of,
            territory_median_value=median,
            acs_dual_income_threshold=ACS_DUAL_INCOME_THRESHOLD,
            vision=vision,
        )
    )


def _degrade(degradations: list[str], reason: str) -> None:
    """Record one lost signal and say so once, where an operator will see it."""
    logger.warning(reason)
    degradations.append(reason)


if __name__ == "__main__":  # pragma: no cover - exercised by `make pipeline`
    raise SystemExit(main())
