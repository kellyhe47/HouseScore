# Door Score V2 — implementation handoff

You are implementing the Door Score V2 contract for HouseAccount (Ramsey, NJ door-knocking
intelligence; 540 single-family parcels, deterministic 0–100 score per door). This document
is self-contained — you do not need the original brief.

## Sources of truth, in precedence order

1. **`eval/v2/golden/` (42 fixtures) + `eval/v2/golden-manifest.json`** — the acceptance
   contract. Envelope conventions in `eval/v2/provenance.md` are binding.
2. **`docs/plans/2026-08-18-2221-feat-door-score-v2-plan.md`** — the V2 product contract
   (R1–R38). It supersedes the *scoring* requirements of `docs/PRD.md`; the rest of that
   PRD (surfaces, routes, ethics page, MCP contract) remains authoritative where the plan
   does not override it (R27–R35 list the overrides).
3. **`docs/ui-wireframes.html`** (V2-updated) and **`docs/architecture.excalidraw`**
   (generated from `docs/arch.spec.json` — edit the spec, regenerate, re-check).

Conflicts already settled: AE3 = 64.447; R17/R10 share one issue-date fallback rule; SDL is
primary for project lifecycle under the R22 precedence roles (R23's "supplemental" language
is dead); rental −25 ships dormant until a registry exists.

## Commands

- **`validate-spec`** — `python3 ~/.claude/skills/product-inception/scripts/validate_golden.py eval/v2/golden`
  then `python3 eval/v2/verify_fixtures.py`. Validates fixtures/schema/arithmetic/traceability. Runs today.
- **`test-golden`** — you build this: an adapter that maps `when.operation: score_door` to the
  real V2 engine entry point, loads `given` through production boundaries, canonicalizes the
  four observation surfaces per `provenance.md`, and deep-compares against every fixture by ID.
  Every fixture must first **fail for its intended reason**, then pass. Golden expectations are
  never edited to make implementation pass — an expectation change is a spec change (update
  provenance, manifest, review log).

## The invariant

**Every published score reconciles**: capped category subtotals (+ explicit cap-adjustment
entries) + mover lift + rental modifier + rounding/clamp adjustment = the displayed integer,
on every surface (panel, API, MCP, route). If you can't add the evidence list up to the score,
you broke R7/R30.

## Not yet built / dependency facts

- **Rental registry data does not exist.** R6's −25 path is an injected seam exercised only by
  fixtures; every door publishes the `rental_data_missing` gap.
- **SR1A ingest** feeds mover dates (primary); MOD-IV is fallback. The current run manifest is
  V1-shaped — R28 defines the V2 manifest (drop `territory_median_value`, `top_band_days`, etc.).
- **R38 ramp restop** consumes the R34 recalculation report — the report exists before the map change.
- **R35 stale-audit** needs the versioned historical-path allowlist (start: dated review logs,
  `docs/HouseAccount-Prototype.html`, `prototype-decoded.html`, `.qa/report.md`).
- V1 `eval/golden/` (13 fixtures), `eval/verify_claims.py`, SQLite `groups`/`raw_total`, and the
  talk-track angle table in `src/houseaccount/route.py` are all replaced in the one versioned run (R27/R32).

## Before dispatching sub-agents

Put the run on disk first: ticket board, per-ticket scope + acceptance criteria, the two
commands above, and a resume procedure — the run must be resumable from disk after any stall.

## Definition of done

- `validate-spec` and `test-golden` pass from a clean checkout; full golden suite in CI.
- All 540 doors recalculated in one versioned run; R34 report + R35 audit pass (no V1 claim on
  any active surface; allowlisted history only).
- Every surface in R28–R32 regenerated/migrated; ethics page satisfies R29 (including ICP trace
  + R36 validation plan); README/PRD/DEPLOY/handoff counts reconciled (R31).
- Field-outcome joinability per R36 (score version + attempt-time score retained in the outcome log).
