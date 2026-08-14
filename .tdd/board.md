# HouseAccount — TDD run board

**Resume procedure:** "Read this board + `git log --oneline` + `python3 eval/verify_claims.py`.
Trust disk over any prior summary. Continue with the first ticket not marked done/blocked."

Also read `.tdd/config.md` — it holds the verified test commands, the toolchain decisions, and the
live ground-truth findings from Phase 0, so a resumed session never re-derives them.

**Working branch:** `main`. Commits are local only — **never push.**
**Unattended run:** never pause for approval. Genuine ambiguity → mark the ticket `blocked` with a
one-line question here and move to the next ticket. Per-ticket cap: 5 implementation iterations
(handoff says 3; the skill's cap is 5 — we use 5 and record every attempt), then `blocked` with the
failing output in the ticket file.

## Definition of done (from docs/handoff-prompt.md)

- `make pipeline` — harvest→resolve→vision→score→publish, fresh clone, documented env vars only.
- `make eval` — 12 golden fixtures green via the **real** engine + vision P/R + hallucination +
  cost-per-door + entity-resolution match rate ≥95%.
- Map UI + MCP server (tools exactly `get_door_score`, `explain_score`, `plan_route`) deploy-ready
  and publicly reachable; Data & Ethics page live.
- API spend ≤ $50 (projected $0–5); caching/batching visible in code.
- README end-to-end; no hardcoded secrets.

## Invariant

No identity data, ever. `OWNER_NAME` / `ST_ADDRESS` / `CITY_STATE` appear only in
`tests/test_redaction.py`. Guard test lands in T001 and runs in every regression gate thereafter.

## Board

| # | Ticket | Status | Iters | Depends | Wave |
|---|---|---|---|---|---|
| 001 | Foundation: config, cache, HTTP, cost ledger, redaction guard | green | 1 | — | W1 seq |
| 002 | Normalizers: deed YYMMDD, address, join keys | green | 1 | 001 | W2 batch |
| 003 | Score engine + evidence (R6/R7) — 12 fixtures | green | 1 | 001,002 | W2 batch |
| 004 | Harvest: parcels + territory bootstrap (R1) | green | 1 | 001 | W3 par |
| 005 | Harvest: permits + ACS + rental seam (R2.1/R11.3) | green | 1 | 001 | W3 par |
| 006 | Entity resolution + match rate (R3) | green | 1 | 002,004,005 | W4 seq |
| 007 | Vision: schema, provider seam, ortho tiles (R4) | green | 1 | 001 | W3 par |
| 008 | Eval harness (R5/R14) | green | 1 | 003,007 | W5 par |
| 009 | Publish + pipeline orchestrator (R2.2) | green | 1 | 003,004,005,006,007 | W6 seq |
| 010 | Route planner module (R10.1–10.3) | pending | 0 | 003 | W5 par |
| 011 | MCP server + HTTP API (R8) | green | 1 | 009,010 | W7 seq |
| 012 | Map UI core (R9) | green | 1 | 009,011 | W8 seq |
| 013 | Route UI + walk mode (R10 frames 4–4d) | green | 1 | 010,011,012 | W8 seq |
| 014 | Data & Ethics page (R11.4) | green | 1 | 008,012 | W9 batch |
| 015 | README + reproducibility + cost report (R13/R14) | tests-written | 0 | 009,011 | W9 batch |
| 016 | Deploy readiness (R12) — expected blocked-on-human | pending | 0 | 011,012 | W10 |
| 017 | R3.2 match rate: municipal denominator, not territory blocks | green | 1 | 006,008,009 | W6b seq |
| 018 | make eval reads the published run manifest | tests-written | 0 | 009,017 | W9 batch |

## Wave plan

W1 `001` seq · W2 `002`+`003` batched seq · W3 `004`‖`005`‖`007` worktrees ·
W4 `006` seq · W5 `008`‖`010` worktrees · W6 `009` seq · W7 `011` seq ·
W8 `012`→`013` seq (same agents, web area) · W9 `014`+`015` batched · W10 `016`.

## Findings from the first live pipeline run (2026-08-14)

`make pipeline` completes against the real services in **12 seconds**, publishing **540 of 540**
doors. Territory median NET_VALUE = $743,350. Score spread: max 77, min 8, with 11 doors ≥60 —
the score separates doors rather than rating everyone warm, which is what fixture 05 demanded.
ACS, rental and vision all declined for missing credentials and were logged as degradations; the
run still published every door. The permit match-rate defect this surfaced became ticket 017.

## Open questions raised during the run

_(none yet — appended here when a ticket is marked blocked)_
