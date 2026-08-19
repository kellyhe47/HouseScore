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

**SR1A sales history:** no API — flat zips at https://www.nj.gov/treasury/taxation/lpt/statdata.shtml (YTDSR1A2026.zip, annuals to ~2020; layout PDF SR1Afilelayout.pdf).

**SR1A re-audited 2026-08-14 and now HARVESTED (ticket 020).** The earlier note here —
"parcel service already carries latest sale per parcel; SR1A only for full history" — was wrong in
the way that mattered: MOD-IV's `DEED_DATE` is the latest deed *the county extract knows about*,
and that extract lagged 20 months. Measured against the live files:

- `YTDSR1A2026.zip`: 10,984,774 bytes, last modified **2026-08-12**; unzips to one 113 MB text file,
  169,935 records, **every record exactly 663 chars** as the layout declares.
- Ramsey (county `02` + district `48`) = **240 records**, deed dates **2025-01-10 → 2026-06-15**.
  MOD-IV's newest deed anywhere in Ramsey: 2024-12-06. The same sold parcels read 1996–2022 in
  MOD-IV — the *prior* owner's deed.
- 11 sales inside the 90-day mover window; 10 join a live parcel; 2 are in the published territory.
- **Identity columns (grantor/grantee name, street, city/state, zip) are blank in all 169,935
  records** — the state redacts them at publication. The parser still reads an allowlist only.
- **Condo trap:** a complex's units share one block/lot (20 Ramsey sales on block 4001 lot 22) and
  differ only by `QUALIFICATION-CODES`. The parcel layer's matching field is **`PCLQCODE`** and its
  PAMS_PIN is `0248_4001_22_C0115`, so the sales join key must include the qualifier.
- **Residual limit:** `DATE-RECORDED` stops at 2026-06-30 in a file published 2026-08-12 — a ~6-week
  recording-and-publication lag, so the 30-day/100-point mover band stays unreachable regardless.

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

**Permits:** The production pipeline uses NJ statewide construction permits,
Socrata SODA API, free:
`https://data.nj.gov/Reference-Data/NJ-Construction-Permit-Data/w9se-dmra`
(filter Ramsey).

**SDL update (2026-08-18):** The earlier instruction not to collect from
Ramsey's SDL portal reflected the authorization status during the original
audit. Based on the user's representation that SDL authorized “Option 1”
manual portal-result collection, the project now has a point-in-time browser
snapshot for the full 540-parcel territory. SDL property pages were available
for 532 parcels and display 3,648 permit applications, 6,168 inspections, and
91 violations; eight parcels returned SDL 404s and no visible address-search
result. The primary artifact groups these fields with public property,
assessment, sale, and map metadata under each house. It also retains the earlier
2,043-row `roof` search snapshot and attaches all 210 matched roof detail pages,
including 14 supplemental roof records absent from the property-page tables.
Owner/mailing and permit-agent fields are excluded. This remains supplemental
research, not a replacement for the Socrata source used by scoring. See
[`data/README-sdl-property-history.md`](../data/README-sdl-property-history.md)
for provenance, schema, privacy choices, property-page limitations, roof
keyword coverage, and false-positive handling.

**Census ACS5:** B23007 (children × parents' employment — dual-income proxy), B19013 (median HH income), B08303 (commute time) confirmed; block-group level; Ramsey = state 34 / county 003 / cousub 61170. Free API key required (keyless calls now blocked).

**Zillow/Redfin: excluded** — no public API, ToS bans scraping (both). Cite in ethics section.

**Cost:** total ≈ $0–5 of $50 (Street View free tier + sub-$1 mini-tier vision). Cost is not the constraint; Street View ToS is.

## Resolved items

- Territory GeoJSON is present at `data/territory.geojson` and contains the 540
  properties used for the published run and SDL address match.
- The authorized manual SDL property/construction-history snapshot was
  collected and documented on 2026-08-18, together with its roof-search
  provenance. The production permit adapter remains Socrata-backed.
