---
id: 012
title: "Map UI core: choropleth, filter, evidence panel, degraded states (R9)"
status: pending
depends_on: [009, 011]
touches: [web/index.html, web/styles.css, web/js/ramp.js, web/js/panel.js, web/js/filter.js, web/js/state.js, web/js/map.js, web/js/ramp.test.js, web/js/panel.test.js, web/js/filter.test.js, web/js/state.test.js]
iterations: 0
test_files: []
branch: ""
---

## Scope

The deployed map surface. **`docs/prototype-decoded.html` is the approved visual reference — read
it and drive it, don't invent.** (It is the readable de-bundled form of
`docs/HouseAccount-Prototype.html`; `docs/ui-wireframes.html` has the frame structure and
`docs/DESIGN-ADDITIONS.md` the accepted additions.)

Pure logic goes in `web/js/*.js` ES modules so `node --test` covers it without a browser; the
map rendering itself is verified by a live browser probe. Route/walk UI is T013, not this ticket.

Palette from the prototype (do not re-pick): background `#F2F1ED`, text `#1B1E23`, link `#2A5B8F`,
hairline `#DBD9D2`, mono `IBM Plex Mono`, sans `IBM Plex Sans`.
Score ramp stops (linear interpolation between them, RGB):
`[0,237,239,242] [25,191,208,226] [45,127,163,201] [65,65,114,159] [85,30,76,126] [100,18,47,85]`.

## Acceptance criteria

- [ ] `scoreColor(score)` interpolates the ramp above; endpoints and a midpoint are pinned; a
      `null` score returns the distinct unscored grey (not a ramp colour).
- [ ] `coverageText(scored, total)` → `"537 of 540 scored"`.
- [ ] `filterDoors(doors, [min, max], {showUnscored})` is inclusive at both ends; unscored doors
      are excluded unless `showUnscored` is set.
- [ ] `buildPanel(door)` returns evidence rows ordered by descending `|points|`, each with signed
      display (`"+15"`, `"−15"`); zero-point context rows render without a sign badge (R7.3).
- [ ] A door with `score === null` yields the exclusion panel state with exactly the prototype's
      copy: `"Not scored — parcel record incomplete in county data."` (R9.4).
- [ ] A door with no vision-derived evidence yields the footer `"No imagery signals for this
      parcel"` (R9.3) — one message, no no-coverage/unreadable distinction.
- [ ] `copyAddress(door)` returns `"<PROP_LOC>, Ramsey NJ <ZIP>"` and emits a toast event (R9.5).
- [ ] A `state.js` machine covers `loading → ready` and `loading → error`, with `retry()` from
      `error` returning to `loading` (R9.3 frame 6).
- [ ] `panelLayout(viewportWidth)` returns `"sheet"` below 768px and `"side"` at/above it — the
      375px bottom-sheet requirement (R9.2), with the header wrap fix from DESIGN-ADDITIONS.
- [ ] Live probe: `index.html` loads against the running server with **zero console errors**,
      renders the parcels, and the score filter visibly changes the rendered set.
