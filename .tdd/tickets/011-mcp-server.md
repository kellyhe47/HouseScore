---
id: 011
title: "MCP server + HTTP API for the UI (R8)"
status: pending
depends_on: [009, 010]
touches: [src/houseaccount/server/app.py, src/houseaccount/server/mcp_tools.py, src/houseaccount/server/api.py, tests/test_server.py, tests/test_mcp_tools.py]
iterations: 0
test_files: []
branch: ""
---

## Scope

FastAPI app serving two surfaces from the published artifacts: the MCP tool surface (R8) and the
REST endpoints the Map UI consumes. Route planning delegates to `houseaccount.route` — no second
implementation (R10.3).

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
