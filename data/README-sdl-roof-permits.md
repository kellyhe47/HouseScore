# Ramsey SDL roof-permit collection

This directory contains a point-in-time research collection of publicly
displayed Ramsey Borough construction-permit records returned by SDL Portal's
`roof` keyword search. The collection was matched to all 540 properties in the
HouseAccount territory so researchers can examine the kind of work performed,
permit status, dates, costs, fees, inspections, and other fields shown on the
permit pages.

This is a supplemental research dataset. It is **not** an input to the current
HouseAccount scoring pipeline, which continues to use New Jersey's statewide
Socrata construction-permit dataset.

For the broader successor collection—public property fields plus every permit,
inspection, and violation displayed for each territory parcel—start with
[`README-sdl-property-history.md`](README-sdl-property-history.md). This
roof-keyword snapshot remains the detailed provenance source for 210 permits
and is nested into that coalesced per-house artifact where applicable.

## Snapshot summary

| Item | Value |
|---|---:|
| Collection date | 2026-08-18 |
| Territory properties | 540 |
| SDL search-result records | 2,043 |
| Records matched to territory properties | 210 |
| Territory properties with at least one match | 198 |
| Matched records with explicit roofing language | 207 |
| Properties with explicit roof work | 197 |
| SDL keyword-only results without explicit roofing language | 3 |
| Matched detail pages collected | 210 of 210 |
| Ambiguous address matches | 0 |

The 342 properties without a match should be read as “no exact address match in
this `roof` keyword snapshot,” not “no permit exists” or “no roof work ever
occurred.”

## Source and authorization record

