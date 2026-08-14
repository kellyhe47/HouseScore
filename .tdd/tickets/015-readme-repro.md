---
id: 015
title: "README, reproducibility guarantees, cost report (R13, R14)"
status: tests-written
depends_on: [009, 011]
touches: [README.md, .env.example, tests/test_repro.py]
iterations: 0
test_files: [tests/test_repro.py]
branch: ""
---

## Scope

Make a reviewer able to clone and re-run everything, and prove there are no secrets in the tree.

## Acceptance criteria

- [ ] `README.md` documents, in order: prerequisites, `make setup`, the three env vars and which
      are optional, `make pipeline`, `make eval`, `make serve`, `make test`, and where the
      published artifacts land.
- [ ] README documents the data sources with their licences/ToS position and links the Data &
      Ethics page; it also records the deliberate GeoPandas→shapely deviation from R12.
- [ ] `.env.example` lists `ANTHROPIC_API_KEY`, `CENSUS_API_KEY`, `GOOGLE_MAPS_KEY` with empty
      values and a one-line note each.
- [ ] `tests/test_repro.py` asserts: no secret-shaped literal in `src/`, `eval/`, `web/`
      (`sk-ant-`, `AIza`, long hex assigned to a key-named variable); every `make` target named in
      the README exists in the Makefile; every env var named in the README appears in
      `.env.example` and vice versa.
- [ ] README states the projected API spend (≤$50 budget, projected $0–5) and points at the
      cost-per-door line in `make eval` output (R14).
