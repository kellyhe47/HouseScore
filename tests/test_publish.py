"""Serving artifacts: doors.geojson + SQLite + run_manifest.json (T009, R2.2/R2.3/R9.4).

Three files come out of one call, and each answers a different question:

* `data/doors.geojson` is what the map renders and what the rep clicks. Every
  door in the territory appears exactly once — including the ones that could not
  be scored, because R9.4 makes them clickable and counts them in the "537 of
  540" readout. A door missing from the file is a door nobody can click.
* `data/houseaccount.sqlite` is what the MCP server reads (`get_door_score`,
  `explain_score`). Two tables, `doors` and `evidence`, joined on `PAMS_PIN`.
* `data/run_manifest.json` is what makes the run reproducible (R13): the
  once-per-run inputs the score was computed against, when each source was
  retrieved, what the run cost, and which code produced it.

**The seam.** `publish(scored, *, report, manifest, data_dir)` where `scored` is
a sequence of `(DoorFacts, ScoreResult | None)` pairs in publication order. The
pipeline already holds all of that; publish re-derives nothing, which is what
keeps the artifacts consistent with the numbers the resolve report published.

**What "unscored" means.** A `None` score is published as an exclusion, and the
rule that produces one is `parcel_record_incomplete`: the county record carries
*none* of the three parcel facts the score is built from — no deed date, no year
built, no assessed value. Any one of them still scores (degraded, `low`
confidence, with a `data_gap` line — that is the engine's R6.1 path, and it is
deliberately not this rule). Only a record with nothing in it at all yields a
number that would be pure fabrication, so that record gets no number.

**Null geometry is published, not skipped.** `territory.write_territory_geojson`
drops geometry-less parcels because an unrenderable feature only breaks a map.
Here the opposite holds: the coverage readout and the exclusion panel are
per-door, so the feature ships with `"geometry": null` and the door stays
countable and clickable.

**The identity guard.** The published property set is an exact allowlist, so
nothing can arrive in these files by being copied wholesale off a record. The
token scan underneath is the standing regression on top of that.

No network, no fixtures on disk: every door below is synthetic and built
in-module, and every artifact is written under `tmp_path`.
"""

import json
import re
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from houseaccount.publish import (
    DOORS_GEOJSON_NAME,
    EXCLUSION_REASON,
    RUN_MANIFEST_NAME,
    SQLITE_NAME,
    PublishResult,
    RunManifest,
    parcel_record_incomplete,
    publish,
)
from houseaccount.resolve import DoorFacts, ResolveReport
from houseaccount.scoring.engine import score_door
from houseaccount.scoring.weights import THRESHOLDS
from houseaccount.vision.run import NO_KEY_REASON as VISION_NO_KEY_REASON

AS_OF = date(2026, 8, 14)
RUN_AT = datetime(2026, 8, 14, 6, 30, 0, tzinfo=timezone.utc)

TERRITORY_MEDIAN_VALUE = 700000.0
ACS_DUAL_INCOME_THRESHOLD = 0.35

LON, LAT = -74.1560, 41.0447

RETRIEVED = {
    "parcel": date(2026, 8, 13),
    "permits": date(2026, 8, 13),
    "acs": date(2026, 8, 12),
}

#: A vision payload complete enough to leave an imagery attachment on the
#: evidence line, so the artifact is pinned to carry the re-openable frame.
IMAGE_REF = "https://maps.nj.gov/arcgis/rest/services/Basemap/Orthos_Natural_2020_NJ_WM/MapServer/export?bbox=1"
POOL_VISION = {
    "pool": True,
    "solar": False,
    "condition_2015": None,
    "condition_2020": None,
    "imagery": {
        "pool": {
            "image_url": IMAGE_REF,
            "bbox": [-8255000.0, 5030000.0, -8254880.0, 5030120.0],
            "model_confidence": 0.93,
            "capture_date": "2020-01-01",
        }
    },
}


def polygon(lon=LON, lat=LAT):
    step = 0.0002
    return {
        "type": "Polygon",
        "coordinates": [
            [
                [lon, lat],
                [lon + step, lat],
                [lon + step, lat + step],
                [lon, lat + step],
                [lon, lat],
            ]
        ],
    }


# --- builders ----------------------------------------------------------------


