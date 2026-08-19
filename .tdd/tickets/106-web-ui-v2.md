---
id: 106
title: Web UI — map ramp restop (R38), panel math, route chips, degradation copy
status: green
depends_on: [104, 105]
touches: [web/js/map.js, web/js/panel.js, web/js/route.js, web/js/*.test.js (V1-encoding fixtures)]
iterations: 1
test_files: [web/js ramp/panel/route-ui/share/no-v1-strings tests + test-fixtures.js]
branch: ""
---

## Scope

Migrate the browser surfaces to V2 (R30/R38). Map choropleth ramp + legend recalibrated to
the V2 distribution using quantile stops taken from the R34 recalculation report (build-time
constant or fetched from report/manifest — implementer's seam choice; must be derived, not
hand-picked). Panel shows V2 category names/caps and reconciling arithmetic (base subtotals +
cap adjustments + mover lift + rental + adjustment = integer). Route UI chips render the R30
chip from the API. Degradation messages use V2 gap vocabulary. JS test fixtures encoding V1
groups/maxima/sentences replaced (R32). Batched with 107 (one test-writer/implementer pair,
disjoint files).

## Acceptance criteria

- [ ] Ramp stops equal the documented quantiles of the R34 report distribution (test compares against report values); legend labels match; scores remain 0–100 ints.
- [ ] Panel arithmetic reconciles for representative payloads incl. cap-adjustment and clamp cases; V2 names only.
- [ ] Route view shows chip + average from displayed scores; version mismatch on shared URL prompts refresh.
- [ ] No V1 strings (group names, "raw_total", −15 rental, 90-day-cutoff copy) in web/js runtime files (grep test).
- [ ] JS suite green; browser live probe (orchestrator gate): map renders with new ramp, open a door panel, arithmetic reconciles on screen.

## Test plan

## Attempt log
