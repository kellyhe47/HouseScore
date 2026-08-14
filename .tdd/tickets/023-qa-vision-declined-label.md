---
id: 023
title: "Data & Ethics calls the vision stage DECLINED on a run where it produced 119 doors of imagery"
status: green
source: qa
depends_on: []
touches: [web/js/ethics.js, src/houseaccount/publish.py]
iterations: 1
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

## Resolution

Green in 1 iteration — **code complete and tested, but the live page still reads DECLINED until the
run manifest is regenerated.** See the artifact note below; that part is the human's call.

The vision stage now publishes its own state instead of being inferred. `OpenAIVisionProvider` counts
the requests it issues, `VisionRun.answers_lost` is derived from the parse failures it already holds
(so it cannot drift), and `publish` emits a `vision` block:

    "vision": {"available": true, "declination_reason": null,
               "answers_total": 270, "answers_lost": 31, "doors_with_imagery": 119}

`doors_with_imagery` is counted off the features being written — doors with >=1 evidence line carrying
an `imagery` attachment — exactly as `doors_scored` is, so it cannot be passed a number nobody checked.
An absent block still means "this run never measured the stage", so older publishes keep rendering.

The page gains a third state: `status` in {live, partial, declined} on every availability row, with
`live === (status !== 'declined')` preserved for existing callers, and the partial row quantifying the
loss from the manifest's own numbers rather than prose.

- tests locked: `a729f1e` · implementation: `731d604` · merged (conflict in test-fixtures.js resolved
  by keeping both additive sections)
- full suite on merged branch: 1373 Python, 236 JS

### The artifact gap — decision required

`data/run_manifest.json` was published before the `vision` block existed, so the live page falls
through to the legacy degradation-string path and still prints DECLINED. Verified in a browser after
the merge.

**Regenerating is not straightforward, and the obvious move is wrong.** With a WARM vision cache the
provider issues no requests, so a re-run would publish `answers_total: 0, answers_lost: 0` and the page
would render the vision row as `live` — truthful about *that* run, but it would NOT reproduce the
31-of-270 partial state this ticket exists to display. Reproducing that needs a COLD vision cache, i.e.
paying for ~270 vision requests again (~$0.52 at the measured low-detail rate).

The scope in `touches` was wrong, recorded per the deviation rule: it named `web/js/ethics.js` and
`publish.py` only. The wiring the pipeline tests drive also required `pipeline.py`, `vision/run.py`,
`vision/provider.py`, `web/ethics.html` and `web/styles.css`.

### Noticed, not fixed (no ticket filed)

The `mover_deed_vintage` row is a third-state candidate too: it renders `live` while carrying a
recording-lag disclaimer — the same shape `partial` was introduced for. Left exactly as it was.