#: Distinguishes "the builder's default polygon" from "this door has none".
DEFAULT_GEOMETRY = object()


def facts(
    pin="0248_01101_00003",
    *,
    deed=date(2026, 6, 15),
    yr_constr=1962,
    net_value=980000.0,
    sale_price=1150000.0,
    geometry=DEFAULT_GEOMETRY,
    street="MAPLE",
    lot="3",
    **overrides,
):
    """One resolved door. Defaults are a complete, scoreable county record."""
    fields = dict(
        pams_pin=pin,
        prop_class="2",
        prop_loc=f"{lot} {street} ST",
        zip5="07446",
        pclblock="1101",
        pcllot=lot,
        parcel_key=f"0248-1101-{lot}",
        address_key=f"{lot} {street} ST",
        situs=f"{lot} {street} ST, Ramsey NJ 07446",
        centroid=(LON, LAT),
        geometry=polygon() if geometry is DEFAULT_GEOMETRY else geometry,
        deed_date=deed,
        sale_price=sale_price,
        sales_code="",
        yr_constr=yr_constr,
        net_value=net_value,
        calc_acre=0.61,
    )
    fields.update(overrides)
    return DoorFacts(**fields)


def scored(door, *, vision=None):
    """`(door, ScoreResult)` — the real engine, never a stand-in."""
    return (
        door,
        score_door(
            door.to_score_input(
                as_of=AS_OF,
                territory_median_value=TERRITORY_MEDIAN_VALUE,
                acs_dual_income_threshold=ACS_DUAL_INCOME_THRESHOLD,
                vision=vision,
            )
        ),
    )


def unscored(door):
    return (door, None)


def manifest(**overrides):
    fields = dict(
        run_at=RUN_AT,
        as_of=AS_OF,
        code_version="a1b2c3d",
        territory_median_value=TERRITORY_MEDIAN_VALUE,
        acs_dual_income_threshold=ACS_DUAL_INCOME_THRESHOLD,
        retrieved=dict(RETRIEVED),
        cost_usd=3.0,
        degradations=(),
    )
    fields.update(overrides)
    return RunManifest(**fields)


def report(**overrides):
    fields = dict(as_of=AS_OF, doors_total=1, doors_with_signal=1, coverage=1.0)
    fields.update(overrides)
    return ResolveReport(**fields)


@pytest.fixture
def data_dir(tmp_path):
    """Deliberately not created: publishing into a fresh clone must work."""
    return tmp_path / "data"


def run(scored_pairs, *, data_dir, report_=None, manifest_=None):
    pairs = list(scored_pairs)
    return publish(
        pairs,
        report=report_ if report_ is not None else report(doors_total=len(pairs)),
        manifest=manifest_ if manifest_ is not None else manifest(),
        data_dir=data_dir,
    )


def read_geojson(data_dir):
    return json.loads((Path(data_dir) / DOORS_GEOJSON_NAME).read_text(encoding="utf-8"))


def read_manifest(data_dir):
    return json.loads((Path(data_dir) / RUN_MANIFEST_NAME).read_text(encoding="utf-8"))


def rows(data_dir, sql, *params):
    connection = sqlite3.connect(Path(data_dir) / SQLITE_NAME)
    try:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute(sql, params).fetchall()]
    finally:
        connection.close()


# --- doors.geojson ------------------------------------------------------------


def test_publish_writes_all_three_artifacts(data_dir):
    result = run([scored(facts())], data_dir=data_dir)

    assert result.geojson_path == data_dir / DOORS_GEOJSON_NAME
    assert result.sqlite_path == data_dir / SQLITE_NAME
    assert result.manifest_path == data_dir / RUN_MANIFEST_NAME
    assert all(path.is_file() for path in (result.geojson_path, result.sqlite_path, result.manifest_path))
    assert isinstance(result, PublishResult)


def test_geojson_is_a_feature_collection_in_publication_order(data_dir):
    pairs = [
        scored(facts("0248_01101_00003", lot="3")),
        unscored(facts("0248_01101_00004", lot="4")),
        scored(facts("0248_01101_00005", lot="5")),
    ]
    run(pairs, data_dir=data_dir)

    payload = read_geojson(data_dir)
    assert payload["type"] == "FeatureCollection"
    assert [feature["properties"]["PAMS_PIN"] for feature in payload["features"]] == [
        "0248_01101_00003",
        "0248_01101_00004",
        "0248_01101_00005",
    ]


