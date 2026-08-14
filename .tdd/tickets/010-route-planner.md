---
id: 010
title: "Route planner module — one implementation shared by MCP and UI (R10.1–R10.3)"
status: green
depends_on: [003]
touches: [src/houseaccount/route.py, tests/test_route.py]
iterations: 1
test_files: [tests/test_route.py]
branch: "tdd/010" (merged)
---

## Scope

`src/houseaccount/route.py` — the single route implementation. R10.3 is explicit: the MCP
`plan_route` tool and the Map UI both call this module (the UI via the server's HTTP surface).
No paid routing API: walking time is straight-line distance × a detour factor.

## Acceptance criteria

- [ ] `plan_route(doors, hours, start_point, max_doors=None) -> Route` whose `stops` carry
      `pams_pin`, `address`, `score`, `walk_minutes` (leg), `cumulative_minutes`, `talk_track`.
- [ ] Greedy selection by score per walk-minute from the current position.
- [ ] Walking time = haversine distance × detour factor 1.3 at 3 mph, exposed as named constants
      and labelled an estimate in the returned object.
- [ ] The hour budget is respected: `stops[-1].cumulative_minutes <= hours * 60`, and a budget too
      small for any door returns an empty route rather than raising.
- [ ] `max_doors` caps the stop count when supplied.
- [ ] Ties (equal score-per-minute) break deterministically by `PAMS_PIN`; the same inputs always
      produce the same ordering.
- [ ] 540 candidate doors plan in **under 2 seconds** (timed test).
- [ ] Doors with `score is None` are never routed.
- [ ] `exclude(pins)` re-plans without those doors, preserving determinism.
- [ ] `encode_share(stops)` → a compact URL-fragment string; `decode_share` round-trips the exact
      ordered PIN list.

## Attempt log

- iter 1: implementation green on 80/81. The 81st was a genuine TEST bug — the parametrized
  `{"score": None}` case expanded to `door(..., score=100, score=None)`, a TypeError at argument
  binding that no implementation could satisfy. Verified by the orchestrator, fixed through the
  test-writer (not the implementer), re-locked. Did not consume an implementation iteration.
- Share links are deflate + base64url with an `r1` prefix (20 pins → 105 chars vs 339 naive);
  garbage decodes to `()` rather than raising. `route.py` imports stdlib only — a locked test
  enforces that it never pulls in the pipeline (R10.3).
