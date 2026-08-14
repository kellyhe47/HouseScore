# HouseAccount

A door-scoring pipeline for a ~540-parcel territory in Ramsey, New Jersey. Every
door gets a single House Score, 0–100, measuring how well the property matches a
home-services ICP — recently moved, hires out rather than DIYs, can pay, and has
work coming due — together with the evidence behind the number, phrased so a rep
can read it out on the doorstep.

Everything it knows comes from public records and public-domain imagery. It
holds no owner names and no mailing addresses: the identifying columns are
dropped in the source adapter, before anything reaches storage, scoring or the
map. A full run takes about twelve seconds against the live APIs, and runs with
no credentials at all — each source that needs a key declines, says so, and the
run still scores every door.

---

## Prerequisites

| What | Why |
|---|---|
| [`uv`](https://docs.astral.sh/uv/) | Creates the virtualenv and installs the project. |
| Python 3.12 | `uv` installs it into `.venv` for you. |
| Node 18+ | Runs the browser-side tests through `node --test`. No JS dependencies to install. |
| `make` | The five commands below are the whole interface. |
| Network access | The pipeline calls live public APIs. No credentials needed; see below. |

Do not reach for the system interpreter. On macOS `python3` is still 3.9 and
this project will not run on it — use the `.venv/bin/python` that setup creates.

## 1. Set up

```sh
make setup
```

Creates `.venv` on Python 3.12, installs the package and its dev extras in
editable mode, and does the (dependency-free) npm install for the web tests.

## 2. Configure the environment — all of it optional

Copy `.env.example` to `.env`, fill in whichever keys you have, and export them
(`set -a; . ./.env; set +a`). All three variables are **optional**, and a run
with none of them set is a supported, tested configuration — that is what the
published run in `data/` was produced by.

| Variable | Unset means | Costs you |
|---|---|---|
| `OPENAI_API_KEY` | The vision stage declines before a single tile is fetched. | Pool, solar and exterior-condition signals. |
| `CENSUS_API_KEY` | The ACS API refuses keyless callers, so no block groups are fetched. | The 5-point dual-income neighbourhood prior. |
| `GOOGLE_MAPS_KEY` | Nothing. The pipeline never calls it today; it is reserved for the demo-scale Street View look-up. | Nothing. |

Optional here means optional: no key is checked at startup, none is required to
publish a complete run, and nothing is guessed to fill a gap. Every declination
is instead written into
`degradations[]` in the run manifest, printed by the run, and shown on the Data
& Ethics page — a missing signal is a stated absence, never an imputed value.

## 3. Run the pipeline

```sh
make pipeline
```

Five stages — harvest, resolve, vision, score, publish — against the live
sources, roughly twelve seconds end to end. Only the parcel harvest is
load-bearing; anything else that fails degrades and is named. Responses are
cached under `cache/`, so a second run re-reads parcels, permits, block groups,
ortho tiles and vision detections off disk and spends nothing.

The four published artifacts land in `data/` (listed under
[Artifacts](#artifacts) below), and the numbers describing the run itself —
coverage, cost, entity-resolution rates, declinations — go into
`data/run_manifest.json`.

## 4. Evaluate

```sh
make eval
```

Runs the thirteen golden fixtures through the *shipped* score engine (not a
re-implementation of the rules — a weight table that drifts from the spec fails
here), then reports vision precision, recall and hallucination rate, the
**cost per door**, and the entity-resolution match rate. With no flags it reads
the run you just published out of `data/run_manifest.json`; the report is also
written to `eval/report.json` for the Data & Ethics page to render.

The current run:

```
GOLDEN FIXTURES
  reproduced:               12/12
COST
  doors scored:             540
  cost per door:            $0.0000
ENTITY RESOLUTION (R3.2)
  municipal match rate:     0.974
  required floor:           0.950
```

The exit code is the gate: a fixture that stops reproducing, or a municipal
permit-to-parcel match rate below 0.95, fails the run. The vision numbers are
still frozen fixture arithmetic rather than a live measurement, and the harness
says so, at length, every time it prints them.

## 5. Serve

```sh
make serve
```

One FastAPI process on `:8000` with two front doors, both answering from a
single read of the published run:

- **MCP** at `/mcp` — `get_door_score`, `explain_score`, `plan_route`.
- **REST** — `GET /api/doors.geojson`, `GET /api/door/{pams_pin}`,
  `POST /api/route`, `GET /health`.

The map is static: serve `web/` on `http://localhost:5173` (already in the
server's CORS allowlist) and set `window.HOUSEACCOUNT_API_BASE` to
`http://localhost:8000/api`. It gives you the choropleth over all 540 parcels,
the score-range filter, the click-through evidence panel, the route planner and
walk mode, plus the [Data & Ethics page](web/ethics.html).

Missing artifacts fail the boot rather than the request: starting the server
before the pipeline gets you a `DataUnavailable` naming the empty directory,
not a healthy-looking deployment of a town with no houses in it.

## 6. Test

```sh
make test
```

1,403 Python tests and 254 JavaScript tests. `make test-py` and `make test-web`
run each half on its own. The JS suite is `node --test` over `web/js/*.test.js`
with no framework to install.

## Artifacts

`make pipeline` publishes exactly four files, and everything downstream — the
server, the map, the eval harness — reads them rather than re-deriving anything:

| Path | What it is |
|---|---|
| `data/doors.geojson` | Every scored door: geometry, score, evidence, coverage state. The map's only input. |
| `data/houseaccount.sqlite` | The run's records — parcels, permits, detections, scores — for querying. |
| `data/run_manifest.json` | The run itself: `run_at`, code version, coverage, cost, entity-resolution rates, `degradations[]`. |
| `data/territory.geojson` | The territory boundary the ~540 doors were selected against. |

`make clean` removes the derived artifacts and the caches.

## Data sources, and their licence position

Six upstreams, all public. The full write-up — including the signal→ICP trace
table, the weights and the validation plan — is the
[Data & Ethics page](web/ethics.html), which the pipeline's own manifest drives.

| Source | Endpoint | Licence / ToS position |
|---|---|---|
| NJ statewide parcels + MOD-IV assessment records | ArcGIS FeatureServer, municipality code 0248 | Public record, open data. Owner-identity and mailing-address columns dropped at ingest for Daniel's Law (R11.1). |
| NJ SR1A sales register | `nj.gov/treasury/taxation` year-to-date sales flat file (fixed-width, zipped) | Public record. Supplies deed recency wherever it is fresher than MOD-IV's — which on the shipped run is the only reason the Mover signal fires at all. The download is statewide and its layout reserves grantor/grantee identity columns, so only non-identity columns are read and the raw file is never cached (R11.1). |
| NJ construction permits | Socrata, `data.nj.gov` dataset `w9se-dmra` | NJ Open Data public record; joined to parcels by block and lot. |
| Census ACS 5-year block groups (B23007, B19013, B08303) | `api.census.gov` | US federal government work, public domain; used per the Census API terms. Neighbourhood context only, never a household claim. |
| NJ orthophotography, 2015 and 2020 vintages | `maps.nj.gov` | Public domain. The source of **every** bulk imagery signal. |
| Ramsey municipal rental registration | Municipal record, requested under OPRA | Public record on request. Not obtained by build time, so the absentee modifier ships as a documented declination (R11.3). |

Two exclusions are deliberate and load-bearing. Google Street View: GMP ToS
§3.2.3 forbids creating derived datasets, and a per-parcel signal in a database
is exactly that — so bulk imagery comes from the public-domain state orthos
instead, and Street View stays at demo scale. Zillow and Redfin: excluded
outright, their terms forbid scraping and redistribution. Where a licence and a
better signal conflict, the licence wins and the gap is disclosed.

## Deliberate deviation from the PRD

**R12 names GeoPandas; this build uses shapely.** GeoPandas drags in a
GDAL / pyproj / fiona stack whose wheels are the single most common way a fresh
clone fails to build — and it buys nothing here. The geometry this pipeline
actually does is point-in-polygon, centroids and haversine distance, all of
which shapely does natively with a few lines of arithmetic around it. Trading a
fragile install for a dependency we would use at ten percent was not a good
trade. Nothing else in R12 changes: FastAPI serves MCP and REST, the map is
static.

## Cost

The budget ceiling is **≤ $50** total; projected spend is **$0–5** (the Street
View free tier exceeds our volume, the metadata endpoint is free, and
gpt-4o-mini vision over ~1,080 ortho tiles runs well under $1). This is not asserted, it is
measured: the ledger records what each source spends as it spends it, the
manifest carries the total, and `make eval` prints the `cost per door` line
above from it. The published run cost **$0.00** — with no `OPENAI_API_KEY`
the vision stage declines, and every other source is free.

## Layout

```
src/houseaccount/     pipeline, scoring engine, sources, server
  sources/            one adapter per upstream, all behind one HTTP seam
  scoring/            the R6 weights table and the deterministic engine
  server/             FastAPI app: MCP tools + REST API
eval/                 the harness, 13 golden fixtures, verify_claims.py
web/                  static MapLibre UI + the Data & Ethics page
tests/                the suite
docs/                 PRD, wireframes, design notes
data/                 published artifacts (see above)
cache/                warm HTTP + vision cache; safe to delete
```

`eval/verify_claims.py` answers a different question from the harness: it
re-derives the golden fixtures from the written scoring rules with a
stdlib-only reimplementation, proving the spec is internally consistent. The
harness proves the shipped code agrees with it. Both have to pass.
