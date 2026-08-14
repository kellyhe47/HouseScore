"""`make eval` — the evaluation harness (T008, R5.1 and R14).

`eval/verify_claims.py` answers a different question: it re-derives the golden
fixtures from the written R6 rules with a stdlib-only reimplementation, which
proves the *spec* is internally consistent. It cannot prove anything about the
code that ships. This module does the other half — it imports
`houseaccount.scoring` and runs the real engine, the real normalizer and the
real cost ledger over the same fixtures, so a weight table that drifts away
from the spec fails here even though `verify_claims.py` keeps passing.

What it reports, and why each number is in the report:

* **fixtures reproduced** — the regression floor. Twelve fixtures are the
  contract; anything less than 12/12 is a broken build, not a warning.
* **precision / recall / hallucination rate** — the rubric's non-negotiable
  vision metrics. The hallucination rate divides by its *own* verified-negative
  universe, never by the P/R universe: they measure different failures (a wrong
  call on a real image vs. a detection conjured from nothing) and pooling them
  would let a big clean denominator hide the conjuring.
* **cost per door** — "cheap enough to run on a whole town" is a claim this
  project makes out loud, so it is measured rather than asserted.
* **entity-resolution match rate** — R3.2 grades it, and a run whose permits
  stopped landing on doors is producing scores from missing signals. The rate
  read here is `municipal_match_rate`: in-window permits joining any municipal
  parcel over all in-window permits. `ResolveReport`'s other rate,
  `permit_match_rate`, is territory-scoped, so its denominator holds permits on
  parcels the territory does not own and can never match — grading the floor on
  it reported 0.62 for a join that was in fact 0.9742.

**The honest gap.** The hand labels (~40 pool parcels plus 20 verified
negatives) have not been collected yet. Rather than invent model predictions to
fill the hole, the harness falls back to fixture 09's frozen confusion set and
says so on its own line — `source: frozen fixture 09 (hand labels not yet
collected)`. Drop real labels into `eval/labels/*.json` (format in
`eval/labels/README.md`) and the same arithmetic runs over them instead, with
the source line changing to say so. A malformed label file is an error, never a
silent slide back to the frozen numbers: falling back quietly is exactly how a
measurement turns into a decoration.

**Where a bare run gets its numbers (T018).** `make eval` is invoked with no
flags, but two of the numbers it must report — the entity-resolution match rate
(R3.2) and the cost per door (R14) — are produced by `make pipeline`, not by the
fixtures. They are already published, in `data/run_manifest.json`. So `main`
discovers that manifest and feeds it in; before it did, a bare run printed
`n/a (no resolve report for this run)` and `doors scored: 0` with the real
numbers sitting on disk beside it. Discovery lives in `main` and never in
`run_eval`: `run_eval` stays pure, and `run_eval(resolve_report=None)` keeps
meaning "no report" rather than quietly meaning "go and find one". An explicit
`--resolve-report` / `--ledger` / `--doors-scored` always wins over the
manifest, and a manifest that is missing or unreadable is a missing input, not
a failing eval — the run says so on one line and still exits 0.

No network and no model calls: every number here is arithmetic over frozen
counts and deterministic code.
"""

from __future__ import annotations

import argparse
import copy
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence

from houseaccount.cost import CostLedger, SourceCost
from houseaccount.normalize import parse_deed_date
from houseaccount.scoring.engine import ScoreInput, score_door

#: Stated verbatim in the report while the hand labels do not exist yet.
FROZEN_SOURCE = "frozen fixture 09 (hand labels not yet collected)"

#: Stated instead once `eval/labels/*.json` exists.
LABELS_SOURCE = "hand labels"

#: Neither available — a golden set with no vision fixture and no labels. Not a
#: failure (the caller may be running one fixture on purpose), but never a
#: silent zero either: the report says the metrics are missing, not that they
#: are 0.0 because the model is perfect.
UNAVAILABLE_SOURCE = "unavailable (no vision fixture in this golden set, no hand labels)"

#: R3.2: a real resolve report below this fails the run.
MATCH_RATE_FLOOR = 0.95

