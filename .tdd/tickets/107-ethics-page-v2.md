---
id: 107
title: Data & Ethics page V2 (R29)
status: green
depends_on: [104]
touches: [web/js/ethics.js, web/js/ethics.test.js]
iterations: 1
test_files: [web/js/ethics.test.js]
branch: ""
---

## Scope

Rewrite the live Data & Ethics page content for V2 (R29): objective, exact category caps
(25/25/30), mover blend formula + exponential decay (with the derived 275 / B/8 rationale),
project + roof evidence rules (conservative interpretation), rental precedence incl. −25
dormant status, missing-data behavior + gap vocabulary, source limitations (SDL point-in-time,
SR1A lag, no contractor identity, imagery vintages), current evaluation claims sourced from
the V2 eval/report.json. Remove: old additive formula, old group maxima, provider-churn claim,
deferred-maintenance bonus, 90-day cutoff, −15 rental language. Retain signal-to-ICP trace
table and state the R36 validation plan (PRD R11.4). Batched with 106.

## Acceptance criteria

- [ ] Page-scan tests: required V2 claims present (caps, blend, decay window 90/365, −25 dormant, neutral-unknown rules, ICP trace, R36 plan); forbidden V1 strings absent (whole-page scan like the existing R6.2 test).
- [ ] Fixture/eval counts on the page come from report.json, not hardcoded literals (regression of V1 ticket 025).
- [ ] ACS described as neighborhood-level prior; condition as historical decline.
- [ ] JS suite green; browser probe with 106.

## Test plan

## Attempt log
