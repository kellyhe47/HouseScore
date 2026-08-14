# Manual QA — HouseAccount Map UI

```yaml
sha: b11b74b
branch: fix/vision-throughput-and-run-durability
tree: clean
launched: |
  API   PYTHONPATH=src .venv/bin/python -m uvicorn houseaccount.server.app:create_app --factory --port 8100
  build sh scripts/vercel-build.sh "http://127.0.0.1:8100/api" "http://127.0.0.1:8100/api"
  UI    npx serve web -l 5173  ->  http://localhost:5173
```

**Spec:** `docs/PRD.md` (R7 evidence · R9 map · R10 route · R11.4 ethics page).
**Designs:** `docs/ui-wireframes.html`, `docs/HouseAccount-Prototype.html` — repo mockups, compared at the
level of structure and intent.

**Boot note.** Port 8000 was already held by an unrelated `ai-red-team` service, whose `/health`
answers `{"status":"ok","service":"ai-red-team"}`. The runbook's port was therefore *not* the app
under test, and the first `vercel-build.sh` injection pointed the UI at it. Re-injected against
:8100. Anyone following `docs/DEPLOY.md` on this machine hits the same trap.

**Screenshot note.** The browser tool returns images inline and cannot write files, so
`.qa/screens/` is empty and findings cite the reproducing step instead of a path. Every observation
below was seen on screen; nothing is inferred from source. Where a step could not be driven by a
real gesture it says so explicitly.

---

## Flows walked

| # | Flow | Requirements | Result |
|---|---|---|---|
| 1 | Load map → read coverage → filter by score | R9.1 | pass |
| 2 | Click parcel → evidence → show math → copy address | R7.1, R7.3, R6.2, R8.1, R9.5 | pass |
| 3 | Click imagery-less parcel → footer state | R9.3 | pass |
| 4 | Plan route → exclude → share link → reopen shared link | R10.1, R10.2 | pass w/ finding 1 |
| 5 | Walk mode → done/skip → reload → resume/discard | R10.4 | partial — summary not reached |
| 6 | Data & Ethics end to end | R11.4, R3.2, R11.1, R11.2, R11.3, R5.1 | pass w/ findings 3–5 |
| 7 | Reload the map | R9.1 | **fail — finding 2** |
| 8 | Mobile 375px → tap parcel → bottom sheet | R9.2, R6.1 | pass |
| 9 | Simulated data-fetch error → retry | R9.3 | pass |

### What worked, concretely

- Map draws 540 parcels on one ramp; readout `540 of 540 scored`; filter to `score 80–100` isolates
  three parcels and fades the rest.
- Evidence panel carries signed points, human sentence, source name and retrieval date per item,
  plus `IMAGERY EVIDENCE — pool · conf 0.90 · captured 2020-01-01`. Vision output reaches the UI.
- Zero-point context evidence renders with a `·` marker (R7.3), and ACS lines are phrased as
  block-group context with "nothing is known about who lives here" (R6.2).
- `Show math` reveals group arithmetic: Mover 0/100, Hires-out 20/60, Capacity 30/30, Need 20/30,
  `raw 70 = score 70` (R8.1).
- Copy address fires a `role="status"` toast reading "Address copied" (R9.5). It fades fast enough
  to miss on a screenshot — verified in the DOM rather than filed as a bug.
- Imagery-less doors show exactly the specified string: "No imagery signals for this parcel".
- Route rows show elapsed offsets ("+3 min into route · 1 min walk from prev"), never clock times
  (R10.2), with a per-row exclude-and-re-plan control.
- Share link round-tripped: 20 stops restored in identical order from the `#route=` fragment.
- Walk mode shows "1 of 20 · 13 min left", Skip / Done, and an UP NEXT queue; state persists to
  `houseaccount.walk.v1` and a reload offers **Resume** / **Discard** (R10.4).
- Mobile 375px turns the evidence panel into a bottom sheet with a drag handle (R9.2), and surfaced
  the degraded state: score 13, `confidence: low`, an amber low-confidence callout, and a data-gap
  line — "deed date missing from the parcel record… Read this score as a floor rather than a
  verdict" (R6.1).
- Data & Ethics discloses the eval honestly: "These vision figures come from a frozen scoring
  fixture, not from a hand-labelled sample… Read them as a check that the arithmetic is stable, not
  as a measurement of how well the detector sees." Real `$0.0014` cost per door. Municipal (97.4%)
  and permit (62.1%) match rates reported separately, with the reason conflating them would mislead.

---

## Findings

### 1 — Talk track is truncated mid-sentence, in the panel and every route row

**Flow 4, and flow 2.** Severity: high — this is the line a rep reads aloud at a door.

*Repro.* Plan any route, or open any door whose top evidence item is a permit (e.g.
`62 PINE STREET, Ramsey NJ 07446`, score 56).

*Expected* — R7.2: "Rep talk-track: 1-sentence opener generated from top evidence item, shown in
panel + route rows." The evidence item on that same screen reads in full:

> 1 permit filed here in the last 24 months (Alteration) — work at this address gets contracted out
> **rather than done in-house.**