@pytest.mark.parametrize("pair_builder", [scored, unscored], ids=["scored", "unscored"])
def test_feature_properties_are_exactly_the_published_allowlist(data_dir, pair_builder):
    """An exact set, not a superset: nothing reaches the browser by being copied
    wholesale off a record (R11.1)."""
    run([pair_builder(facts())], data_dir=data_dir)

    properties = read_geojson(data_dir)["features"][0]["properties"]
    assert set(properties) == {
        "PAMS_PIN",
        "score",
        "confidence",
        "evidence",
        "situs",
        "exclusion_reason",
    }


def test_scored_feature_carries_the_score_confidence_situs_and_geometry(data_dir):
    door, result = scored(facts())
    run([(door, result)], data_dir=data_dir)

    feature = read_geojson(data_dir)["features"][0]
    assert feature["type"] == "Feature"
    assert feature["geometry"] == door.geometry
    assert feature["properties"]["score"] == result.score
    assert isinstance(feature["properties"]["score"], int)
    assert feature["properties"]["confidence"] == result.confidence
    assert feature["properties"]["situs"] == door.situs
    assert feature["properties"]["exclusion_reason"] is None


def test_evidence_items_carry_exactly_the_r7_fields_in_order(data_dir):
    door, result = scored(facts(), vision=POOL_VISION)
    run([(door, result)], data_dir=data_dir)

    evidence = read_geojson(data_dir)["features"][0]["properties"]["evidence"]
    assert [item["sentence"] for item in evidence] == [item.sentence for item in result.evidence]
    assert [item["points"] for item in evidence] == [item.points for item in result.evidence]
    for item in evidence:
        assert set(item) == {"type", "points", "sentence", "source", "retrieved", "imagery"}
        assert item["retrieved"] == AS_OF.isoformat()


def test_evidence_points_still_sum_to_the_unclamped_total_in_the_artifact(data_dir):
    door, result = scored(facts(), vision=POOL_VISION)
    run([(door, result)], data_dir=data_dir)

    evidence = read_geojson(data_dir)["features"][0]["properties"]["evidence"]
    assert sum(item["points"] for item in evidence) == result.raw_total


def test_vision_evidence_keeps_its_reopenable_imagery_attachment(data_dir):
    run([scored(facts(), vision=POOL_VISION)], data_dir=data_dir)

    evidence = read_geojson(data_dir)["features"][0]["properties"]["evidence"]
    attachments = [item["imagery"] for item in evidence if item["imagery"] is not None]
    assert attachments, "a vision-derived evidence line must publish its frame"
    assert attachments[0]["image_url"] == IMAGE_REF
    assert set(attachments[0]) == {"image_url", "bbox", "model_confidence", "capture_date"}


def test_unscored_door_publishes_a_null_score_and_the_exclusion_reason(data_dir):
    """R9.4: the gap in "537 of 540" is clickable and explains itself."""
    run([unscored(facts(deed=None, yr_constr=0, net_value=0.0))], data_dir=data_dir)

    properties = read_geojson(data_dir)["features"][0]["properties"]
    assert properties["score"] is None
    assert properties["confidence"] is None
    assert properties["evidence"] == []
    assert properties["exclusion_reason"] == EXCLUSION_REASON


def test_the_exclusion_reason_is_the_wording_the_ui_ships():
    assert EXCLUSION_REASON == "parcel record incomplete in county data"


def test_a_door_with_no_geometry_is_published_with_null_geometry(data_dir):
    run([scored(facts(geometry=None, centroid=None))], data_dir=data_dir)

    features = read_geojson(data_dir)["features"]
    assert len(features) == 1, "a door with no polygon is still a door in the count"
    assert features[0]["geometry"] is None
    assert features[0]["properties"]["score"] is not None


def test_zero_doors_publishes_an_empty_but_valid_collection(data_dir):
    result = run([], data_dir=data_dir, report_=report(doors_total=0, doors_with_signal=0, coverage=0.0))

    assert read_geojson(data_dir) == {"type": "FeatureCollection", "features": []}
    assert read_manifest(data_dir)["doors_total"] == 0
    assert read_manifest(data_dir)["coverage"] == 0.0
    assert result.doors_total == 0
    assert rows(data_dir, "SELECT * FROM doors") == []


