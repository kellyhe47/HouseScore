# HouseAccount — House Score PRD

**Acceptance contract:** the fixtures in [`eval/golden/`](../eval/golden) and the wireframes [`docs/ui-wireframes.html`](ui-wireframes.html) + [`docs/architecture.excalidraw`](architecture.excalidraw) are the spec; this document explains them. Conflicts resolve fixtures-first.

Tags: `[source]` = rubric/audit · `[decided]` = user's call · `[proposal]` = pending approval.

## Requirement index
R1 territory · R2 harvest · R3 entity resolution · R4 vision · R5 eval harness · R6 House Score · R7 evidence · R8 MCP server · R9 map UI · R10 route planner · R11 ethics · R12 deployment · R13 reproducibility · R14 cost

---

## R1 — Territory
- R1.1 `[decided]` Territory = a contiguous polygon of ~540 class-2 residential parcels near Ramsey Golf & Country Club, chosen by us, committed as `data/territory.geojson`. No external polygon will ever be provided; ours is the definitive territory.
- R1.2 `[source]` Parcels fetched from NJ statewide parcel/MOD-IV ArcGIS service (audited live, free, no key): `services2.arcgis.com/XVOqAjTOJ5P6ngMu/.../Parcels_Composite_NJ_WM/FeatureServer/0`, `PCL_MUN='0248'`, paginated (maxRecordCount 2000).

## R2 — Agentic harvest
- R2.1 `[source]` Sources (all $0, audited): NJ parcels+MOD-IV (above) · NJ construction permits Socrata `data.nj.gov/.../w9se-dmra` · Census ACS5 (B23007, B19013, B08303; block group; free key) · NJ orthos 2015+2020 `maps.nj.gov/.../Orthos_Natural_2020_NJ_WM` + NAIP · Street View Static (demo-scale only, see R11.2).
- R2.2 `[source]` "Pipeline autonomy is graded" (rubric). `[proposal]` Realized as one orchestrated command (`make pipeline`) running harvest→resolve→vision→score→publish end-to-end; per-source agents discover/fetch/cache with retries; autonomy test: a fresh clone + documented env vars completes the pipeline with zero manual edits.
- R2.3 `[proposal]` All raw responses cached on disk (content-addressed) so re-runs are free and reproducible; cache is the cost-discipline evidence the rubric asks to see in code.

## R3 — Entity resolution
- R3.1 `[source]` Canonical key = `PAMS_PIN`. Permits/ACS/imagery joined to parcels via normalized situs address (`PROP_LOC`) + block/lot; report match rate (rubric metric).
- R3.2 `[proposal]` Address normalizer handles the messy formats audited (abbreviations, missing suffixes); unmatched records logged, never silently dropped; coverage % = doors with ≥1 non-parcel source / 540. Target: ≥95% permit-to-parcel match rate on records whose address falls in the territory; actual rate reported on the Data & Ethics page and in eval output.
- R3.3 `[proposal]` R4.3 vision output schema: `{pams_pin, signal, present: bool, confidence: 0-1, image_ref, capture_date}` per detection; condition uses the R6 ordinal enum.

## R4 — Vision pipeline
- R4.1 `[decided]` Top signal = **pool detection from free NJ orthos**, run over all ~540 parcels (ToS-clean). Secondary ortho signals: solar, lot/lawn condition (2015 vs 2020 for trajectory, R6 Need).
- R4.2 `[decided]` Street View signals (provider trucks, yard signs, door-level condition) demo-scale only (~50 doors), with ToS position disclosed (R11.2).
- R4.3 `[proposal]` Model: Claude Haiku tier on 640px tiles; structured JSON output with per-detection confidence; batch + cache.

## R5 — Eval harness (rubric non-negotiable)
- R5.1 `[source]` Runnable code (not a spreadsheet): labeled samples, precision/recall on top signal, hallucination rate, cost per door. `[proposal]` Sizes: ~40 hand-labeled parcels (pool), 20-image verified-negative probe set. Arithmetic pinned by fixture 09 (P=0.818, R=0.9, H=0.05 on the frozen confusion set).
- R5.2 `[proposal]` Harness also runs all golden fixtures (12 at time of writing) against the score engine; CI-style single command `make eval`.

