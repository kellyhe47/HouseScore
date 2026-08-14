# TDD run config

**Working branch:** `fix/vision-throughput-and-run-durability`
**Baseline:** `afb4e8d` — 1350 Python + 215 JS green.
**Source of tickets:** manual-QA findings 021–025 (`.qa/report.md`).

## Verified test commands

Each was smoke-tested against a throwaway probe at setup and confirmed to
discover, execute and report it.

| Purpose | Command |
|---|---|
| Python unit | `.venv/bin/python -m pytest` (via `PYTHONPATH=src`, i.e. `make test-py`) |
| Single Python file | `PYTHONPATH=src .venv/bin/python -m pytest <path> -q` |
| JS unit | `npm --prefix web test` (node --test over `web/js/*.test.js`, i.e. `make test-web`) |
| Full suite | `make test` |

Python tests live in `tests/test_*.py`; JS tests in `web/js/*.test.js`.

## Live probe (R12 surfaces)

Port 8000 on this machine is held by an unrelated `ai-red-team` service — use
8100. UI must be served on 5173 specifically, because `UI_ORIGINS` in
`src/houseaccount/server/app.py` allows only that origin.

```
PYTHONPATH=src .venv/bin/python -m uvicorn houseaccount.server.app:create_app --factory --port 8100
sh scripts/vercel-build.sh "http://127.0.0.1:8100/api" "http://127.0.0.1:8100/api"
npx serve web -l 5173
# restore afterwards:
git checkout web && rm -f web/js/config.js
```

## Standing constraints

- **Never run `make pipeline`.** It spends real money against the user's OpenAI
  account. `data/` and `eval/report.json` are current and committed — verify
  against those artifacts.
- Nothing is ever pushed. Local commits only.
- `.tdd/worktrees/` is gitignored.

## Investigated touch areas (corrected from the QA-time guesses)

The ticket files were written by QA, which guessed broadly. Verified at setup:

| Ticket | Real touches | Note |
|---|---|---|
| 021 | `src/houseaccount/route.py` | Talk track is generated in exactly one place — `route.talk_track_for` via `_as_clause` (`_EVIDENCE_LIMIT = 110`). `server/published.py:308` reuses it for the panel, so both surfaces are fixed by one Python change. **No JS work.** |
| 022 | `web/js/map.js` | Camera fitting only. |
| 023 | `web/js/ethics.js`, `src/houseaccount/publish.py` | `signalAvailability()` gives the vision provider `reasonField: null, availableField: null`, so it infers status by pattern-matching degradation strings — which is why a partial loss reads DECLINED. |
| 025 | `web/js/ethics.js` | Hardcoded literal at `ethics.js:651`. |

023 and 025 both edit `ethics.js`, so they are batched into one dispatch pair.
