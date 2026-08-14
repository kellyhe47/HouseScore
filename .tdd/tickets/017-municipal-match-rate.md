---
id: 017
title: "Report the R3.2 match rate against the municipality, not the territory block set"
status: tests-written
depends_on: [006, 008, 009]
touches: [src/houseaccount/resolve.py, src/houseaccount/pipeline.py, eval/harness.py, tests/test_resolve.py, tests/test_harness.py, tests/test_pipeline.py]
iterations: 0
test_files: [tests/test_resolve.py, tests/test_harness.py, tests/test_pipeline.py]
branch: ""
---

## Why this ticket exists (found by the first live pipeline run, 2026-08-14)

`make pipeline` against the real services reported `permit_match_rate: 0.62`, apparently failing
R3.2's ≥95% requirement. Diagnosis showed the join is fine and the **metric** is wrong:

- In-window Ramsey permits joining **any** Ramsey parcel by block/lot: **1696 / 1741 = 0.9742.**
- The reported 0.62 uses ticket 006's denominator — in-window permits whose *block* the territory
  occupies. A block holds many parcels, so permits on the ~4,600 municipal parcels that are not
  among our 540 sit in the denominator and can never match. The rate under-reports by construction.

Ticket 006 chose that denominator to avoid a trivially-1.0 rate, which was the right instinct for
the wrong statistic. The rubric asks how well permits resolve to parcels — a municipality-wide
question. Both numbers should be reported, and the ≥0.95 gate should read the municipal one.

The residual 2.6% misses are genuine and should be visible, not hidden: block/lot placeholders
(`0000`/`00`), and permits naming lots absent from the parcel file (e.g. block 4203 lot 4).
Lot-suffix variants like `4.2` vs `4.02` are **distinct lots** in NJ MOD-IV convention and must
NOT be collapsed by the normalizer.

## Acceptance criteria

- [ ] `resolve(...)` accepts the full municipal parcel set (all classes, not just the territory)
      and reports `municipal_match_rate` = in-window permits joining any municipal parcel /
      all in-window permits, alongside the existing territory-scoped rates.
- [ ] The report distinguishes the two denominators clearly enough that a reader cannot confuse
      them (field names + a docstring that states which one R3.2 grades).
- [ ] Permits carrying placeholder block/lot values are counted in their own bucket and reported,
      not silently folded into "unmatched".
- [ ] The existing territory-scoped fields keep their current meaning and values — no locked test
      in `tests/test_resolve.py` changes its expected numbers.
- [ ] `eval/harness.py`'s ≥0.95 gate reads the **municipal** rate; `MATCH_RATE_FLOOR` unchanged.
- [ ] `pipeline.py` passes the municipal parcel set through, and a live-shaped test pins that the
      published manifest carries both rates.
- [ ] Running the real pipeline afterwards reports a municipal match rate ≥ 0.95.
