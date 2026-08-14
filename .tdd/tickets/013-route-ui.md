---
id: 013
title: "Route UI + walk mode (R10, wireframe frames 4–4d)"
status: green
depends_on: [010, 011, 012]
touches: [web/js/route-ui.js, web/js/walk.js, web/js/share.js, web/js/panel.js, web/js/route-ui.test.js, web/js/walk.test.js, web/js/share.test.js, web/js/panel.test.js, web/index.html, web/js/map.js, src/houseaccount/server/api.py, src/houseaccount/server/published.py, tests/test_server.py]
iterations: 1
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

## Attempt log

- iter 1: green (154 JS tests, 1145 python). Orchestrator live probe drove the whole flow against
  real data: Plan Route → set start on map → 20 numbered stops with scores, reason chips, talk
  tracks and `+N min` offsets → walk mode (done/skip) → reload → resume banner
  "Walk in progress — stop 3 of 20" with BOTH Resume and Discard → localStorage held
  `{schema:1, index:2, knocked:1, skipped:1, rows:20}`.
- Share format is mirrored in JS via `CompressionStream('deflate')`. It emits a different zlib
  compression level than Python's (`eJw` vs `eNo`), so the bytes differ while both sides decode
  each other; two golden-token tests pin that in both directions.
- `POST /api/route` gained `exclude`, so excluding a stop re-plans server-side through
  `Route.exclude` rather than filtering in JS — R10.3 holds.

## Cosmetic defects found in the live probe (carried to the final polish pass)

1. The header buttons still carry stale `title` tooltips reading "…ships in the next build" —
   both features now ship. User-visible misinformation; fix.
2. The map does not refit its bounds on viewport resize, so a desktop↔mobile resize leaves the
   territory small and off-centre until reload. Initial load at any size is correct.
