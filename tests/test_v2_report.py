"""Ticket 104 / R34: the V2 recalculation report, PII scan and `make eval`.

Pinned report key schema (eval/report.json, V2 shape):

    score_contract_version   "v2"
    as_of                    manifest as_of (str, YYYY-MM-DD)
    golden.fixtures_total    42
    coverage.doors_total     540
    coverage.doors_scored    540
    coverage.coverage        1.0-ish float
    categories.{project,capacity,fit}.{min,max,mean}
    score_distribution.{min,max,mean}
    score_distribution.quantiles.{q10..q90}   # deciles, for the R38 map ramp
    missing_signals.{<gap type>: count}       # from published data_gaps
    source_freshness.{parcel,permits,acs,sales}  # manifest retrieved dates
    exclusions.stale_open_neutralized         # published evidence counts,
    exclusions.non_arms_length_sales          #   not re-derivation
    exclusions.voided_or_admin
    exclusions.cross_source_dedup_collapsed
    vision.{precision,recall,hallucination_rate,cost_per_door,
            metrics_source,banner}            # banner mandatory when no
                                              # hand labels: NOT-A-MEASUREMENT
    pii_scan.{ok,findings}

`build_report(data_dir, out_path) -> dict` writes out_path and returns the
dict. PII behavior pinned: build_report raises PIIViolationError when a
published artifact carries an owner-name / mailing-address / permit-agent
identity field — that is what makes the eval entrypoint exit nonzero.
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
from pathlib import Path

import pytest

from eval.v2.report import PIIViolationError, build_report

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data"

REQUIRED_ARTIFACTS = ("doors.geojson", "houseaccount.sqlite", "run_manifest.json")

needs_published_run = pytest.mark.skipif(
    not all((DATA / name).exists() for name in REQUIRED_ARTIFACTS),
    reason="published 540-door V2 run artifacts not present in data/",
)

DECILE_KEYS = [f"q{n}" for n in range(10, 100, 10)]  # q10..q90


@pytest.fixture()
def report(tmp_path):
    out = tmp_path / "report.json"
    result = build_report(DATA, out)
    return result, out


# ---------------------------------------------------------------- content


@needs_published_run
class TestReportContent:
    def test_envelope(self, report):
        rep, out = report
        assert rep["score_contract_version"] == "v2"
        manifest = json.loads((DATA / "run_manifest.json").read_text())
        assert rep["as_of"] == manifest["as_of"]
        assert rep["golden"]["fixtures_total"] == 42
        assert out.exists()
        assert json.loads(out.read_text()) == rep

    def test_coverage_540(self, report):
        rep, _ = report
        cov = rep["coverage"]
        assert cov["doors_total"] == 540
        assert cov["doors_scored"] == 540
        assert cov["coverage"] == pytest.approx(1.0)

    def test_category_distributions(self, report):
        rep, _ = report
        for cat in ("project", "capacity", "fit"):
            dist = rep["categories"][cat]
            assert {"min", "max", "mean"} <= set(dist)
            assert dist["min"] <= dist["mean"] <= dist["max"]

    def test_score_distribution_deciles_for_r38_ramp(self, report):
        rep, _ = report
        dist = rep["score_distribution"]
        assert {"min", "max", "mean"} <= set(dist)
        q = dist["quantiles"]
        assert list(q) == DECILE_KEYS  # pinned key set AND order
        values = [q[k] for k in DECILE_KEYS]
        assert values == sorted(values)  # monotone, usable as ramp stops
        assert dist["min"] <= values[0] and values[-1] <= dist["max"]

    def test_missing_signal_counts_by_gap_type(self, report):
        rep, _ = report
        gaps = rep["missing_signals"]
        # The rental registry is OPRA-only and absent from every live run, so
        # its gap is on all 540 doors; the other counts vary with which keys
        # and caches the run had, and are asserted only to be well-formed.
        assert gaps["rental_data_missing"] == 540
        for key, count in gaps.items():
            assert isinstance(count, int) and 0 <= count <= 540, key

    def test_source_freshness_from_manifest(self, report):
        rep, _ = report
        manifest = json.loads((DATA / "run_manifest.json").read_text())
        assert rep["source_freshness"] == manifest["retrieved"]
        for value in rep["source_freshness"].values():
            assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)

    def test_exclusion_counts_from_published_evidence(self, report):
        """Counts come from the published evidence table / manifest, not a
        re-derivation of the rules — so they must equal what is published."""
        rep, _ = report
        excl = rep["exclusions"]
        db = sqlite3.connect(DATA / "houseaccount.sqlite")
        try:
            stale_open = db.execute(
                "select count(*) from evidence where type='project_neutralized'"
            ).fetchone()[0]
            non_arms = db.execute(
                "select count(*) from evidence where type='mover_invalid_sale'"
            ).fetchone()[0]
        finally:
            db.close()
        assert excl["stale_open_neutralized"] == stale_open
        assert excl["non_arms_length_sales"] == non_arms
        # Published-or-zero; the keys exist per conservative rule regardless.
        assert isinstance(excl["voided_or_admin"], int)
        assert excl["voided_or_admin"] >= 0
        assert isinstance(excl["cross_source_dedup_collapsed"], int)
        assert excl["cross_source_dedup_collapsed"] >= 0

    def test_vision_block_retained_with_banner(self, report):
        """PRD R5.1 metrics survive the V1 harness deletion, and the
        honest-gap banner survives with them: when the numbers are not
        measured over hand labels, the report says so, loudly."""
        rep, _ = report
        vision = rep["vision"]
        for key in ("precision", "recall", "hallucination_rate", "cost_per_door"):
            assert isinstance(vision[key], (int, float)), key
        assert isinstance(vision["metrics_source"], str) and vision["metrics_source"]
        if vision["metrics_source"] != "hand labels":
            assert "MEASUREMENT" in vision["banner"].upper()
            assert "NOT" in vision["banner"].upper() or "NO " in vision["banner"].upper()

    def test_pii_scan_block_clean_run(self, report):
        rep, _ = report
        assert rep["pii_scan"]["ok"] is True
        assert rep["pii_scan"]["findings"] == []


# ------------------------------------------------------------ determinism


@needs_published_run
def test_report_is_deterministic(tmp_path):
    out_a = tmp_path / "a.json"
    out_b = tmp_path / "b.json"
    rep_a = build_report(DATA, out_a)
    rep_b = build_report(DATA, out_b)
    assert rep_a == rep_b
    assert out_a.read_bytes() == out_b.read_bytes()


# --------------------------------------------------------------- PII scan


@needs_published_run
def test_planted_owner_name_fails_the_eval(tmp_path):
    """Plant an owner-name field in a tmp copy of the published artifacts:
    build_report must raise PIIViolationError (which the eval entrypoint
    turns into a nonzero exit)."""
    planted = tmp_path / "data"
    planted.mkdir()
    for name in REQUIRED_ARTIFACTS:
        shutil.copy(DATA / name, planted / name)
    geo = json.loads((planted / "doors.geojson").read_text())
    geo["features"][0]["properties"]["owner_name"] = "JANE Q PUBLIC"
    (planted / "doors.geojson").write_text(json.dumps(geo))

    with pytest.raises(PIIViolationError):
        build_report(planted, tmp_path / "report.json")


# ------------------------------------------------------- make eval wiring


def test_make_eval_runs_golden_and_report():
    """Content pin: the Makefile `eval` target runs the 42-fixture golden
    suite AND the R34 report builder — no longer the bare test-golden alias."""
    makefile = (REPO / "Makefile").read_text()
    match = re.search(r"^eval:(?P<deps>[^\n]*)\n(?P<recipe>(?:\t[^\n]*\n?)*)",
                      makefile, re.MULTILINE)
    assert match, "Makefile has no eval target"
    block = match.group("deps") + "\n" + match.group("recipe")
    assert "test-golden" in block
    assert "eval.v2.report" in block or "eval/v2/report" in block
