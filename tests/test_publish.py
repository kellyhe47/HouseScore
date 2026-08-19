"""Serving artifacts: doors.geojson + SQLite + run_manifest.json — V2 cutover
(ticket 103, plan R27/R28/R7/R37).

Three files come out of one call, and each answers a different question:

* `data/doors.geojson` is what the map renders and what the rep clicks. Every
  door in the territory appears exactly once — including the ones that could not
  be scored, because R9.4 makes them clickable and counts them in the "537 of
  540" readout. A door missing from the file is a door nobody can click.
* `data/houseaccount.sqlite` is what the MCP server reads (`get_door_score`,
  `explain_score`). Two tables, `doors` and `evidence`, joined on `PAMS_PIN`.
  The V1 `groups`/`raw_total` columns are GONE (R28); the doors table carries
  the V2 category fields instead.
* `data/run_manifest.json` is what makes the run reproducible: the once-per-run
  inputs, when each source was retrieved, what the run cost, which code produced
  it — and, new in V2, `score_contract_version` (R27/R28). The V1 fields
  `territory_median_value`, `top_band_days` and `doors_in_top_band` are gone.

**The seam.** `publish(scored, *, report, manifest, data_dir)` where `scored` is
a sequence of `(DoorFacts, envelope | None)` pairs in publication order and
`envelope` is the `score_door_v2` result mapping (the pipeline may have enriched
imagery-derived evidence entries with a re-openable `imagery` frame). Publish
re-derives nothing.

**What "unscored" means.** A `None` envelope is published as an exclusion via
`parcel_record_incomplete`: the county record carries none of the three parcel
facts a score could be built from. Anything less than that scores through V2's
data-gap path instead (typed gaps, `low` confidence at 2+ — R23/R37).

**Byte reproducibility (R28).** Publishing the same doors with the same
manifest into a fresh directory reproduces every artifact byte-identically.

No network, no fixtures on disk: every door below is synthetic and built
in-module through the real V2 engine, and every artifact is written under
`tmp_path`.
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
from houseaccount.scoring.v2 import V2Bundle, score_door_v2

AS_OF = date(2026, 8, 14)
RUN_AT = datetime(2026, 8, 14, 6, 30, 0, tzinfo=timezone.utc)

ACS_DUAL_INCOME_THRESHOLD = 0.35

#: V2's fresh-mover band (plan R4): the window `deed_vintage` discloses.
MOVER_WINDOW_DAYS = 90

#: The published per-door property allowlist (R11.1), now V2-shaped (R27/R30).
FEATURE_PROPERTIES = {
    "PAMS_PIN",
    "score",
    "confidence",
    "evidence",
    "situs",
    "exclusion_reason",
    "score_contract_version",
    "categories",
    "base",
    "mover",
    "mover_lift",
    "rental_modifier",
    "adjustment",
    "data_gaps",
}

#: V1 vocabulary that may appear nowhere in any published artifact (R28/R32).
FORBIDDEN_KEYS = {
    "groups",
    "raw_total",
    "territory_median_value",
    "top_band_days",
    "doors_in_top_band",
}

LON, LAT = -74.1560, 41.0447

RETRIEVED = {
    "parcel": date(2026, 8, 13),
    "permits": date(2026, 8, 13),
    "acs": date(2026, 8, 12),
}

#: A re-openable frame the pipeline attaches to imagery-derived evidence.
IMAGE_REF = "https://maps.nj.gov/arcgis/rest/services/Basemap/Orthos_Natural_2020_NJ_WM/MapServer/export?bbox=1"
IMAGERY_FRAME = {
    "image_url": IMAGE_REF,
    "bbox": [-8255000.0, 5030000.0, -8254880.0, 5030120.0],
    "model_confidence": 0.93,
    "capture_date": "2020-01-01",
}

# The vision run's declination sentence; inlined rather than imported so this
# module needs nothing from the vision stack.
VISION_NO_KEY_REASON_MATCH = "OPENAI_API_KEY"


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


def envelope_for(door, *, imagery_frame=None, pool=False):
    """The real V2 engine's envelope for `door` — never a stand-in (R27).

    When `pool` is set, a qualifying pool observation is scored, and when
    `imagery_frame` is also given, that evidence entry is enriched with the
    re-openable frame the way the pipeline does before publishing.
    """
    imagery = (
        {"available": True, "observations": [{"kind": "pool", "confidence": 0.93}]}
        if pool
        else {}
    )
    bundle = V2Bundle(
        parcel={
            "construction_year": door.yr_constr or None,
            "assessed_value": door.net_value or None,
            "lot_acres": door.calc_acre or None,
        },
        sales=(
            [{"date": door.deed_date.isoformat(), "price": door.sale_price, "sales_code": door.sales_code}]
            if door.deed_date is not None
            else []
        ),
        imagery=imagery,
    )
    result = dict(score_door_v2(bundle, AS_OF))
    if imagery_frame is not None:
        result["evidence"] = [
            {**item, "imagery": dict(imagery_frame)} if item["type"] == "fit_pool" else dict(item)
            for item in result["evidence"]
        ]
    return result


def scored(door, **envelope_kwargs):
    return (door, envelope_for(door, **envelope_kwargs))


def unscored(door):
    return (door, None)


def manifest(**overrides):
    """The V2 `RunManifest`: no `territory_median_value`, no `doors_in_top_band`
    (R28) — a caller passing either must be a TypeError, not a silent field."""
    fields = dict(
        run_at=RUN_AT,
        as_of=AS_OF,
        code_version="a1b2c3d",
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


def every_key(payload):
    """Every mapping key anywhere in a decoded JSON document."""
    if isinstance(payload, dict):
        for key, value in payload.items():
            yield key
            yield from every_key(value)
    elif isinstance(payload, list):
        for item in payload:
            yield from every_key(item)


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
def test_feature_properties_are_exactly_the_v2_allowlist(data_dir, pair_builder):
    """An exact set, not a superset: nothing reaches the browser by being copied
    wholesale off a record (R11.1) — and the set is the V2 one (R27/R30)."""
    run([pair_builder(facts())], data_dir=data_dir)

    properties = read_geojson(data_dir)["features"][0]["properties"]
    assert set(properties) == FEATURE_PROPERTIES


def test_every_published_door_names_the_score_contract_version(data_dir):
    """R27: mixed-version outputs must be rejectable, so every record — scored
    or excluded — says which contract produced the run."""
    pairs = [
        scored(facts("0248_01101_00003", lot="3")),
        unscored(facts("0248_01101_00004", lot="4", deed=None, yr_constr=0, net_value=0.0)),
    ]
    run(pairs, data_dir=data_dir)

    versions = {
        feature["properties"]["score_contract_version"]
        for feature in read_geojson(data_dir)["features"]
    }
    assert versions == {"v2"}


def test_scored_feature_carries_the_v2_envelope_fields(data_dir):
    door, envelope = scored(facts())
    run([(door, envelope)], data_dir=data_dir)

    properties = read_geojson(data_dir)["features"][0]["properties"]
    feature = read_geojson(data_dir)["features"][0]
    assert feature["geometry"] == door.geometry
    assert properties["score"] == envelope["score"]
    assert isinstance(properties["score"], int)
    assert properties["confidence"] == envelope["confidence"]
    assert properties["situs"] == door.situs
    assert properties["exclusion_reason"] is None
    assert properties["categories"] == dict(envelope["categories"])
    assert set(properties["categories"]) == {"project", "capacity", "fit"}
    assert properties["base"] == envelope["base"]
    assert properties["mover"] == dict(envelope["mover"])
    assert properties["mover_lift"] == pytest.approx(envelope["mover_lift"])
    assert properties["rental_modifier"] == envelope["rental_modifier"]
    assert properties["adjustment"] == pytest.approx(envelope["adjustment"])
    assert properties["data_gaps"] == list(envelope["data_gaps"])


def test_published_evidence_is_the_envelope_trail_in_order(data_dir):
    door, envelope = scored(facts(), pool=True)
    run([(door, envelope)], data_dir=data_dir)

    evidence = read_geojson(data_dir)["features"][0]["properties"]["evidence"]
    assert [item["type"] for item in evidence] == [item["type"] for item in envelope["evidence"]]
    assert [item["points"] for item in evidence] == pytest.approx(
        [item["points"] for item in envelope["evidence"]]
    )
    assert [item["reason"] for item in evidence] == [item["reason"] for item in envelope["evidence"]]
    for item in evidence:
        assert {"type", "points", "reason"} <= set(item) <= {
            "type",
            "points",
            "reason",
            "source",
            "retrieved",
            "imagery",
        }
        # V1's `sentence` field stays gone; `source`/`retrieved` returned by
        # request as the panel's attribution line.
        assert "sentence" not in item


def test_the_published_score_reconciles_per_r7(data_dir):
    """R7 on the artifact itself: capped category subtotals + mover lift +
    rental modifier + the rounding/clamp adjustment equal the published
    integer, and the evidence trail sums to the pre-rounding total."""
    door, envelope = scored(facts(), pool=True)
    run([(door, envelope)], data_dir=data_dir)

    properties = read_geojson(data_dir)["features"][0]["properties"]
    categories = properties["categories"]
    assert sum(categories.values()) == properties["base"]
    assert properties["score"] == pytest.approx(
        properties["base"]
        + properties["mover_lift"]
        + properties["rental_modifier"]
        + properties["adjustment"]
    )
    assert sum(item["points"] for item in properties["evidence"]) == pytest.approx(
        properties["base"] + properties["mover_lift"] + properties["rental_modifier"]
    )


def test_vision_evidence_keeps_its_reopenable_imagery_attachment(data_dir):
    run([scored(facts(), pool=True, imagery_frame=IMAGERY_FRAME)], data_dir=data_dir)

    evidence = read_geojson(data_dir)["features"][0]["properties"]["evidence"]
    attachments = [item.get("imagery") for item in evidence if item.get("imagery")]
    assert attachments, "an imagery-derived evidence line must publish its frame"
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
    assert properties["categories"] is None
    assert properties["data_gaps"] is None
    assert properties["score_contract_version"] == "v2"


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


def test_no_v1_vocabulary_survives_anywhere_in_the_published_json(data_dir):
    """R28/R32: `groups`, `raw_total` and the V1 manifest fields are gone from
    every key of every published JSON document."""
    run(
        [scored(facts()), unscored(facts("0248_01101_00004", lot="4", deed=None, yr_constr=0, net_value=0.0))],
        data_dir=data_dir,
        report_=report(doors_total=2),
    )

    keys = set(every_key(read_geojson(data_dir))) | set(every_key(read_manifest(data_dir)))
    assert keys & FORBIDDEN_KEYS == set()


# --- byte reproducibility (R28) ------------------------------------------------


def test_publishing_twice_produces_identical_geojson_bytes(data_dir):
    pairs = [scored(facts("0248_01101_00003", lot="3")), unscored(facts("0248_01101_00004", lot="4"))]

    run(pairs, data_dir=data_dir)
    first = (data_dir / DOORS_GEOJSON_NAME).read_bytes()
    run(pairs, data_dir=data_dir)

    assert (data_dir / DOORS_GEOJSON_NAME).read_bytes() == first


def test_the_same_inputs_reproduce_every_artifact_byte_identically(tmp_path):
    """R28: same sources, same as_of, fresh directory -> the same bytes, for
    all three artifacts. Hand-edited artifacts must fail this check."""
    pairs = [
        scored(facts("0248_01101_00003", lot="3"), pool=True, imagery_frame=IMAGERY_FRAME),
        unscored(facts("0248_01101_00004", lot="4", deed=None, yr_constr=0, net_value=0.0)),
    ]
    a, b = tmp_path / "a", tmp_path / "b"
    run(pairs, data_dir=a, report_=report(doors_total=2))
    run(pairs, data_dir=b, report_=report(doors_total=2))

    for name in (DOORS_GEOJSON_NAME, SQLITE_NAME, RUN_MANIFEST_NAME):
        assert (a / name).read_bytes() == (b / name).read_bytes(), name


# --- the SQLite db ------------------------------------------------------------


def test_sqlite_carries_a_doors_table_and_an_evidence_table(data_dir):
    run([scored(facts())], data_dir=data_dir)

    names = {
        row["name"]
        for row in rows(data_dir, "SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {"doors", "evidence"} <= names


def test_the_doors_table_has_the_v2_schema_and_no_v1_columns(data_dir):
    """R28: `groups`/`raw_total` replaced by the V2 category fields."""
    run([scored(facts())], data_dir=data_dir)

    columns = {row["name"] for row in rows(data_dir, "PRAGMA table_info(doors)")}
    assert {
        "pams_pin",
        "score",
        "confidence",
        "situs",
        "exclusion_reason",
        "score_contract_version",
        "project",
        "capacity",
        "fit",
        "base",
        "mover_strength",
        "mover_lift",
        "rental_modifier",
        "adjustment",
    } <= columns
    assert "groups" not in columns
    assert "raw_total" not in columns

    evidence_columns = {row["name"] for row in rows(data_dir, "PRAGMA table_info(evidence)")}
    assert {"pams_pin", "seq", "type", "points", "reason"} <= evidence_columns
    assert "sentence" not in evidence_columns


def test_doors_rows_carry_the_v2_arithmetic(data_dir):
    pairs = [
        scored(facts("0248_01101_00003", lot="3")),
        unscored(facts("0248_01101_00004", lot="4", deed=None, yr_constr=0, net_value=0.0)),
    ]
    run(pairs, data_dir=data_dir)
    door, envelope = pairs[0]

    by_pin = {row["pams_pin"]: row for row in rows(data_dir, "SELECT * FROM doors")}
    assert set(by_pin) == {"0248_01101_00003", "0248_01101_00004"}
    row = by_pin["0248_01101_00003"]
    assert row["score"] == envelope["score"]
    assert row["confidence"] == envelope["confidence"]
    assert row["situs"] == door.situs
    assert row["exclusion_reason"] is None
    assert row["score_contract_version"] == "v2"
    assert row["project"] == envelope["categories"]["project"]
    assert row["capacity"] == envelope["categories"]["capacity"]
    assert row["fit"] == envelope["categories"]["fit"]
    assert row["base"] == envelope["base"]
    assert row["mover_strength"] == pytest.approx(envelope["mover"]["strength"])
    assert row["mover_lift"] == pytest.approx(envelope["mover_lift"])
    assert row["rental_modifier"] == envelope["rental_modifier"]
    assert row["adjustment"] == pytest.approx(envelope["adjustment"])

    excluded = by_pin["0248_01101_00004"]
    assert excluded["score"] is None
    assert excluded["exclusion_reason"] == EXCLUSION_REASON
    assert excluded["score_contract_version"] == "v2"


def test_evidence_rows_reproduce_the_trail_in_order(data_dir):
    """`explain_score` reads this back, so the order the engine produced has to
    survive the round trip — hence an explicit sequence column."""
    door, envelope = scored(facts(), pool=True)
    run([(door, envelope)], data_dir=data_dir)

    trail = rows(
        data_dir,
        "SELECT * FROM evidence WHERE pams_pin = ? ORDER BY seq",
        door.pams_pin,
    )
    assert [row["type"] for row in trail] == [item["type"] for item in envelope["evidence"]]
    assert [row["points"] for row in trail] == pytest.approx(
        [item["points"] for item in envelope["evidence"]]
    )
    assert [row["reason"] for row in trail] == [item["reason"] for item in envelope["evidence"]]


def test_an_unscored_door_contributes_no_evidence_rows(data_dir):
    door = facts(deed=None, yr_constr=0, net_value=0.0)
    run([unscored(door)], data_dir=data_dir)

    assert rows(data_dir, "SELECT * FROM evidence WHERE pams_pin = ?", door.pams_pin) == []


def test_republishing_replaces_rows_rather_than_appending(data_dir):
    pairs = [scored(facts(), pool=True)]

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
    assert payload["score_contract_version"] == "v2"
    assert payload["acs_dual_income_threshold"] == ACS_DUAL_INCOME_THRESHOLD
    assert payload["retrieved"] == {name: day.isoformat() for name, day in RETRIEVED.items()}
    assert payload["cost_usd"] == 3.0
    assert isinstance(payload["code_version"], str) and payload["code_version"]


def test_manifest_drops_the_v1_fields(data_dir):
    """R28, the negative half: the V1 fields are absent, not null."""
    run([scored(facts())], data_dir=data_dir)

    payload = read_manifest(data_dir)
    assert "territory_median_value" not in payload
    for key in ("top_band_days", "doors_in_top_band"):
        assert key not in set(every_key(payload)), key


def test_the_run_manifest_dataclass_no_longer_accepts_the_v1_fields():
    """The field is gone from the seam, not just from the JSON: a caller still
    measuring the V1 inputs must fail loudly at construction time."""
    with pytest.raises(TypeError):
        manifest(territory_median_value=700000.0)
    with pytest.raises(TypeError):
        manifest(doors_in_top_band=0)


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


# --- the deed vintage disclosure, V2-shaped -------------------------------------
#
# The vintage disclosure survives the cutover — a reader still needs to tell "no
# movers here right now" from "the mover rule is broken" — but the V1 top-band
# fields (`top_band_days`, `doors_in_top_band`) and the 90-day-cutoff degradation
# prose are gone (R28): V2's mover strength decays to 365 days, so "newest deed
# outside 90 days" no longer means the signal cannot fire.


def read_deed_vintage(data_dir):
    return read_manifest(data_dir)["deed_vintage"]


def test_manifest_publishes_the_v2_deed_vintage_block(data_dir):
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
        "sales_register",
    }
    assert block["latest_deed_date"] == "2024-12-06"
    assert block["mover_window_days"] == MOVER_WINDOW_DAYS
    assert block["doors_in_mover_window"] == 0
    assert block["sales_register"] == {
        "latest_sale_date": None,
        "source_files": [],
        "doors_superseding_modiv": 0,
    }


def test_an_extract_with_no_parseable_deed_publishes_a_null_vintage(data_dir):
    run(
        [scored(facts(deed=None))],
        data_dir=data_dir,
        manifest_=manifest(latest_deed_date=None, doors_in_mover_window=0),
    )

    block = read_deed_vintage(data_dir)
    assert block["latest_deed_date"] is None
    assert block["doors_in_mover_window"] == 0


def test_publish_copies_the_degradations_it_was_given_and_invents_none(data_dir):
    given = ("CENSUS_API_KEY is not set",)
    run(
        [scored(facts())],
        data_dir=data_dir,
        manifest_=manifest(
            degradations=given, latest_deed_date=date(2024, 12, 6), doors_in_mover_window=0
        ),
    )

    assert read_manifest(data_dir)["degradations"] == list(given)


def test_no_ninety_day_cutoff_prose_survives_in_the_manifest(data_dir):
    """R28: the V1 "mover group could not fire / 90-day window" sentence is a
    claim about a deleted rule, and publish must not synthesise it."""
    run(
        [scored(facts())],
        data_dir=data_dir,
        manifest_=manifest(latest_deed_date=date(2024, 12, 6), doors_in_mover_window=0),
    )

    text = json.dumps(read_manifest(data_dir)["degradations"])
    assert not re.search(r"90-day|could not fire|top band|top-band", text, re.I)


# --- the vision stage's own state ----------------------------------------------
#
# Unchanged by the cutover: three outcomes, told apart from the block alone.
#
#   ran clean      available=True,  declination_reason=None, answers_lost == 0
#   ran, lost some available=True,  declination_reason=None, 0 < answers_lost
#   declined       available=False, declination_reason=<the refusal>, no counts


def read_vision(data_dir):
    return read_manifest(data_dir).get("vision")


def imagery_doors(data_dir):
    """Published doors carrying at least one evidence line with a frame."""
    return [
        feature
        for feature in read_geojson(data_dir)["features"]
        if any(item.get("imagery") for item in feature["properties"]["evidence"])
    ]


def test_manifest_publishes_the_vision_state_the_run_measured(data_dir):
    run(
        [scored(facts(), pool=True, imagery_frame=IMAGERY_FRAME)],
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
    run(
        [scored(facts(), pool=True, imagery_frame=IMAGERY_FRAME)],
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


def test_a_declined_vision_stage_publishes_the_refusal_and_counts_nothing(data_dir):
    reason = "OPENAI_API_KEY is not set; the vision stage was skipped"
    run(
        [scored(facts())],
        data_dir=data_dir,
        manifest_=manifest(
            vision_available=False,
            vision_declination_reason=reason,
            degradations=(reason,),
        ),
    )

    block = read_vision(data_dir)
    assert block["available"] is False
    assert block["declination_reason"] == reason
    assert (block["answers_total"], block["answers_lost"]) == (0, 0)
    assert block["doors_with_imagery"] == 0


def test_the_doors_carrying_imagery_are_counted_from_what_was_published(data_dir):
    pairs = [
        scored(facts("0248_01101_00003", lot="3"), pool=True, imagery_frame=IMAGERY_FRAME),
        scored(facts("0248_01101_00005", lot="5"), pool=True, imagery_frame=IMAGERY_FRAME),
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


def test_a_run_that_never_measured_the_vision_stage_claims_nothing(data_dir):
    run([scored(facts(), pool=True)], data_dir=data_dir)

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
    """One surviving fact is a degraded score (V2's data-gap path); none is no
    score at all."""
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
            scored(facts("0248_01101_00003", lot="3"), pool=True, imagery_frame=IMAGERY_FRAME),
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
