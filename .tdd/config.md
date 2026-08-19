# TDD run config — V2 run (Door Score V2, 2026-08-19)

**Working branch:** `feature/score` (checked out at invocation; V1 run's branch note is historical).
**Baseline:** `5686c52` — 1476 Python passed (18 skipped) + 273 JS pass, exit 0.
**Source of tickets:** `docs/plans/2026-08-18-2221-feat-door-score-v2-plan.md` (R1–R38) +
`eval/v2/golden/` (42 fixtures) + `eval/v2/provenance.md` (binding envelope conventions).
Handoff-settled conflicts: AE3 = 64.447; R17/R10 share one issue-date fallback rule; SDL is
primary for project lifecycle (R23 "supplemental" language dead); rental −25 ships dormant.

## Verified test commands (re-verified 2026-08-19 via baseline run)

| Purpose | Command |
|---|---|
| Python unit | `PYTHONPATH=src .venv/bin/python -m pytest` (`make test-py`) |
| Single Python file | `PYTHONPATH=src .venv/bin/python -m pytest <path> -q` |
| JS unit | `npm --prefix web test` (`make test-web`) |
| Full suite | `make test` |
| validate-spec | `python3 ~/.claude/skills/product-inception/scripts/validate_golden.py eval/v2/golden && python3 eval/v2/verify_fixtures.py` |
| test-golden (built by ticket 101) | `make test-golden` → pytest over eval/v2 adapter, 42 fixtures |

## Standing constraints

- **Never run the vision stage with a live API key.** `make pipeline` is allowed ONLY when no
  `ANTHROPIC_API_KEY`/`OPENAI_API_KEY` is exported and the content-addressed cache is warm
  (warm run = 0 network calls, $0, ~0.3s). The recalculation run (ticket 103) uses this mode.
- Golden expectations in `eval/v2/golden/` are NEVER edited to make implementation pass.
  An expectation change is a spec change (provenance + manifest + review log) and needs the user.
- Nothing is ever pushed. Local commits only. `.tdd/worktrees/` gitignored.
- Port 8000 busy on this machine; use 8100 for live probes. UI origin must be 5173.

## The invariant (R7/R30)

Capped category subtotals (+ explicit cap-adjustment entries) + mover lift + rental modifier
+ rounding/clamp adjustment = displayed integer, on every surface (panel, API, MCP, route).

## Known dependency facts

- Rental registry data does not exist; −25 path is fixture-only; every real door publishes
  `rental_data_missing` gap.
- SR1A primary for mover dates; MOD-IV fallback. Current run manifest is V1-shaped; R28 defines V2.
- R38 ramp restop consumes the R34 recalculation report (report before map change).
- R35 allowlist start set: dated review logs, `docs/HouseAccount-Prototype.html`,
  `prototype-decoded.html`, `.qa/report.md`.
- V1 `eval/golden/` (13 fixtures), `eval/verify_claims.py`, SQLite `groups`/`raw_total`,
  route.py talk-track angle table: all replaced in the one versioned run (R27/R32), not before —
  V1 path stays green until the ticket 103 cutover.
