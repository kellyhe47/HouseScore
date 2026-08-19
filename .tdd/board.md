# HouseAccount — TDD run board — V2 (Door Score V2)

V1 run history: `.tdd/board-v1.md` (tickets 001–025, complete). This board owns tickets 101+.

**Resume procedure:** Read this board + `.tdd/config.md` + `git log --oneline -15` + run
`make test` and (once it exists) `make test-golden`. Trust disk over any prior summary.
Continue with the first ticket not green/blocked, at the step its status names.

**Working branch:** `feature/score`. Local commits only — **never push.**
**Unattended run:** never pause for approval; blocked tickets get a one-line question here.
Per-ticket cap: 5 implementation iterations.

## Definition of done (handoff)

- `validate-spec` + `test-golden` pass from clean checkout; full golden suite in CI (Makefile target).
- 540 doors recalculated in one versioned run; R34 report + R35 audit pass.
- Every R28–R32 surface regenerated/migrated; ethics page satisfies R29 (ICP trace + R36 plan);
  README/PRD/DEPLOY/handoff counts reconciled (R31).
- R36 joinability: score version + attempt-time score retained in outcome log contract.

## Board

| # | Ticket | Status | Iters | Depends | Mode |
|---|---|---|---|---|---|
| 101 | V2 scoring engine + golden adapter (test-golden), R1–R26/R37 | green | 1 | — | seq |
| 102 | Live evidence-bundle builder (real sources → V2 ScoreInput, R8/R13/R14/R22/R23) | green | 1 | 101 | seq |
| 103 | Cutover: pipeline/publish/SQLite/manifest V2 + delete V1 path + 540-door versioned run (R27/R28) | green | 1 | 102 | seq |
| 104 | R34 recalculation report + PII check + eval harness replacement (R32/R34) + R36 outcome-log contract | pending | 0 | 103 | seq |
| 105 | Server + route surfaces: API, MCP, published, talk-track template map (R30) | green | 1 | 103 | seq |
| 106 | Web UI: map ramp restop (R38), panel math, route chips, degradation copy | pending | 0 | 104,105 | batch W-web |
| 107 | Ethics page V2 (R29) | pending | 0 | 104 | batch W-web |
| 108 | R35 stale audit + allowlist; docs R31 (README/PRD/DEPLOY/handoff/wireframes pointers) | pending | 0 | 103,104,105,106,107 | seq |

## Wave plan

101 → 102 → 103 → 104 → 105 → (106+107 batched, one test-writer/implementer pair, web area) → 108.
All sequential in the main working dir — heavy shared-file contention (pipeline/publish/server
chain); 104/105 could parallelize but share eval-report consumers and the Makefile; serialized.

## Status log

- 2026-08-19: board created; baseline `5686c52` green (1476 py pass, 18 skip + 273 js).
  validate-spec passes (42 fixtures, anchors hold).
- 101 green iter 1 (tests a515dad, impl 4f7e527): 42/42 fixtures + 59 boundary unit tests via real engine; red gate verified (101 failing pre-impl); full suite exit 0.
- 102 green iter 1 (tests a620a59, impl HEAD): bundle.py, 22 tests, 540/540 disk-only integration; full suite/js/golden exit 0.
- 103+105 implemented together (105 tests pulled forward: engine deletion import-breaks server/route tests).
  Tests 7cb971a + 5327cc5, impl 46db641. V1 engine/weights/eval-golden/verify_claims deleted;
  540/540 republished v2 (as_of 2026-08-15, warm cache, $0, zero network; scores 2-92, mean 12.4).
  KNOWN BRIDGE: 4 js reds in ethics.test.js (pins of deleted weights.py) — owned by 106/107.
