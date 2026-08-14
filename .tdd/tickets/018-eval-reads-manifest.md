---
id: 018
title: "make eval reads the published run manifest (match rate, cost, doors scored)"
status: pending
depends_on: [009, 017]
touches: [Makefile, eval/harness.py, tests/test_harness.py]
iterations: 0
test_files: []
branch: ""
---

## Why this ticket exists (found immediately after 017 landed)

The definition of done says `make eval` reports the entity-resolution match rate and cost per
door. It currently prints `municipal match rate: n/a (no resolve report for this run)` and
`cost per door: $0.0000` even when `data/run_manifest.json` is sitting right there with real
numbers, because bare `make eval` passes no `--resolve-report` / `--ledger` / `--doors-scored`.

The harness already accepts all three flags and is fully tested; this is a wiring gap, not a
logic gap.

## Acceptance criteria

- [ ] With `data/run_manifest.json` present, bare `make eval` reports the municipal match rate,
      the run cost and doors scored from it — no flags required.
- [ ] The harness reads the manifest's nested `resolve` block and its cost/doors fields, and
      accepts the manifest path wherever `--resolve-report` is accepted today.
- [ ] With no manifest on disk (a fresh clone that has not run the pipeline), `make eval` still
      exits 0 and says plainly that no run has been published yet — it must never fail merely
      because the pipeline has not been run.
- [ ] An explicit `--resolve-report` / `--ledger` / `--doors-scored` still overrides the
      auto-discovered manifest.
- [ ] The ≥0.95 gate still reads `municipal_match_rate` (ticket 017) and still fails the run when
      a published manifest is below it.
