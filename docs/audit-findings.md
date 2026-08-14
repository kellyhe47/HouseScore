# Phase 2 Audit — Ground Truth Findings

## NJ Parcel + MOD-IV data (VERIFIED LIVE, 2026-08-13)

**Endpoint (works, no auth, no key):**
`https://services2.arcgis.com/XVOqAjTOJ5P6ngMu/arcgis/rest/services/Parcels_Composite_NJ_WM/FeatureServer/0`
(layer `Cad_parcel_mod4`, NJOGIS, EPSG:3857; `f=geojson` returns lon/lat polygons)
Alternate: `https://maps.nj.gov/arcgis/rest/services/Framework/Cadastral/MapServer/0`
Bulk: `https://geoapps.nj.gov/njgin/parcel/parcels_shp_dbf_Bergen.zip`

**Ramsey Borough = `PCL_MUN='0248'`** → 5,671 parcels; `PROP_CLASS='2'` (residential ≤4 family) → 5,166.
Rubric territory is a ~540-home polygon subset of this.

**Key fields confirmed present:**
- `YR_CONSTR` (year built; some 0/null)
- `DEED_DATE` (last deed, string **YYMMDD** 2-digit year, e.g. "080122" = 2008-01-22; some null) + DEED_BOOK/PAGE
- `SALE_PRICE` (⚠ $1/$100 = non-arm's-length; filter via `SALES_CODE`)
- `LAND_VAL`, `IMPRVT_VAL`, `NET_VALUE`, `LAST_YR_TX` (prior-year tax)
- `PROP_CLASS`, `PROP_LOC` (situs address), `PAMS_PIN`/`GIS_PIN`, `CALC_ACRE`, `BLDG_DESC`, `BLDG_CLASS`
- `OWNER_NAME` / `ST_ADDRESS` / `CITY_STATE` (mailing): **ALL EMPTY for Ramsey — Daniel's Law redaction confirmed** (0 of 5,671 non-empty). Absentee-owner detection via mailing≠situs is NOT possible from this source.

**Pagination:** maxRecordCount 2000, `resultOffset` pagination supported. Ramsey = 3 pages. Public domain data.

**SR1A sales history:** no API — flat zips at https://www.nj.gov/treasury/taxation/lpt/statdata.shtml (YTDSR1A2026.zip, annuals to ~2020; layout PDF SR1Afilelayout.pdf). Parcel service already carries latest sale per parcel; SR1A only for full history.

## Gotchas
- DEED_DATE 2-digit year parse; SALE_PRICE nominal-sale noise; YR_CONSTR=0; MOD-IV attrs lag current tax year.
- Absentee detection blocked by redaction — must use alternate proxy (e.g. property class 2 + rental listing signals) or drop; ethics handling is graded, note it.

## Imagery / aux sources (VERIFIED 2026-08-13)

**Google Street View Static API**
- $7/1k images BUT 10k free events/month (Essentials tier) → 540–1,080 images = $0.
- Metadata endpoint (`/maps/api/streetview/metadata`) fully free/unlimited → free coverage + pano-date checks per address.
- Params: max 640x640, fov 10–120, heading, pitch, `source=outdoor`.
- **⚠ ToS conflict:** GMP ToS §3.2.3 prohibits creating derived datasets from Street View (their own example: "index of tree locations from Street View") and prohibits ML train/test/validate use. Storing per-door vision-derived signals ≈ derived dataset. Rubric says "scraping outside ToS = automatic judgment failure" yet names Street View. Surface as decision; likely path: use Street View narrowly at demo/eval scale with explicit ToS discussion, lean on free NJ orthos for bulk signals.

**Free aerial (clean ToS, $0):**
- NJ 2020 1-ft orthos: `https://maps.nj.gov/arcgis/rest/services/Basemap/Orthos_Natural_2020_NJ_WM/MapServer` (live, no key; 2015/2012 also exist → time-series). 2020 is newest on that server.
- USGS NAIP ~60cm public domain: `https://imagery.nationalmap.gov/arcgis/rest/services/USGSNAIPPlus/ImageServer`.
- Aerial sees roofs/pools/lawn/driveway/solar, not doors/trucks/signs.

**Permits:** NJ statewide construction permits, Socrata SODA API, free: `https://data.nj.gov/Reference-Data/NJ-Construction-Permit-Data/w9se-dmra` (filter Ramsey). Ramsey's own SDL portal is per-permit/login — don't scrape.

**Census ACS5:** B23007 (children × parents' employment — dual-income proxy), B19013 (median HH income), B08303 (commute time) confirmed; block-group level; Ramsey = state 34 / county 003 / cousub 61170. Free API key required (keyless calls now blocked).

**Zillow/Redfin: excluded** — no public API, ToS bans scraping (both). Cite in ethics section.

**Cost:** total ≈ $0–5 of $50 (Street View free tier + ~$1–3 Haiku-tier vision). Cost is not the constraint; Street View ToS is.

## Open items
- Territory GeoJSON polygon NOT in repo — awaiting from user, else derive from parcel data.