#: Where the fixtures, the labels and the written report live by default.
EVAL_DIR = Path(__file__).resolve().parent
DEFAULT_GOLDEN_DIR = EVAL_DIR / "golden"
DEFAULT_LABELS_DIR = EVAL_DIR / "labels"
DEFAULT_REPORT_PATH = EVAL_DIR / "report.json"

#: What `make pipeline` publishes, and therefore where a bare `make eval` looks
#: for the run it should be reporting on. Read through the module global at call
#: time rather than being frozen into an argparse default, so a test (or a
#: caller grading someone else's run) can point it somewhere else.
DEFAULT_MANIFEST_PATH = EVAL_DIR.parent / "data" / "run_manifest.json"

#: Printed by a run that found no manifest — a fresh clone, or a repo whose
#: pipeline has not been run yet. Absence of an input, stated; never a silent
#: "n/a" the reader has to explain to themselves.
NO_MANIFEST_NOTE = (
    "  note: no run manifest found, so the cost and entity-resolution numbers "
    "above are from this invocation's flags alone -- `make pipeline` publishes "
    "data/run_manifest.json and a later `make eval` reads them from it."
)

#: The single ledger source a discovered manifest reconstructs into. The
#: manifest records what the run spent in total, not which API spent it, so the
#: harness must not pretend to a per-source breakdown it was never given.
MANIFEST_COST_SOURCE = "published run (data/run_manifest.json)"

#: Every field one hand label must carry. `probe` is the only optional one
#: (default False) because "this image is in the negative universe" is a claim
#: about the sampling frame, not about the image.
REQUIRED_LABEL_FIELDS = ("pams_pin", "image_ref", "signal", "actual", "predicted")


class LabelFormatError(ValueError):
    """A hand-label file the harness refuses to guess at.

    A `ValueError` so a caller that only cares that its input was bad can catch
    the broad thing; a distinct type so the CLI can report the filename.
    """


@dataclass(frozen=True)
class FixtureFailure:
    """One golden fixture that did not reproduce, and what went wrong."""

    name: str
    detail: str


@dataclass(frozen=True)
class VisionMetrics:
    """Precision, recall and hallucination rate, plus where they came from.

    `source` travels with the numbers rather than being reattached later —
    metrics whose provenance is stored somewhere else eventually get printed
    without it.
    """

    precision: float
    recall: float
    hallucination_rate: float
    source: str


