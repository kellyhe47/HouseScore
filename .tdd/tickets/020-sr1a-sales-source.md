---
id: 020
title: "NJ SR1A sales flat-file as a fresh deed source — make the Mover group fire"
status: green
depends_on: [019]
touches:
  - src/houseaccount/sources/sales.py
  - src/houseaccount/sources/parcels.py
  - src/houseaccount/normalize.py
  - src/houseaccount/resolve.py
  - src/houseaccount/pipeline.py
  - src/houseaccount/scoring/engine.py
  - web/js/ethics.js
  - eval/golden/13_sr1a_supersedes_stale_modiv.json
iterations: 1
test_files:
  - tests/test_sales.py
  - tests/test_normalize.py
  - tests/test_parcels.py
  - tests/test_resolve.py
  - tests/test_pipeline.py
  - tests/test_redaction.py
branch: claude/nj-sr1a-sales-source-932565
---

## Decision this ticket implements

Ticket 019 disclosed that the MOD-IV extract's newest deed anywhere in Ramsey is **2024-12-06**,
so zero doors fell in the 90-day mover window and the 100-point Mover group — the heaviest signal
in the model — scored zero on every door. 019 left one question for the human: add NJ SR1A sales
flat-files as a fresher deed source, or ship with the vintage disclosed?

**The human chose: add SR1A.** The stated reason is that new-homeowner recency is the single most
important component of the House Score, so the data feeding it must be fresh.

## Ground truth verified live before any code (2026-08-14)

Checked against the real published files, not assumed:

