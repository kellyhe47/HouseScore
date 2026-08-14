---
id: 008
title: "Eval harness: fixtures + vision P/R + hallucination + cost per door (R5, R14)"
status: green
depends_on: [003, 007]
touches: [eval/harness.py, eval/labels/README.md, eval/report.json, tests/test_harness.py]
iterations: 1
test_files: [tests/test_harness.py]
branch: "tdd/008" (merged)
---

## Scope

`make eval` — runnable code, not a spreadsheet (rubric non-negotiable). Runs every golden fixture
through the **real** score engine, computes vision precision/recall + hallucination rate + cost per
door, and reports the entity-resolution match rate.

Per the handoff: hand labels (~40 pool, 20 negatives) do not exist yet, so the vision metrics run
against fixture 09's frozen confusion set **and say so explicitly in the output**. Never fabricate
model predictions to fill the gap.

Do not modify `eval/verify_claims.py` or any fixture.

## Acceptance criteria

- [ ] `python -m eval.harness` exits 0 on a healthy repo and prints a report containing: fixture
      pass count (12/12), precision, recall, hallucination rate, cost per door, and the
      entity-resolution match rate.
- [ ] All 12 fixtures are exercised through the real engine (`houseaccount.scoring`), including
      fixture 11's parse cases and fixture 09's metric arithmetic — not re-implemented arithmetic.
- [ ] Fixture 09 reproduces P=0.818, R=0.9, hallucination=0.05 within abs tolerance 0.001, with
      the hallucination denominator being its own 20-image universe (not the 40).
- [ ] If `eval/labels/*.json` hand labels exist, metrics come from them; otherwise from the frozen
      fixture-09 set, and the report line says `source: frozen fixture 09 (hand labels not yet
      collected)`.
- [ ] `cost_per_door` = ledger total / doors scored, and is `0.0` (not an exception) when the
      ledger is empty or zero doors were scored.
- [ ] The report is written to `eval/report.json` as machine-readable JSON as well as printed —
      the Data & Ethics page (T014) consumes that file.
- [ ] Non-zero exit if any fixture fails, or if a real resolve report is present and its match rate
      is below 0.95.

## Attempt log

- iter 1: green. `make eval` prints PASS with 12/12 fixtures. Vision metrics come from
  fixture 09's frozen set with a loud NOT-A-MEASUREMENT caveat block — no model outputs were
  fabricated. Cost and match rate read `$0.0000` / `n/a` until the pipeline (009) passes
  `--ledger` / `--doors-scored` / `--resolve-report`.
- Fixtures dispatch by *shape*, not filename, so a 13th fixture is picked up free.
- The comparative `assertion` string is deliberately NOT eval()'d — both `score` and
  `baseline_score` are pinned against the engine instead, which pins the difference without
  executing text from a data file.
