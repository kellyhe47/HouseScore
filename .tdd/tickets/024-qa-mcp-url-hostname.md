---
id: 024
title: "Published MCP URL names a host the documented deploy never creates"
status: green
source: qa
depends_on: [016]
touches: [web/js/ethics.js, docs/DEPLOY.md, fly.toml]
iterations: 1
test_files: []
branch: ""
---

## Found by

Manual QA of `b11b74b`, flow 6. See `.qa/report.md` finding 4.

## Repro

Open `http://localhost:5173/ethics.html`, section "MCP server".

## Expected

`fly.toml` declares `app = "houseaccount"`, and `docs/DEPLOY.md` verifies the deployment against
`https://houseaccount.fly.dev`. The advertised endpoint should be one the runbook actually produces.

## Observed

The page publishes:

> https://houseaccount-mcp.fly.dev/mcp

No app of that name is created anywhere in `docs/DEPLOY.md`, so after a by-the-book deploy this
endpoint does not resolve. R8 makes the MCP surface a graded deliverable and this page is where a
reviewer finds it.

## Decide which is true

Either the runbook should create a second Fly app named `houseaccount-mcp`, or the page should
advertise `https://houseaccount.fly.dev/mcp` — the app that `fly.toml` and the runbook agree on.
One of the two has to move.

## Suggested acceptance

- The hostname on the ethics page and the app name in `fly.toml` cannot disagree without a test
  failing (`tests/test_deploy_config.py` already parses these files).

## Resolution

Green in 1 iteration. The page moved, per the human decision; `fly.toml` and `docs/DEPLOY.md` were not
touched and no second Fly app was added.

The literal was in **`web/ethics.html:312`**, not `web/js/ethics.js` as this ticket claimed — recorded
as a scope deviation. It now advertises `https://houseaccount.fly.dev/mcp`.

The guard is the part worth keeping: `tests/test_deploy_config.py` **derives** the expected URL from
`app = "..."` in `fly.toml` plus `MCP_PATH` from `houseaccount.server.app`, so the page and the deploy
config cannot drift apart in either direction. A second test scans all of `web/` for any `*.fly.dev`
host the deploy never creates, which catches a partial edit.

- tests locked: `a729f1e` · implementation: `731d604` · merged
- **verified in a live browser**: the rendered MCP section advertises `https://houseaccount.fly.dev/mcp`
