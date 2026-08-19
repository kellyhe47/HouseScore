# Door Score V2 fixture provenance

Source of truth: `docs/plans/2026-08-18-2221-feat-door-score-v2-plan.md` (the V2 contract).
Every `traces_to` ID in `golden-manifest.json` and `golden/*.json` resolves here.

## ID registry

| ID | Meaning |
|---|---|
| `R1`–`R36` | Numbered requirements in the plan's **Requirements** section. |
| `AE1`–`AE12` | The plan's **Acceptance Examples**. |
| `DEC-001` | Key Decision: blend movers into a priority band (governs R2, R4, R5). |
| `DEC-002` | Key Decision: exponential mover decay (governs R4). |
| `DEC-003` | Key Decision: separate roof need from project behavior (governs R9–R11, R17). |
| `DEC-004` | Key Decision: conservative municipal evidence (governs R8–R12, R17, R23). |
| `DEC-005` | Key Decision: local + territory-wide capacity (governs R13–R16). |
| `DEC-006` | Key Decision: stronger verified-rental demotion, −25 (governs R6). |
| `DEC-007` | Key Decision: replace the V1 contract across the product (governs R27–R33). |

## Scope

The fixtures cover the scoring contract (R1–R26), including source precedence (R22 via B-024), as_of temporal determinism (R24 via B-022), and PII exclusion (R25 via B-023). R24's retrieval-date retention and R25's published-artifact PII check are additionally enforced by the R34 report contract. Migration/audit requirements
(R27–R35) are process gates verified by the implementation's stale-reference audit and
recalculation report, not by per-door fixtures. R36 (field outcomes) is deferred by design.

## Envelope conventions (binding on the product golden runner)

- `when.operation` is `score_door`: score one property's normalized evidence bundle as of
  `given.clock.as_of`. Scoring is pure — `state_changes`, `emitted_events`, and
  `external_calls` are exactly `[]`.
- Floats (`mover.strength`, `mover_lift`, `pre_rounding`, `adjustment`) round to 3 dp
  before comparison; the score itself is `round_half_up(clamp(P + rental, 0, 100))` (R7).
- `result.evidence` is projected to `{type, points}` and sorted by `type`. Entries exist
  for every nonzero signal and for these consequential neutralizations (R26):
  `project_neutralized` (permit records present, zero project points), `fit_roof_age: 0`
  (roof records present but no qualifying completed installation, or installation younger
  than 10 years), `fit_condition_superseded`, `mover_invalid_sale`, `rental_stale`. When a category's raw signals exceed its cap, a `project_cap_adjustment` / `capacity_cap_adjustment` / `fit_cap_adjustment` entry carries the negative delta so evidence points sum to the capped subtotal (R7).
- `result.confidence` is `low` when `result.data_gaps` has 2+ entries, else `normal` (R37).
- `result.data_gaps` is `{type}` sorted by `type`; gap vocabulary:
  `rental_data_missing`, `local_comparables_insufficient`, `acs_missing`,
  `assessed_value_missing`, `sdl_page_unavailable`, `imagery_missing`.

## Validation commands (spec, not product)

```bash
python3 ~/.claude/skills/product-inception/scripts/validate_golden.py eval/v2/golden
python3 eval/v2/verify_fixtures.py
```

`verify_fixtures.py` re-derives every envelope from `given` and asserts the AE1–AE4
anchors and all band boundaries (R13, R14, R17, R18). The expected envelopes were
generated from the same derivation, so this proves internal consistency and anchor
correctness — product behavior is proven only by the implementation's `test-golden`
runner, in which each fixture must first fail for its intended reason.

## Relationship to V1

Rental currency with no valid sale on record uses a provisional 24-month window before `as_of` (R6). Deed-date normalization (YYMMDD century pivot, PRD R6.0) happens upstream of `score_door`; fixtures consume ISO dates.

`eval/golden/` (13 V1 fixtures), `eval/verify_claims.py`, and `eval/report.json` remain
the live V1 contract until implementation. Per R27/R32, implementation replaces them with
this suite in one versioned run; they must not be partially migrated.
