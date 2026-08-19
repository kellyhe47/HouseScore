---
id: 104
title: R34 recalculation report + PII check + eval harness replacement + R36 outcome-log contract
status: green
depends_on: [103]
touches: [eval/v2/report.py (new), eval/report.json, Makefile (eval target), src/houseaccount/outcomes.py (new), tests/test_v2_report.py, tests/test_outcomes.py]
iterations: 1
test_files: [tests/test_v2_report.py, tests/test_outcomes.py]
branch: ""
---

## Scope

`make eval` becomes: run test-golden (42 fixtures) + generate the R34 report from the
published run — score coverage, category distributions, score distribution (quantiles usable
by R38 ramp), missing-signal counts, source freshness, per-conservative-rule
exclusion/dedup counts, retained vision metrics (PRD R5.1: P/R on top signal, hallucination
rate, cost per door — carried from the existing vision eval path with its NOT-A-MEASUREMENT
banner), and an automated PII scan asserting no published artifact contains owner names,
mailing addresses, or permit-agent identity fields (R25/R34). Output `eval/report.json` (V2
shape) consumed by README claims and ethics page.

R36: a small outcome-log contract module — schema/validator for field-outcome records that
retain `score_contract_version` and attempt-time score, primary outcome (receptive/qualified
conversation per answered door) + secondary (follow-up requested, service booked), band/quantile
join helper. No capture UI (deferred by plan).

## Acceptance criteria

- [ ] `make eval` runs golden suite + emits R34 report deterministically from published artifacts (no network); re-run → identical report given identical run.
- [ ] Report contains: 540 coverage, per-category distributions, score quantiles, missing-signal counts by gap type, source freshness dates, exclusion counts per conservative rule (stale-open, voided/admin, non-arm's-length, dup-coalesced), vision metrics block, PII-scan result.
- [ ] PII scan fails the eval (nonzero exit) when a planted owner-name field appears in a published artifact (mutate-and-revert demo after checkpoint commit).
- [ ] Outcome-record validator rejects records missing score version or attempt-time score; accepts the documented shape; band-lift helper computes rates by quantile deterministically.
- [ ] Full suite + test-golden green.

## Test plan

## Attempt log
