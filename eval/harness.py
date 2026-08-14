"""`make eval` — the evaluation harness (T008). NOT YET IMPLEMENTED.

Interface stub only: `tests/test_harness.py` pins the full contract (seams,
report shape, CLI flags, hand-label file format). Every callable here raises
until the implementer fills it in.
"""

from __future__ import annotations

#: Stated verbatim in the report while the hand labels do not exist yet.
FROZEN_SOURCE = "frozen fixture 09 (hand labels not yet collected)"

#: Stated instead once `eval/labels/*.json` exists.
LABELS_SOURCE = "hand labels"

#: R3.2: a real resolve report below this fails the run.
MATCH_RATE_FLOOR = 0.95


class LabelFormatError(ValueError):
    """A hand-label file the harness refuses to guess at."""


class FixtureFailure:
    """One golden fixture that did not reproduce: `.name`, `.detail`."""


class EvalReport:
    """The eval result: `.as_dict()` is written to JSON, `.render()` is printed."""


def run_eval(
    *,
    golden_dir=None,
    labels_dir=None,
    resolve_report=None,
    ledger=None,
    doors_scored: int = 0,
) -> EvalReport:
    raise NotImplementedError("T008: run_eval")


def main(argv=None) -> int:
    raise NotImplementedError("T008: main")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
