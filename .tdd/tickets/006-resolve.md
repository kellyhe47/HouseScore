---
id: 006
title: "Entity resolution + match-rate reporting (R3)"
status: pending
depends_on: [002, 004, 005]
touches: [src/houseaccount/resolve.py, tests/test_resolve.py]
iterations: 0
test_files: []
branch: ""
---

## Scope

Join permits / ACS / rental signals onto territory parcels keyed by `PAMS_PIN`, and report the
match rate the rubric grades (R3.2, target ≥95%).

Phase 0 finding that changes R3.1's assumption: the permit dataset carries `block`/`lot`, not a
situs address. So the **primary** join is `parcel_key(mun, block, lot)` (T002) and the normalized
situs address is the **fallback**. Report both rates.

## Acceptance criteria

- [ ] `resolve(parcels, permits, acs_by_bg, rental_provider, as_of) -> ResolveResult` with
      `.doors: dict[PAMS_PIN, DoorFacts]` and `.report`.
- [ ] `DoorFacts` carries exactly what the score engine needs: parcel fields, `permits_2yr`,
      `acs_block_group`, `rental_registration_match`, plus provenance (source name + retrieval
      date per signal) for R7.1 evidence.
- [ ] Permits join on block/lot first; unjoined permits retry on normalized situs address;
      anything still unjoined lands in `.report.unmatched` — **never silently dropped**.
- [ ] `.report.permit_match_rate` = matched / (permits whose block+lot falls in the territory);
      a fixture of real-shaped records with a known answer pins the arithmetic, and a ≥0.95 case
      asserts the rubric threshold is representable and reported.
- [ ] `.report.coverage` = doors with ≥1 non-parcel source / total territory doors.
- [ ] Determinism: same inputs → identical `.doors` ordering and identical report numbers.
- [ ] A door with zero permits, no ACS and no rental data still resolves to valid `DoorFacts`
      (it will simply score low) — resolution never drops a territory door.
