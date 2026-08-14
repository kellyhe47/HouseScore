---
id: 011
title: "MCP server + HTTP API for the UI (R8)"
status: green
depends_on: [009, 010]
touches: [src/houseaccount/server/app.py, src/houseaccount/server/mcp_tools.py, src/houseaccount/server/api.py, src/houseaccount/publish.py, tests/test_server.py, tests/test_mcp_tools.py]
iterations: 1
test_files: [tests/test_server.py, tests/test_mcp_tools.py]
branch: ""
---

## Scope

FastAPI app serving two surfaces from the published artifacts: the MCP tool surface (R8) and the
REST endpoints the Map UI consumes. Route planning delegates to `houseaccount.route` — no second
implementation (R10.3).

### Scope amendment (orchestrator decisions on the test-writer's three scope questions)

1. **`explain_score` needs group math that nothing publishes.** Approved: `publish` gains
   `doors.groups` (TEXT, JSON object, NULL when unscored) and `doors.raw_total` (INTEGER, NULL
   when unscored) in the **SQLite** artifact. Not in `doors.geojson` — a locked test freezes that
   property allowlist. Rejected the alternative of re-deriving groups by summing evidence points
   per type, which would put a second copy of engine knowledge in the server.
2. **Full-situs lookup.** Approved: both `"12 OAK ST"` and the published
   `"12 OAK ST, Ramsey NJ 07446"` resolve to the same door. R9.1 hands the rep that full string to
   copy, so a rep pasting it back must work.
3. **Missing `data/`.** Approved: fail fast at construction with the path it looked in, rather
   than a live server answering 200 over an empty territory. A reviewer who has not run the
   pipeline gets a sentence telling them to.

## Acceptance criteria

- [ ] The MCP tool registry contains **exactly** three tools, named `get_door_score`,
      `explain_score`, `plan_route` — assert the full list, so an extra tool fails the test.
- [ ] `get_door_score(address)` → `{score, confidence, evidence[]}`.
- [ ] `explain_score(address)` → the full breakdown: per-group math (`mover`, `hires_out`,
      `capacity`, `need`, `modifier`), `raw_total`, final `score`, plus the evidence list.
- [ ] `plan_route(hours, start_point, max_doors?)` → ordered stops each with a talk track;
      monkeypatching `houseaccount.route.plan_route` changes the tool's output, proving delegation.
- [ ] Addresses resolve through the T002 normalizer: `"12 oak st"`, `"12 OAK STREET"` and
      `"12 Oak St."` all hit the same door.
- [ ] An unknown address returns a **structured error** with a nearest-match suggestion — not a
      raised exception and not a 500.
- [ ] REST for the UI: `GET /api/doors.geojson`, `GET /api/door/{pin}`, `POST /api/route`,
      `GET /health`. CORS allows the UI origin.
- [ ] The app boots under uvicorn and `/health` answers 200 (live probe as part of the gate).

## Attempt log

- iter 1: green (39 tests). Live probe against real data: `/health` → 540 doors,
  `POST /api/route` returns ordered stops with talk tracks, MCP `tools/list` over streamable
  HTTP returns exactly the three R8 names, `explain_score` returns real group math
  (`{capacity, hires_out, modifier, mover, need}` + `raw_total`).
- A fourth module `server/published.py` holds `DataUnavailable` + the artifact reader, because
  both `app` and `mcp_tools` need them and `app` imports `mcp_tools` (cycle otherwise).
  `app.py` re-exports `DataUnavailable`, so the pinned seam holds.
- `/mcp` is adopted into the app's route table rather than `Mount`ed: a Mount answered
  `POST /mcp` with a 307 to `/mcp/`, which a client that doesn't re-POST on redirect sees as an
  empty body.
- Fixed `make serve`, which pointed at `app:app` — a target that never existed.
