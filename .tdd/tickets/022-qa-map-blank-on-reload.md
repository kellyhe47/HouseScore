---
id: 022
title: "Map renders blank on every reload — initial camera does not frame the territory"
status: green
source: qa
depends_on: []
touches: [web/js/map.js]
iterations: 1
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

## Resolution

Green in 1 iteration.

**Cause, corrected from the ticket's own guess.** Not a fetch-vs-style race — `renderMap()` is called
from inside `loadDoors()` after the JSON resolves, so data always precedes map construction and the
`load` handler can never be missed. The live ordering is **style-load vs container layout**:
`fitBounds` with `padding: 48` against a container too small to hold that padding is a *silent no-op*
in MapLibre (`cameraForBounds` returns undefined, camera untouched, nothing thrown). That is why the
console was clean and the camera sat on the hardcoded `center`/`zoom`. A fresh tab worked only because
the network fetch bought a frame for layout; on reload `doors.geojson` came from cache and removed it.

**Fix.** `frameTerritory()` re-measures, checks the canvas can hold `FIT_PADDING`, and counts the
attempt only when it lands. A `ResizeObserver` registered before the style can be up re-runs it when
the container becomes measurable, so whichever of layout and load arrives second carries the fit.
`territoryFramed` latches on first success and the observer disconnects, so user panning is never
fought.

- tests locked: `63ce508` · implementation: `ba4caca` · merged to working branch
- full suite on merged branch: 1357 Python, 219 JS

### Live-browser confirmation: INCONCLUSIVE — needs a human

The automated browser pane here is persistently hidden, which gives the map container zero height. In
that state **both the fixed and the unfixed build render blank**, verified by an A/B (stash the fix,
reload, compare). So this environment cannot reproduce the "fresh tab works, reload is blank" split
that QA saw on a real screen.

What that A/B does establish: the fix is **not a regression**. What it cannot establish: that the fix
actually resolves the original bug in a real browser. The unit evidence for that is the two previously
red tests driving the exact ordering, plus two guards that stay green to prove the harness is not
vacuous.

**A human should load the deployed or local UI in a real browser and hammer reload.** That is the one
check this run could not make for itself.

### Noticed, not fixed (no ticket filed)

- The coverage readout still asserts "540 of 540 scored" over an empty map — the ticket raised it as a
  "consider whether", and it is an honesty question about the readout, not a camera fix.
- A container narrower than 96px would never latch and the observer would retry on every size change.
  Real `fitBounds` refuses there too, so this mirrors MapLibre rather than inventing a failure.
- `window.addEventListener('resize')` at the bottom of map.js now duplicates logic `frameTerritory`
  could serve; consolidating needs a behavior decision about refitting after the initial framing.
