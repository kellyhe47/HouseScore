# Ramsey SDL property and construction history

This directory contains a point-in-time research snapshot of the public SDL
property pages for the 540 parcels in the HouseAccount Ramsey territory. The
primary JSON keeps each house together: parcel and assessment fields, sale
fields, map references, every permit application listed on the property page,
inspections, violations, and any public tax-map, attachment, or online-form
metadata displayed by SDL.

This is a supplemental research dataset. It is not an input to the current
HouseAccount score, map, API, or published pipeline artifacts.

## Snapshot summary

| Item | Value |
|---|---:|
| Collection date | 2026-08-18 |
| Territory properties | 540 |
| SDL property pages collected | 532 |
| SDL property pages unavailable | 8 |
| Properties with at least one coalesced permit | 525 |
| Permit rows displayed on property pages | 3,648 |
| Supplemental roof-search permits absent from those tables | 14 |
| Coalesced permit records | 3,662 |
| Inspection rows | 6,168 |
| Violation rows | 91 across 61 properties |
| Roof keyword detail pages attached | 210 |
| Properties with assessed valuation | 532 |
| Properties with a displayed last-sale date | 445 |
| Properties with map coordinates | 532 |
| Public tax maps / attachments / online forms displayed | 0 / 0 / 0 |

The eight unavailable parcels remain in the primary file with
`collection_status: "not_collected"`. Their direct block/lot URL and an
alternate fractional-lot URL both returned SDL's 404 page, and SDL's visible
property search returned no result for the exact territory address. They are
also listed in `collection_errors`; no substitute parcel was guessed.

## Source and authorization record

