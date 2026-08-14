---
id: 004
title: "Harvest: NJ parcels + territory bootstrap (R1.1, R1.2, R2.1)"
status: green
depends_on: [001]
touches: [src/houseaccount/sources/parcels.py, src/houseaccount/territory.py, tests/test_parcels.py, tests/test_territory.py, data/territory.geojson]
iterations: 1
test_files: [tests/test_parcels.py, tests/test_territory.py]
branch: "tdd/004" (merged, removed)
---

## Scope

Fetch Ramsey parcels from the NJ ArcGIS service through the T001 cache, and bootstrap the
definitive territory polygon set. Everything downstream reads `data/territory.geojson`.

Ground truth verified live in Phase 0 (see `.tdd/config.md`):
`https://services2.arcgis.com/XVOqAjTOJ5P6ngMu/arcgis/rest/services/Parcels_Composite_NJ_WM/FeatureServer/0/query`
· `PCL_MUN='0248'` → 5671 parcels · `PROP_CLASS='2'` → 5166 · maxRecordCount 2000, `resultOffset`
pagination · `f=geojson` gives lon/lat polygons · block/lot fields are **`PCLBLOCK` / `PCLLOT`**.

Ramsey Golf & Country Club is at approximately **41.0447 N, −74.1560 W** — the territory is the
~540 nearest class-2 parcels to that point (R1.1: the territory is ours, chosen by us; no external
polygon is coming).

Tests use a fixture-backed fake transport. No network in the test suite.

## Acceptance criteria

- [ ] `ParcelSource.fetch(mun="0248")` paginates with `resultOffset` until a short page returns,
      caching each page; a fake transport serving 2 pages yields all records and the request
      params show the expected offsets.
- [ ] Warm cache → **zero** transport calls on a second `fetch` (transport raises if invoked).
- [ ] Records map to a `Parcel` dataclass carrying PAMS_PIN, PROP_CLASS, PROP_LOC, ZIP5, PCLBLOCK,
      PCLLOT, DEED_DATE, SALE_PRICE, SALES_CODE, YR_CONSTR, NET_VALUE, CALC_ACRE, geometry,
      centroid. Identity fields are never requested in `outFields` and never appear on `Parcel`.
- [ ] `select_territory(parcels, center, target=540)` returns only `PROP_CLASS == "2"` parcels,
      is deterministic (same input → identical ordered output), and yields 500–580 parcels.
- [ ] `write_territory_geojson(path)` emits a FeatureCollection whose features each have a
      `PAMS_PIN` property and a geometry; properties contain no identity fields.
- [ ] Idempotent: running the bootstrap twice over the same input produces byte-identical output.
- [ ] `territory_median_value(parcels)` = median `NET_VALUE` over territory class-2 parcels with
      `NET_VALUE > 0` (this is the number the run manifest persists, R6 Capacity).

## Attempt log

- iter 1: green. Terminates on short page (not exceededTransferLimit); `orderByFields=OBJECTID`
  added because ArcGIS resultOffset paging without a stable sort skips/duplicates records.
  `outFields` is a 21-field allowlist so identity fields are absent by construction.
