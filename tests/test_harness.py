"""The `make eval` harness (T008) — R5.1 and R14.

`tests/test_scoring_fixtures.py` proves the *engine* reproduces the golden
fixtures. This module proves the *harness* runs them through that engine and
reports the result honestly: fixture pass count, vision precision/recall,
hallucination rate, cost per door, entity-resolution match rate — printed and
written to `eval/report.json` for the Data & Ethics page (T014) to consume.

Pinned seams (the implementer must satisfy exactly):

    from eval.harness import (
        FROZEN_SOURCE, LABELS_SOURCE, MATCH_RATE_FLOOR,
        EvalReport, FixtureFailure, LabelFormatError,
        run_eval, main,
    )

    FROZEN_SOURCE  = "frozen fixture 09 (hand labels not yet collected)"
    LABELS_SOURCE  = "hand labels"
    MATCH_RATE_FLOOR = 0.95

    run_eval(*, golden_dir: Path | None = None,   # default eval/golden
                labels_dir: Path | None = None,   # default eval/labels
                resolve_report=None,              # ResolveReport | Mapping | None
                ledger: CostLedger | None = None,
                doors_scored: int = 0) -> EvalReport
        Pure: computes, never writes. Raises LabelFormatError (a ValueError)
        on a malformed label file rather than silently falling back to frozen.

    EvalReport (frozen dataclass) fields:
        fixtures_total: int
        fixtures_passed: int
        fixture_failures: tuple[FixtureFailure, ...]   # .name, .detail
        precision: float
        recall: float
        hallucination_rate: float
        metrics_source: str
        cost_total_usd: float
        doors_scored: int
        cost_per_door: float
        resolve_match_rate: float | None    # the *municipal* rate (see below)
        ok: bool
      methods:
        .as_dict() -> dict          # exactly the JSON written to eval/report.json
        .render()  -> str           # the printed report

**Which match rate the >=0.95 gate reads.** `ResolveReport` carries two, and
they are different numbers: `permit_match_rate` is territory-scoped (its
denominator is in-window permits on a block the territory occupies, which
includes the many municipal parcels the territory does not hold) and
`municipal_match_rate` is in-window permits joining any municipal parcel over
*all* in-window permits. R3.2 grades the municipal one, so that is the field
`run_eval` reads off the report — from the dataclass or from the mapping the
pipeline writes to JSON. A report carrying only the territory rate is treated
as no rate at all rather than being silently graded on the wrong denominator.

    main(argv: Sequence[str] | None = None) -> int
        Flags: --golden-dir --labels-dir --report --resolve-report --ledger
               --doors-scored.  Prints render(), writes as_dict() as JSON to
               --report (default eval/report.json) *even when the run fails*,
               returns 0 iff report.ok.

Pinned render contract: every metric appears on its own line as
`<label>: <value>` (a leading `$` is allowed), so the numbers are machine
checkable without pinning the surrounding prose. The only prose pinned
verbatim is the required `source: frozen fixture 09 (hand labels not yet
collected)` line.

Pinned hand-label format for `eval/labels/*.json` (the implementer documents
it in `eval/labels/README.md`) — every `*.json` file in the directory merges:

    {"signal": "pool",
     "labels": [
       {"pams_pin": "0345_00012_00003", "image_ref": "nj2020/t1.jpg",
        "signal": "pool", "actual": true, "predicted": true, "probe": false}
     ]}

`probe: true` (optional, default false) marks membership in the verified-
negative hallucination universe and requires `actual: false`. Precision and
recall are computed over the non-probe labels; the hallucination rate is
`count(probe and predicted) / count(probe)` — its own universe, never the
P/R one.

No network, no model calls: every metric here is arithmetic over frozen counts.
"""

import copy
import json
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
GOLDEN = REPO / "eval" / "golden"

# `python -m pytest` puts the repo root on sys.path; a bare `pytest` does not,
# and `eval` is a top-level package, not part of `src`.
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from houseaccount.cost import CostLedger  # noqa: E402
from houseaccount.resolve import ResolveReport  # noqa: E402
from houseaccount.scoring.weights import WEIGHTS  # noqa: E402

from eval.harness import (  # noqa: E402
    FROZEN_SOURCE,
    LABELS_SOURCE,
    MATCH_RATE_FLOOR,
    LabelFormatError,
    main,
    run_eval,
)

GOLDEN_FILES = sorted(GOLDEN.glob("*.json"))
FIXTURE_COUNT = len(GOLDEN_FILES)

