---
id: 014
title: "Data & Ethics page — doubles as the one-page score rationale (R11.4)"
status: green
depends_on: [008, 012]
touches: [web/ethics.html, web/js/ethics.js, web/js/ethics.test.js]
iterations: 1
test_files: [web/js/ethics.test.js]
branch: ""
---

## Scope

Wireframe frame 5. This page is a graded deliverable twice over: the ethics/ToS position **and**
the required one-page score rationale (ICP, signal→ICP trace, weights, validation plan, eval
results).

## Acceptance criteria

- [ ] Renders: ICP definition; a signal→ICP trace table covering all five score groups (mover,
      hires-out, capacity, need, absentee modifier); the weights table; the R6.3 validation plan.
- [ ] Live eval numbers are loaded from `eval/report.json` (T008) — precision, recall,
      hallucination rate, cost per door, entity-resolution match rate — not hardcoded in the page.
- [ ] **No weight drift:** the rendered weights come from a single shared constant that mirrors
      the engine's `weights.py`, and a test asserts every rendered number equals the engine's.
- [ ] Daniel's Law section states that owner-name and mailing-address fields are redacted at
      source, are never requested, and that no identity reconstruction is attempted (R11.1).
- [ ] Street View section states the GMP ToS §3.2.3 position (bulk signals from public-domain NJ
      orthos; Street View demo-scale only) and the Zillow/Redfin exclusion (R11.2).
- [ ] Absentee section renders the rental-registration declination text when the provider reports
      unavailable, and the matched-count text when it is available (R11.3).
- [ ] ACS language is neighbourhood-level throughout — no household claims (R6.2).
- [ ] The demo-only "simulate data-fetch error" trigger is present and visually marked as
      demo-only (DESIGN-ADDITIONS).

## Attempt log

- iter 1: green (207 JS tests). Orchestrator live probe: the page renders the run manifest
  verbatim — all three declination reasons (ACS/rental/vision), 540 of 540 doors scored,
  per-source retrieval dates — and the frozen-fixture metrics caveat prints directly under the
  P/R numbers instead of being buried.
- Weights anti-drift works in both directions: the JS mirror is deep-equalled against a parse of
  `src/houseaccount/scoring/weights.py`, and a fourth test guards the parser so an empty mirror
  cannot pass.
- Also fixed: stale header tooltips, Data & Ethics now a real link, `map.resize()` + bounds refit
  on viewport resize, and a sticky `#demo-error` fragment that made Retry re-fail forever.

## Deployment finding (handed to 016)

`ethics.html` reads `eval/report.json` and `data/run_manifest.json` via
`HOUSEACCOUNT_ARTIFACT_BASE`, default `..` — i.e. the repo root relative to `web/`. A static host
serving `web/` as the site root cannot resolve that. The deploy must either serve those two
artifacts through the API or copy them into the published directory.
