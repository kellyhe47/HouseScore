# Deploying HouseAccount

Two surfaces, two hosts (R12):

| Surface | Host | What it is |
| --- | --- | --- |
| Server | Fly.io | `houseaccount.server.app:create_app` — the REST endpoints the map calls, the artifact endpoints the Data & Ethics page reads, and the MCP transport at `/mcp`. |
| Map UI | Vercel | `web/` as static files. No bundler, no framework — hand-written ES modules plus one generated config script. |

Everything the two hosts need is committed and correct: `Dockerfile` builds the
server image with the published run baked in, `fly.toml` exposes it on port 8000
with a `/health` check, `vercel.json` publishes `web/` and injects the API
origin, and `scripts/vercel-build.sh` is the injection step.

What is **not** committed, and cannot be, is anybody's credentials. That is the
line this runbook stops at and hands over.

## Before you start

```sh
brew install flyctl                 # or: curl -L https://fly.io/install.sh | sh
npm install --global vercel
```

You also need accounts on both platforms. The free tiers are enough: the server
is one shared-cpu-1x machine with 512 MB, and the UI is static files.

Rehearse locally first — it costs nothing and catches a stale run:

```sh
make pipeline    # publishes data/doors.geojson + data/houseaccount.sqlite
make eval        # writes eval/report.json, which the Data & Ethics page reads
make test        # Python + JS suites
make serve       # http://127.0.0.1:8000/health should report the door count
```

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
   `ANTHROPIC_API_KEY`, `CENSUS_API_KEY` and `GOOGLE_MAPS_KEY` — the same three
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
| `ANTHROPIC_API_KEY` | The vision stage (pool, solar, exterior condition). | The stage declines before any tile is fetched; R4 imagery terms are absent from scores. |
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
flyctl secrets set ANTHROPIC_API_KEY=<your-key> CENSUS_API_KEY=<your-key>
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

- **New scoring run:** `make pipeline && make eval`, then `flyctl deploy`. The
  artifacts live inside the image, so the server changes only when it is rebuilt.
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
