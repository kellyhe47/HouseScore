---
id: 019
title: "Disclose the MOD-IV deed-date vintage — the Mover group cannot fire on this extract"
status: green
depends_on: [009, 014]
touches: [src/houseaccount/pipeline.py, src/houseaccount/publish.py, web/js/ethics.js, tests/test_pipeline.py, tests/test_publish.py, web/js/ethics.test.js]
iterations: 1
test_files: [tests/test_pipeline.py, tests/test_publish.py, web/js/ethics.test.js]
branch: ""
---

## Why this ticket exists (found in the Phase 3 integration walk)

No door in the published run carries `deed_recency` evidence, and the maximum score is 77 — below
the 85 a single 31-60-day mover would earn on its own. Investigation against live cached data:

- The deed parser is correct: raw `'240920'` → `2024-09-20`, exactly per fixture 11.
- The **newest deed in the entire Ramsey MOD-IV extract is 2024-12-06**, ~20 months before the
  run's `as_of`. Municipality-wide, parcels with a deed inside the 90-day mover window: **0**.
- 442 of 540 territory parcels have a parseable deed; 98 are null (those already degrade to
  `confidence: low` per R6.1).

So the Mover group — the single highest-weighted signal at 100 points, and the ICP's stated
strongest predictor — is structurally unearnable on this data vintage. That is a property of the
source, not a defect in the scoring code, and `docs/audit-findings.md` already warned that
"MOD-IV attrs lag current tax year". Twenty months is a larger lag than that note implies.

A reviewer looking at the map cannot tell the difference between "no movers in this territory
right now" and "the mover rule is broken". The system must say which.

## Acceptance criteria

- [ ] The pipeline computes, from the data it already harvested (never hardcoded): the latest
      parseable deed date in the municipality, and the count of territory doors inside the mover
      window at `as_of`.
- [ ] Both land in `data/run_manifest.json`, alongside the existing degradation reporting.
- [ ] When the count is zero, the manifest carries an explicit note that the Mover group could not
      fire on this extract vintage, in the same shape as the existing `degradations[]` entries —
      a source limitation reported in the same voice as a declined provider.
- [ ] The Data & Ethics page surfaces the deed vintage and that note, so the "what ran, and what
      declined" section tells the whole truth about which signals could actually contribute.
- [ ] Nothing about the score engine changes. The Mover rules stay exactly as fixtures 01, 02, 10
      and 11 pin them — this ticket is disclosure, not rescoring.

## Deliberately NOT in scope (flagged for the human)

The real fix for the signal itself is a fresher deed source: NJ SR1A sales data, published as flat
zips at `nj.gov/treasury/taxation/lpt/statdata.shtml` (`YTDSR1A2026.zip` would carry 2026 sales).
`docs/audit-findings.md` recorded it as "no API — flat zips, fixed-width, layout PDF". Adding it
means a new harvest source, a fixed-width parser against a published layout, and a sale→parcel
join — a substantial addition that PRD R2.1 does not list and R6.0 does not assume.

**Question for the human: add SR1A as a harvest source so the Mover group can actually fire, or
ship with the vintage disclosed?** The board records this as the one decision the PRD does not
pre-make.

**ANSWERED 2026-08-14 — add SR1A. Done in ticket 020.** The register now supplies deed recency
wherever it is fresher than MOD-IV's, and the live run has 2 territory doors inside the mover
window. This ticket's disclosure is not deleted by that: it is replaced by a narrower one, since
NJ's ~6-week recording-and-publication lag still keeps the 30-day/100-point band unreachable.

## Attempt log

- iter 1: green (1263 python, 214 JS). Live `make pipeline` now emits the disclosure:
  "no door in this territory has a deed dated inside the 90-day mover window: the newest deed
  anywhere in the MOD-IV extract is 2024-12-06. The Mover group — the heaviest signal in the
  model — therefore scored zero everywhere on this run. That is the vintage of the county
  extract, not a rule that failed to fire."
- Manifest carries `deed_vintage: {latest_deed_date: "2024-12-06", mover_window_days: 90,
  doors_in_mover_window: 0}`, with the window read from `THRESHOLDS["mover_90d_days"]` so the
  disclosure cannot drift from the rule it describes.
- The note is emitted by the pipeline, not by `publish` — an existing locked test pins
  `manifest["degradations"] == list(result.degradations)`, which a publish-side synthesis would
  have broken on the real run.
