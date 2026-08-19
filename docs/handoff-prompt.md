# Implementation handoff — HouseAccount House Score

Build the system specified in this repo. You do not need the original assignment; the PRD has absorbed it, including all graded deliverables.

## Sources of truth, in precedence order
1. `eval/v2/golden/*.json` — the 42-fixture V2 golden suite (count authority `eval/report.json`); the acceptance contract. `eval/verify_claims.py` re-derives all of them from the scoring rules — run it before and after any change to scoring code or fixtures.
2. `docs/PRD.md` — numbered requirements R1–R14. Settled conflicts: territory is OURS (R1.1, no external polygon is coming); absentee modifier is −15 and applies to everyone including ≤30-day movers (fixture 10); score is the only surfaced output (no tiers).
3. `docs/HouseAccount-Prototype.html` + `docs/DESIGN-ADDITIONS.md` — approved visual reference (drive it, don't guess); `docs/ui-wireframes.html` and `docs/architecture.excalidraw` for structure.

## Invariant that must never be compromised
No identity data, ever: no owner names, no mailing addresses, no deed-book name mining, no broker or listing-scrape data. Absentee = rental-registration match only; ACS evidence is neighborhood-phrased, never a household claim. Check yourself: grep the pipeline for any use of OWNER_NAME / ST_ADDRESS / CITY_STATE — they must appear only in a redaction-verification test.

## Dependency facts (not yet built — inject as typed seams with fixture-backed fakes, never TODOs)
- Rental registration data may never arrive (R11.3): scoring takes `rental_registration_match: bool` from a provider interface; ship the documented-declination fallback if the OPRA path is empty at deploy time.
- Vision signals arrive per R3.3's JSON schema; the score engine consumes that schema, not raw model output.
- The territory polygon (R1.1) is produced by a bootstrap step (select ~540 class-2 parcels near Ramsey Golf & Country Club) — everything downstream reads `data/territory.geojson`.
- Hand-label sets (~40 pool labels, 20 negatives) are produced during build; the eval harness runs against fixture 09's frozen arithmetic until they exist.

## Definition of done
- `make pipeline` runs harvest→resolve→vision→score→publish from a fresh clone with only documented env vars (`OPENAI_API_KEY`, `CENSUS_API_KEY`; optional `GOOGLE_MAPS_KEY`), zero manual steps (R2.2).
- `make eval` runs: the full V2 golden suite (42 fixtures; count authority `eval/report.json`) green via the real score engine; vision P/R + hallucination + cost-per-door computed from labeled sets; entity-resolution match rate ≥95% reported (R3.2).
- Map UI and MCP server (tools exactly: `get_door_score`, `explain_score`, `plan_route`) deployed and publicly reachable (R8, R9, R12); Data & Ethics page live per R11.4.
- Total API spend ≤ $50 (projected $0–5); caching/batching visible in code (R14, R2.3).
- README documents setup end-to-end; no hardcoded secrets (R13).

## Before dispatching your first sub-agent
Put the run on disk: ticket board, scope, acceptance criteria per ticket, the commands above, and a resume procedure. Long runs lose turns to stalls; durable state makes that free. Do not prescribe more sequencing than the dependency facts above force.

## Unattended-run rules (this run executes overnight, nobody is watching)
- Never pause for approval. Every decision is pre-made in the PRD/fixtures; if you hit one that genuinely isn't, mark the ticket `blocked` with a one-line question in the board file and move to the next ticket — do not wait.
- Per-ticket retry cap: 3 implementation attempts against locked tests, then mark `blocked` with the failing output attached and move on. Never loop indefinitely on one ticket.
- Commit per green ticket (local only, never push). The board file + git log ARE the resume state: keep both accurate at all times, updated before starting each ticket, not after.
- Resume procedure (write this into the board header verbatim): "Read this board + `git log --oneline` + `python3 eval/verify_claims.py`. Trust disk over any prior summary. Continue with the first ticket not marked done/blocked."
- Anything requiring real credentials or a deploy decision (Fly.io/Vercel setup) → build to deploy-ready with documented commands, mark the deploy ticket blocked-on-human. Do not attempt account creation.
