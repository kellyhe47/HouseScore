---
id: 013
title: "Route UI + walk mode (R10, wireframe frames 4–4d)"
status: tests-written
depends_on: [010, 011, 012]
touches: [web/js/route-ui.js, web/js/walk.js, web/js/share.js, web/js/panel.js, web/js/route-ui.test.js, web/js/walk.test.js, web/js/share.test.js, web/js/panel.test.js, web/index.html, web/js/map.js, src/houseaccount/server/api.py, src/houseaccount/server/published.py, tests/test_server.py]
iterations: 0
test_files: [web/js/route-ui.test.js, web/js/walk.test.js, web/js/share.test.js, web/js/panel.test.js, tests/test_server.py]
branch: ""
---

## Scope

The field-rep flow: plan a route, adjust it live, walk it. Logic in ES modules; `POST /api/route`
(T011) is the only planner — never re-implement ordering in JS.

### Scope amendment (orchestrator, after 012's live probe)

The approved prototype's evidence panel has **"Rep talk track"** (R7.2) and **"Score breakdown"**
sections that 012 could not build: `GET /api/door/{pin}` returns only the published properties.
The server already has both ingredients — group math in SQLite (ticket 011 amendment 1) and
`route.talk_track_for` — so this ticket also:

- extends `GET /api/door/{pin}` with `groups`, `raw_total` and `talk_track`;
- renders both sections in the evidence panel, matching the prototype, including the
  "→ capped 100" / "→ floored 0" clamp line from DESIGN-ADDITIONS.

## Acceptance criteria

- [ ] `requestRoute({hours, start, maxDoors})` posts to `/api/route` and maps the response into
      row view-models; changing hours or door count re-requests (live adjust, frame 4b).
- [ ] `excludeStop(pin)` re-plans without that door and the resulting order is deterministic.
- [ ] Route rows display **elapsed offsets** (`"+42 min"`), never clock times (R10.2).
- [ ] Walk mode: per-stop `done` / `skip`; state persists to `localStorage` under a stable key.
- [ ] Returning with a walk in progress offers **both** Resume and Discard; Discard clears the
      stored state (R10.4 / DESIGN-ADDITIONS).
- [ ] Finishing a walk yields the summary `"X doors knocked · Y skipped"` (R10.4).
- [ ] `encodeShare(stops)` writes the ordered PIN list into the URL fragment and `readShare()`
      restores exactly that order (must round-trip against the server's `encode_share` format).
- [ ] `copyAsText(stops)` emits one line per stop with address + talk track, and fires a toast.
- [ ] Live probe: plan a route in the browser against the running server, enter walk mode, mark a
      stop done, reload, and confirm the resume banner appears — zero console errors.