*Observed* — the talk track on the same screen:

> "Hi, I'm working PINE STREET today. Quick reason I knocked: 1 permit filed here in the last 24
> months (Alteration) — work at this address gets contracted out **rather than. Is now a bad time?**"

The reason clause is cut at ~110 characters on a word boundary, leaving a dangling conjunction, and
the closing question is appended after it. A second variant truncates one word earlier
("…contracted out rather. Is now a bad time?"). Six of twenty stops in one route were affected;
every permit-led door reproduces it. Value-led and age-led talk tracks are unaffected, which is why
it is easy to miss.

### 2 — The map renders blank on any reload

**Flow 7.** Severity: high — a refresh or a back-navigation shows an empty product.

*Repro.* Open `http://localhost:5173` in a fresh tab (map draws correctly). Reload the page.

*Expected* — R9.1: "all ~540 parcels colored on one 0–100 ramp". Wireframe frame 1.

*Observed.* Empty grey map. The chrome all loads — header, legend, and the readout still claims
`540 of 540 scored` — but no parcels are drawn. No console errors. Clicking zoom-out twice reveals
the whole territory, so the data and the layer are present and only the initial camera is wrong.
Reproduced 4/4 on reload in one tab and confirmed in a second tab: **first load in a fresh tab
always worked, every reload was blank.** localStorage and sessionStorage were empty, so this is not
walk-state related.

The readout claiming "540 of 540 scored" over an empty map is the aggravating part: nothing tells
the user to zoom out, and the screen reads as a broken deploy.

### 3 — Data & Ethics reports the vision stage as DECLINED although it ran

**Flow 6.** Severity: medium — the page contradicts the map beside it.

*Repro.* Open `/ethics.html`, section "What ran, and what declined".

*Expected.* The section's own contract: "where a provider refused, the refusal is printed instead of
the number it would have produced." A stage that ran and partly succeeded is not a refusal.

*Observed.*

> **DECLINED** · Aerial-imagery vision stage · "31 vision answers could not be read as detections;
> the doors they covered scored without their imagery signals"

The stage did run on this publish: 270/270 requests completed and 119 of 540 doors carry imagery
evidence — one is visible two clicks away (`17 SYCAMORE COURT`, "Pool visible from the air",
conf 0.90). 31 of 270 batches failing is a partial degradation, not a declination. A reviewer reads
DECLINED as "no imagery at all" and then finds pool evidence on the map.

### 4 — Published MCP URL points at a hostname the deploy will not create

**Flow 6.** Severity: medium — a published link that 404s.

*Repro.* `/ethics.html`, "MCP server" section.

*Expected.* `fly.toml` declares `app = "houseaccount"` and `docs/DEPLOY.md` verifies against
`https://houseaccount.fly.dev`.

*Observed.* The page publishes `https://houseaccount-mcp.fly.dev/mcp` (`web/js/ethics.js`). No such
app is created by the runbook, so the advertised endpoint will not resolve after a by-the-book
deploy. Either the runbook creates a second app or the page names the wrong host.

### 5 — "twelve golden fixtures" contradicts the harness

**Flow 6.** Severity: low.

*Repro.* `/ethics.html`, "The exact weights" section.

*Expected.* `eval/report.json` reports `fixtures_total: 13`, and `eval/golden/` holds 13 files.

*Observed.* "Deterministic and integer-valued… and **twelve** golden fixtures pin the arithmetic"
(`web/js/ethics.js:651`). Notable because the same page states its own standard two paragraphs
earlier — "every point value is read from the scoring engine's own weight table rather than restated
here — a published weight that disagreed with the engine would make this page a lie about the map
next to it." The weights honour that; this count is hardcoded and has drifted.

---

## Requirements no flow could cover

Reported rather than passed — each is a real gap in this QA run.

- **R9.4 — unscored-door exclusion state.** Unobservable: this publish scored 540/540, so no door
  exists to click. The "Not scored — parcel record incomplete in county data" state and its effect
  on the coverage readout are untested. The legend does render a "not scored" swatch.
- **R9.1 — numeric labels at high zoom.** Not verified.
- **R9.3 — loading skeleton with progress text.** Never observed; local data returns too fast.
- **R10.4 — walk-mode completion summary** ("doors knocked / skipped"). Resume/Discard verified;
  the end-of-walk summary was not reached before the browser pane stopped responding.
- **R9.2 — walk mode as a first-class mobile flow.** The bottom sheet was verified at 375px; walk
  mode at 375px was not.

## Escalations

- **Mobile tap selection.** At 375px the harness translates mouse to touch and taps panned the map
  instead of selecting a parcel, and the browser pane repeatedly timed out as "hidden". The mobile
  evidence panel was therefore opened with a synthetic pointer sequence on the canvas, not a real
  gesture. The bottom-sheet layout and its contents were then observed on screen normally. A human
  should confirm parcel selection by real touch.
- **Deployed surfaces.** R12 requires both hosts publicly reachable. Only the local stack was
  exercised; nothing here says the Fly.io or Vercel deployments behave the same.