@dataclass(frozen=True)
class EvalReport:
    """The eval result: `.as_dict()` is written to JSON, `.render()` is printed."""

    fixtures_total: int
    fixtures_passed: int
    fixture_failures: tuple[FixtureFailure, ...]
    precision: float
    recall: float
    hallucination_rate: float
    metrics_source: str
    cost_total_usd: float
    doors_scored: int
    cost_per_door: float
    resolve_match_rate: float | None
    ok: bool

    def as_dict(self) -> dict[str, Any]:
        """Exactly the JSON written to `eval/report.json`.

        The Data & Ethics page (T014) reads this file, so the threshold ships
        alongside the match rate: a page that renders "0.94" has to be able to
        say what 0.94 failed against without hardcoding 0.95 a second time.
        """
        return {
            "fixtures_total": self.fixtures_total,
            "fixtures_passed": self.fixtures_passed,
            "fixture_failures": [
                {"name": failure.name, "detail": failure.detail}
                for failure in self.fixture_failures
            ],
            "precision": self.precision,
            "recall": self.recall,
            "hallucination_rate": self.hallucination_rate,
            "metrics_source": self.metrics_source,
            "cost_total_usd": self.cost_total_usd,
            "doors_scored": self.doors_scored,
            "cost_per_door": self.cost_per_door,
            "resolve_match_rate": self.resolve_match_rate,
            "resolve_match_rate_threshold": MATCH_RATE_FLOOR,
            "ok": self.ok,
        }

    def render(self) -> str:
        """The printed report — written to be read by a person, not grepped.

        Every metric is on its own `<label>: <value>` line so it stays machine
        checkable, but the grouping, the alignment and the caveat banner are
        there for the human: the one thing nobody may miss is that the vision
        numbers are frozen fixture arithmetic rather than a live measurement.
        """
        lines: list[str] = [
            "HouseAccount eval -- the shipped engine, against the golden fixtures",
            "=" * 68,
            "",
            "GOLDEN FIXTURES",
            _row("reproduced", f"{self.fixtures_passed}/{self.fixtures_total}"),
        ]

        lines += [
            "",
            "VISION (top signal: pool)",
            _row("precision", f"{self.precision:.3f}"),
            _row("recall", f"{self.recall:.3f}"),
            _row("hallucination rate", f"{self.hallucination_rate:.3f}"),
            # Single-spaced on purpose: this exact string is the honest-gap
            # disclosure, and it is pinned character for character.
            f"  source: {self.metrics_source}",
        ]
        lines += _caveat(self.metrics_source)

        lines += [
            "",
            "COST",
            _row("total", f"${self.cost_total_usd:.4f}"),
            _row("doors scored", str(self.doors_scored)),
            _row("cost per door", f"${self.cost_per_door:.4f}"),
        ]

        rate = "n/a (no resolve report for this run)"
        if self.resolve_match_rate is not None:
            rate = f"{self.resolve_match_rate:.3f}"
        lines += [
            "",
            "ENTITY RESOLUTION (R3.2)",
            _row("municipal match rate", rate),
            _row("required floor", f"{MATCH_RATE_FLOOR:.3f}"),
            # Named on the page, because the run also computes a territory-scoped
            # rate and the two disagree by design.
            "  denominator: every in-window permit, against every municipal parcel",
        ]

        if self.fixture_failures:
            lines += ["", "FAILURES"]
            for failure in self.fixture_failures:
                lines.append(f"  {failure.name}")
                lines.append(f"      {failure.detail}")

        lines += ["", "=" * 68, f"RESULT: {'PASS' if self.ok else 'FAIL'}", ""]
        return "\n".join(lines)


def run_eval(
    *,
    golden_dir: Path | None = None,
    labels_dir: Path | None = None,
    resolve_report: Any = None,
    ledger: CostLedger | None = None,
    doors_scored: int = 0,
) -> EvalReport:
    """Run every golden fixture through the real engine and report the result.

    Pure: it computes and returns, and never writes. `main` owns the file and
    the exit code, which is what lets the tests (and the T014 page) call this
    without a tmpdir dance.
    """
    fixtures = _load_fixtures(golden_dir or DEFAULT_GOLDEN_DIR)
    failures: list[FixtureFailure] = []
    frozen: VisionMetrics | None = None

    for name, fixture in fixtures:
        detail, metrics = _check_fixture(fixture)
        if metrics is not None and frozen is None:
            frozen = metrics
        if detail:
            failures.append(FixtureFailure(name=name, detail=detail))

    # Raised, not swallowed: a broken label file must not read as "the frozen
    # numbers are the best we have" when someone believes labels are in play.
    metrics = _label_metrics(labels_dir or DEFAULT_LABELS_DIR) or frozen or _no_metrics()

    ledger = ledger if ledger is not None else CostLedger()
    match_rate = _match_rate(resolve_report)

    return EvalReport(
        fixtures_total=len(fixtures),
        fixtures_passed=len(fixtures) - len(failures),
        fixture_failures=tuple(failures),
        precision=metrics.precision,
        recall=metrics.recall,
        hallucination_rate=metrics.hallucination_rate,
        metrics_source=metrics.source,
        cost_total_usd=ledger.total_usd(),
        doors_scored=doors_scored,
        cost_per_door=ledger.per_door(doors_scored),
        resolve_match_rate=match_rate,
        ok=not failures and (match_rate is None or match_rate >= MATCH_RATE_FLOOR),
    )


