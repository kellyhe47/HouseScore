---
id: 102
title: Live evidence-bundle builder — real sources to V2 ScoreInput
status: green
depends_on: [101]
touches: [src/houseaccount/scoring/bundle.py, src/houseaccount/resolve.py, src/houseaccount/sources/, tests/test_v2_bundle.py]
iterations: 1
test_files: [tests/test_v2_bundle.py]
branch: ""
---

## Scope

New module (suggested `src/houseaccount/scoring/bundle.py`) that assembles, for each of the
540 territory parcels, the V2 normalized evidence bundle (the 101 dataclass) from the real
cached snapshots: territory parcels/MOD-IV, SR1A sales, statewide permits, SDL property
history (`data/sdl_property_history_territory.json`) + roof detail
(`data/sdl_roof_permits_territory.json`), ACS, imagery observations, rental seam.
Additive: V1 pipeline untouched; cutover is 103.

Key mechanics:
- R22 precedence table: MOD-IV primary for identity/assessed value/year/lot; SDL assessed
  valuation fills only on exact+current parcel match (fixtures 40/41 semantics). SR1A primary
  for move date, MOD-IV transfer fallback, SDL displayed sale only when arm's-length
  establishable. SDL primary for project lifecycle/description; statewide fills records
  absent from SDL (dedup by municipal record identity, richer municipal record retained, R8).
- R23: unavailable SDL page / blank field ≠ proof of absence → `sdl_page_unavailable` gap,
  door remains scoreable.
- Local comparables: up-to-20 nearest valid single-family by parcel-centroid distance
  (deterministic tie-break); territory assessed-value list for midrank percentile.
- Deed-date normalization (YYMMDD pivot) upstream, reused from existing normalize.py.
- No owner names / mailing addresses / agent identity in the bundle (redaction guard extends).

## Acceptance criteria

- [ ] `build_bundles(as_of)` (or equivalent) returns exactly one bundle per territory parcel (540) from the cached snapshots, zero network calls.
- [ ] R22 precedence unit-tested per row: lower-precedence fills gap, never overwrites fresher valid higher-precedence fact (assessed value MOD-IV vs SDL; move date SR1A vs MOD-IV vs SDL).
- [ ] SDL+statewide same municipal record coalesces to one project with `sources` listing both and SDL detail retained (R8); amendments/supplements collapse.
- [ ] Eight unavailable SDL pages produce `sdl_page_unavailable` gap and still-complete bundles (AE11 semantics at bundle level).
- [ ] Comparable selection: nearest-20 by centroid distance, subject excluded, invalid/non-single-family excluded; <10 available → truncated list passed through (engine handles neutrality).
- [ ] Bundle dataclass round-trips the golden `given.inputs` shape: fixture-loaded and live-built bundles use one constructor (the 101 production boundary).
- [ ] Redaction: bundle construction from real data emits no OWNER_NAME/ST_ADDRESS/CITY_STATE-derived fields (guard test).
- [ ] Full suite green; `make test-golden` still green.

## Test plan

## Attempt log
