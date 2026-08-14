# Design brief — HouseAccount House Score map

## Product context (one paragraph)
HouseAccount is a home-services concierge that wins by owning neighborhoods for years — and this tool tells its field reps which doors to knock. Every one of ~540 single-family homes in a Ramsey, NJ territory gets a **House Score (0–100)**: the likelihood that household will engage a concierge service. A rep stands at the curb with a phone, sees the scored map, plans a 2-hour walking route, and gets a one-line talk track at each door. It is graded via a desktop demo — **design desktop-first** — but the app must be fully mobile-responsive: the field rep genuinely uses it on a phone at the curb. Every surface works at 375px (panel → bottom sheet; walk mode is a first-class mobile flow). Desktop gets the polish priority; mobile gets full function.

## Where to look (do not restate — read)
- `docs/PRD.md` — numbered requirements; R6 is the score model, R7 evidence, R9 map, R10 route planner. Wireframe frames are cited as contract per requirement.
- `docs/ui-wireframes.html` — 10 low-fi frames: every surface and state to design. Design ALL of them, not just the happy path.
- `eval/golden/*.json` — realistic content for populating designs (real addresses shapes, evidence types, score arithmetic). **Use these values, not lorem ipsum** — e.g. a door showing "Score 100 · raw 108, capped" (fixture 01), a low-confidence door at 15 (fixture 06), an absentee door at 28 (fixture 08).

## Copy that is FIXED (requirements, not suggestions)
- Score display: number 0–100 only — no letter grades, no tiers (a deliberate product decision).
- Confidence values: exactly `normal` and `low`; low-confidence banner: "Deed date and year built unavailable in county data — score from partial signals."
- Evidence lines carry signed points + human sentence + source + retrieval date, e.g. "+85 Moved in 47 days ago (deed 2026-06-15)" / "−15 Matches municipal rental registration — occupant likely tenant".
- ACS evidence must read as neighborhood context ("block group: 41% dual-income"), never a household claim (ethics requirement R6.2).
- No-imagery footer: "No imagery signals for this parcel" (no cause detail).
- Route rows use elapsed offsets ("+42 min into route"), never clock times.

## What a user must grasp at a glance, per surface
- **Map:** where the hot doors cluster — the 0–100 color ramp must make 85 vs 45 legible at neighborhood zoom, and the coverage readout ("537 of 540 scored") must be visible but quiet.
- **Evidence panel:** the score AND its top reason within one second — the "why" is the product's whole credibility; the score-math expansion (frame 2b) is secondary depth.
- **Route mode:** which door is next and what to say there — everything else recedes.
- **Data & Ethics page:** that this system is defensible — sources, what we deliberately don't do (Daniel's Law), eval numbers. It doubles as a graded deliverable; design it like a page evaluators will read closely.

## Hardest design problems (named)
1. One parcel choropleth that stays legible across zooms AND under the score-range filter.
2. The evidence panel must serve three states with the same layout — normal, low-confidence, absentee-flagged — without turning into a debug view (frames 2, 3a, 3b).
3. Walk mode (mobile viewport): big-thumb, glare-readable, one-hand operation; done/skip unmissable; progress legible mid-stride.
4. Score math (frame 2b group bars, raw→capped) must feel like an explanation, not a spreadsheet.

## Visual direction
Trustworthy field tool, not consumer real-estate gloss: high contrast outdoors, dense but calm, generous touch targets. Light mode primary (sunlight). Reference feel: Linear's clarity + a surveying/GIS tool's seriousness. Brand is unset — pick a restrained palette where the score ramp is the only loud element.

## Deliverable
Clickable HTML prototype (static, no build step), every frame/state from the wireframes, desktop-first and fully responsive — show each key surface at both desktop and 375px.

## Design past the spec where the product clearly needs it
You will spot gaps the PRD missed, and those are valuable — that is part of why this step exists. **But label every addition:** for anything you add that the spec doesn't cover, note what you added, what it does, and why the flow needed it (in a `DESIGN-ADDITIONS.md` alongside the prototype). Additions are welcome; *silent* additions are not.
