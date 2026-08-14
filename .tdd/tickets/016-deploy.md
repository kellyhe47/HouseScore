---
id: 016
title: "Deploy readiness: Fly.io server + Vercel UI (R12) — expected blocked-on-human"
status: pending
depends_on: [011, 012]
touches: [Dockerfile, fly.toml, vercel.json, docs/DEPLOY.md, tests/test_deploy_config.py]
iterations: 0
test_files: []
branch: ""
---

## Scope

Build everything a deploy needs and stop at the credential boundary. Per the run rules: **do not
attempt account creation**; finish deploy-ready with documented commands and mark this ticket
`blocked` on the human for the actual `flyctl`/`vercel` invocation.

### Scope amendment (orchestrator, from 014's live probe)

`web/ethics.html` fetches `eval/report.json` and `data/run_manifest.json` through
`window.HOUSEACCOUNT_ARTIFACT_BASE` (default `..`, the repo root relative to `web/`), and
`web/js/map.js` fetches `doors.geojson` through `window.HOUSEACCOUNT_API_BASE` (default `/api`).
A static host serving `web/` as the site root resolves neither. This ticket must close that:
serve both JSON artifacts from the API (e.g. `GET /api/report.json`, `GET /api/manifest.json`) and
have the deploy configuration set both base URLs, so the published page shows real numbers rather
than its honest "no published run" fallback.

## Acceptance criteria

- [ ] `Dockerfile` builds the FastAPI server: python 3.12 base, installs from `pyproject.toml`,
      copies `src/` and `data/`, runs uvicorn on `$PORT` defaulting to 8000, non-root user.
- [ ] `fly.toml` declares the app, internal port 8000, an HTTP health check on `/health`, and
      the env vars as secrets (never values).
- [ ] `vercel.json` serves `web/` statically and injects the API base URL from an env var so the
      UI is not hardcoded to localhost.
- [ ] `docs/DEPLOY.md` gives the exact ordered commands for both deploys and states plainly which
      steps require human credentials.
- [ ] `tests/test_deploy_config.py` asserts the three config files exist and contain the required
      directives (port, health check, build command, env-var indirection) and that **no secret
      values** appear in any of them.
- [ ] Final status: `blocked` with the reason "requires Fly.io/Vercel credentials — human step",
      everything else green.