## R6 — House Score `[decided]`
Single surfaced number 0–100 (no tiers). Deterministic, fixture-tested. `score = clamp(mover + hires_out + capacity + need + modifier, 0, 100)`.

| Group | Max | Rules |
|---|---|---|
| Mover | 100 | deed ≤30d: **100** · ≤60d: 85 · ≤90d: 70 · older: 0. "100" is the Mover *group's* contribution — the additive formula and the absentee modifier still apply (≤30d mover + rental match = 85, fixture 10). Non-arm's-length deeds (SALE_PRICE ≤ $100 **or** SALES_CODE non-empty — MOD-IV non-usable-sale flag) earn 0 (fixtures 03, 12). ICP trace: recently-moved is the #1 stated best-customer trait. |
| Hires-out | 60 | each permit in last **2 yrs** (rolling 730 days from as_of): 20, cap 40 · provider churn: +20 iff ≥2 distinct contractor names within the same window AND no contractor repeats; permits lacking a contractor field are excluded from churn (not from permit points). ICP trace: hires-out-not-DIY, demonstrated. |
| Capacity | 30 | NET_VALUE ≥ territory median: 15 (≥1.5×: 25) · ACS block-group prior (dual_income_pct ≥ 0.35): +5. Territory median = median NET_VALUE over all territory class-2 parcels with NET_VALUE > 0, computed once per pipeline run, persisted in the run manifest. ICP trace: capacity to pay for years of service. |
| Need | 30 | age ≥30yr: 8 · pool: 8 · lot ≥0.5ac: 4 · condition decline 2015→2020 (ordinal scale excellent>good>fair>poor; any ≥1-step drop): 6 · deferred-maintenance combo (age ≥30yr AND zero permits in window AND decline): 4. ICP trace: near-term service demand. |
| Modifier | −15 | absentee-likely (rental-registration match only, R11.3). `[decided]` Mild: rental households still buy services. |

- R6.1 `[decided]` Missing fields degrade gracefully: null/unparseable deed → Mover skipped; YR_CONSTR=0 → age-dependent *components* skipped (pool/lot/decline still score); emit `confidence: low` + explicit data-gap evidence (fixture 06). Confidence is binary: `normal` | `low`.
- R6.0 `[source]` Raw DEED_DATE is a YYMMDD 2-digit-year string (audited); harvest normalizes to ISO before scoring with century pivot: YY ≤ (current 2-digit year + 1) → 2000s, else 1900s (fixture 11). Unparseable → treated as null.
- R6.2 `[proposal]` ACS evidence phrased as neighborhood context ("block group: 41% dual-income"), never a household claim. This is our deliberate answer to the rubric's "household inference" bonus line: we infer at neighborhood level and say so, rather than fabricating household-level claims.
- R6.3 `[proposal]` Validation plan (rubric asks how): if outcomes existed — engagement/conversion per knocked door — validate by score-decile lift curve + calibration; pre-registered weights (this doc, dated) vs hidden ground truth shows we did not fit to it.
- R6.4 `[proposal]` Out of scope: rubric's "neighborhood effects" bonus (truck clustering, competitor density) — declined for budget/ToS reasons, noted in rationale page.

## R7 — Evidence
- R7.1 `[source]` Every score explainable: per-door evidence list, each item = {type, signed points, human sentence, source name, retrieval date} plus optional imagery attachment {image_url, bbox, model_confidence, capture_date} for vision-derived items (wireframe frame 2/2b). Image tiles for attachments are served as static files alongside doors.geojson.
- R7.2 `[proposal]` Rep talk-track: 1-sentence opener generated from top evidence item, shown in panel + route rows. Generated text is presentation-layer only — never asserted in fixtures, never feeds the score.
- R7.3 `[proposal]` Zero-point context evidence is allowed and expected — e.g. `tenure` ("28-year tenure, no permits") explains a *low* score without contributing points (fixture 05).

## R8 — MCP server
- R8.1 `[source]` Tools: `get_door_score(address)` → {score, confidence, evidence[]} · `explain_score(address)` → full breakdown incl. group math · `plan_route(hours, start_point, max_doors?)` → ordered stops with per-stop talk track (answers "2 hours in Ramsey, which 20 doors, what do I say").
- R8.2 `[proposal]` Address arg resolved through the same normalizer (R3); unknown address → structured error with nearest-match suggestion.

