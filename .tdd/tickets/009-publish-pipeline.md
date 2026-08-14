---
id: 009
title: "Publish artifacts + end-to-end pipeline orchestrator (R2.2, R2.3)"
status: green
depends_on: [003, 004, 005, 006, 007]
touches: [src/houseaccount/publish.py, src/houseaccount/pipeline.py, tests/test_publish.py, tests/test_pipeline.py]
iterations: 1
test_files: [tests/test_publish.py, tests/test_pipeline.py]
branch: ""
---

## Scope

Turn resolved+scored doors into the serving artifacts, and wire the whole run into one command:
`make pipeline` → harvest → resolve → vision → score → publish, zero manual steps.

## Acceptance criteria

- [ ] `publish(doors, ...)` writes `data/doors.geojson`: a FeatureCollection where each feature has
      `PAMS_PIN`, `score` (int or null), `confidence`, `evidence[]`, the situs display address, and
      geometry.
- [ ] Same call writes a SQLite db (`doors` + `evidence` tables) and `data/run_manifest.json`.
- [ ] The manifest records: run timestamp, `territory_median_value` (computed once per run),
      the ACS dual-income threshold, per-source retrieval dates, doors scored / doors total,
      total cost, and a code version — everything needed to reproduce the run.
- [ ] Unscored doors are published with `score: null` and an `exclusion_reason`
      ("parcel record incomplete in county data") and are counted in the coverage numbers (R9.4).
- [ ] Guard: no identity field appears anywhere in `doors.geojson` or the SQLite db.
- [ ] `python -m houseaccount.pipeline` runs all five stages in order and, with a warm cache,
      completes with **zero** network calls (inject a transport that raises).
- [ ] Degradation: an unavailable optional source (ACS declination, vision without a key, rental
      provider declining) is logged and the run still completes and publishes.
- [ ] Idempotent: two consecutive runs on the same warm cache produce identical `doors.geojson`
      content (manifest timestamp excepted).

## Attempt log

- iter 1: green. First live `make pipeline` completed in 12s, publishing 540/540 doors with
  ACS, rental and vision all declining for missing credentials — the degradation path is the
  one that actually runs here, and it published anyway.
- Only the parcel harvest is load-bearing; every other source's SourceError is a logged
  degradation recorded in the manifest.
- The live run surfaced the match-rate reporting defect that became ticket 017.