- Source: [SDL Portal — Ramsey Borough](https://www.sdlportal.com/towns/nj/bergen/ramsey)
- Page type: public property pages and their displayed construction sections
- Source municipality: Ramsey Borough, Bergen County, New Jersey
- Territory reference: `territory.geojson`
- Collection method: browser-visible property-page review in checkpointed
  batches; no direct HTTP or private API access
- Authorization basis recorded with the data: the user represented on
  2026-08-18 that SDL authorized “Option 1,” manual portal-result collection

The repository records that representation as provenance; it does not
independently verify or extend SDL's authorization. Anyone refreshing,
redistributing, or using the snapshot outside this project should confirm the
terms that apply to that use.

## Files

| Path | Purpose |
|---|---|
| `sdl_property_history_territory.json` | Primary artifact. All 540 territory parcels, with public SDL fields and construction history nested under the corresponding house. |
| `sdl_property_pages_raw.json` | Browser collection snapshot for the 532 available pages plus the eight explicit collection errors. |
| `sdl_roof_permits_territory.json` | Earlier `roof` keyword snapshot used to enrich matching permits and preserve 14 roof records omitted from SDL property-page tables. |
| `sdl_roof_permits_raw.json` | Deduplicated raw `roof` search rows retained for provenance. |
| `sdl_roof_permit_details.json` | The 210 collected roof permit-detail pages, keyed by SDL URL. |
| `territory.geojson` | The 540-parcel territory and authoritative PAMS parcel IDs. |
| `../scripts/build_sdl_property_history.py` | Offline deterministic coalescer and privacy guard; performs no network access. |
| `../scripts/match_sdl_permits.py` | Offline exact-address matcher and roof-keyword relevance classifier used to build the roof sidecar. |
| `../tests/test_build_sdl_property_history.py` | Tests for territory completeness, per-house grouping, privacy exclusions, roof enrichment, and supplemental permits. |
| `../tests/test_match_sdl_permits.py` | Tests for roof-search address matching, range handling, detail enrichment, and keyword false positives. |

## Roof keyword snapshot provenance

The coalesced dataset includes an earlier, separately collected SDL permit
search for the keyword `roof`. Its exact source was the
[Ramsey permit search](https://www.sdlportal.com/towns/nj/bergen/ramsey/search2?st=permits&limit=100&as=1&pc=&loc=&kw=roof&wt=&stps=all&ug=all&stpd=all&psd=0&ped=0).
That snapshot is retained because it supplies richer detail-page fields and
shows where the property-page permit tables were incomplete.

| Roof snapshot item | Value |
|---|---:|
| Search-result records | 2,043 |
| Records matched to territory properties | 210 |
| Territory properties with at least one match | 198 |
| Matched records with explicit roofing language | 207 |
| Properties with explicit roof work | 197 |
| SDL keyword-only results without explicit roofing language | 3 |
| Matched detail pages collected | 210 of 210 |
| Ambiguous address matches | 0 |

The 342 properties without a match mean only that no exact address match was
present in this keyword snapshot. They do not establish that a property has no
permit or no historic roof work.

The roof search was collected and reconciled as follows:

1. Date-filtered result batches were saved in intervals small enough to avoid
   SDL's 100-result display cap. The unrestricted latest-100 batch was also
   retained so recent records without a displayed issue date were not lost.
2. Overlapping rows were deduplicated while preserving the accepted source
   batches in each record's `retrieval_batches` field.
3. Rows were normalized and matched to `territory.geojson` by exact normalized
   situs address. Ambiguous addresses were not matched, address ranges were not
   expanded, landmark suffixes were removed only when the preceding text began
   with a street number, and no owner or occupant data was used.
4. Detail pages were collected for all 210 territory matches and joined by SDL
   detail URL.
5. A conservative whole-word classifier separated explicit roofing work from
   rows SDL returned only because `roof` appeared as a substring elsewhere.

The accepted date-filtered batches cover displayed issue dates from 2002-01-01
through 2026-12-31. The snapshot was collected on 2026-08-18, so the end date is
a query boundary rather than a claim about future records. SDL's date picker
could not produce a bounded pre-2002 search, and pre-2002 queries that still
hit the 100-result cap were excluded. Coverage before 2002 is therefore not
complete.

Three matched results contained phrases such as “child proof” rather than an
explicit roofing description. They remain for auditability with
`keyword_relevance.classification: "portal_keyword_only"`; the other 207 are
classified as `explicit_roof_work`. Use that classification when conducting a
roof-specific analysis.

The roof sidecar's `detail_page` may include the displayed timeline, comments,
use group, status and plan-review history, subcodes, related permits,
construction costs, fees and balances, inspections, attachments, contractors,
and status history. Empty arrays mean that SDL displayed no entry in this
snapshot, not that the underlying event or contractor did not exist. All 210
detail pages displayed comments, 207 displayed a construction cost, and 247
inspection rows appeared across 118 permits. No contractor entries were
displayed.

The property-history artifact remains the primary dataset. As an intermediate
matching audit artifact, `sdl_roof_permits_territory.json` has a `properties[]`
entry for each territory parcel with `pams_pin`, `block`, `lot`, `address`,
`normalized_address`, and `permits`. Matched permits preserve
`control_number`, `permit_number`, `issue_date`, `location`, `status`,
`work_type`, `subcodes`, `work_description`, `detail_url`, and
`retrieval_batches`; matching adds `match_method`, `normalized_location`,
`keyword_relevance`, and `detail_page`.

For audits that need the intermediate matching record,
`sdl_roof_permits_territory.json` also preserves `coverage`,
`accepted_batches`, `match_policy`, summary counts, and the unmatched,
ambiguous, and duplicate-territory-address collections. There were no duplicate
normalized territory addresses and no ambiguous matches in this snapshot. Any
search row with no territory match remains in `unmatched_records`; a row whose
normalized address identifies multiple territory parcels remains in
`ambiguous_records`. Each retained row includes its matching reason.

## Primary schema

`sdl_property_history_territory.json` is a JSON object with these sections:

- `generated_at` is copied from the raw snapshot's stable `collected_at`
  timestamp, so rebuilding identical local inputs produces identical output.
- `source`, `authorization_basis`, `collection`, and `privacy` describe
  provenance and collection boundaries.
- `summary` contains the counts shown above.
- `properties` contains exactly 540 objects in territory order.
- `collection_errors` records the eight unresolved SDL pages and every
  resolution attempt.

Each property object contains:

- territory identity: `pams_pin`, `block`, `lot`, `address`, and
  `normalized_address`;
- `collection_status` and `source_page` provenance;
- `location`, `property_details`, `geoareas`, and `assessed_valuation`;
- `map`, including the SDL map URL, FEMA map URL, and displayed coordinates;
- `property_data.tax_maps`, `attachments`, and `online_forms`; and
- `construction.permit_applications`, `inspections`, and `violations`.

Permit applications preserve displayed control/permit numbers, issue and close
dates, work type and description, subcodes, status, certificates, cost, and
detail URL. When a permit was also in the earlier `roof` search, its existing
detail snapshot is nested under `roof_keyword_enrichment`.

Fourteen earlier roof records did not appear in the corresponding property-page
permit table, including four on unavailable property pages. They are retained
in the same house's permit array with
`source_scope: "roof_keyword_search_supplement"`. Thus all 210 existing roof
detail pages remain reachable from the coalesced property artifact without
pretending they came from the property table.

## Privacy choices

The collection deliberately excludes:

- the entire SDL owner section, including name and mailing address;
- the permit-agent column, even when SDL masks it;
- Google property-image URLs, which embed a browser API key; and
- the contents of linked tax maps, attachments, or online forms.

Public municipal staff fields displayed in construction records—such as
inspector and issuing-officer names—are retained because they describe the
inspection or enforcement record, not the resident. No owner or occupant data
is used for matching or scoring.

## Coverage and limitations

This is a page snapshot, not a certified municipal record. SDL itself warns
that original source documents may differ and recommends verification. Pages
and values can change after collection.

The property-page permit tables are not guaranteed to be exhaustive. The
comparison with the earlier roof snapshot found 14 permit-detail records absent
from those tables; their explicit supplemental provenance is why they remain in
the coalesced file. Blank work descriptions and dates mean “not displayed on
this page,” not “no work” or “no date.” An unavailable page does not mean the
property has no permits.

Tax-map, attachment, and online-form sections were inspected as metadata only.
No rows were displayed for the 532 collected pages, and no linked document was
downloaded.

## Rebuild and validate

Rebuild the primary artifact entirely from local files:

```sh
PYTHONPATH=. .venv/bin/python scripts/build_sdl_property_history.py \
  --raw data/sdl_property_pages_raw.json \
  --territory data/territory.geojson \
  --roof-territory data/sdl_roof_permits_territory.json \
  --output data/sdl_property_history_territory.json
```

If the roof matching inputs change, rebuild that sidecar first:

```sh
PYTHONPATH=src .venv/bin/python scripts/match_sdl_permits.py \
  --raw data/sdl_roof_permits_raw.json \
  --territory data/territory.geojson \
  --details data/sdl_roof_permit_details.json \
  --output data/sdl_roof_permits_territory.json
```

Run the focused tests and useful integrity checks:

```sh
PYTHONPATH=. .venv/bin/pytest -q \
  tests/test_build_sdl_property_history.py \
  tests/test_match_sdl_permits.py
jq '.summary' data/sdl_property_history_territory.json
jq '.properties | length' data/sdl_property_history_territory.json
jq '[.. | objects | select(has("owner") or has("agent"))] | length' \
  data/sdl_property_history_territory.json
jq '.summary' data/sdl_roof_permits_territory.json
jq '.records | length' data/sdl_roof_permit_details.json
```

The expected results are 540 properties, 210 roof enrichments, and zero owner
or agent objects.

## Responsible use and citation

The files contain public situs addresses and municipal property/construction
records. Do not use them to infer protected traits, household composition,
ownership, occupancy, or an individual's behavior. Aggregate findings where
property-level detail is unnecessary, and verify current values against the
source documents before an official or property-specific decision.

A suggested citation is:

> SDL Portal — Ramsey Borough, public property and construction-history pages;
> plus public permit-search and detail pages for keyword “roof”; browser
> snapshots collected 2026-08-18 for the HouseAccount 540-parcel Ramsey
> territory; exact normalized situs-address matching; owner and mailing fields
> excluded; eight SDL property pages unavailable.