assert FIXTURE_COUNT == 13, f"expected 13 golden fixtures, found {FIXTURE_COUNT}"

AS_OF = date(2026, 8, 1)

REQUIRED_JSON_KEYS = {
    "fixtures_total",
    "fixtures_passed",
    "fixture_failures",
    "precision",
    "recall",
    "hallucination_rate",
    "metrics_source",
    "cost_total_usd",
    "doors_scored",
    "cost_per_door",
    "resolve_match_rate",
    "resolve_match_rate_threshold",
    "ok",
}


# --- helpers ----------------------------------------------------------------


def golden_dir_with(tmp_path, name, mutate):
    """A copy of the real golden dir with one fixture deliberately broken.

    The real fixtures are never touched — they are the spec.
    """
    out = tmp_path / "golden"
    out.mkdir()
    for path in GOLDEN_FILES:
        fixture = json.loads(path.read_text(encoding="utf-8"))
        if fixture["name"] == name:
            fixture = mutate(copy.deepcopy(fixture))
        out.joinpath(path.name).write_text(json.dumps(fixture), encoding="utf-8")
    return out


def only_fixture(tmp_path, fixture, filename="99_case.json"):
    """A golden dir holding exactly one hand-built fixture."""
    out = tmp_path / "golden-one"
    out.mkdir()
    out.joinpath(filename).write_text(json.dumps(fixture), encoding="utf-8")
    return out


def deed_parse_fixture(as_of, cases):
    """Fixture 11's shape, with our own as_of and cases."""
    return {
        "name": "deed_date_yymmdd_parse",
        "given": {"as_of": as_of, "cases": cases},
        "expect": {"all_cases_pass": True},
    }


def label(index, actual, predicted, probe=False):
    return {
        "pams_pin": f"0345_00012_{index:05d}",
        "image_ref": f"nj2020/tile-{index}.jpg",
        "signal": "pool",
        "actual": actual,
        "predicted": predicted,
        "probe": probe,
    }


def write_labels(tmp_path, labels, name="pool.json"):
    out = tmp_path / "labels"
    out.mkdir(exist_ok=True)
    out.joinpath(name).write_text(
        json.dumps({"signal": "pool", "labels": labels}), encoding="utf-8"
    )
    return out


#: 4 TP / 1 FP / 1 FN / 4 TN -> P 0.8, R 0.8; probe 10 with 2 claimed -> H 0.2.
#: Deliberately different from fixture 09's numbers so "came from the labels"
#: is observable rather than a coincidence.
HAND_LABELS = (
    [label(i, True, True) for i in range(4)]
    + [label(10, False, True)]
    + [label(11, True, False)]
    + [label(20 + i, False, False) for i in range(4)]
    + [label(30 + i, False, i < 2, probe=True) for i in range(10)]
)


def rendered_value(text, label_text):
    """The number on the `<label>: <value>` line. See the render contract."""
    match = re.search(
        re.escape(label_text) + r"\s*:\s*\$?\s*(-?\d+(?:\.\d+)?)", text, re.IGNORECASE
    )
    assert match, f"no `{label_text}: <number>` line in:\n{text}"
    return float(match.group(1))


