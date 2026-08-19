---
id: 103
title: Cutover — pipeline/publish/SQLite/manifest to V2, delete V1 path, one versioned 540-door run
status: green
depends_on: [102]
touches: [src/houseaccount/pipeline.py, src/houseaccount/publish.py, src/houseaccount/scoring/engine.py (delete), src/houseaccount/scoring/weights.py (delete), eval/golden/ (delete), eval/verify_claims.py (delete), data/, tests/test_publish.py, tests/test_pipeline.py, tests/test_scoring_fixtures.py, Makefile]
iterations: 1
test_files: [tests/test_publish.py, tests/test_pipeline.py, tests/test_v2_cutover.py]
branch: ""
---

## Scope

The one-way switch (R27): pipeline scores every door through bundle→V2 engine; publish writes
V2 artifacts; V1 engine/weights, V1 fixture tests, `eval/golden/` (13 fixtures) and
`eval/verify_claims.py` are deleted in this same ticket (no side-by-side paths survive).
Then execute the versioned recalculation run: warm-cache `make pipeline` with **no API keys
exported** (zero network, $0) regenerating door GeoJSON, scores.sqlite, run manifest, route
inputs, cached examples.

- SQLite score store: drop `groups`/`raw_total`; add V2 category fields (project, capacity,
  fit, base, mover_strength, mover_lift, rental_modifier, adjustment, score,
  score_contract_version, confidence) — R28.
- Run manifest R28: carries `score_contract_version: "v2"` + `as_of`; drops
  `territory_median_value`, `top_band_days`, `doors_in_top_band`, 90-day-cutoff degradation
  prose.
- Published per-door records identify score-contract version (R27 mixed-version rejection).
- Byte-reproducibility: re-running publish from the same sources + as_of reproduces every
  published artifact byte-identically (R28) — test-enforced on a sample and honored by the run.
- Test migration goes through the test-writer: V1-encoding tests (old group names/maxima,
  mover bands, rental −15, evidence sentences) are replaced/revised (R32), not weakened.

## Acceptance criteria

- [ ] `make pipeline` (warm cache, no keys) publishes 540/540 doors with V2 envelopes; every published score satisfies the R7 reconciliation invariant (test walks all 540 published records).
- [ ] scores.sqlite has V2 schema; no `groups`/`raw_total` anywhere; loader tests updated.
- [ ] run_manifest.json is R28-shaped (version + as_of present; V1 fields absent).
- [ ] `grep -r` finds no runtime reference to deleted V1 engine/weights; `eval/golden/` and `verify_claims.py` gone; Makefile `eval` target no longer references them (104 rebuilds it — leave a stub target failing loudly is acceptable interim ONLY if 104 lands same run; prefer wiring `make eval` to test-golden now).
- [ ] Byte-identical regeneration test: publish twice with same inputs/as_of → identical bytes.
- [ ] Every real door carries `rental_data_missing` gap (registry absent); rental −25 exercised only by fixtures.
- [ ] Full suite green post-deletion; `make test-golden` green.

## Test plan

## Attempt log