def main(argv: Sequence[str] | None = None) -> int:
    """`python -m eval.harness`. Prints the report, writes the JSON, sets the code.

    The JSON is written even when the run fails — a failing eval is the run you
    most want a machine-readable record of.

    Also where the published run manifest is discovered, so that `make eval`
    with no flags reports the run that actually happened. Every flag beats the
    manifest: whoever passed one is grading something specific on purpose.
    """
    args = _parse_args(argv)
    manifest = _published_manifest(DEFAULT_MANIFEST_PATH)

    try:
        report = run_eval(
            golden_dir=args.golden_dir,
            labels_dir=args.labels_dir,
            resolve_report=(
                _load_json(args.resolve_report) if args.resolve_report else manifest
            ),
            ledger=(
                CostLedger.load(args.ledger)
                if args.ledger
                else _manifest_ledger(manifest)
            ),
            doors_scored=(
                args.doors_scored
                if args.doors_scored is not None
                else _manifest_doors(manifest)
            ),
        )
    except LabelFormatError as error:
        print(f"eval: refusing to run -- {error}")
        return 2

    print(report.render())
    if manifest is None and args.resolve_report is None:
        print(NO_MANIFEST_NOTE)
        print()

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report.as_dict(), indent=2) + "\n", encoding="utf-8")

    return 0 if report.ok else 1


# --- the fixtures, through the real engine ----------------------------------


def _load_fixtures(golden_dir: Path) -> list[tuple[str, Any]]:
    """(name, payload) per `*.json`, name falling back to the filename.

    An unparseable fixture comes back as the string it failed to be, so it
    surfaces as one named failure instead of taking the whole run down.
    """
    out: list[tuple[str, Any]] = []
    for path in sorted(Path(golden_dir).glob("*.json")):
        try:
            fixture = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            out.append((path.name, f"unparseable: {error}"))
            continue
        out.append((str(fixture.get("name") or path.stem), fixture))
    return out


def _check_fixture(fixture: Any) -> tuple[str, VisionMetrics | None]:
    """Dispatch one fixture by shape and return (failure detail, metrics).

    By shape rather than by name: a thirteenth fixture that pins a score is
    checked for free, and renaming one cannot silently stop it being checked.
    """
    if isinstance(fixture, str):
        return fixture, None
    if not isinstance(fixture, Mapping):
        return f"not a fixture object: {type(fixture).__name__}", None

    given = fixture.get("given") or {}
    expect = fixture.get("expect") or {}

    if "score" in expect:
        return _check_scored(given, expect), None
    if "labeled_samples" in given:
        return _check_vision(given, expect)
    if "cases" in given:
        return _check_deed_parse(given), None
    return "unrecognised fixture shape: no expect.score, labeled_samples or cases", None


def _check_scored(given: Mapping[str, Any], expect: Mapping[str, Any]) -> str:
    """A scored fixture, through `ScoreInput.from_fixture` + `score_door`."""
    result = score_door(ScoreInput.from_fixture(given))
    problems: list[str] = []

    if result.score != expect["score"]:
        problems.append(f"expected score {expect['score']}, engine produced {result.score}")

    want_confidence = expect.get("confidence", "normal")
    if result.confidence != want_confidence:
        problems.append(
            f"expected confidence {want_confidence!r}, engine produced {result.confidence!r}"
        )

    present = {item.type for item in result.evidence}
    missing = sorted(
        {e["type"] for e in expect.get("evidence_must_include", ())} - present
    )
    if missing:
        problems.append(f"missing required evidence {missing}")

    forbidden = {e["type"] for e in expect.get("evidence_must_exclude", ())}
    forbidden |= set(expect.get("must_not_contain_evidence_types", ()))
    found = sorted(forbidden & present)
    if found:
        problems.append(f"forbidden evidence present {found}")

    # R11.1 (Daniel's Law): fixture 08 forbids identity-derived prose outright,
    # so the harness reads the sentences the rep would actually see.
    blob = " ".join(
        f"{item.type} {item.sentence} {item.source}" for item in result.evidence
    ).lower()
    leaked = sorted(p for p in expect.get("must_not_contain", ()) if p.lower() in blob)
    if leaked:
        problems.append(f"forbidden text present {leaked}")

    problems += _check_comparative(given, expect)
    return "; ".join(problems)


