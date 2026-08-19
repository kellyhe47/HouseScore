---
id: 101
title: V2 scoring engine + golden adapter (test-golden)
status: pending
depends_on: []
touches: [src/houseaccount/scoring/, eval/v2/, tests/test_v2_engine.py, tests/test_v2_golden.py, Makefile]
iterations: 0
test_files: []
branch: ""
---

## Scope

Build the Door Score V2 engine as a new module `src/houseaccount/scoring/v2.py` (plus any
helpers under `scoring/`), and the `test-golden` adapter that runs all 42 fixtures in
`eval/v2/golden/` through it. **Additive only:** the V1 `engine.py`/`weights.py` path and all
existing tests stay untouched and green; V1 deletion happens in ticket 103.

The engine is pure (no I/O): input is a normalized evidence bundle (dataclass mirroring the
fixtures' `given.inputs` shape — parcel, sales[], permits[], local_comparables[],
territory_assessed_values[], acs_block_group, rental_registry, imagery), plus `as_of`.
Output is the fixture `result` envelope: `score_contract_version: "v2"`, `confidence`,
`categories {project, capacity, fit}`, `base`, `mover {eligible, days_since_move, strength}`,
`mover_lift`, `rental_modifier`, `pre_rounding`, `adjustment`, `score`, `evidence`, `data_gaps`.

The adapter (`eval/v2/run_golden.py` + `tests/test_v2_golden.py` parametrized over fixture IDs)
maps `when.operation: score_door` to the engine entry point, loads `given` through the
production bundle constructor, canonicalizes per `eval/v2/provenance.md` (floats to 3 dp;
evidence projected to `{type, points}` sorted by type; data_gaps `{type}` sorted), and
deep-compares. Add a `make test-golden` target.

NOT in scope: real-data bundle building (102), pipeline/publish (103), any surface.

## Acceptance criteria

Contract: plan R1–R26, R37; envelope conventions in `eval/v2/provenance.md` are binding.

- [ ] All 42 golden fixtures pass by ID via `make test-golden`, deep-compared after canonicalization; `state_changes`/`emitted_events`/`external_calls` are exactly `[]`.
- [ ] Red gate: before implementation, every fixture test fails for its intended reason (envelope mismatch/missing entry point), not import typos.
- [ ] M(d): 90 on days 0–90; `t=(d-90)/275`, `M=90*(exp(-2t)-exp(-2))/(1-exp(-2))` days 91–364; 0 at ≥365; no intermediate rounding (R4). Unit tests at d=90, 91, 180 (AE3 ≈40.005 strength, 64.447 pre-rounding, score 64), 364, 365.
- [ ] Blend `P = B + (M/90)*((90+B/8)-B)`; final `round_half_up(clamp(P + rental, 0, 100))`; `adjustment` reconciles pre_rounding→score; evidence points sum to capped subtotals + mover_lift + rental (R5/R7 invariant, unit-tested directly).
- [ ] Mover eligibility R3: price ≤ $100, nonempty sales_code, invalid or future date → no influence; freshest *valid* sale wins; disqualified sale doesn't shadow older valid one.
- [ ] Project R8–R12: cross-source dedup by municipal_id counts once; active = non-terminal + qualifying event within 12mo (+15); completed within 24mo with terminal disposition + lifecycle date (+8, issue-date fallback only when explicitly terminal and no later date — same rule as roof R17); 2+ distinct permits in 24mo (+5); explicit major scope (+5); voided/admin/stale-open neutral with `project_neutralized` evidence; cap 25 with `project_cap_adjustment`.
- [ ] Capacity R13–R16: nearest-≤20 comparables median bands 0/3/7/10 (boundaries 1.0x excl, 1.2x incl, 1.5x incl); <10 comparables → neutral + `local_comparables_insufficient` gap; midrank percentile bands 0/3/7/10 at 50/75/90 half-open; ACS ≥35% → +5; per-signal neutrality (R16); cap 25.
- [ ] Fit R17–R21: roof-age bands 0/4/8/12 from explicit completed replacement/reroof/reshingle only (generic ROOF/repair/partial/solar-only never establish age; unknown neutral; `fit_roof_age: 0` entry when records present but non-qualifying or <10y); home-age bands 0/2/5/8 at 30/50/75 truncated years; condition decline +8 at ≥0.60 confidence, superseded by later completed exterior permit (`fit_condition_superseded`); pool +5 / solar +5 scored once each, active install permit doesn't establish feature; lot ≥0.5ac +5; cap 30 with `fit_cap_adjustment`.
- [ ] R6 rental: −25 when registration dated ≥ latest valid sale, or within 24mo of as_of when no valid sale; stale → `rental_stale` neutral; missing registry → `rental_data_missing` gap.
- [ ] R37: confidence `low` iff ≥2 data gaps. Gap vocabulary exactly per provenance.md.
- [ ] R24/R25: all ages truncated calendar days/years from as_of; no owner/mailing/agent fields consumed (bundle type has no such fields; test asserts fixture 39 identity fields ignored).
- [ ] Full existing suite still green (additive change).

## Test plan

(test-writer fills in)

## Attempt log
