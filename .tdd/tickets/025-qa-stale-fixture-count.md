---
id: 025
title: "Ethics page hardcodes 'twelve golden fixtures'; the harness reports thirteen"
status: in-progress
source: qa
depends_on: []
touches: [web/js/ethics.js]
iterations: 0
test_files: []
branch: ""
---

## Found by

Manual QA of `b11b74b`, flow 6. See `.qa/report.md` finding 5.

## Repro

Open `http://localhost:5173/ethics.html`, section "The exact weights".

## Expected

`eval/report.json` reports `fixtures_total: 13`, and `eval/golden/` contains 13 files.

## Observed

> "Deterministic and integer-valued. The same door and the same inputs produce the same score on
> every run, and **twelve** golden fixtures pin the arithmetic."

`web/js/ethics.js:651`.

## Why it is worth fixing rather than editing

Two paragraphs earlier the same page sets its own standard:

> "every point value is read from the scoring engine's own weight table rather than restated here —
> a published weight that disagreed with the engine would make this page a lie about the map next
> to it."

The weights honour that and are read live. The fixture count is hardcoded prose and has already
drifted once. Reading it from `eval/report.json` — which the page already fetches for the eval
figures — removes the whole class of drift rather than resetting the counter.

## Suggested acceptance

- The count renders from the published report, not a literal.
- Low severity; no user is misled about a score, but the page's accuracy claim is the point of it.
