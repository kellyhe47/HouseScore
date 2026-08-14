# TDD run config — HouseAccount House Score

**Working branch:** `main` (dev's choice; all commits local, **never pushed**)
**Repo root:** `/Users/kellyhe/Documents/gauntlet/houseaccount`
**Run mode:** unattended overnight. Never pause for approval. Blocked tickets get a one-line
question in the board and the run moves on.

## Verified commands (smoke-tested in Phase 0)

| Purpose | Command |
|---|---|
| Python unit suite | `.venv/bin/python -m pytest` |
| Web JS unit suite | `npm --prefix web test`  (→ `node --test 'js/**/*.test.js'`) |
| Full suite | `make test` |
| Fixture re-derivation (spec self-check) | `python3 eval/verify_claims.py` |
| Eval harness | `make eval` |
| End-to-end pipeline | `make pipeline` |
| Env bootstrap | `make setup` (uv venv, python 3.12) |

Python is uv-managed at `.venv` (3.12). System python3 is 3.9 — **always use `.venv/bin/python`**.
`python3 eval/verify_claims.py` is the one exception: it is stdlib-only by design.

## Toolchain decisions (pre-made, do not relitigate)

- **No GeoPandas.** PRD R12 named it; we use `shapely` + stdlib json instead. GeoPandas pulls a
  GDAL/pyproj/fiona stack that breaks fresh-clone reproducibility (R13) for zero benefit —
  everything we do is point-in-polygon, centroids, and haversine. Recorded as a deliberate
  deviation in the README.
- **Web UI is vanilla ES modules + MapLibre from CDN**, matching `docs/HouseAccount-Prototype.html`.
  Pure logic (score ramp, filtering, route rendering, walk state) lives in `web/js/*.js` as
  importable modules so `node --test` can cover it without a browser.
- **Live probe** for UI tickets = load the page in the in-app browser and read the console.
  No Playwright (150MB install, not worth it for this run).

## Environment / credentials reality (checked in Phase 0)

`ANTHROPIC_API_KEY`, `CENSUS_API_KEY`, `GOOGLE_MAPS_KEY` are **all unset** in this environment.
Consequences, pre-decided:

- Every network source is behind the content-addressed cache (T001). Cached responses are
  committed where small enough, so `make pipeline` and `make eval` run offline and reproducibly.
- Census ACS: keyless calls are blocked upstream. The ACS source ships a provider seam with a
  documented, fixture-backed fallback and logs the declination — the pipeline never crashes.
- Vision: `ClaudeVisionProvider` is real code but cannot be exercised here. The pipeline runs
  through `CachedVisionProvider`; `make eval` reports vision P/R against fixture 09's frozen
  confusion set (exactly what the handoff prescribes "until hand labels exist").
  **Never fabricate model outputs to fill this gap** — report the frozen-set arithmetic honestly.
- Deploy (Fly.io/Vercel) is build-to-ready + documented commands, ticket marked
  `blocked-on-human`. No account creation.

## Live ground truth confirmed in Phase 0 (2026-08-14)

- Parcels: `services2.arcgis.com/XVOqAjTOJ5P6ngMu/.../Parcels_Composite_NJ_WM/FeatureServer/0`,
  `PCL_MUN='0248'` → **5671** parcels. Field names are `PCLBLOCK` / `PCLLOT` (not `PCL_BLOCK`).
  Full field list includes PAMS_PIN, PROP_LOC, DEED_DATE, SALE_PRICE, SALES_CODE, YR_CONSTR,
  NET_VALUE, CALC_ACRE, PROP_CLASS, ZIP5.
- Permits: `data.nj.gov/resource/w9se-dmra.json?comu=0248` works. Records carry
  `block`/`lot`/`permitdate`/`permittypedesc`/`constcost` — **there is no contractor field**.
  Therefore real-data provider churn (+20) is structurally unearnable; R6 already covers this
  ("permits lacking a contractor field are excluded from churn"). Join key is comu+block+lot,
  not address — better than the address join R3.1 assumed. Report both.
- Orthos 2020 MapServer: live, no key.

## Invariant (checked by a permanent guard test, T001)

No identity data. `OWNER_NAME` / `ST_ADDRESS` / `CITY_STATE` may appear **only** in
`tests/test_redaction.py`. Any other occurrence in `src/`, `eval/`, or `web/` fails the suite.
