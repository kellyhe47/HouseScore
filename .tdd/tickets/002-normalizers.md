---
id: 002
title: "Normalizers: deed YYMMDD parse, address normalization, parcel/permit join keys"
status: tests-written
depends_on: [001]
touches: [src/houseaccount/normalize.py, tests/test_normalize.py]
iterations: 0
test_files: [tests/test_normalize.py]
branch: ""
---

## Scope

One module of pure functions: `src/houseaccount/normalize.py`. No I/O, no network.
Fixture 11 (`eval/golden/11_deed_date_yymmdd_parse.json`) is the contract for the deed parser and
the tests must be driven from that file, not from copied literals.

## Acceptance criteria

- [ ] `parse_deed_date(raw, as_of)` reproduces **every** case in fixture 11 (test reads the JSON).
- [ ] The century pivot is derived from `as_of.year` (`YY <= as_of.year % 100 + 1` → 2000s, else
      1900s) — not a hardcoded 2026. Verify with a second `as_of` (e.g. 2030-01-01) that shifts a
      boundary case.
- [ ] Structurally-valid-but-impossible dates (`"261332"`, `"260230"`) → `None`, no exception.
      Empty / short / non-digit input → `None`.
- [ ] An already-ISO string (`"2026-07-12"`) passes through to the same date — the score engine
      is fed both shapes (golden fixtures use ISO, live MOD-IV uses YYMMDD).
- [ ] `normalize_address(s)` → uppercased, punctuation stripped, whitespace collapsed, street-type
      abbreviations standardised (ST/STREET, AVE/AVENUE, RD/ROAD, DR/DRIVE, LN, CT, PL, TER,
      BLVD, WAY, CIR), unit designators dropped. `"12  Oak  St."`, `"12 OAK STREET"`,
      `"12 Oak St, Unit 2"` all normalise to the same key.
- [ ] `parcel_key(mun, block, lot)` tolerates leading zeros, whitespace and float-ish lot strings
      so that permit `block`/`lot` (Socrata) joins parcel `PCLBLOCK`/`PCLLOT` (ArcGIS).
      `parcel_key("0248", " 2702 ", "15")` == `parcel_key("248", "2702", "15.0")`.
- [ ] `situs_display(prop_loc, zip5)` → `"12 OAK ST, Ramsey NJ 07446"` (the copy-address string,
      R9.1); missing zip degrades to `"12 OAK ST, Ramsey NJ"`.