## R9 — Map UI
- R9.1 `[source]` Deployed MapLibre map: all ~540 parcels colored on one 0–100 ramp, numeric labels at high zoom, coverage readout ("537 of 540 scored"), score-range filter slider, click → evidence panel. Wireframes frames 1–3 are the contract, incl. degraded states 3a–3c. "Copy address" copies the situs address (`PROP_LOC` + Ramsey NJ + ZIP).
- R9.2 `[decided]` Desktop-first for design/QA effort (the graded demo is a desktop demo), AND fully mobile-responsive as a real requirement — the field rep uses it on a phone. All surfaces work at 375px: evidence panel becomes a bottom sheet, route walk mode (frame 4d) is a first-class mobile flow. Both viewports are QA'd; desktop gets the polish priority.
- R9.4 `[proposal]` Unscored doors (the gap in "537 of 540") are clickable and show an explicit exclusion state: "Not scored — parcel record incomplete in county data," counted against the coverage readout. (Accepted design addition.)
- R9.5 `[proposal]` Copy/share actions confirm with a toast. (Accepted design addition.)
- R9.3 `[proposal]` Global states per wireframe frame 6: loading skeleton with progress text; data-fetch error banner with retry; a door with no imagery signals shows a single footer message "No imagery signals for this parcel" (no distinction between no-coverage and unreadable-tile in the UI; the distinction is logged pipeline-side).

## R10 — Route planner
- R10.1 `[source]` Inputs hours + start + doors target; greedy score-per-walk-minute ordering over the score field; wireframe frames 4–4d are the contract (input states, live adjust, exclude-door, walk mode with done/skip + localStorage resume, copy/share).
- R10.2 `[proposal]` Walking-time estimate via straight-line×detour factor (no paid routing API); stated in UI as estimate. Route rows show elapsed offsets ("+42 min"), not clock times (no start-time input exists). Route computation completes < 2s for 540 candidates; ties broken deterministically by PAMS_PIN. Share link encodes the ordered stop list (PINs) in the URL fragment.
- R10.4 `[proposal]` Walk mode ends with a summary (doors knocked / skipped); an interrupted walk offers Resume or Discard on return. (Accepted design additions.)
- R10.3 `[proposal]` Route planning logic lives in one shared module used by both the MCP `plan_route` tool and the Map UI (UI calls the MCP server's HTTP surface) — one implementation, no drift.

## R11 — Ethics & ToS (graded dimension)
- R11.1 `[source]` Daniel's Law: OWNER_NAME + mailing addresses verified fully redacted (audit). We never attempt identity reconstruction (no deed-book name mining, no broker data). Documented on the Data & Ethics page.
- R11.2 `[decided]` Street View: GMP ToS §3.2.3 prohibits derived datasets; therefore bulk signals come from public-domain NJ orthos/NAIP; Street View used only at demo scale with the ToS analysis published. Zillow/Redfin excluded (ToS), cited.
- R11.3 `[decided]` Absentee detection only via municipal rental-registration match (OPRA request if needed); if unobtainable by build time, ship the hook + documented declination (fixture 08 stays as the contract).
- R11.4 `[source]` Data & Ethics page (wireframe frame 5) doubles as the required one-page score rationale: ICP definition, signal→ICP trace table, weights, validation plan, eval results.

## R12 — Deployment `[decided]`
Pipeline Python (GeoPandas). MCP server FastAPI on Fly.io. Map UI on Vercel. Both publicly reachable (rubric: local-only fails).

## R13 — Reproducibility `[source]`
README with setup; env vars documented (`ANTHROPIC_API_KEY`, `CENSUS_API_KEY`, optional `GOOGLE_MAPS_KEY`); no hardcoded secrets; reviewer can re-run pipeline end-to-end.

## R14 — Cost `[source]`
≤$50 total; projected $0–5 (Street View free tier ≥ our volume; metadata endpoint free; Haiku vision ~$1–3). Cost per door computed and reported by the eval harness.

## Risks
- Rental-registration data may be request-only → R11.3 fallback.
- 2020 orthos are the newest free vintage; "condition" is as-of-2020 — disclosed in evidence dates.
- Hidden ground-truth mismatch: mitigated by pre-registered, ICP-traceable weights (R6.3), not fitting.
