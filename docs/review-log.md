# Spec review — round 1 (2026-08-13)

3 parallel reviewers: numeric+coverage · contradictions+testability · diagram+design.

## Fixed (P0)
- "100 flat" ambiguity: defined — Mover group contributes 100; formula stays additive; absentee −15 applies to everyone (user's rental ruling). Pinned by new fixture 10.
- Wireframe 3b showed stale −50 absentee → corrected to −15 / score 28.

## Fixed (P1)
- YYMMDD deed-date parse unpinned → fixture 11 (century pivot rule, R6.0).
- SALES_CODE OR-branch untestable → fixture 03 now price-only, new fixture 12 code-only.
- R6.1 "group skipped" → component-level skip semantics (matches fixture 06).
- Territory median undefined → defined in R6 table (per-run, class-2, NET_VALUE>0, manifest).
- Condition ordinal scale undefined → excellent/good/fair/poor, any ≥1-step drop (R6 table).
- Churn window/repeat/missing-contractor semantics → defined in R6 table.
- Diagram: added rental-registration + territory sources, hand-labels node, score→eval edge, map→MCP plan_route edge (R10.3 single shared routing module).
- Wireframe frame 2 score inconsistency (87 vs 108-capped) → fixed to 100.
- Frame 4d clock arrival times without start-time input → elapsed offsets (R10.2).
- Frame 6 contract orphan → R9.3.
- Frame 2b imagery evidence beyond data model → R7.1 imagery attachment schema + static tile hosting.
- Match-rate unfalsifiable → R3.2 target ≥95%, reported.
- Tag mislabels (R2.2, R5.1, R6.3 [source]→split/[proposal]) → fixed.

## Fixed (P2)
- Fixture 08 stale clamp-floor claim removed (clamp ceiling exercised by fixtures 01/10 instead).
- Tenure zero-point evidence → R7.3. Confidence enum {normal, low} → R6.1.
- Neighborhood-effects bonus → explicit out-of-scope R6.4; household-inference bonus → R6.2 sentence.
- Copy-address semantics (R9.1), share-link encoding + <2s + tie-break (R10.2), no-imagery footer collapse (R9.3).

## Rejected (with reason)
- "R1.1 contradicts rubric's 'provided polygon'": user explicitly decided 2026-08-13 that no polygon will be provided and ours is definitive. Not open. (Two reviewers raised it; both overruled by user decision.)

## Verification
`python3 eval/verify_claims.py` — OK, all 12 fixtures reproduce from R6 rules including comparatives.

# Round 2 (2026-08-13)
Re-verification agent: both scripts pass; all round-1 fixes confirmed landed. Residual P1s fixed: PRD R5.2 stale "9 fixtures" → "all (12)"; wireframe frame 4 route row 87 → 100 (matches frame 2). P2 nits fixed: fixture 10 stale clamp claim removed; frame 3c footer matches R9.3. verify_claims.py re-run: OK.
Result: NO remaining P0/P1.

# Design round (2026-08-13)
Prototype driven (desktop + 375px) + full source review. Result: 1 P0 (parcel click doesn't open evidence panel), 6 P1 (route mode un-exitable; mobile map off-viewport; churn-door evidence over-sums 120 vs 100; ~19 low-conf doors show contradictory age evidence; share link missing; DESIGN-ADDITIONS.md missing), 2 P2. Fixed copy / About page / 7 of 8 showcase doors' math verified clean. Six unlabeled additions found — all accepted into PRD (R9.4, R9.5, R10.4). Change requests: docs/design-change-requests.md — hand back to the SAME design agent.
