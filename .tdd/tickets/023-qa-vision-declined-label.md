---
id: 023
title: "Data & Ethics calls the vision stage DECLINED on a run where it produced 119 doors of imagery"
status: pending
source: qa
depends_on: []
touches: [web/js/ethics.js, src/houseaccount/publish.py]
iterations: 0
test_files: []
branch: ""
---

## Found by

Manual QA of `b11b74b`, flow 6. See `.qa/report.md` finding 3.

## Repro

Open `http://localhost:5173/ethics.html`, section "What ran, and what declined".

## Expected

The section states its own contract: "where a provider refused, the refusal is printed instead of
the number it would have produced." A stage that ran and partly succeeded is not a refusal.

## Observed

> **DECLINED** · Aerial-imagery vision stage · "31 vision answers could not be read as detections;
> the doors they covered scored without their imagery signals"

On this publish the vision stage ran to completion: 270 of 270 requests returned, and 119 of 540
doors carry imagery evidence. One is two clicks away on the map — `17 SYCAMORE COURT` shows
"Pool visible from the air — a standing maintenance commitment", conf 0.90, captured 2020-01-01.

31 of 270 batches failing to parse is a partial degradation, not a declination.

## Why it matters

The page is the graded rationale document (R11.4), and its credibility rests on describing the run
accurately. A reviewer reads DECLINED as "no imagery signals at all", then finds pool evidence on
the map next to it — the page understates the pipeline and contradicts the product in the same
sitting.

## Suggested acceptance

- A stage that ran with partial loss renders as its own state (e.g. PARTIAL / DEGRADED), distinct
  from DECLINED.
- The line quantifies the loss against the whole: 31 of 270 answers, 119 of 540 doors with imagery.
- The status is derived from the run manifest, not from the presence of a degradation string.
