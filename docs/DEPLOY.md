# Deploying HouseAccount

Two surfaces — the server and the Map UI — and two supported ways to host them:

| Topology | Hosts | Use when |
| --- | --- | --- |
| **One service** | Railway | The default. One container serves the API, the MCP transport and the static UI from a single origin. |
| **Two hosts** | Fly.io + Vercel | The split deploy. The server on Fly, `web/` on Vercel as static files. |

Both ship from the same `Dockerfile`. It copies `web/` into the image and
generates a same-origin `js/config.js`, and `houseaccount.server.app` mounts the
directory when it is present — so the container serves the UI on its own origin
either way. On Railway that mounted copy *is* the UI. On Fly it is an unused
duplicate, because Vercel serves the same files from its own origin and
`scripts/vercel-build.sh` regenerates `config.js` there pointing at the Fly
hostname, across origins, against the `UI_ORIGINS` allowlist.

The mount is added last and cannot shadow the API: Starlette matches routes in
order, so `/api/*` and `/mcp` are claimed before the catch-all sees them.

Everything either topology needs is committed and correct: `Dockerfile` builds
the image with the published run baked in, `railway.json` sets the builder and
the `/health` check, `fly.toml` exposes port 8000 with the same check,
`vercel.json` publishes `web/` and injects the API origin, and
`scripts/vercel-build.sh` is the injection step.

What is **not** committed, and cannot be, is anybody's credentials. That is the
line this runbook stops at and hands over.

## Railway (one service)

This is the short path. Railway builds the committed `Dockerfile`, and because
the image carries `web/`, the deployed URL is the map — there is no second host,
no CORS preflight and no API base to configure.

Connect the repo once, in the dashboard: **New Project → Deploy from GitHub
repo → `kellyhe47/HouseScore`**. Railway reads `railway.json`, builds the
Dockerfile, and every push to `main` redeploys. Then **Settings → Networking →
Generate Domain** to get a public hostname.

Or from the CLI, in this directory:

```sh
railway login                                      # ← your credentials
railway link                                       # pick the project
railway up
```

Nothing needs to be set for the server to serve doors: it answers from
`data/doors.geojson` and `data/houseaccount.sqlite` and makes no outbound calls.
The three pipeline credentials are optional and belong in **Variables** in the
dashboard, never in `railway.json` — see the table below.

Two things Railway supplies on its own, which is why neither is configured here:

- **`PORT`.** Railway injects it and the container's `CMD` expands it. Nothing
  in `railway.json` sets it.
- **The health gate.** `healthcheckPath` is `/health`, so a container that boots
  into an empty territory fails the deploy instead of replacing a working one.
- **`RAILWAY_PUBLIC_DOMAIN`.** The MCP transport validates the `Host` header, so
  it has to know the hostname it is reached at, and the platform is the only
  thing that knows it. `houseaccount.server.app` reads that variable (and
  `FLY_APP_NAME` on Fly) to build the allowlist.

  If `initialize` ever answers **421 Misdirected Request**, that variable was not
  present: set `HOUSEACCOUNT_PUBLIC_HOST` to the domain by hand in **Variables**
  (comma-separated for a custom domain beside the generated one) and redeploy.
  It is the one symptom that leaves `/health` green and the map working while
  every MCP client is refused.

Verify, substituting your generated domain:

```sh
curl https://<your-app>.up.railway.app/health
#   {"status":"ok","doors":540}          ← a door count of 0 means a stale data/
```

