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

assert FIXTURE_COUNT == 12, f"expected 12 golden fixtures, found {FIXTURE_COUNT}"

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
    assert report.fixtures_total == FIXTURE_COUNT == 12
    assert report.fixtures_passed == 12
    assert tuple(report.fixture_failures) == ()
    assert report.ok is True


def test_scored_fixtures_run_through_the_real_engine(monkeypatch):
    """Bend the engine's weights table and the harness must notice.

    A harness that re-implemented the arithmetic (the way `verify_claims.py`
    does) would keep reporting 12/12 here.
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
    assert report.fixtures_total == 12
    assert report.fixtures_passed == 11, why
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
    assert payload["fixtures_total"] == 12
    assert payload["fixtures_passed"] == 12
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
    assert "12/12" in run_eval().render()


# --- the exit path ----------------------------------------------------------


def test_main_exits_zero_on_a_healthy_repo_and_writes_the_json(report_path, capsys):
    assert main(["--report", str(report_path)]) == 0

    printed = capsys.readouterr().out
    assert "12/12" in printed
    assert f"source: {FROZEN_SOURCE}" in printed

    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert REQUIRED_JSON_KEYS <= set(payload)
    assert (payload["fixtures_passed"], payload["fixtures_total"]) == (12, 12)
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
    assert payload["fixtures_passed"] == 11
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
    assert "12/12" in proc.stdout
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
