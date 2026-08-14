---
id: 007
title: "Vision: R3.3 detection schema, provider seam, ortho tiling (R4)"
status: pending
depends_on: [001]
touches: [src/houseaccount/vision/schema.py, src/houseaccount/vision/provider.py, src/houseaccount/vision/tiles.py, src/houseaccount/vision/run.py, tests/test_vision_schema.py, tests/test_vision_provider.py, tests/test_vision_tiles.py]
iterations: 0
test_files: []
branch: ""
---

## Scope

The vision subsystem as a typed seam. Real Claude code exists and is unit-tested against a **fake
Anthropic client** — `ANTHROPIC_API_KEY` is unset in this environment and no test may hit the
network. The score engine consumes the R3.3 schema, never raw model output.

Ortho endpoints (verified live, no key):
`https://maps.nj.gov/arcgis/rest/services/Basemap/Orthos_Natural_2020_NJ_WM/MapServer/export`
and the 2015 sibling service, EPSG:3857, `size=640,640`, `f=image`.

## Acceptance criteria

- [ ] `Detection` schema is exactly R3.3: `{pams_pin, signal, present: bool, confidence: 0-1,
      image_ref, capture_date}`. Validation rejects: confidence outside [0,1], unknown `signal`,
      missing `pams_pin`, non-bool `present`.
- [ ] Condition uses the R6 ordinal enum `excellent > good > fair > poor`; comparing two vintages
      yields a decline only on a ≥1-step drop.
- [ ] `tile_url(centroid, year, size=640)` builds a deterministic EPSG:3857 bbox export request
      for 2015 and 2020; same input → same URL; the bbox is square and centred on the parcel.
- [ ] `VisionProvider` protocol: `detect(tiles) -> list[Detection]`.
- [ ] `ClaudeVisionProvider` batches multiple tiles per request, requests structured JSON output,
      parses into `Detection`s, and records spend in the T001 `CostLedger`. Unit-tested with a
      fake client — assert the batch size honoured and the ledger incremented.
- [ ] Malformed / unparseable model output is recorded as a parse failure and excluded from
      detections; the run does not crash.
- [ ] `CachedVisionProvider` wraps another provider and never re-requests a seen `image_ref`
      (assert the inner call count across two runs).
- [ ] `to_score_vision(detections) -> dict` produces exactly the score engine's vision dict
      (`pool`, `solar`, `condition_2015`, `condition_2020`), dropping detections below a
      documented confidence floor — this is the only path from vision into scoring.
- [ ] With no API key configured, the vision stage yields an empty-but-valid result plus a logged
      declination, and the pipeline can still score every door.
