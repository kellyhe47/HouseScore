---
id: 105
title: Server + route surfaces — API, MCP, published, talk-track template map (R30)
status: pending
depends_on: [103]
touches: [src/houseaccount/server/api.py, src/houseaccount/server/mcp_tools.py, src/houseaccount/server/published.py, src/houseaccount/route.py, tests/test_server.py, tests/test_mcp_tools.py, tests/test_route.py]
iterations: 0
test_files: []
branch: ""
---

## Scope

Migrate every Python-served surface to V2 (R30): API responses, MCP tool descriptions and
outputs (`get_door_score`, `explain_score`, `plan_route` — names unchanged per PRD),
`published.py` panel payloads, `route.py`. Replace the V1 talk-track angle table with a
deterministic template map keyed by V2 evidence type; per-door route reason chip = highest-
point evidence entry, ties by descending points then ascending evidence type. Value-percentile
and local-ratio evidence is unspeakable at the door (PRD R7.2.1) — excluded from talk tracks
and chips. Route aggregates computed from displayed V2 scores; shared route URL carries
`score_contract_version` and the payload signals mismatch-refresh (R27/R30).

## Acceptance criteria

- [ ] API/MCP/panel responses expose V2 fields (categories, base, mover, mover_lift, rental_modifier, adjustment, score, score_contract_version, confidence, data_gaps); no V1 group names/maxima in any served string (tested by template inspection).
- [ ] Template map covers every V2 evidence type (exhaustiveness test against the engine's evidence-type registry); R15 phrasing = neighborhood-level prior, never household claim; R19 phrasing = historical decline, never current condition.
- [ ] Chip selection rule tested incl. tie-break; percentile/local-ratio types never surface in talk track or chip.
- [ ] Every served evidence list reconciles to the displayed score (R7 invariant test at the API boundary).
- [ ] Route URLs / shared payloads carry score version; mismatched version → refresh signal.
- [ ] Live probe (orchestrator, gate): server on port 8100, hit /api doors + MCP tools, verify V2 envelope + reconciliation on a real door.
- [ ] Full suite + test-golden green.

## Test plan

## Attempt log