def run_module(*args):
    """`python -m eval.harness` exactly as `make eval` invokes it."""
    env = dict(os.environ, PYTHONPATH="src")
    return subprocess.run(
        [str(REPO / ".venv" / "bin" / "python"), "-m", "eval.harness", *args],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def report_path(tmp_path):
    """Never the repo's real eval/report.json."""
    return tmp_path / "report.json"


# --- all 12 fixtures, through the real engine -------------------------------


def test_healthy_repo_passes_every_golden_fixture():
    report = run_eval()
    assert report.fixtures_total == FIXTURE_COUNT == 13
    assert report.fixtures_passed == 13
    assert tuple(report.fixture_failures) == ()
    assert report.ok is True


def test_scored_fixtures_run_through_the_real_engine(monkeypatch):
    """Bend the engine's weights table and the harness must notice.

    A harness that re-implemented the arithmetic (the way `verify_claims.py`
    does) would keep reporting a full pass count here.
    """
    monkeypatch.setitem(WEIGHTS, "mover_30d", 3)
    report = run_eval()
    assert report.fixtures_passed < report.fixtures_total
    assert report.ok is False


@pytest.mark.parametrize(
    "as_of, raw, expect_iso, should_pass, why",
    [
        ("2026-08-01", "080122", "2008-01-22", True, "fixture 11's own case"),
        ("2026-08-01", "280101", "1928-01-01", True, "pivot 27 at as_of 2026"),
        ("2030-01-01", "280101", "2028-01-01", True, "pivot moves with as_of"),
        ("2030-01-01", "280101", "1928-01-01", False, "a hardcoded pivot 27"),
        ("2026-08-01", "9903", None, True, "malformed -> None, not a crash"),
        ("2026-08-01", "990315", "2099-03-15", False, "wrong century"),
    ],
)
def test_deed_parse_cases_run_through_the_real_normalizer(
    tmp_path, as_of, raw, expect_iso, should_pass, why
):
    """Fixture 11 is exercised, `as_of` and all, not assumed."""
    golden = only_fixture(
        tmp_path, deed_parse_fixture(as_of, [{"raw": raw, "expect_iso": expect_iso}])
    )
    report = run_eval(golden_dir=golden)
    assert report.fixtures_passed == (1 if should_pass else 0), why
    assert report.ok is should_pass


@pytest.mark.parametrize(
    "name, mutate, why",
    [
        (
            "new_mover_high_score",
            lambda f: {**f, "expect": {**f["expect"], "score": 7}},
            "a scored fixture that no longer reproduces",
        ),
        (
            "vision_eval_contract",
            lambda f: {**f, "expect": {**f["expect"], "precision": 0.5}},
            "fixture 09's metric arithmetic",
        ),
        (
            "deed_date_yymmdd_parse",
            lambda f: {
                **f,
                "given": {
                    **f["given"],
                    "cases": [{"raw": "080122", "expect_iso": "1908-01-22"}],
                },
            },
            "fixture 11's parse cases",
        ),
    ],
)
def test_a_broken_expectation_is_reported_as_a_failure(tmp_path, name, mutate, why):
    report = run_eval(golden_dir=golden_dir_with(tmp_path, name, mutate))
    assert report.fixtures_total == 13
    assert report.fixtures_passed == FIXTURE_COUNT - 1, why
    assert [f.name for f in report.fixture_failures] == [name]
    assert report.fixture_failures[0].detail, "a failure must explain itself"
    assert report.ok is False


# --- fixture 09: precision / recall / hallucination -------------------------


def test_frozen_fixture_09_metrics_reproduce_within_tolerance():
    report = run_eval()
    assert report.precision == pytest.approx(0.818, abs=0.001)
    assert report.recall == pytest.approx(0.9, abs=0.001)
    assert report.hallucination_rate == pytest.approx(0.05, abs=0.001)


def test_hallucination_denominator_is_its_own_twenty_image_universe():
    report = run_eval()
    assert report.hallucination_rate == pytest.approx(1 / 20, abs=1e-9)
    assert report.hallucination_rate != pytest.approx(1 / 40, abs=1e-9)


# --- hand labels vs the frozen set ------------------------------------------


def test_frozen_source_line_states_the_labels_do_not_exist_yet():
    assert FROZEN_SOURCE == "frozen fixture 09 (hand labels not yet collected)"
    report = run_eval(labels_dir=None)
    assert report.metrics_source == FROZEN_SOURCE
    assert f"source: {FROZEN_SOURCE}" in report.render()


def test_an_empty_labels_dir_still_falls_back_to_the_frozen_set(tmp_path):
    empty = tmp_path / "labels"
    empty.mkdir()
    report = run_eval(labels_dir=empty)
    assert report.metrics_source == FROZEN_SOURCE
    assert report.precision == pytest.approx(0.818, abs=0.001)


def test_hand_labels_replace_the_frozen_metrics(tmp_path):
    report = run_eval(labels_dir=write_labels(tmp_path, HAND_LABELS))
    assert report.metrics_source == LABELS_SOURCE
    assert report.precision == pytest.approx(0.8, abs=0.001)
    assert report.recall == pytest.approx(0.8, abs=0.001)
    assert report.hallucination_rate == pytest.approx(0.2, abs=0.001)
    assert FROZEN_SOURCE not in report.render()


def test_labels_merge_across_files(tmp_path):
    labels_dir = write_labels(tmp_path, HAND_LABELS[:5], name="pool-a.json")
    write_labels(tmp_path, HAND_LABELS[5:], name="pool-b.json")
    report = run_eval(labels_dir=labels_dir)
    assert report.metrics_source == LABELS_SOURCE
    assert report.precision == pytest.approx(0.8, abs=0.001)
    assert report.hallucination_rate == pytest.approx(0.2, abs=0.001)


@pytest.mark.parametrize(
    "payload, why",
    [
        ("{not json", "unparseable file"),
        (
            json.dumps({"signal": "pool", "labels": [{"pams_pin": "x", "actual": True}]}),
            "a label with no prediction",
        ),
        (
            json.dumps(
                {
                    "signal": "pool",
                    "labels": [dict(label(1, True, True), probe=True)],
                }
            ),
            "a probe label whose ground truth is positive",
        ),
    ],
)
def test_a_malformed_label_file_is_an_error_not_a_silent_fallback(tmp_path, payload, why):
    labels_dir = tmp_path / "labels"
    labels_dir.mkdir()
    labels_dir.joinpath("pool.json").write_text(payload, encoding="utf-8")

    with pytest.raises(LabelFormatError) as excinfo:
        run_eval(labels_dir=labels_dir)
    assert "pool.json" in str(excinfo.value), why
    assert issubclass(LabelFormatError, ValueError)


def test_a_malformed_label_file_exits_non_zero(tmp_path, report_path):
    labels_dir = tmp_path / "labels"
    labels_dir.mkdir()
    labels_dir.joinpath("pool.json").write_text("{not json", encoding="utf-8")

    code = main(["--labels-dir", str(labels_dir), "--report", str(report_path)])
    assert code != 0


# --- cost per door ----------------------------------------------------------


@pytest.mark.parametrize(
    "spend, doors, expected_total, expected_per_door, why",
    [
        (None, 0, 0.0, 0.0, "no ledger at all"),
        (None, 500, 0.0, 0.0, "no ledger, doors scored"),
        ([], 500, 0.0, 0.0, "empty ledger"),
        ([("vision", 100, 2.50)], 0, 2.50, 0.0, "zero doors is 0.0, not a crash"),
        ([("vision", 100, 2.50)], 500, 2.50, 0.005, "total / doors"),
        (
            [("vision", 100, 2.50), ("geocode", 200, 1.00)],
            700,
            3.50,
            0.005,
            "summed across sources",
        ),
    ],
)
def test_cost_per_door(spend, doors, expected_total, expected_per_door, why):
    ledger = None
    if spend is not None:
        ledger = CostLedger()
        for source, units, usd in spend:
            ledger.record(source, units=units, usd=usd)

    report = run_eval(ledger=ledger, doors_scored=doors)
    assert report.cost_total_usd == pytest.approx(expected_total), why
    assert report.doors_scored == doors
    assert report.cost_per_door == pytest.approx(expected_per_door), why
    assert report.ok is True, "cost never fails the run"


def test_a_saved_ledger_on_disk_is_picked_up(tmp_path, report_path):
    ledger = CostLedger()
    ledger.record("vision", units=100, usd=2.50)
    ledger_path = tmp_path / "cost_ledger.json"
    ledger.save(ledger_path)

    code = main(
        [
            "--ledger",
            str(ledger_path),
            "--doors-scored",
            "500",
            "--report",
            str(report_path),
        ]
    )
    assert code == 0
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["cost_per_door"] == pytest.approx(0.005)
    assert payload["doors_scored"] == 500


# --- entity-resolution match rate -------------------------------------------


#: A resolve report whose two rates disagree — the territory-scoped one sits
#: below the floor throughout, so every assertion below is also an assertion
#: about *which* denominator the harness read.
TERRITORY_RATE = 0.62


def as_mapping(rate):
    return {
        "municipal_match_rate": rate,
        "permit_match_rate": TERRITORY_RATE,
        "coverage": 0.99,
        "doors_total": 500,
    }


def as_dataclass(rate):
    return ResolveReport(
        as_of=AS_OF, municipal_match_rate=rate, permit_match_rate=TERRITORY_RATE
    )


def test_match_rate_floor_is_ninety_five_percent():
    assert MATCH_RATE_FLOOR == 0.95


def test_an_absent_resolve_report_is_null_and_does_not_fail_the_run():
    report = run_eval(resolve_report=None)
    assert report.resolve_match_rate is None
    assert report.ok is True
    assert report.as_dict()["resolve_match_rate"] is None
    assert "match rate" in report.render().lower()


@pytest.mark.parametrize("form", [as_mapping, as_dataclass], ids=["mapping", "dataclass"])
@pytest.mark.parametrize(
    "rate, ok, why",
    [
        (1.0, True, "everything matched"),
        (0.96, True, "above the floor"),
        (0.95, True, "the floor is inclusive"),
        (0.9499, False, "just below"),
        (0.5, False, "well below"),
    ],
)
def test_a_real_resolve_report_below_the_floor_fails_the_run(form, rate, ok, why):
    report = run_eval(resolve_report=form(rate))
    assert report.resolve_match_rate == pytest.approx(rate)
    assert report.fixtures_passed == report.fixtures_total
    assert report.ok is ok, why
    assert rendered_value(report.render(), "match rate") == pytest.approx(rate, abs=0.001)


@pytest.mark.parametrize("form", [as_mapping, as_dataclass], ids=["mapping", "dataclass"])
def test_the_gate_reads_the_municipal_rate_not_the_territory_one(form):
    """R3.2 is a municipality-wide question. A run whose municipal rate clears
    the floor passes even though its territory-scoped rate is 0.62."""
    report = run_eval(resolve_report=form(0.9742))

    assert report.resolve_match_rate == pytest.approx(0.9742)
    assert report.ok is True


@pytest.mark.parametrize("form", [as_mapping, as_dataclass], ids=["mapping", "dataclass"])
def test_a_high_territory_rate_cannot_rescue_a_failing_municipal_rate(form):
    report = run_eval(resolve_report=form(0.80))

    assert report.resolve_match_rate == pytest.approx(0.80)
    assert report.ok is False


def test_a_report_carrying_only_the_territory_rate_grades_nothing():
    """Reading `permit_match_rate` here is the bug this ticket exists to fix, so
    a report without the municipal number is treated as no number at all."""
    report = run_eval(resolve_report={"permit_match_rate": 0.62, "coverage": 0.99})

    assert report.resolve_match_rate is None
    assert report.ok is True


def test_the_printed_report_says_which_denominator_the_rate_used():
    rendered = run_eval(resolve_report=as_mapping(0.97)).render().lower()
    assert "municipal" in rendered


# --- the report: printed and machine-readable -------------------------------


def test_report_json_carries_every_key_the_ethics_page_needs():
    payload = run_eval().as_dict()
    assert REQUIRED_JSON_KEYS <= set(payload)
    assert payload["fixtures_total"] == 13
    assert payload["fixtures_passed"] == 13
    assert payload["fixture_failures"] == []
    assert payload["metrics_source"] == FROZEN_SOURCE
    assert payload["resolve_match_rate_threshold"] == MATCH_RATE_FLOOR
    assert payload["ok"] is True
    json.dumps(payload)  # must be JSON-serialisable as-is


@pytest.mark.parametrize(
    "label_text, attribute",
    [
        ("precision", "precision"),
        ("recall", "recall"),
        ("hallucination rate", "hallucination_rate"),
        ("cost per door", "cost_per_door"),
        ("match rate", "resolve_match_rate"),
    ],
)
def test_printed_report_carries_every_required_number(label_text, attribute):
    ledger = CostLedger()
    ledger.record("vision", units=100, usd=2.50)
    report = run_eval(ledger=ledger, doors_scored=500, resolve_report=as_mapping(0.97))

    assert rendered_value(report.render(), label_text) == pytest.approx(
        getattr(report, attribute), abs=0.001
    )


def test_printed_report_carries_the_fixture_pass_count():
    assert f"{FIXTURE_COUNT}/{FIXTURE_COUNT}" in run_eval().render()


# --- the exit path ----------------------------------------------------------


def test_main_exits_zero_on_a_healthy_repo_and_writes_the_json(report_path, capsys):
    assert main(["--report", str(report_path)]) == 0

    printed = capsys.readouterr().out
    assert f"{FIXTURE_COUNT}/{FIXTURE_COUNT}" in printed
    assert f"source: {FROZEN_SOURCE}" in printed

    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert REQUIRED_JSON_KEYS <= set(payload)
    assert (payload["fixtures_passed"], payload["fixtures_total"]) == (
        FIXTURE_COUNT,
        FIXTURE_COUNT,
    )
    assert payload["ok"] is True


def test_main_writes_the_json_even_when_the_run_fails(tmp_path, report_path):
    golden = golden_dir_with(
        tmp_path,
        "new_mover_high_score",
        lambda f: {**f, "expect": {**f["expect"], "score": 7}},
    )
    code = main(["--golden-dir", str(golden), "--report", str(report_path)])

    assert code != 0
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["ok"] is False
    assert payload["fixtures_passed"] == FIXTURE_COUNT - 1
    assert [f["name"] for f in payload["fixture_failures"]] == ["new_mover_high_score"]


@pytest.mark.parametrize("rate, expected_zero", [(0.97, True), (0.94, False)])
def test_main_exit_code_tracks_the_resolve_match_rate(tmp_path, report_path, rate, expected_zero):
    resolve_path = tmp_path / "resolve_report.json"
    resolve_path.write_text(json.dumps(as_mapping(rate)), encoding="utf-8")

    code = main(
        ["--resolve-report", str(resolve_path), "--report", str(report_path)]
    )
    assert (code == 0) is expected_zero
    assert json.loads(report_path.read_text(encoding="utf-8"))[
        "resolve_match_rate"
    ] == pytest.approx(rate)


def test_python_dash_m_eval_harness_exits_zero(report_path):
    """`make eval` itself. Report redirected: never the repo's real one."""
    proc = run_module("--report", str(report_path))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert f"{FIXTURE_COUNT}/{FIXTURE_COUNT}" in proc.stdout
    assert f"source: {FROZEN_SOURCE}" in proc.stdout
    assert json.loads(report_path.read_text(encoding="utf-8"))["ok"] is True


def test_python_dash_m_eval_harness_exits_non_zero_on_a_broken_fixture(tmp_path, report_path):
    golden = golden_dir_with(
        tmp_path,
        "absentee_modifier",
        lambda f: {**f, "expect": {**f["expect"], "score": 3}},
    )
    proc = run_module("--golden-dir", str(golden), "--report", str(report_path))
    assert proc.returncode != 0
    assert "absentee_modifier" in proc.stdout + proc.stderr


# --- T018: bare `make eval` reads the published run manifest -----------------
#
# The definition of done says `make eval` reports the entity-resolution match
# rate and the cost per door. Until this ticket, bare `make eval` passed no
# --resolve-report / --ledger / --doors-scored, so it printed
# `n/a (no resolve report for this run)` and `doors scored: 0` while
# `data/run_manifest.json` sat next to it holding the real numbers. A wiring
# gap, not a logic gap: the flags below all worked already.
#
# Pinned seams (in addition to the ones at the top of this module):
#
#     eval.harness.DEFAULT_MANIFEST_PATH : Path
#         Where a bare run looks for the published manifest. Read at call time
#         (these tests monkeypatch it), so it is <repo>/data/run_manifest.json
#         for a real run and a tmp_path for a test.
#
#     eval.harness.NO_MANIFEST_NOTE : str
#         The one line a bare run prints when nothing has been published yet.
#         Its wording is the implementer's; that it appears, and that the run
#         still exits 0, is not.
#
# Discovery lives in `main`, never in `run_eval`: `run_eval` stays pure, and
# `run_eval(resolve_report=None)` keeps meaning "no report" (pinned above at
# test_an_absent_resolve_report_is_null_and_does_not_fail_the_run) rather than
# quietly meaning "go and find one".
#
# The manifest's rate lives in a nested `resolve` block, so the reader that
# already accepts a ResolveReport dataclass or a flat mapping must also accept
# a whole run manifest -- wherever `--resolve-report` is accepted today.

REAL_MANIFEST = REPO / "data" / "run_manifest.json"

#: The municipal rate the real published manifest carries. Used only as a
#: realistic value; no test depends on the real file except the one that skips
#: when it is absent.
MANIFEST_MUNICIPAL_RATE = 0.974152785755313


def run_manifest(
    rate=MANIFEST_MUNICIPAL_RATE, *, cost_usd=0.0, doors_scored=540, resolve=True
):
    """A run manifest in the shape `publish` writes: cost and doors at the top
    level, the entity-resolution numbers in a nested `resolve` block."""
    payload = {
        "run_at": "2026-08-14T08:38:26.522178+00:00",
        "as_of": "2026-08-14",
        "code_version": "125f61e",
        "cost_usd": cost_usd,
        "cost_per_door": (cost_usd / doors_scored) if doors_scored else 0.0,
        "doors_total": doors_scored,
        "doors_scored": doors_scored,
        "coverage": 1.0,
    }
    if resolve is True:
        payload["resolve"] = {
            "municipal_match_rate": rate,
            "permit_match_rate": TERRITORY_RATE,
            "permits_in_window": 1741,
            "permits_matched_municipal": 1696,
        }
    elif isinstance(resolve, dict):
        payload["resolve"] = resolve
    return payload


@pytest.fixture
def published(tmp_path, monkeypatch):
    """Publish a manifest under tmp_path and point auto-discovery at it.

    Never the repo's real data/run_manifest.json: a fresh clone has none, and a
    test that passes only because the pipeline happens to have been run is not
    a test.
    """

    def _publish(payload=None, *, raw=None):
        path = tmp_path / "data" / "run_manifest.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        body = raw if raw is not None else json.dumps(run_manifest() if payload is None else payload)
        path.write_text(body, encoding="utf-8")
        monkeypatch.setattr("eval.harness.DEFAULT_MANIFEST_PATH", path)
        return path

    return _publish


@pytest.fixture
def unpublished(tmp_path, monkeypatch):
    """A fresh clone: the manifest path exists as a path and not as a file."""
    path = tmp_path / "data" / "run_manifest.json"
    monkeypatch.setattr("eval.harness.DEFAULT_MANIFEST_PATH", path)
    return path


def written(report_path):
    return json.loads(report_path.read_text(encoding="utf-8"))


# --- AC1: a bare run reports the manifest's numbers, with no flags -----------


def test_bare_eval_reports_the_manifests_match_rate_cost_and_doors(
    published, report_path, capsys
):
    published(run_manifest(cost_usd=2.70, doors_scored=540))

    assert main(["--report", str(report_path)]) == 0

    payload = written(report_path)
    assert payload["resolve_match_rate"] == pytest.approx(MANIFEST_MUNICIPAL_RATE)
    assert payload["doors_scored"] == 540
    assert payload["cost_total_usd"] == pytest.approx(2.70)
    assert payload["cost_per_door"] == pytest.approx(2.70 / 540)

    printed = capsys.readouterr().out
    assert rendered_value(printed, "match rate") == pytest.approx(0.974, abs=0.001)
    assert rendered_value(printed, "doors scored") == 540
    assert rendered_value(printed, "cost per door") == pytest.approx(2.70 / 540, abs=0.0001)


def test_a_bare_run_with_a_manifest_never_prints_the_no_report_placeholder(
    published, report_path, capsys
):
    """The exact symptom this ticket exists to kill."""
    published()
    main(["--report", str(report_path)])

    printed = capsys.readouterr().out
    assert "no resolve report for this run" not in printed.lower()
    assert rendered_value(printed, "doors scored") == 540


@pytest.mark.skipif(not REAL_MANIFEST.is_file(), reason="pipeline has not been run here")
def test_the_real_published_manifest_is_reported_by_a_bare_run(report_path):
    """`make eval` as the Makefile invokes it, against whatever this repo has
    actually published. Report redirected; no other flags, on purpose."""
    manifest = json.loads(REAL_MANIFEST.read_text(encoding="utf-8"))
    expected_rate = (manifest.get("resolve") or {}).get("municipal_match_rate")
    assert expected_rate is not None, "the published manifest carries no municipal rate"

    proc = run_module("--report", str(report_path))

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert rendered_value(proc.stdout, "match rate") == pytest.approx(
        expected_rate, abs=0.001
    )
    assert rendered_value(proc.stdout, "doors scored") == manifest["doors_scored"]
    assert written(report_path)["resolve_match_rate"] == pytest.approx(expected_rate)


# --- AC2: the nested resolve block, wherever a resolve report is accepted ----


def test_run_eval_reads_the_match_rate_out_of_the_nested_resolve_block():
    report = run_eval(resolve_report=run_manifest())

    assert report.resolve_match_rate == pytest.approx(MANIFEST_MUNICIPAL_RATE)
    assert report.ok is True


def test_the_resolve_report_flag_accepts_a_whole_run_manifest(tmp_path, report_path):
    manifest_path = tmp_path / "run_manifest.json"
    manifest_path.write_text(json.dumps(run_manifest()), encoding="utf-8")

    code = main(["--resolve-report", str(manifest_path), "--report", str(report_path)])

    assert code == 0
    assert written(report_path)["resolve_match_rate"] == pytest.approx(
        MANIFEST_MUNICIPAL_RATE
    )


@pytest.mark.parametrize(
    "manifest, why",
    [
        (run_manifest(resolve=False), "a manifest with no resolve block"),
        (run_manifest(resolve={"permit_match_rate": TERRITORY_RATE}), "territory rate only"),
        ({}, "an empty manifest object"),
    ],
)
def test_a_manifest_without_a_municipal_rate_grades_nothing(manifest, why):
    """No gate is a knowable gap; a gate on the wrong denominator is a wrong
    answer stated confidently. The nested block changes nothing about that."""
    report = run_eval(resolve_report=manifest)

    assert report.resolve_match_rate is None, why
    assert report.ok is True, why


def test_a_flat_resolve_report_still_works_alongside_the_nested_one():
    """The pipeline's standalone resolve report predates the manifest; adding
    the nested reader must not stop the flat one being read."""
    assert run_eval(resolve_report=as_mapping(0.97)).resolve_match_rate == pytest.approx(0.97)


# --- AC3: a fresh clone that has never run the pipeline ----------------------


def test_no_manifest_on_disk_still_exits_zero(unpublished, report_path):
    assert not unpublished.exists()

    assert main(["--report", str(report_path)]) == 0

    payload = written(report_path)
    assert payload["resolve_match_rate"] is None
    assert payload["doors_scored"] == 0
    assert payload["ok"] is True


def test_no_manifest_says_plainly_that_no_run_has_been_published(
    unpublished, report_path, capsys
):
    import eval.harness as harness

    note = getattr(harness, "NO_MANIFEST_NOTE", None)
    assert isinstance(note, str) and note.strip(), "the harness must own this line"
    assert "publish" in note.lower(), "it has to say a run has not been published"

    main(["--report", str(report_path)])
    assert note in capsys.readouterr().out


@pytest.mark.parametrize(
    "raw, why",
    [
        ("{not json at all", "truncated garbage"),
        ("", "an empty file"),
        ("null", "valid JSON that is not an object"),
        ("[]", "a list where an object belongs"),
    ],
)
def test_a_malformed_manifest_never_fails_the_run(published, report_path, capsys, raw, why):
    """`make eval` grades the engine. A manifest it cannot read is a missing
    input, not a failing eval."""
    published(raw=raw)

    assert main(["--report", str(report_path)]) == 0, why
    assert written(report_path)["resolve_match_rate"] is None, why
    assert capsys.readouterr().out  # something was still printed


# --- AC4: explicit flags override the discovered manifest --------------------


def test_an_explicit_resolve_report_overrides_the_discovered_manifest(
    published, tmp_path, report_path
):
    published(run_manifest(MANIFEST_MUNICIPAL_RATE))
    explicit = tmp_path / "resolve_report.json"
    explicit.write_text(json.dumps(as_mapping(0.80)), encoding="utf-8")

    code = main(["--resolve-report", str(explicit), "--report", str(report_path)])

    assert code != 0, "the explicit report is below the floor and must decide the run"
    assert written(report_path)["resolve_match_rate"] == pytest.approx(0.80)


def test_an_explicit_doors_scored_overrides_the_manifest(published, report_path):
    published(run_manifest(cost_usd=5.0, doors_scored=540))

    main(["--doors-scored", "100", "--report", str(report_path)])

    payload = written(report_path)
    assert payload["doors_scored"] == 100
    assert payload["cost_per_door"] == pytest.approx(5.0 / 100)


def test_an_explicit_ledger_overrides_the_manifest_cost(published, tmp_path, report_path):
    published(run_manifest(cost_usd=9.99, doors_scored=540))
    ledger = CostLedger()
    ledger.record("vision", units=100, usd=2.50)
    ledger_path = tmp_path / "cost_ledger.json"
    ledger.save(ledger_path)

    main(
        [
            "--ledger",
            str(ledger_path),
            "--doors-scored",
            "500",
            "--report",
            str(report_path),
        ]
    )

    payload = written(report_path)
    assert payload["cost_total_usd"] == pytest.approx(2.50)
    assert payload["cost_per_door"] == pytest.approx(0.005)


# --- AC5: the >=0.95 gate still decides the run ------------------------------


@pytest.mark.parametrize(
    "rate, expected_zero, why",
    [
        (1.0, True, "everything matched"),
        (MANIFEST_MUNICIPAL_RATE, True, "the real run clears the floor"),
        (0.95, True, "the floor is inclusive"),
        (0.9499, False, "just below"),
        (0.5, False, "well below"),
    ],
)
def test_a_published_manifest_below_the_floor_fails_the_run(
    published, report_path, rate, expected_zero, why
):
    published(run_manifest(rate))

    assert (main(["--report", str(report_path)]) == 0) is expected_zero, why
    assert written(report_path)["resolve_match_rate"] == pytest.approx(rate)


def test_a_manifests_territory_rate_cannot_rescue_its_municipal_rate(published, report_path):
    published(
        run_manifest(
            resolve={"municipal_match_rate": 0.80, "permit_match_rate": 0.99}
        )
    )

    assert main(["--report", str(report_path)]) != 0
    assert written(report_path)["resolve_match_rate"] == pytest.approx(0.80)