def _check_comparative(given: Mapping[str, Any], expect: Mapping[str, Any]) -> list[str]:
    """The fixture's stated baseline, re-scored by the engine.

    Only the two scores are checked. The fixture's `assertion` string ("score -
    baseline_score == 10") is arithmetic *between* them, so pinning both
    numbers pins the difference — and the harness never has to evaluate a
    string out of a data file to prove it.
    """
    comparative = expect.get("comparative") or {}
    if "baseline_score" not in comparative:
        return []

    description = str(comparative.get("baseline", ""))
    mutated = copy.deepcopy(dict(given))
    if "vision={}" in description:
        mutated["vision"] = {}
    if "rental_registration_match=false" in description:
        mutated["rental_registration_match"] = False
    # A structured override, rather than one more phrase sniffed out of the
    # prose: a comparative that changes a parcel field names the field. Fixture
    # 13's baseline is the same door read from the stale parcel deed, which no
    # sentence-matching rule could express.
    overrides = comparative.get("baseline_parcel") or {}
    if overrides:
        mutated["parcel"] = {**(mutated.get("parcel") or {}), **overrides}

    baseline = score_door(ScoreInput.from_fixture(mutated)).score
    if baseline != comparative["baseline_score"]:
        return [
            f"baseline ({description}) expected {comparative['baseline_score']}, "
            f"engine produced {baseline}"
        ]
    return []


def _check_vision(
    given: Mapping[str, Any], expect: Mapping[str, Any]
) -> tuple[str, VisionMetrics | None]:
    """Fixture 09: the frozen confusion set, and the metrics it pins."""
    samples = given.get("labeled_samples") or {}
    counts = samples.get("predictions") or {}
    probe = given.get("hallucination_probe") or {}
    tolerance = float((expect.get("within_tolerance") or {}).get("abs", 0.001))

    tp = int(counts.get("true_positive", 0))
    fp = int(counts.get("false_positive", 0))
    fn = int(counts.get("false_negative", 0))
    tn = int(counts.get("true_negative", 0))
    universe = int(samples.get("universe", 0))

    problems: list[str] = []
    if tp + fp + fn + tn != universe:
        problems.append(
            f"confusion counts sum to {tp + fp + fn + tn}, not the {universe}-image universe"
        )

    precision = _ratio(tp, tp + fp)
    recall = _ratio(tp, tp + fn)
    hallucination = _ratio(
        int(probe.get("claimed_detections", 0)), int(probe.get("universe", 0))
    )

    for label, got, want in (
        ("precision", precision, expect.get("precision")),
        ("recall", recall, expect.get("recall")),
        ("hallucination rate", hallucination, expect.get("hallucination_rate")),
    ):
        if want is not None and abs(got - float(want)) > tolerance:
            problems.append(f"{label} computed {got:.4f}, fixture pins {want}")

    metrics = VisionMetrics(
        precision=precision,
        recall=recall,
        hallucination_rate=hallucination,
        source=FROZEN_SOURCE,
    )
    return "; ".join(problems), metrics


def _check_deed_parse(given: Mapping[str, Any]) -> str:
    """Fixture 11's cases, through `normalize.parse_deed_date` at its own `as_of`.

    `as_of` is read from the fixture rather than assumed: the century pivot is
    derived from it, and a harness that hardcoded today's would agree with a
    hardcoded pivot in the normalizer for another year or two.
    """
    as_of = date.fromisoformat(str(given["as_of"]))
    problems: list[str] = []

    for case in given.get("cases") or ():
        raw = case.get("raw")
        want_iso = case.get("expect_iso")
        want = date.fromisoformat(want_iso) if want_iso else None
        got = parse_deed_date(raw, as_of)
        if got != want:
            problems.append(f"{raw!r} at as_of {as_of.isoformat()} -> {got}, expected {want}")

    return "; ".join(problems)


# --- hand labels -------------------------------------------------------------