| Fact | Value |
|---|---|
| `YTDSR1A2026.zip` | HTTP 200, 10,984,774 bytes, **last modified 2026-08-12** — two days before `as_of` |
| Unzipped | one file, `YTDSR1A2026.txt`, 113,006,775 bytes, 169,935 records |
| Record width | **exactly 663 on all 169,935 lines**, matching `SR1Afilelayout.pdf` |
| Ramsey (county `02`, district `48`) records | **240** |
| Ramsey deed-date range | **2025-01-10 → 2026-06-15** (MOD-IV's newest, anywhere: 2024-12-06) |
| Ramsey `DATE-RECORDED` range | 2025-07-01 → 2026-06-30 |
| Deeds within 90d of `as_of` | **11** (10 of them class-2 residential) |
| Deeds within 60d / 30d | **1 / 0** |
| Join to a live parcel | **10 of 11** resolve to a real parcel record |
| In the published 540-door territory | **2** — `0248_3201_22` (41 Ramsey Ave, sold 2026-06-01) and `0248_3509_22.01` (5 Sycamore Ct, sold 2026-05-28) |

MOD-IV's own deed date for those same sold parcels reads 1996, 2002, 2009, 2016, 2019, 2022 — the
*prior* owner's deed. These are real 2026 sales the current pipeline is completely blind to.

`DEED-DATE` is `9(6)` — the same `YYMMDD` shape `normalize.parse_deed_date` already parses and
fixture 11 already pins. No new date handling is needed.

## Two findings from the audit that change the design

**1. Condo units collapse onto one block/lot.** `0248_4001_22` carries **20 different sales** —
224/226/252 Cambridge Dr, 112/116/121 Surrey Ct, 300 Coventry Ct … one parcel key, twenty
addresses. They are separated only by `QUALIFICATION-CODES` (`C0226`, `C0361`, …). The parcel
layer exposes the matching field **`PCLQCODE`**, and its `PAMS_PIN` is `0248_4001_22_C0115`.

  → Joining on block/lot alone would attach one unit's sale to the whole complex and mint a fresh
  mover for 25 doors that did not move. **The join key must include the qualifier.** `PCLQCODE`
  joins the parcel `OUT_FIELDS` allowlist, and sales get their own 4-part key. `parcel_key` is
  left byte-identical for permits — the 0.974 municipal match rate must not move.

**2. The layout reserves grantor/grantee identity columns** — name, street, city-state, zip for
both parties (offsets 110-203 and 204-297). Measured occupancy across all 169,935 records:
**0 populated**. NJ redacts them at publication. That is the current file's behaviour, not a
guarantee about the next one, so the parser reads an **allowlist of non-identity columns** and
those offsets are never sliced — the same discipline `parcels.py` applies to `outFields`. A test
feeds a record with every identity column populated and asserts none of it survives into a `Sale`.

## Scope

- New harvest source `sources/sales.py`: fetch the YTD zip, unzip in memory, stream 663-char
  records, filter to the municipality, slice the non-identity allowlist. Reaches back into the
  prior year's annual file only when the mover window crosses 1 January.
- **Only the distilled projection is cached** — ~240 non-identity rows, not the 113 MB raw file.
  A warm run still makes zero network calls (R2.3), and no identity-bearing byte is ever written
  to `cache/` or `data/`.
- `PCLQCODE` → `Parcel.qualifier`; `sale_key(mun, block, lot, qualifier)` in `normalize`.
- `resolve` merges: the most recent sale by `(deed_date, date_recorded)` wins. SR1A supersedes
  MOD-IV only when strictly newer; the NU code lands in `sales_code`, so the engine's existing
  non-arm's-length rule (`sale_price <= 100 OR sales_code non-empty`) applies unchanged.
- Provenance `SOURCE_SR1A`, so an evidence line cites the file the date actually came from.
- Non-load-bearing: a fetch failure degrades and the run continues on MOD-IV alone.
- `_deed_vintage` measures the merged view; the manifest and the ethics page report the real
  vintage and the residual limit below.

## The residual limit — must stay disclosed, not quietly dropped

SR1A does **not** make the 100-point ≤30-day band reachable. The freshest deed in the file is
2026-06-15, 60 days before `as_of`, and `DATE-RECORDED` stops at 2026-06-30 against a file
published 2026-08-12 — a **~6-week statutory recording-and-publication lag**. So on this run the
Mover group fires in the 61-90 day band (70 pts), one door reaches the 31-60 band (85), and the
top band stays structurally unearnable.

That is a smaller and much more precise disclosure than 019's, and it replaces it rather than
deleting it. The bands themselves do not move: `mover_30d=100 / 60d=85 / 90d=70` is a settled
product decision and this ticket does not relitigate it.

## Acceptance criteria

- [x] `sources/sales.py` parses the real 663-char layout; a wrong-width record is rejected, never
      silently mis-sliced.
- [x] Identity columns are never read. Test: a fully-populated identity record yields a `Sale`
      carrying none of those bytes; `tests/test_redaction.py` stays green.
- [x] Condo correctness: a sale qualified `C0224` lands on `0248_4001_22_C0224` and on no other
      unit. Test pins that one unit's sale does not move its 24 neighbours.
- [x] `parcel_key` output is unchanged for permits; municipal match rate stays ≥ 0.95.
- [x] SR1A supersedes MOD-IV only when strictly newer; NU-coded sales earn 0 mover points via the
      existing rule, with no engine change.
- [x] Only distilled records are cached. No identity byte on disk; warm run makes zero calls.
- [x] Golden fixture 13 pins "fresh SR1A sale supersedes a stale MOD-IV deed → Mover fires".
- [x] Fixtures 01, 02, 03, 10, 11, 12 unchanged and green — the Mover rules do not move.
- [x] `make pipeline` publishes with the Mover group firing on the territory doors that actually
      sold; manifest carries the SR1A vintage and the residual ≤30-day limit.
- [x] Ethics page lists SR1A, its recording lag, and the discard-identity-at-parse guarantee.

## Attempt log

- iter 0: ground truth audited live (table above). Two design changes forced by the data:
  qualifier-aware join, allowlist parser.
- iter 1: **green.** 1,320 Python tests + 215 JS + 13/13 golden fixtures via the real engine.
  Municipal permit match rate unchanged at **0.974** — `parcel_key` was left byte-identical and
  only sales use the new 4-part `sale_key`.

  Live `make pipeline` against the real register (cold 16s, warm 0.34s, $0.00):

  - `deed_vintage.latest_deed_date` moved **2024-12-06 → 2026-06-15**.
  - **18 of 540 doors** had a stale MOD-IV deed superseded by a recorded sale.
  - **2 doors entered the mover window and now score 100** — `0248_3509_22.01` (5 Sycamore Ct,
    sold 2026-05-28, 78 days) and `0248_3201_22` (41 Ramsey Ave, sold 2026-06-01, 74 days). Both
    carry `deed_recency +70` sourced to "NJ SR1A sales register". Territory max score went 77 → 100.
  - The T019 zero-mover disclosure is *replaced*, not deleted, by a narrower and more precise one:
    "no door is inside the 30-day top mover band: the freshest sale in the SR1A register closed
    2026-06-15, 60 days ago, because a deed reaches the published register only after county
    recording and the state's next file release."
  - Cache holds **one 59 KB JSON of 240 distilled Ramsey rows** — no `PK` header anywhere on disk.
    The 113 MB statewide download is never persisted.

  Two things the ground-truth audit caught that the ticket would otherwise have shipped wrong:
  the condominium qualifier (20 sales on one block/lot would have marked 25 doors as movers), and
  the ethics page reporting the mover signal as a plain green "live" — which would have quietly
  dropped the recording-lag limit T019 exists to keep visible.
