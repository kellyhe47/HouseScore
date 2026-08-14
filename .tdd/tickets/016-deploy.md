---
id: 016
title: "Deploy readiness: Fly.io server + Vercel UI (R12) — expected blocked-on-human"
status: blocked
depends_on: [011, 012]
touches: [Dockerfile, fly.toml, vercel.json, docs/DEPLOY.md, tests/test_deploy_config.py]
iterations: 1
test_files: [tests/test_deploy_config.py, tests/test_server.py]
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

## Attempt log

- iter 1: all 68 tests green; everything buildable is built. Live-probed on a real boot:
  `/health` 200 (540 doors), `/api/doors.geojson` 200, `/api/eval/report.json` 200,
  `/api/data/run_manifest.json` 200, `POST /api/route` 200.
- The artifact routes deliberately mirror the paths `web/ethics.html` already fetches, so the
  deploy sets `HOUSEACCOUNT_ARTIFACT_BASE` and nothing in `web/` changes. A locked test parses
  those `artifact('…')` calls out of the HTML, so either side drifting goes red.
- `scripts/vercel-build.sh` is the real injection step: it writes `web/js/config.js` with both
  `window.HOUSEACCOUNT_*` globals and links it from every `web/*.html` head.
- Implementer switched `app.include_router(...)` to `app.routes.extend(router.routes)`: FastAPI
  0.141 stores an opaque `_IncludedRouter` in `app.routes`, so `route.path` was `''` for every
  REST route and the anti-drift test could not see them. Verified live that all routes still serve.

## BLOCKED — requires human credentials

**Reason: the actual deploy needs Fly.io and Vercel accounts. Per the run rules, no account
creation was attempted.** Everything up to that boundary is done and tested.

Commands for the human, in order (★ = needs your credentials):

Prereqs, no credentials: `brew install flyctl` · `npm install --global vercel` ·
`make pipeline && make eval` (artifacts are baked into the image).

1. ★ `flyctl auth login`
2. `flyctl apps create houseaccount` (if the name is taken, change `app` in `fly.toml`)
3. ★ `flyctl secrets set ANTHROPIC_API_KEY=<key> CENSUS_API_KEY=<key>` — both optional; the
   deployed server calls neither, they only matter if you re-run the pipeline in the cloud
4. `flyctl deploy`
5. `curl https://<fly-app>.fly.dev/health` → expect a non-zero door count
6. ★ `vercel login`
7. `vercel link` — name it `houseaccount`, or add its origin to `UI_ORIGINS` in
   `src/houseaccount/server/app.py` and re-run `flyctl deploy`
8. `vercel env add HOUSEACCOUNT_API_BASE production` → `https://<fly-app>.fly.dev/api`
9. `vercel env add HOUSEACCOUNT_ARTIFACT_BASE production` → same value
10. `vercel --prod`
11. Open the site: map draws ~540 parcels, evidence panel works, Plan Route works, and
    Data & Ethics shows real numbers rather than the "no published run" fallback.
