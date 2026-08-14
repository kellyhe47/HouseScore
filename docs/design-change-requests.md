# Design change requests — round 1 (2026-08-13)

Prototype: `docs/HouseAccount-Prototype.html`. Findings from driving the prototype (desktop + 375px) and full source review. Fix in place and keep the same file; note fixes inline below when closing out.

## P0
1. **Map parcel click does not open the evidence panel.** State: default map view (no route active) and route-active view. Clicking any parcel — tested multiple, both zooms — does nothing; the evidence panel opens only via route-list rows. Violates R9.1 ("click → evidence panel") and the rubric's demo line: "every door scored, click for evidence." Fix: wire canvas hit-test → evidence panel (hit-testing demonstrably works — start-point placement uses it).

## P1
2. **Route mode cannot be exited.** After planning a route and closing the Plan Route panel, the map stays dimmed with numbered pins indefinitely; no control returns to the normal choropleth. Fix: closing the route panel (or an explicit "Clear route") restores the default map.
3. **Mobile (375px) map viewport broken.** Territory renders mostly off-screen right with large empty canvas left; header wraps awkwardly. Violates R9.2 (fully mobile-responsive). Fix: fit-territory-to-viewport on load/resize; compact header.
4. **Churn showcase door's evidence over-sums.** "Crescent Dr" door: Hires-out group is 60 (40 cap + 20 churn) but the panel renders 3 × "+20 permit" lines + "+20 churn" = 80; panel totals 120 vs raw 100. The permit cap must be represented in evidence (e.g. third permit line "+0 (cap reached)" or two lines +20 +20 then cap note). Only the `permitList` path is wrong; generic path caps correctly.
5. **~19 random low-confidence doors show contradictory evidence.** Generator zeroes mover/age for `conf='low'` doors but leaves age detail populated and omits the data-gap line → banner says "year built unavailable" while an evidence line reads "+8 Home built 1975 (51 yrs)". Fix: low-conf path must set `gap` and suppress age/deed evidence, matching the fixture-06 door (which is correct).
6. **Share link missing.** R10.2 / frame 4d require a route share link (URL-encoded stops). Only "Copy as text" exists. Fix: add Share link control encoding ordered PINs in the URL fragment.
7. **DESIGN-ADDITIONS.md missing.** The brief required every past-spec addition be labeled; six were found unlabeled (see Accepted additions below — now specced, so document them and any others).

## P2
8. No cancel affordance during route computing (frame 4c specifies one).
9. Imagery evidence thumbnails not tappable (frame 2b: tap → full-size with detection bbox).

## Accepted additions (now folded into PRD as requirements — keep them)
- Unscored-door panel state with exclusion copy → R9.4
- Walk-finished summary ("X knocked · Y skipped") → R10.4
- Resume banner with Discard action → R10.4
- Copy/share confirmation toasts → R9.5
- "→ floored 0" math-line variant → consistent with R6 clamp; keep
- About-page "simulate a data-fetch error" link → keep, but visually mark as demo-only

## Rejected additions
- none

## Not changing — checked and correct
- Fixed copy: all exact (banner, ACS phrasing, no-imagery footer, elapsed offsets, no tiers/clock times)
- About/Data & Ethics page: complete per R11.4 incl. eval table, match rate, MCP endpoints
- Showcase-door math for fixtures 01/02/05/06/07/08/10 doors: exact, incl. raw 118→capped and 100−15=85
- Route input gating (Plan disabled pre-start), outside-territory toast, empty state, walk-mode progress/resume

---
# Round 2 close-out (2026-08-13, re-drive of revised prototype)

## Fixed — verified
- #2 Route mode exit: closing panel restores full choropleth ✓ (driven)
- #3 Mobile viewport: territory fits/centers at 375px ✓ (driven; residual nit below)
- #4 Churn-door evidence sum: permitList now renders `i<2 ? 20 : 0` — third permit +0, group sums 60 ✓ (source)
- #5 Low-conf contradiction: generator sets gap=true, nulls age/deed details ✓ (source)
- #6 Share link: present in route footer ✓ (driven)

## NOT fixed — carry to round 3
- **#1 (P0) Map parcel click still does not open the evidence panel.** Re-tested on a clean load, real clicks dead-center on multiple parcels, both viewports. Panel opens only from route rows. This is the rubric's core demo interaction.
- **#7 (P1) DESIGN-ADDITIONS.md still missing.**

## New (minor)
- P2: at 375px the "Data & Ethics" header button clips off the right edge (top bar doesn't wrap/collapse).

## Not re-verified this round
- P2 #8 (compute cancel), P2 #9 (tappable imagery thumbs) — check in round 3.
