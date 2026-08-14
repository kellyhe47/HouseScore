---
id: 021
title: "Talk track is truncated mid-sentence wherever the top evidence item is a permit"
status: green
source: qa
depends_on: []
touches: [src/houseaccount/server/api.py, src/houseaccount/route.py, web/js/panel.js, web/js/route.js]
iterations: 1
test_files: []
branch: ""
---

## Found by

Manual QA of `b11b74b`, flows 2 and 4. See `.qa/report.md` finding 1.

## Repro

1. Boot the stack (API :8100, `scripts/vercel-build.sh`, `web/` on :5173).
2. Open `http://localhost:5173`, click `62 PINE STREET, Ramsey NJ 07446` (score 56) — or plan any
   route and read the rows.

## Expected

R7.2: "Rep talk-track: 1-sentence opener generated from top evidence item, shown in panel + route
rows."

The evidence item on the very same screen reads in full:

> 1 permit filed here in the last 24 months (Alteration) — work at this address gets contracted out
> **rather than done in-house.**

## Observed

The talk track on that same screen:

> "Hi, I'm working PINE STREET today. Quick reason I knocked: 1 permit filed here in the last 24
> months (Alteration) — work at this address gets contracted out **rather than. Is now a bad time?**"

The reason clause is cut at ~110 characters on a word boundary, leaving a dangling conjunction, and
the closing question is appended after it. A second variant lands one word earlier: "…contracted out
rather. Is now a bad time?"

## Scope of the defect

- Affects the evidence panel *and* route rows — it is in talk-track generation, not row layout.
- 6 of 20 stops in a single planned route were affected.
- Value-led and age-led talk tracks are unaffected, so it is easy to miss in a spot check.
- This is the sentence a rep reads aloud at a stranger's door, which is why it is filed as high.

## Suggested acceptance

- No generated talk track ends its reason clause on a conjunction or mid-clause.
- Truncation, if it must stay, falls back to a shorter *complete* evidence sentence rather than
  cutting one.
- A test covers a permit-led door in both surfaces.

## Resolution

Green in 1 iteration. `_as_clause` now shortens an over-long evidence sentence only to a boundary the
sentence already contains (em/en dash, semicolon, colon, period), and carries it whole when there is no
such mark inside `_EVIDENCE_LIMIT`. The limit survives but bounds *where a stop is looked for*, not
where the text is chopped.

One function, both surfaces: `route.talk_track_for` feeds route rows directly and the evidence panel
via `server/published.py:308`.

- tests locked: `efd9d24` · implementation: `62813b7` · merged to working branch
- live probe on a real server: 20-stop route, **0 dangling openers** (was 6 of 20)
- full suite on the merged branch: 1357 Python, 215 JS

Noted by the implementer, deliberately not fixed here (out of scope, no ticket filed yet): the
no-boundary fallback removes the only length ceiling a route row had, so a pathological evidence
sentence with no internal punctuation now arrives in full — layout is the surface that absorbs it.
