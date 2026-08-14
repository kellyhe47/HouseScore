# Design additions beyond the spec

(Authored by the spec reviewer from a full source audit of the prototype, in lieu of the design agent's own log. All were reviewed and ACCEPTED into the PRD — see requirement refs.)

| Addition | What it does | Why the flow needed it | PRD ref |
|---|---|---|---|
| Unscored-door panel state | Clicking one of the 3 unscored parcels explains the exclusion and ties it to the coverage count | Otherwise unscored doors look broken | R9.4 |
| Walk-finished summary | "X doors knocked · Y skipped" end screen | Walk mode needed an end state | R10.4 |
| Resume banner Discard action | Interrupted walk offers Resume or Discard | Resume-only trapped users in stale walks | R10.4 |
| Copy/share toasts | Confirmation feedback on Copy address / Copy as text / Share link | Silent clipboard actions feel dead | R9.5 |
| "→ floored 0" math-line variant | Score-math shows the clamp floor when raw < 0 | Consistency with the cap display | R6 (clamp) |
| About-page "simulate data-fetch error" link | Demo-only trigger for the frame-6 error state | Error state otherwise undemoable | demo-only; mark visually |

## Known-minor deviations from spec (carried to implementation, not blocking)
- Imagery evidence thumbnails are static (frame 2b wanted tap → full-size with detection bbox) — implement per R7.1.
- At 375px the "Data & Ethics" header button clips; prototype's mobile header needs a wrap/collapse — implement responsively.