def _label_metrics(labels_dir: Path) -> VisionMetrics | None:
    """Metrics from `eval/labels/*.json`, or None when there are no labels yet.

    Precision and recall come from the non-probe labels; the hallucination rate
    divides by the probe labels alone. Two universes by construction, so a
    large clean sample can never dilute a conjured detection.
    """
    labels = _load_labels(Path(labels_dir))
    if not labels:
        return None

    graded = [entry for entry in labels if not entry["probe"]]
    probes = [entry for entry in labels if entry["probe"]]

    tp = sum(1 for e in graded if e["actual"] and e["predicted"])
    fp = sum(1 for e in graded if not e["actual"] and e["predicted"])
    fn = sum(1 for e in graded if e["actual"] and not e["predicted"])

    return VisionMetrics(
        precision=_ratio(tp, tp + fp),
        recall=_ratio(tp, tp + fn),
        hallucination_rate=_ratio(sum(1 for e in probes if e["predicted"]), len(probes)),
        source=LABELS_SOURCE,
    )


def _load_labels(labels_dir: Path) -> list[dict[str, Any]]:
    """Every label in the directory, merged. Malformed -> LabelFormatError.

    Files merge because hand-labelling is done in batches by different people
    on different days, and forcing one big file would make two labellers
    conflict on every save.
    """
    if not labels_dir.is_dir():
        return []

    merged: list[dict[str, Any]] = []
    for path in sorted(labels_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise LabelFormatError(f"{path.name}: not valid JSON ({error})") from error

        if not isinstance(payload, Mapping):
            raise LabelFormatError(f"{path.name}: top level must be an object")
        entries = payload.get("labels")
        if not isinstance(entries, list):
            raise LabelFormatError(f"{path.name}: missing a `labels` list")

        for index, entry in enumerate(entries):
            merged.append(_validated_label(path.name, index, entry))
    return merged


def _validated_label(filename: str, index: int, entry: Any) -> dict[str, Any]:
    """One label, checked hard. Every message names the file and the position."""
    where = f"{filename}: labels[{index}]"
    if not isinstance(entry, Mapping):
        raise LabelFormatError(f"{where} is not an object")

    missing = [field for field in REQUIRED_LABEL_FIELDS if field not in entry]
    if missing:
        raise LabelFormatError(f"{where} is missing {', '.join(missing)}")

    for field in ("actual", "predicted"):
        if not isinstance(entry[field], bool):
            raise LabelFormatError(f"{where}: `{field}` must be true or false")

    probe = entry.get("probe", False)
    if not isinstance(probe, bool):
        raise LabelFormatError(f"{where}: `probe` must be true or false")
    if probe and entry["actual"]:
        raise LabelFormatError(
            f"{where}: a probe image is a VERIFIED negative, so `actual` must be false"
        )

    return {"actual": entry["actual"], "predicted": entry["predicted"], "probe": probe}


def _no_metrics() -> VisionMetrics:
    return VisionMetrics(0.0, 0.0, 0.0, UNAVAILABLE_SOURCE)


# --- the other inputs --------------------------------------------------------


def _match_rate(resolve_report: Any) -> float | None:
    """`municipal_match_rate` off a `ResolveReport`, a mapping, or nothing.

    A mapping as well as the dataclass because the pipeline writes its resolve
    report to JSON and `make eval` runs later, from the file.

    Only the municipal rate is read. `ResolveReport` also carries
    `permit_match_rate`, whose denominator is territory-scoped and therefore
    contains permits on municipal parcels the territory does not hold — grading
    R3.2 on it is what made a healthy 0.9742 join report 0.62. A report carrying
    only that number is treated as carrying no rate at all: no gate is a knowable
    gap, a gate on the wrong denominator is a wrong answer stated confidently.

    A whole run manifest is accepted too, because a bare `make eval` grades the
    published run and `publish` files the resolve numbers in a nested `resolve`
    block. Nesting changes nothing about which rate counts: a manifest whose
    block carries only `permit_match_rate` grades nothing, exactly as a flat
    report of the same shape does.
    """
    if resolve_report is None:
        return None

    rate = _municipal_rate(resolve_report)
    if rate is None and isinstance(resolve_report, Mapping):
        rate = _municipal_rate(resolve_report.get("resolve"))
    return None if rate is None else float(rate)


def _municipal_rate(report: Any) -> Any:
    """`municipal_match_rate` off one level of a report — mapping or dataclass."""
    if isinstance(report, Mapping):
        return report.get("municipal_match_rate")
    return getattr(report, "municipal_match_rate", None)


def _published_manifest(path: Path) -> Mapping[str, Any] | None:
    """The run manifest `publish` wrote, or None when there is not a readable one.

    Unreadable is deliberately the same answer as absent. `make eval` grades the
    scoring engine; a truncated or half-written manifest is an input this run did
    not get, and failing the eval over it would report a problem the engine does
    not have.
    """
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, Mapping) else None