def test_publishing_twice_produces_identical_geojson_bytes(data_dir):
    pairs = [scored(facts("0248_01101_00003", lot="3")), unscored(facts("0248_01101_00004", lot="4"))]

    run(pairs, data_dir=data_dir)
    first = (data_dir / DOORS_GEOJSON_NAME).read_bytes()
    run(pairs, data_dir=data_dir)

    assert (data_dir / DOORS_GEOJSON_NAME).read_bytes() == first


# --- the SQLite db ------------------------------------------------------------


def test_sqlite_carries_a_doors_table_and_an_evidence_table(data_dir):
    run([scored(facts())], data_dir=data_dir)

    names = {
        row["name"]
        for row in rows(data_dir, "SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {"doors", "evidence"} <= names


def test_doors_table_holds_one_row_per_door_scored_or_not(data_dir):
    pairs = [
        scored(facts("0248_01101_00003", lot="3")),
        unscored(facts("0248_01101_00004", lot="4", deed=None, yr_constr=0, net_value=0.0)),
    ]
    run(pairs, data_dir=data_dir)

    by_pin = {row["pams_pin"]: row for row in rows(data_dir, "SELECT * FROM doors")}
    assert set(by_pin) == {"0248_01101_00003", "0248_01101_00004"}
    assert by_pin["0248_01101_00003"]["score"] == pairs[0][1].score
    assert by_pin["0248_01101_00003"]["confidence"] == pairs[0][1].confidence
    assert by_pin["0248_01101_00003"]["situs"] == pairs[0][0].situs
    assert by_pin["0248_01101_00003"]["exclusion_reason"] is None
    assert by_pin["0248_01101_00004"]["score"] is None
    assert by_pin["0248_01101_00004"]["exclusion_reason"] == EXCLUSION_REASON


def test_evidence_rows_reproduce_the_trail_in_order(data_dir):
    """`explain_score` reads this back, so the order the engine produced has to
    survive the round trip — hence an explicit sequence column."""
    door, result = scored(facts(), vision=POOL_VISION)
    run([(door, result)], data_dir=data_dir)

    trail = rows(
        data_dir,
        "SELECT * FROM evidence WHERE pams_pin = ? ORDER BY seq",
        door.pams_pin,
    )
    assert [row["sentence"] for row in trail] == [item.sentence for item in result.evidence]
    assert [row["points"] for row in trail] == [item.points for item in result.evidence]
    assert [row["type"] for row in trail] == [item.type for item in result.evidence]
    assert {row["retrieved"] for row in trail} == {AS_OF.isoformat()}


def test_an_unscored_door_contributes_no_evidence_rows(data_dir):
    door = facts(deed=None, yr_constr=0, net_value=0.0)
    run([unscored(door)], data_dir=data_dir)

    assert rows(data_dir, "SELECT * FROM evidence WHERE pams_pin = ?", door.pams_pin) == []


def test_republishing_replaces_rows_rather_than_appending(data_dir):
    pairs = [scored(facts(), vision=POOL_VISION)]

    run(pairs, data_dir=data_dir)
    before = (
        len(rows(data_dir, "SELECT * FROM doors")),
        len(rows(data_dir, "SELECT * FROM evidence")),
    )
    run(pairs, data_dir=data_dir)

    assert (
        len(rows(data_dir, "SELECT * FROM doors")),
        len(rows(data_dir, "SELECT * FROM evidence")),
    ) == before


# --- the run manifest ---------------------------------------------------------


def test_manifest_records_everything_needed_to_reproduce_the_run(data_dir):
    run([scored(facts())], data_dir=data_dir)

    payload = read_manifest(data_dir)
    assert payload["run_at"] == RUN_AT.isoformat()
    assert payload["as_of"] == AS_OF.isoformat()
    assert payload["territory_median_value"] == TERRITORY_MEDIAN_VALUE
    assert payload["acs_dual_income_threshold"] == ACS_DUAL_INCOME_THRESHOLD
    assert payload["retrieved"] == {name: day.isoformat() for name, day in RETRIEVED.items()}
    assert payload["cost_usd"] == 3.0
    assert isinstance(payload["code_version"], str) and payload["code_version"]


def test_manifest_counts_scored_and_unscored_doors_for_the_coverage_readout(data_dir):
    """"537 of 540 scored" — the unscored doors are counted, not dropped (R9.4)."""
    pairs = [
        scored(facts("0248_01101_00003", lot="3")),
        scored(facts("0248_01101_00005", lot="5")),
        scored(facts("0248_01101_00006", lot="6")),
        unscored(facts("0248_01101_00004", lot="4", deed=None, yr_constr=0, net_value=0.0)),
    ]
    result = run(pairs, data_dir=data_dir, report_=report(doors_total=4))

    payload = read_manifest(data_dir)
    assert (payload["doors_total"], payload["doors_scored"], payload["doors_unscored"]) == (4, 3, 1)
    assert payload["coverage"] == 0.75
    assert (result.doors_total, result.doors_scored, result.doors_unscored) == (4, 3, 1)


def test_manifest_amortises_the_run_cost_over_the_doors_scored(data_dir):
    pairs = [
        scored(facts("0248_01101_00003", lot="3")),
        scored(facts("0248_01101_00005", lot="5")),
        scored(facts("0248_01101_00006", lot="6")),
        unscored(facts("0248_01101_00004", lot="4")),
    ]
    run(pairs, data_dir=data_dir, manifest_=manifest(cost_usd=3.0))

    assert read_manifest(data_dir)["cost_per_door"] == pytest.approx(1.0)


def test_manifest_carries_the_resolve_report_numbers(data_dir):
    resolved = report(
        doors_total=1,
        doors_with_signal=1,
        coverage=1.0,
        permits_in_territory=10,
        permits_matched=10,
        permit_match_rate=1.0,
        acs_available=True,
    )
    run([scored(facts())], data_dir=data_dir, report_=resolved)

    block = read_manifest(data_dir)["resolve"]
    assert block["permit_match_rate"] == 1.0
    assert block["doors_with_signal"] == 1
    assert block["acs_available"] is True


def test_manifest_publishes_the_degradations_it_was_given(data_dir):
    reasons = ("CENSUS_API_KEY is not set", "municipal rental registration not obtained via OPRA")
    run([scored(facts())], data_dir=data_dir, manifest_=manifest(degradations=reasons))

    assert read_manifest(data_dir)["degradations"] == list(reasons)


# --- the MOD-IV deed vintage (T019) -------------------------------------------
#
# The Mover group is the highest-weighted signal in the model, and on the Ramsey
# extract it is structurally unearnable: the newest deed in the whole feed is
# roughly 20 months before `as_of`, so no door is inside the 90-day window. That
# is a property of the source, not a defect in the engine, and the manifest has
# to say which.
#
# The seam is split deliberately. `publish` *writes* what the run measured — the
# `deed_vintage` block — and copies `degradations` through verbatim; the sentence
# that says the Mover group could not fire is the pipeline's to record, because
# `manifest["degradations"] == list(result.degradations)` is a contract the
# pipeline suite already pins and a note invented here would break it.

#: The window the disclosure describes is the window the engine scores on. Read,
#: never re-typed, so the two cannot drift apart.
MOVER_WINDOW_DAYS = THRESHOLDS["mover_90d_days"]


def read_deed_vintage(data_dir):
    return read_manifest(data_dir)["deed_vintage"]


def test_manifest_publishes_the_deed_vintage_the_run_measured(data_dir):
    """Both numbers, under one named block: the newest deed anywhere in the
    municipal extract, and how many territory doors that leaves in the window."""
    run(
        [scored(facts())],
        data_dir=data_dir,
        manifest_=manifest(latest_deed_date=date(2024, 12, 6), doors_in_mover_window=0),
    )

    block = read_deed_vintage(data_dir)
    assert set(block) == {
        "latest_deed_date",
        "mover_window_days",
        "doors_in_mover_window",
        "doors_in_top_band",
        "top_band_days",
        "sales_register",
    }
    assert block["latest_deed_date"] == "2024-12-06"
    assert block["doors_in_mover_window"] == 0
    # No sales register was read on this run, and the block says so rather than
    # implying MOD-IV's date came from somewhere fresher.
    assert block["sales_register"] == {
        "latest_sale_date": None,
        "source_files": [],
        "doors_superseding_modiv": 0,
    }


def test_the_disclosed_mover_window_is_the_rule_it_describes(data_dir):
    """`90` is written once, in `THRESHOLDS`. A disclosure carrying its own copy
    would keep saying "90-day window" after the rule moved."""
    run(
        [scored(facts())],
        data_dir=data_dir,
        manifest_=manifest(latest_deed_date=date(2026, 6, 15), doors_in_mover_window=1),
    )

    block = read_deed_vintage(data_dir)
    assert block["mover_window_days"] == MOVER_WINDOW_DAYS
    assert block["doors_in_mover_window"] == 1


def test_an_extract_with_no_parseable_deed_publishes_a_null_vintage(data_dir):
    """Every deed null or unreadable is a real state — there is no latest date to
    report, and `null` says that where a fabricated date would not."""
    run(
        [scored(facts(deed=None))],
        data_dir=data_dir,
        manifest_=manifest(latest_deed_date=None, doors_in_mover_window=0),
    )

    block = read_deed_vintage(data_dir)
    assert block["latest_deed_date"] is None
    assert block["doors_in_mover_window"] == 0


def test_publish_copies_the_degradations_it_was_given_and_invents_none(data_dir):
    """The zero-mover note is recorded by the run, not synthesised at write time:
    a second copy made here would double it in the published manifest."""
    given = ("CENSUS_API_KEY is not set",)
    run(
        [scored(facts())],
        data_dir=data_dir,
        manifest_=manifest(
            degradations=given, latest_deed_date=date(2024, 12, 6), doors_in_mover_window=0
        ),
    )

    assert read_manifest(data_dir)["degradations"] == list(given)


# --- the vision stage's own state (T023) --------------------------------------
#
# The published run's Data & Ethics page printed **DECLINED** beside a vision
# stage that had answered 270 of 270 requests and left imagery evidence on 119 of
# its 540 doors. It had nothing better to go on: the manifest records a *sentence*
# ("31 vision answers could not be read as detections") and nothing else, so a
# reader — page or human — has to pattern-match prose to decide whether the stage
# refused, ran, or ran badly. Prose cannot carry that distinction, and the loss it
# describes has no denominator anywhere in the file.
#
# So the run states it. Three outcomes, told apart from the block alone:
#
#   ran clean      available=True,  declination_reason=None, answers_lost == 0
#   ran, lost some available=True,  declination_reason=None, 0 < answers_lost
#   declined       available=False, declination_reason=<the refusal>, no counts
#
# `answers_*` counts vision *requests* — one answer per request, 270 on the
# published run — and is what the run measured, so it is carried through
# unchanged. `doors_with_imagery` is counted here instead, off the features being
# written, exactly like `doors_scored`: it is a fact about the artifact, and a
# number counted from what was published cannot disagree with `doors.geojson`
# sitting beside it. Against the real run that rule yields 119, and `doors_total`
# is already published as its denominator.
#
# A run that never measured any of this publishes no block. An unmeasured stage
# reported as `available: true, answers_total: 0` would be a claim nobody made,
# on the same principle as the null `latest_deed_date` above.


def read_vision(data_dir):
    return read_manifest(data_dir).get("vision")


def imagery_doors(data_dir):
    """Published doors carrying at least one evidence line with a frame."""
    return [
        feature
        for feature in read_geojson(data_dir)["features"]
        if any(item["imagery"] for item in feature["properties"]["evidence"])
    ]


def test_manifest_publishes_the_vision_state_the_run_measured(data_dir):
    """The partial run — the state this ticket exists to make expressible."""
    run(
        [scored(facts(), vision=POOL_VISION)],
        data_dir=data_dir,
        manifest_=manifest(
            vision_available=True,
            vision_answers_total=270,
            vision_answers_lost=31,
            degradations=(
                "31 vision answers could not be read as detections; the doors they "
                "covered scored without their imagery signals",
            ),
        ),
    )

    block = read_vision(data_dir)
    assert set(block) == {
        "available",
        "declination_reason",
        "answers_total",
        "answers_lost",
        "doors_with_imagery",
    }
    assert block["available"] is True
    assert block["declination_reason"] is None
    assert (block["answers_total"], block["answers_lost"]) == (270, 31)


def test_a_partial_vision_run_is_not_published_as_a_declination(data_dir):
    """The bug in one assertion: a recorded parse-failure sentence must not turn
    a stage that answered into a stage that refused."""
    run(
        [scored(facts(), vision=POOL_VISION)],
        data_dir=data_dir,
        manifest_=manifest(
            vision_available=True,
            vision_answers_total=270,
            vision_answers_lost=31,
            degradations=("31 vision answers could not be read as detections",),
        ),
    )

    block = read_vision(data_dir)
    assert block["available"] is True
    assert block["declination_reason"] is None
    assert block["answers_lost"] > 0, "a partial loss that reports no loss is invisible"
    assert block["doors_with_imagery"] > 0, "this stage's evidence reached the map"


def test_a_clean_vision_run_reports_no_loss_to_disclaim(data_dir):
    run(
        [scored(facts(), vision=POOL_VISION)],
        data_dir=data_dir,
        manifest_=manifest(
            vision_available=True, vision_answers_total=270, vision_answers_lost=0
        ),
    )

    block = read_vision(data_dir)
    assert (block["available"], block["declination_reason"]) == (True, None)
    assert block["answers_lost"] == 0
    assert block["answers_total"] == 270


def test_a_declined_vision_stage_publishes_the_refusal_and_counts_nothing(data_dir):
    """No key, so nothing ran — and the refusal is printed, as it always was."""
    run(
        [scored(facts())],
        data_dir=data_dir,
        manifest_=manifest(
            vision_available=False,
            vision_declination_reason=VISION_NO_KEY_REASON,
            degradations=(VISION_NO_KEY_REASON,),
        ),
    )

    block = read_vision(data_dir)
    assert block["available"] is False
    assert block["declination_reason"] == VISION_NO_KEY_REASON
    assert (block["answers_total"], block["answers_lost"]) == (0, 0)
    assert block["doors_with_imagery"] == 0


def test_the_three_vision_outcomes_are_told_apart_by_the_block_alone(tmp_path):
    """No reader of this manifest should have to parse a sentence to know which
    of the three happened."""
    states = {}
    for name, fields in {
        "clean": dict(vision_available=True, vision_answers_total=270, vision_answers_lost=0),
        "partial": dict(vision_available=True, vision_answers_total=270, vision_answers_lost=31),
        "declined": dict(
            vision_available=False, vision_declination_reason=VISION_NO_KEY_REASON
        ),
    }.items():
        target = tmp_path / name
        run([scored(facts(), vision=POOL_VISION)], data_dir=target, manifest_=manifest(**fields))
        states[name] = read_vision(target)

    assert len({json.dumps(block, sort_keys=True) for block in states.values()}) == 3
    assert states["partial"] != states["declined"]
    assert states["partial"]["available"] is not states["declined"]["available"]
    assert states["partial"]["answers_lost"] != states["clean"]["answers_lost"]


def test_the_doors_carrying_imagery_are_counted_from_what_was_published(data_dir):
    """The numerator of "119 of 540", counted off the features rather than passed
    in: a hand-supplied count could disagree with `doors.geojson` next to it."""
    pairs = [
        scored(facts("0248_01101_00003", lot="3"), vision=POOL_VISION),
        scored(facts("0248_01101_00005", lot="5"), vision=POOL_VISION),
        scored(facts("0248_01101_00006", lot="6")),
    ]
    run(
        pairs,
        data_dir=data_dir,
        manifest_=manifest(
            vision_available=True, vision_answers_total=6, vision_answers_lost=1
        ),
    )

    payload = read_manifest(data_dir)
    assert payload["vision"]["doors_with_imagery"] == len(imagery_doors(data_dir)) == 2
    assert payload["doors_total"] == 3


def test_the_published_loss_is_quantifiable_without_inventing_a_denominator(data_dir):
    """Both fractions the page prints — 31 of 270 answers, 119 of 540 doors — are
    readable from this file and nowhere else."""
    run(
        [scored(facts(), vision=POOL_VISION)],
        data_dir=data_dir,
        manifest_=manifest(
            vision_available=True, vision_answers_total=270, vision_answers_lost=31
        ),
    )

    payload = read_manifest(data_dir)
    block = payload["vision"]
    assert 0 < block["answers_lost"] < block["answers_total"]
    assert 0 < block["doors_with_imagery"] <= payload["doors_total"]


def test_a_run_that_never_measured_the_vision_stage_claims_nothing(data_dir):
    """Every artifact published before this was measured, and every caller that
    still constructs a `RunManifest` without it."""
    run([scored(facts(), vision=POOL_VISION)], data_dir=data_dir)

    assert not read_vision(data_dir), "an unmeasured stage must not publish a state"


# --- the unscorable rule ------------------------------------------------------


@pytest.mark.parametrize(
    "deed, yr_constr, net_value, incomplete",
    [
        (date(2026, 6, 15), 1962, 980000.0, False),
        (None, 1962, 980000.0, False),
        (None, 0, 980000.0, False),
        (None, 1962, 0.0, False),
        (date(2026, 6, 15), 0, 0.0, False),
        (None, 0, 0.0, True),
    ],
    ids=[
        "complete",
        "no-deed",
        "value-only",
        "year-only",
        "deed-only",
        "nothing-at-all",
    ],
)
def test_a_record_is_incomplete_only_when_it_carries_no_scoreable_fact(
    deed, yr_constr, net_value, incomplete
):
    """One surviving fact is a degraded score (R6.1's job); none is no score."""
    door = facts(deed=deed, yr_constr=yr_constr, net_value=net_value)
    assert parcel_record_incomplete(door) is incomplete


def test_a_missing_polygon_does_not_make_a_record_incomplete():
    """Geometry is how a door is drawn, not what it is scored from."""
    assert parcel_record_incomplete(facts(geometry=None, centroid=None)) is False


# --- the identity guard -------------------------------------------------------

#: The parcel service's identity-bearing field names. `tests/` is exempt from
#: the repo-wide guard in `test_redaction.py`, so they may be named here — the
#: assertions above are structural allowlists precisely so `src/` never has to.
IDENTITY_TOKENS = ("OWNER_NAME", "ST_ADDRESS", "CITY_STATE")

#: Anything shaped like a person or a mailing destination has no business being
#: a key in a published artifact.
IDENTITY_KEY = re.compile(r"owner|mail|resident|occupant|tenant|phone|deed_book|deed_page", re.I)


def identity_hits(text):
    return [token for token in IDENTITY_TOKENS if token in text]


def every_key(payload):
    """Every mapping key anywhere in a decoded JSON document."""
    if isinstance(payload, dict):
        for key, value in payload.items():
            yield key
            yield from every_key(value)
    elif isinstance(payload, list):
        for item in payload:
            yield from every_key(item)


def sqlite_columns_and_values(data_dir):
    connection = sqlite3.connect(Path(data_dir) / SQLITE_NAME)
    try:
        columns, values = [], []
        tables = [
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        ]
        for table in tables:
            cursor = connection.execute(f"SELECT * FROM {table}")
            columns += [description[0] for description in cursor.description]
            values += [str(cell) for row in cursor.fetchall() for cell in row]
        return columns, values
    finally:
        connection.close()


@pytest.fixture
def published(data_dir):
    run(
        [
            scored(facts("0248_01101_00003", lot="3"), vision=POOL_VISION),
            unscored(facts("0248_01101_00004", lot="4", deed=None, yr_constr=0, net_value=0.0)),
        ],
        data_dir=data_dir,
        report_=report(doors_total=2, doors_with_signal=1, coverage=0.5),
    )
    return data_dir


def test_no_identity_shaped_key_appears_in_the_published_artifacts(published):
    keys = list(every_key(read_geojson(published))) + list(every_key(read_manifest(published)))
    columns, _ = sqlite_columns_and_values(published)

    offenders = [name for name in keys + columns if IDENTITY_KEY.search(name)]
    assert offenders == [], "published artifacts must carry no owner or mailing fields"


def test_no_identity_token_appears_in_any_published_value(published):
    text = (published / DOORS_GEOJSON_NAME).read_text(encoding="utf-8")
    _, values = sqlite_columns_and_values(published)

    assert identity_hits(text) == []
    assert [token for value in values for token in identity_hits(value)] == []


@pytest.mark.parametrize("token", IDENTITY_TOKENS)
def test_the_identity_scan_can_actually_fail(token):
    """A guard that never bites passes forever."""
    assert identity_hits(f'{{"properties": {{"{token}": "SOMEBODY"}}}}') == [token]
    assert IDENTITY_KEY.search("owner_name") is not None