- Source: [SDL Portal — Ramsey Borough permit search](https://www.sdlportal.com/towns/nj/bergen/ramsey/search2?st=permits&limit=100&as=1&pc=&loc=&kw=roof&wt=&stps=all&ug=all&stpd=all&psd=0&ped=0)
- Search type: permits
- Search keyword: `roof`
- Source municipality: Ramsey Borough, Bergen County, New Jersey
- Collection method: manually initiated browser review and saving of public
  search-result and permit-detail pages
- Authorization basis recorded with the data: the user represented on
  2026-08-18 that SDL authorized “Option 1,” manual portal-result collection

The repository records that authorization representation as provenance; it
does not independently verify or extend SDL's authorization. Anyone refreshing,
redistributing, or using the data outside this project should confirm the terms
that apply to that use.

## Files

| Path | Purpose |
|---|---|
| `sdl_roof_permits_raw.json` | Deduplicated municipal search-result records, accepted query batches, source URLs, collection time, authorization basis, and coverage limits. |
| `sdl_roof_permit_details.json` | The 210 matched permit-detail pages, keyed by source URL. Contains the additional fields displayed on each page. |
| `sdl_roof_permits_territory.json` | Primary analysis artifact: all 540 territory properties, matched permits and detail-page data, summary counts, unmatched records, and match policy. |
| `territory.geojson` | The 540-property territory used as the matching reference. |
| `../scripts/match_sdl_permits.py` | Offline, deterministic address matcher and keyword-relevance classifier. It performs no network access. |
| `../tests/test_match_sdl_permits.py` | Tests for address normalization, exact matching, non-expansion of address ranges, detail enrichment, and keyword false positives. |

The raw and detail files preserve source fields and URLs. The territory file is
derived locally from those two inputs plus `territory.geojson`.

## Collection and matching method

1. The public Ramsey SDL permit search was run with the keyword `roof`.
2. Date-filtered result batches were saved in intervals small enough to avoid
   the portal's 100-result display cap. The unrestricted latest-100 batch was
   also retained so undated recent results were not lost.
3. Overlapping rows were deduplicated. Each record's `retrieval_batches` field
   preserves the accepted batch or batches in which it appeared.
4. Search rows were normalized and matched to `PROP_LOC` in
   `territory.geojson` by exact normalized situs address.
5. Detail pages were collected for all 210 records matched to a territory
   property and joined back by their SDL source URL.
6. A conservative text classifier separated records containing an explicit
   whole-word roofing term from rows returned only because SDL found the
   substring `roof` elsewhere.

The matching policy intentionally does not guess:

- ambiguous addresses are not matched;
- address ranges are not expanded to individual homes;
- landmark suffixes are removed only when the preceding text begins with a
  street number; and
- no owner or occupant data is used.

There were no duplicate normalized territory addresses and no ambiguous matches
in this snapshot. A record that did not resolve to exactly one territory situs
address remains in `unmatched_records` with its reason.

## Coverage and known limitations

The accepted date-filtered batches cover issue dates from 2002-01-01 through
2026-12-31. Because the snapshot was collected on 2026-08-18, the end date is a
filter boundary, not a claim that records after the collection time exist. The
unrestricted result batch also preserves recent records with no displayed issue
date.

The portal date picker could not create a bounded pre-2002 search. Pre-2002
queries that still hit the 100-result cap were excluded, so coverage before
2002 is not complete. The source can also be edited after collection; use each
record's SDL URL to verify current values before making an official or
property-specific decision.

SDL's keyword search is substring-based. Three matched records were returned
because their displayed text included phrases such as “child proof,” not
because the work description explicitly identified roofing. They are retained
for auditability and marked:

```json
{
  "keyword_relevance": {
    "classification": "portal_keyword_only"
  }
}
```

The other 207 matched records are marked `explicit_roof_work`. Filtering on
that classification is recommended for roof-specific analysis.

## Primary artifact schema

`sdl_roof_permits_territory.json` is a JSON object with these main sections:

- `source`, `authorization_basis`, `coverage`, and `accepted_batches` describe
  provenance and collection boundaries.
- `match_policy` states the address-resolution rules.
- `summary` contains the counts shown above.
- `properties` contains all 540 properties. Each property has `pams_pin`,
  `block`, `lot`, `address`, `normalized_address`, and a `permits` array.
- `unmatched_records` and `ambiguous_records` retain records that were not
  assigned to a property.
- `duplicate_territory_addresses` records any non-unique normalized territory
  keys; it is empty in this snapshot.

Each matched permit preserves the search-result fields (`control_number`,
`permit_number`, `issue_date`, `location`, `status`, `work_type`, `subcodes`,
`work_description`, `detail_url`, and `retrieval_batches`) and adds:

- `match_method` and `normalized_location`;
- `keyword_relevance`; and
- `detail_page`, including displayed address, block/lot, timeline, description,
  comments, use group, status, plan-review status, subcodes, related permits,
  construction costs, fees, balances, inspections, attachments, contractors,
  and status history where the portal displayed them.

All 210 detail pages include comments; 207 display a construction cost. There
are 247 inspection rows across 118 permits. No contractor entries were displayed
on the collected detail pages, so an empty contractor list means “not displayed
in this snapshot,” not “the work had no contractor.”

## Rebuild and validate

Rebuild the primary artifact without contacting SDL:

```sh
PYTHONPATH=src .venv/bin/python scripts/match_sdl_permits.py \
  --raw data/sdl_roof_permits_raw.json \
  --territory data/territory.geojson \
  --details data/sdl_roof_permit_details.json \
  --output data/sdl_roof_permits_territory.json
```

Run the focused matching tests:

```sh
PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_match_sdl_permits.py
```

Useful integrity checks:

```sh
jq '.summary' data/sdl_roof_permits_territory.json
jq '.properties | length' data/sdl_roof_permits_territory.json
jq '.records | length' data/sdl_roof_permit_details.json
```

## Responsible use and citation

The files contain public property addresses and municipal permit information,
but no owner or occupant names were collected. Do not use permit activity to
infer protected traits, household composition, ownership, occupancy, or an
individual's behavior. Aggregate findings where property-level detail is not
necessary.

A suggested citation is:

> SDL Portal — Ramsey Borough, public permit-search and permit-detail pages,
> keyword “roof”; manually collected 2026-08-18; matched to the HouseAccount
> 540-property Ramsey territory by exact normalized situs address.
