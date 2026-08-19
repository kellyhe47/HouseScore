---
id: 108
title: R35 stale-contract audit + versioned allowlist; docs reconciliation (R31/R33)
status: pending
depends_on: [103, 104, 105, 106, 107]
touches: [scripts/audit_stale_contract.py (new), eval/v2/allowlist-historical.json (new), tests/test_stale_audit.py, README.md, docs/PRD.md, docs/DEPLOY.md, docs/handoff-prompt.md, Makefile]
iterations: 0
test_files: []
branch: ""
---

## Scope

Repository-wide stale-contract audit (R35): scans active surfaces for V1 assertions (old
additive formula, old group names/maxima, mover 90-day hard cutoff as current, −15 rental,
provider churn, deferred-maintenance bonus, `raw_total`/`groups` as current schema) and fails
unless the path is on the explicit versioned allowlist. Start allowlist: dated review logs,
`docs/HouseAccount-Prototype.html`, `prototype-decoded.html`, `.qa/report.md` (+ `.tdd/`
board history). Allowlist changes are reviewed changes — file lives in-repo, versioned.
Wire into `make test` (or a `make audit` target run by CI + eval).

Docs (R31): README, `docs/PRD.md` (mark scoring sections superseded, pointer to the V2 plan),
`docs/DEPLOY.md`, `docs/handoff-prompt.md` fixture counts reconciled with `eval/report.json`
(42); `docs/ui-wireframes.html` already V2 — verify and pointer only. Field-outcome
attempt-time scores documented as R33 historical records.

## Acceptance criteria

- [ ] Audit passes on the finished repo; fails (demo: plant a V1 formula sentence in README, observe failure, revert — after checkpoint commit) on any V1 assertion outside the allowlist.
- [ ] Adding an allowlist path requires editing the versioned file (no runtime heuristics); audit output names offending file+line.
- [ ] README/PRD/DEPLOY/handoff name V2 as current scoring authority; all fixture/door counts match eval/report.json.
- [ ] Full suite + test-golden + validate-spec green from a clean state.

## Test plan

## Attempt log