Then open the domain itself. The map is served at `/`, the Data & Ethics page at
`/ethics.html`, and both read the API at `/api` on that same origin. Skip to
[Verify the whole thing](#3-verify-the-whole-thing) for what to click.

**Redeploying:** push to `main`, or `railway up`. A new scoring run means
`make pipeline && make eval`, committing the changed artifacts, then pushing —
Railway builds from the repo, so an uncommitted `data/` is a run the deploy
never sees.

---

The rest of this runbook is the two-host Fly + Vercel deploy. Nothing below is
needed for Railway.

## Before you start

```sh
brew install flyctl                 # or: curl -L https://fly.io/install.sh | sh
npm install --global vercel
```

You also need accounts on both platforms. The free tiers are enough: the server
is one shared-cpu-1x machine with 512 MB, and the UI is static files.

Rehearse locally first — it costs nothing and catches a stale run:

```sh
cp .env.example .env    # then fill in whichever keys you have
make pipeline    # publishes data/doors.geojson + data/houseaccount.sqlite
make eval        # writes eval/report.json, which the Data & Ethics page reads
make test        # Python + JS suites
make serve       # http://127.0.0.1:8000/health should report the door count
```

`make pipeline` and `make eval` source `.env` themselves when it exists, so
there is nothing to export by hand. **Check `degradations[]` in
`data/run_manifest.json` before you build the image.** Every credential is
optional and a missing one degrades a stage instead of failing the run, so a
keyless `make pipeline` exits 0 having published a complete, internally
consistent, three-signals-poorer territory — no vision terms, no ACS prior — and
the image build below will bake exactly that in. A run that used its keys
reports `"vision": {"available": true, ...}` and lists only the two structural
degradations (no OPRA rental list, no door in the 30-day mover band).

`make pipeline` is what puts the artifacts in `data/`, and the `Dockerfile`
copies that directory into the image. **Deploying without a current `data/` ships
a stale territory, and deploying without `data/` at all ships an image that
cannot boot** — `create_app` raises `DataUnavailable` before it binds a port.

## Steps that need your credentials (the human boundary)

Four things here need a person with account access, and nothing in this repo can
do them for you:

1. **`flyctl auth login`** — authenticating to Fly.io.
2. **`vercel login`** — authenticating to Vercel.
3. **Setting the three application credentials as Fly secrets.** They are
   `OPENAI_API_KEY`, `CENSUS_API_KEY` and `GOOGLE_MAPS_KEY` — the same three
   `.env.example` declares, and all three are optional (see below).
4. **Setting the two UI base variables in Vercel**, `HOUSEACCOUNT_API_BASE` and
   `HOUSEACCOUNT_ARTIFACT_BASE`. Not secrets, but they depend on the hostname
   Fly.io hands you, which does not exist until step 1's account does.

Everything else below is a command you can paste.

### About the three credentials

The *pipeline* reads them; the *server* does not. It answers from
`data/doors.geojson` and `data/houseaccount.sqlite` and makes no outbound calls,
so a deployment with no secrets at all serves the same doors as one with all
three. Set them anyway if you have them: they are what a stage moved behind the
API later would need, and setting them now means the deployed app and
`.env.example` describe one list.

| Variable | What it buys | Missing means |
| --- | --- | --- |
| `OPENAI_API_KEY` | The vision stage (pool, solar, exterior condition). | The stage declines before any tile is fetched; R4 imagery terms are absent from scores. |
| `CENSUS_API_KEY` | The ACS block-group dual-income prior, worth 5 points. | No block-group context; recorded in `degradations[]` in `data/run_manifest.json`. |
| `GOOGLE_MAPS_KEY` | Reserved for the demo-scale Street View look-up. | Nothing — the pipeline does not call it today. |

Never put a value in `fly.toml`, `Dockerfile`, `vercel.json` or this file.
`[env]` in `fly.toml` is baked into the machine's public config, and
`tests/test_deploy_config.py` scans all four files for credential-shaped
literals on every run.

## 1. The server, on Fly.io

```sh
flyctl auth login                                  # ← your credentials
flyctl apps create houseaccount                    # the name in fly.toml
```

If that name is taken, pick another and change `app = "..."` in `fly.toml` to
match. Remember what you picked — it is the hostname the UI will call.

Set the application credentials. Secrets are stored encrypted and injected as
environment variables at boot; they never enter the image or this repository:

```sh
flyctl secrets set OPENAI_API_KEY=<your-key> CENSUS_API_KEY=<your-key>
flyctl secrets set GOOGLE_MAPS_KEY=<your-key>
```

Skip any credential you do not have — all three are optional, and the server
needs none of them. Then build and ship the image:

```sh
flyctl deploy
```

Verify before touching the UI. Substitute your app name throughout:

```sh
curl https://houseaccount.fly.dev/health
#   {"status":"ok","doors":540}          ← a door count of 0 means a stale data/

curl https://houseaccount.fly.dev/api/doors.geojson | head -c 200
curl https://houseaccount.fly.dev/api/eval/report.json
curl https://houseaccount.fly.dev/api/data/run_manifest.json
```

A 404 on the last two is survivable and honest — the Data & Ethics page renders
its "no published run" fallback — but it means `make eval` or `make pipeline`
had not run when the image was built. Re-run it and deploy again.

## 2. The Map UI, on Vercel

The UI needs one fact: where the API is. It reads that fact from two globals,
`window.HOUSEACCOUNT_API_BASE` (in `web/js/map.js`) and
`window.HOUSEACCOUNT_ARTIFACT_BASE` (in `web/ethics.html`), and
`scripts/vercel-build.sh` writes both into `web/js/config.js` at build time from
the matching environment variables. **Both are the same value** — the app's
`/api` — because the server serves the artifacts at exactly the paths the ethics
page fetches.

```sh
vercel login                                       # ← your credentials
vercel link                                        # name the project: houseaccount
```

The project name matters. `UI_ORIGINS` in `src/houseaccount/server/app.py`
allows `https://houseaccount.vercel.app`, and a browser will not call across
origins without that header. If you name the project something else, add its
origin to `UI_ORIGINS` and re-run `flyctl deploy`.

Now the two base URLs, once per environment. Each command prompts for the value;
paste `https://houseaccount.fly.dev/api` (your app's hostname, with `/api`, no
trailing slash) at both prompts:

```sh
vercel env add HOUSEACCOUNT_API_BASE production
vercel env add HOUSEACCOUNT_ARTIFACT_BASE production
```

Then publish:

```sh
vercel --prod
```

The build fails loudly if either variable is missing, rather than deploying a
map that silently calls nothing.

## 3. Verify the whole thing

Open `https://houseaccount.vercel.app`. In order:

1. The map draws ~540 parcels shaded by score. If it is empty, open the console:
   a CORS error means the Vercel origin is not in `UI_ORIGINS`; a 404 means
   `HOUSEACCOUNT_API_BASE` is missing its `/api`.
2. Click a parcel — the evidence panel shows the score breakdown and a talk track.
3. **Plan Route** returns an ordered walk with an estimate disclosure.
4. **Data & Ethics** shows the real fixture counts and the run manifest, not the
   "no published run" fallback. If it shows the fallback,
   `HOUSEACCOUNT_ARTIFACT_BASE` is wrong or the eval report never made it into
   the image.

## Redeploying

- **New scoring run:** `make pipeline && make eval`, then check
  `degradations[]` in `data/run_manifest.json`, then `flyctl deploy`. The
  artifacts live inside the image, so the server changes only when it is rebuilt
  — and a degraded run deploys just as cleanly as a complete one.
- **UI change only:** `vercel --prod`. Nothing on Fly.io needs to move.
- **Rotating a credential:** `flyctl secrets set` again; Fly restarts the
  machines for you.

## Notes for whoever reads this next

- `scripts/vercel-build.sh` edits `web/` in place — it writes `web/js/config.js`
  (git-ignored) and adds one `<script>` tag to each page's `<head>`. Both edits
  are idempotent, so running it locally to see what a deploy produces leaves a
  re-runnable tree; `git checkout web` puts the tags back.
- The Docker image installs the project with `pip install -e .` deliberately:
  `houseaccount.config` finds `data/` and `cache/` by walking up to the directory
  holding `pyproject.toml`, so the package has to stay beside it.
- `fly.toml` keeps one machine warm (`min_machines_running = 1`). A reviewer
  opening a cold demo link should not be the one who pays for the boot.