def _manifest_ledger(manifest: Mapping[str, Any] | None) -> CostLedger | None:
    """The run's spend, rebuilt as a one-source ledger, or None if unknowable."""
    if manifest is None:
        return None
    try:
        usd = float(manifest.get("cost_usd") or 0.0)
    except (TypeError, ValueError):
        return None
    return CostLedger(
        {MANIFEST_COST_SOURCE: SourceCost(units=_manifest_doors(manifest), usd=usd)}
    )


def _manifest_doors(manifest: Mapping[str, Any] | None) -> int:
    """`doors_scored` off the manifest. Unknown is 0 — the same as no manifest."""
    if manifest is None:
        return 0
    try:
        return int(manifest.get("doors_scored") or 0)
    except (TypeError, ValueError):
        return 0


def _load_json(path: Path | None) -> Any:
    if path is None:
        return None
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _ratio(numerator: int, denominator: int) -> float:
    """A rate that reports 0.0 rather than dividing by an empty universe."""
    return numerator / denominator if denominator else 0.0


# --- rendering ---------------------------------------------------------------

#: Column at which every value lines up, so the numbers read as a column.
_LABEL_WIDTH = 26


def _row(label: str, value: str) -> str:
    return f"  {label + ':':<{_LABEL_WIDTH}}{value}"


def _caveat(source: str) -> list[str]:
    """The banner under the vision block. Impossible to skim past on purpose."""
    if source == LABELS_SOURCE:
        return ["", "  Measured over the hand labels in eval/labels/*.json."]
    if source == UNAVAILABLE_SOURCE:
        return [
            "",
            "  !! NO VISION METRICS FOR THIS RUN. The zeros above are absence of",
            "  !! measurement, not a measured zero.",
        ]
    return [
        "",
        "  !! NOT A MEASUREMENT OF THE SHIPPED VISION MODEL. These are fixture 09's",
        "  !! frozen counts (9 TP / 2 FP / 1 FN / 28 TN over 40 images, and a",
        "  !! separate 20-image verified-negative probe). No model was run to",
        "  !! produce them. Hand-label 40 parcels into eval/labels/*.json (see",
        "  !! eval/labels/README.md) and these become a real measurement.",
    ]


# --- the CLI -----------------------------------------------------------------


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m eval.harness",
        description="Run the golden fixtures through the shipped House Score engine.",
    )
    parser.add_argument(
        "--golden-dir", type=Path, default=DEFAULT_GOLDEN_DIR, help="golden fixtures directory"
    )
    parser.add_argument(
        "--labels-dir", type=Path, default=DEFAULT_LABELS_DIR, help="hand-label directory"
    )
    parser.add_argument(
        "--report", type=Path, default=DEFAULT_REPORT_PATH, help="where to write the JSON report"
    )
    # All three default to None rather than to a value, so "not given" stays
    # distinguishable from "given, and it happens to be zero" — that difference
    # is what lets the published manifest fill the gaps without ever overriding
    # a caller who asked for `--doors-scored 0`.
    parser.add_argument(
        "--resolve-report",
        type=Path,
        default=None,
        help="a resolve report or run manifest JSON to grade R3.2 on "
        "(default: the published data/run_manifest.json)",
    )
    parser.add_argument(
        "--ledger",
        type=Path,
        default=None,
        help="a saved CostLedger JSON from a run "
        "(default: the published manifest's cost_usd)",
    )
    parser.add_argument(
        "--doors-scored",
        type=int,
        default=None,
        help="doors the ledger's spend is amortised over "
        "(default: the published manifest's doors_scored)",
    )
    return parser.parse_args(list(argv) if argv is not None else None)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
