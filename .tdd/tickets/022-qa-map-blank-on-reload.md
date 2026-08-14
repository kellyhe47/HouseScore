---
id: 022
title: "Map renders blank on every reload — initial camera does not frame the territory"
status: in-progress
source: qa
depends_on: []
touches: [web/js/map.js]
iterations: 0
test_files: []
branch: ""
---

## Found by

Manual QA of `b11b74b`, flow 7. See `.qa/report.md` finding 2.

## Repro

1. Open `http://localhost:5173` in a fresh tab — the map draws all 540 parcels correctly.
2. Reload the page.

## Expected

R9.1: "all ~540 parcels colored on one 0–100 ramp". Wireframe frame 1.

## Observed

An empty grey map. All chrome loads — header, legend, zoom controls — and the coverage readout still
claims `540 of 540 scored`, but no parcels are drawn. No console errors.

Clicking zoom-out twice reveals the entire territory, so the data loaded and the layer rendered;
only the initial camera is wrong.

Reproduced 4/4 on reload in one tab, then confirmed in a second tab: **the first load in a fresh tab
always drew the map, and every reload was blank.** `localStorage` and `sessionStorage` were both
empty at the time, so this is not walk-state related.

## Why this is high severity

A refresh or a back-navigation is ordinary use, and the result is an app that looks completely
broken. Nothing on screen suggests zooming out, and the readout actively asserts that 540 doors are
present — so the user concludes the deploy is dead rather than mis-framed.

## Suggested acceptance

- The territory is framed on every load, not only the first in a tab.
- A regression test covers reload, not just first paint.
- Consider whether the coverage readout should stay authoritative when nothing is rendered.
