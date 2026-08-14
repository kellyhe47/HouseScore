---
id: 005
title: "Harvest: construction permits + Census ACS + rental-registration seam"
status: green
depends_on: [001]
touches: [src/houseaccount/sources/permits.py, src/houseaccount/sources/acs.py, src/houseaccount/sources/rental.py, tests/test_permits.py, tests/test_acs.py, tests/test_rental.py]
iterations: 1
test_files: [tests/test_permits.py, tests/test_acs.py, tests/test_rental.py]
branch: "tdd/005" (merged, removed)
---

## Scope

Three non-parcel sources, each behind the T001 cache and each degrading rather than crashing when
its dependency is unavailable.

Ground truth verified live in Phase 0 (see `.tdd/config.md`):
- Permits: `https://data.nj.gov/resource/w9se-dmra.json?comu=0248` — Socrata, free, no key.
  Fields present: `block`, `lot`, `permitdate`, `permittype`, `permittypedesc`, `constcost`,
  `totalfee`, `usegroup`, `recordid`. **There is NO contractor field in this dataset.**
- ACS5: state 34 / county 003, block-group level, tables B23007 (dual-income proxy), B19013
  (median HH income). Keyless calls are blocked upstream and `CENSUS_API_KEY` is unset here.
- Rental registration: OPRA-request-only, may never arrive (R11.3).

## Acceptance criteria

- [ ] `PermitSource.fetch(comu="0248", since=...)` pages Socrata with `$limit`/`$offset` through
      the cache; a fake transport serving 2 pages yields all records; warm cache → zero calls.
- [ ] Records map to a `Permit` dataclass `{record_id, block, lot, date, type, contractor, cost}`
      where `contractor is None` for every record from this source — explicitly, with a
      module-level note, not as an accident.
- [ ] `permits_within(permits, as_of, days=730)` filters the rolling 2-year window inclusive of
      the boundary day and excludes the day after it.
- [ ] `AcsSource.fetch(...)` builds the correct ACS5 block-group URL for the configured tables and
      geography; with a canned response, `dual_income_pct` and `median_hh_income` come out per a
      documented formula over the B23007 components.
- [ ] With `CENSUS_API_KEY` unset, `AcsSource.fetch` returns a **declination result**
      (`available=False`, human-readable `reason`) and never raises; callers can still score
      (the ACS component simply contributes 0).
- [ ] `RentalRegistrationProvider` is a typed protocol taking a PAMS_PIN and returning `bool`.
      `NullRentalProvider` returns `False` for every PIN and reports a declination reason
      ("municipal rental registration not obtained via OPRA"); `FixtureRentalProvider(pins)`
      returns `True` for seeded PINs. Nothing else about a household is ever returned.
- [ ] No source module references owner names or mailing-address fields (guard test from T001
      covers this repo-wide; do not reintroduce them).

## Attempt log

- iter 1: green. `$order=:id` on the Socrata walk for the same offset-stability reason.
  `permits.Permit` re-exports the engine's type — no second Permit definition.
  ACS declination path is the one that runs today (no CENSUS_API_KEY in this environment).
