---
id: 003
title: "House Score engine + evidence (R6/R7) — all 12 golden fixtures"
status: pending
depends_on: [001, 002]
touches: [src/houseaccount/scoring/engine.py, src/houseaccount/scoring/evidence.py, src/houseaccount/scoring/weights.py, tests/test_scoring_fixtures.py, tests/test_evidence.py]
iterations: 0
test_files: []
branch: ""
---

## Scope

The deterministic score engine and its evidence generator — the heart of the system.
Consumes already-normalised inputs (the R3.3 vision dict, ISO or YYMMDD deed dates, a
`rental_registration_match: bool`); performs no I/O and calls no network.

`eval/golden/*.json` is the contract, in full. Tests must be **parametrized over the fixture
files themselves** — no hand-copied expected numbers. `python3 eval/verify_claims.py` must still
exit 0 afterwards (fixtures are immutable here).

## Acceptance criteria

- [ ] `score_door(ScoreInput) -> ScoreResult` exposing `.score`, `.confidence`, `.evidence`,
      `.groups` (`mover`, `hires_out`, `capacity`, `need`, `modifier`) and `.raw_total`.
- [ ] Every fixture carrying `expect.score` reproduces **exactly**: 01,02,03,04,05,06,07,08,10,12.
- [ ] Every `expect.comparative.baseline_score` reproduces (07 `vision={}` → 28,
      08 `rental_registration_match=false` → 43, 10 → 100) and the stated assertion holds.
- [ ] Evidence: each `evidence_must_include[].type` is present in `.evidence`; each
      `evidence_must_exclude[].type` and `must_not_contain_evidence_types[]` entry is absent.
- [ ] Fixture 06 → `confidence == "low"`; a complete-data fixture → `"normal"`. Confidence is
      binary (R6.1).
- [ ] Evidence item shape (R7.1): `{type, points: int (signed), sentence: str, source: str,
      retrieved: date}` plus optional `imagery: {image_url, bbox, model_confidence, capture_date}`
      for vision-derived items. Zero-point context evidence is legal and expected — fixture 05
      emits a `tenure` item with `points == 0` (R7.3).
- [ ] Group maxima are respected: hires-out permit points cap at 40; churn (+20) only when ≥2
      distinct contractors AND no name repeats; permits with no contractor are excluded from the
      churn test but still earn permit points.
- [ ] Clamp both ends: raw > 100 → 100 (fixture 01), raw < 0 → 0.
- [ ] Non-arm's-length OR-rule: `SALE_PRICE <= 100` **or** non-empty `SALES_CODE` → mover 0, and a
      `non_arms_length_transfer` evidence item is emitted (fixtures 03 and 12 isolate each branch).
- [ ] End-to-end with the T002 normalizer: a parcel whose raw `DEED_DATE` is `"260712"` at
      `as_of=2026-08-01` scores identically to fixture 10's ISO input (fixture 11 `end_to_end`).
- [ ] ACS evidence is neighbourhood-phrased (R6.2): its sentence mentions "block group" and makes
      no household-level claim.
- [ ] Weights live in one `weights.py` constant table that the ethics page (T014) will import —
      no magic numbers scattered through the engine.
- [ ] `python3 eval/verify_claims.py` exits 0 (run it as part of the ticket's test set).
