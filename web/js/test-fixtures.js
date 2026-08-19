// Test-only fixtures. NOT app code — nothing in web/js/*.js (other than *.test.js)
// should import this. Shapes mirror the V2 data/doors.geojson feature `properties`
// exactly (R11.1/R27/R30):
//   { PAMS_PIN, score, confidence, evidence[], situs, exclusion_reason,
//     score_contract_version, categories{project,capacity,fit}, base,
//     mover{eligible,days_since_move,strength}, mover_lift, rental_modifier,
//     adjustment, data_gaps[] }
// and each evidence item:
//   { type, points, reason, imagery }
// Arithmetic in every scored fixture reconciles per R7:
//   sum(evidence points) == base + mover_lift + rental_modifier
//   base + mover_lift + rental_modifier + adjustment == score
// Every export is a FACTORY so tests never share mutable state.

const NO_MOVER = () => ({ eligible: false, days_since_move: null, strength: 0.0 });

/**
 * A normal-confidence scored door with a capacity cap adjustment.
 *
 * Capacity signals total 28 against the 25 cap, so the trail carries an
 * explicit −3 cap-adjustment entry (R7): 15 + 15 + 8 + 5 − 3 + 5 + 0 = 45.
 */
export function scoredDoor(overrides = {}) {
  return {
    PAMS_PIN: '0248_1503_7',
    score: 45,
    confidence: 'normal',
    situs: '128 SNYDER AVE, Ramsey NJ 07446',
    exclusion_reason: null,
    score_contract_version: 'v2',
    categories: { project: 15, capacity: 25, fit: 5 },
    base: 45,
    mover: NO_MOVER(),
    mover_lift: 0.0,
    rental_modifier: 0,
    adjustment: 0.0,
    data_gaps: [{ type: 'imagery_missing' }, { type: 'rental_data_missing' }],
    evidence: [
      {
        type: 'project_active',
        points: 15,
        reason: 'active qualifying project with recent lifecycle activity',
        imagery: null,
      },
      {
        type: 'capacity_territory_percentile',
        points: 15,
        reason: 'assessed value ranks high among territory single-family properties',
        imagery: null,
      },
      {
        type: 'capacity_local_relative_value',
        points: 8,
        reason: 'assessed value above the median of the nearest comparables',
        imagery: null,
      },
      {
        type: 'capacity_acs_dual_income_prior',
        points: 5,
        reason:
          'neighborhood-level dual-income prior: the ACS block group reports a '
          + 'dual-income share at or above the 35% threshold',
        imagery: null,
      },
      {
        type: 'capacity_cap_adjustment',
        points: -3,
        reason: 'capacity signals total 28; subtotal capped at 25',
        imagery: null,
      },
      {
        type: 'fit_lot',
        points: 5,
        reason: 'lot of at least half an acre',
        imagery: null,
      },
      {
        type: 'mover_invalid_sale',
        points: 0,
        reason:
          'a transfer was disqualified from mover influence (nominal price, '
          + 'disqualifying code, or invalid/future date)',
        imagery: null,
      },
    ],
    ...overrides,
  };
}

/** A scored door carrying vision-derived evidence with an imagery attachment. */
export function visionDoor(overrides = {}) {
  return {
    PAMS_PIN: '0248_3502_8.01',
    score: 28,
    confidence: 'normal',
    situs: '27 FAWN HILL RD, Ramsey NJ 07446',
    exclusion_reason: null,
    score_contract_version: 'v2',
    categories: { project: 8, capacity: 0, fit: 20 },
    base: 28,
    mover: NO_MOVER(),
    mover_lift: 0.0,
    rental_modifier: 0,
    adjustment: 0.0,
    data_gaps: [{ type: 'acs_missing' }],
    evidence: [
      {
        type: 'fit_pool',
        points: 8,
        reason: 'in-ground pool visible in aerial imagery',
        imagery: {
          image_url: 'https://example.invalid/tiles/0248_3502_8.01_2020.jpg',
          bbox: [-74.14, 41.05, -74.139, 41.051],
          model_confidence: 0.88,
          capture_date: '2020-04-11',
        },
      },
      {
        type: 'fit_roof_age',
        points: 12,
        reason: 'latest explicit completed roof installation is old enough to need service',
        imagery: null,
      },
      {
        type: 'project_completed',
        points: 8,
        reason: 'qualifying project completed within 24 months',
        imagery: null,
      },
    ],
    ...overrides,
  };
}

/** The exclusion case: no score, county record incomplete. */
export function unscoredDoor(overrides = {}) {
  return {
    PAMS_PIN: '0248_4101_3',
    score: null,
    confidence: null,
    situs: '9 CRESCENT DR, Ramsey NJ 07446',
    exclusion_reason: 'parcel record incomplete',
    evidence: [],
    score_contract_version: 'v2',
    categories: null,
    base: null,
    mover: null,
    mover_lift: null,
    rental_modifier: null,
    adjustment: null,
    data_gaps: null,
    ...overrides,
  };
}

/** A scored door with an empty evidence list. */
export function noEvidenceDoor(overrides = {}) {
  return {
    PAMS_PIN: '0248_2201_1',
    score: 0,
    confidence: 'low',
    situs: '412 MAIN ST, Ramsey NJ 07446',
    exclusion_reason: null,
    score_contract_version: 'v2',
    categories: { project: 0, capacity: 0, fit: 0 },
    base: 0,
    mover: NO_MOVER(),
    mover_lift: 0.0,
    rental_modifier: 0,
    adjustment: 0.0,
    data_gaps: [
      { type: 'acs_missing' },
      { type: 'imagery_missing' },
      { type: 'rental_data_missing' },
      { type: 'sdl_page_unavailable' },
    ],
    evidence: [],
    ...overrides,
  };
}

/** Minimal door for filter tests — only the fields filterDoors reads. */
export function doorWithScore(score, pin = 'pin_' + String(score)) {
  return {
    PAMS_PIN: pin,
    score,
    confidence: score === null ? null : 'normal',
    situs: '1 TEST RD, Ramsey NJ 07446',
    exclusion_reason: score === null ? 'parcel record incomplete' : null,
    score_contract_version: 'v2',
    evidence: [],
  };
}

// --- T013 / T106 ----------------------------------------------------------------
// The route/walk surfaces read two more shapes: the `GET /api/door/{pin}` body —
// the V2 published properties plus exactly three detail fields (`talk_track`,
// `talk_track_branches`, `reason_chip`) — and the `POST /api/route` payload,
// whose stops are `houseaccount.route.Stop` verbatim.

/**
 * A door as `GET /api/door/{pin}` serves it under V2: a fresh mover on the
 * blend, arithmetic mirroring tests/test_server.py's OAK door.
 *
 * Evidence sums to base + mover_lift + rental_modifier:
 *   15 + 7 + 5 + 12 + 55.875 = 94.875 = 39 + 55.875 + 0
 * and 39 + 55.875 + 0 + 0.125 = 95, the displayed integer.
 */
export function detailedDoor(overrides = {}) {
  return {
    ...scoredDoor(),
    categories: { project: 15, capacity: 12, fit: 12 },
    base: 39,
    mover: { eligible: true, days_since_move: 12, strength: 1.0 },
    mover_lift: 55.875,
    rental_modifier: 0,
    adjustment: 0.125,
    data_gaps: [],
    evidence: [
      {
        type: 'project_active',
        points: 15,
        reason: 'active qualifying project with recent lifecycle activity',
        imagery: null,
      },
      {
        type: 'capacity_local_relative_value',
        points: 7,
        reason: 'assessed value above the median of the nearest comparables',
        imagery: null,
      },
      {
        type: 'capacity_acs_dual_income_prior',
        points: 5,
        reason:
          'neighborhood-level dual-income prior: the ACS block group reports a '
          + 'dual-income share at or above the 35% threshold',
        imagery: null,
      },
      {
        type: 'fit_roof_age',
        points: 12,
        reason: 'latest explicit completed roof installation is old enough to need service',
        imagery: null,
      },
      {
        type: 'mover_recency',
        points: 55.875,
        reason: 'recent valid arm\'s-length move blends the score toward the mover priority band',
        imagery: null,
      },
    ],
    score: 95,
    reason_chip: 'mover_recency',
    // Assessed value is the top evidence here, and it is one of the signals a
    // rep can never say out loud — so the opener falls through to the angle
    // that mentions nothing about the house. The evidence trail above still
    // shows the number; the doorstep never hears it.
    talk_track:
      "Hey, I'm with HouseAccount — we're doing work for a few of your neighbors " +
      'here on Snyder Ave this week. Are you the one who deals with the house stuff, ' +
      'or is that somebody else?',
    talk_track_branches: [
      {
        trigger: '"That\'s me"',
        line:
          "Then you're the one I should be bugging, sorry. HouseAccount's one number " +
          'for the whole house — mounting a TV up to fixing the roof. Want me to leave a card?',
      },
      { trigger: '"My partner"', line: 'Fair enough — want me to leave a card for them?' },
      {
        trigger: '"I rent"',
        line:
          'Ah, got it. We do work for landlords too. Want to pass the card along, or is ' +
          'there a better number for the owner?',
      },
    ],
    ...overrides,
  };
}

/**
 * The clamp case as `GET /api/door/{pin}` serves it: a demoted rental floored
 * at 0, arithmetic mirroring tests/test_server.py's CEDAR door.
 *
 *   evidence 8 + 5 − 25 + 0 = −12 = 13 + 0 − 25
 *   13 + 0 − 25 + 12 = 0, the clamp surfacing as `adjustment: 12`.
 */
export function clampedDoor(overrides = {}) {
  return {
    ...scoredDoor(),
    PAMS_PIN: '0248_01101_00031',
    situs: '31 CEDAR CT, Ramsey NJ 07446',
    confidence: 'low',
    categories: { project: 8, capacity: 0, fit: 5 },
    base: 13,
    mover: NO_MOVER(),
    mover_lift: 0.0,
    rental_modifier: -25,
    adjustment: 12.0,
    data_gaps: [{ type: 'acs_missing' }, { type: 'assessed_value_missing' }],
    evidence: [
      {
        type: 'project_completed',
        points: 8,
        reason: 'qualifying project completed within 24 months',
        imagery: null,
      },
      { type: 'fit_lot', points: 5, reason: 'lot of at least half an acre', imagery: null },
      {
        type: 'rental_registration',
        points: -25,
        reason: 'current verified rental registration demotes the door',
        imagery: null,
      },
      {
        type: 'mover_invalid_sale',
        points: 0,
        reason:
          'a transfer was disqualified from mover influence (nominal price, '
          + 'disqualifying code, or invalid/future date)',
        imagery: null,
      },
    ],
    score: 0,
    reason_chip: 'project_completed',
    talk_track:
      "Hey, I'm with HouseAccount — we're doing work for a few of your neighbors "
      + 'here on Cedar Ct this week. Who handles the house stuff here?',
    talk_track_branches: [],
    ...overrides,
  };
}

/**
 * One stop exactly as the server serializes `houseaccount.route.Stop`.
 *
 * `path` is the leg the planner measured — `[lon, lat]` from wherever the rep
 * was to this door, along the streets. It is the map's route line: a browser
 * joining centroids would draw a walk through the middle of a block and
 * disagree with the minutes the server put beside it (R10.3).
 */
export function stop(overrides = {}) {
  return {
    pams_pin: '0248_01101_00012',
    address: '12 OAK ST, Ramsey NJ 07446',
    score: 100,
    reason_chip: 'mover_recency',
    walk_minutes: 0.0,
    cumulative_minutes: 0.0,
    talk_track:
      "Hey, I'm with HouseAccount — we're doing work for a few of your neighbors here " +
      'on Oak St this week. Who do you usually call when something on the house needs doing?',
    talk_track_branches: [
      {
        trigger: 'Names one person',
        line:
          "Oh, is he good? That's the thing though — most people have someone for one " +
          "thing and then they're googling for everything else. HouseAccount's one number " +
          "for all of it, mounting a TV up to fixing the roof. Want me to leave a card for " +
          "the stuff he doesn't do?",
      },
      {
        trigger: '"Depends what it is"',
        line:
          "Right, that's the annoying part. HouseAccount's one number for all of it — a " +
          'TV mount up to roofing. Want me to leave a card?',
      },
      {
        trigger: '"I do it myself"',
        line:
          "Respect. We're one number for the ones that aren't worth your Saturday — " +
          'furnaces, roofs, that end of it. Want me to leave a card?',
      },
    ],
    path: [
      [-74.156, 41.0447],
      [-74.156, 41.0447],
    ],
    ...overrides,
  };
}

/**
 * A three-stop `POST /api/route` body.
 *
 * The scores deliberately do NOT descend: the planner orders on score per
 * walking minute, and anything that re-sorts this list in the browser has
 * re-implemented the planner (R10.3).
 *
 * Every leg starts where the one before it ended, and the middle vertices are
 * corners rather than a straight run — the shape a walk along streets has.
 */
export function routePayload(overrides = {}) {
  return {
    stops: [
      stop(),
      stop({
        pams_pin: '0248_01101_00020',
        address: '20 MAPLE AVE, Ramsey NJ 07446',
        score: 58,
        reason_chip: 'fit_lot',
        walk_minutes: 4.4,
        cumulative_minutes: 4.4,
        talk_track:
          "Hey, I'm with HouseAccount — we're doing work for a few of your neighbors " +
          'here on Maple Ave this week. Have you been here long?',
        talk_track_branches: [
          {
            trigger: 'Just moved in',
            line:
              "Oh nice, congrats. HouseAccount's basically one number for the whole house " +
              '— we handle everything from mounting a TV to fixing the roof. First year in ' +
              'a place, most people are still working out who to call for what. Want me to ' +
              'leave a card?',
          },
          {
            trigger: 'A couple of years',
            line:
              "Oh okay. HouseAccount's one number for the whole house — a running toilet " +
              'up to a furnace, same call. Want me to leave a card?',
          },
          {
            trigger: 'A long time',
            line:
              "Wow, okay — so you've seen the whole street change. HouseAccount's one " +
              'number for the whole house, a running toilet up to a furnace. Mostly we end ' +
              'up doing the stuff people have been meaning to get to. Want me to leave a card?',
          },
        ],
        path: [
          [-74.156, 41.0447],
          [-74.1558, 41.0448],
          [-74.1551, 41.0452],
          [-74.155, 41.0454],
        ],
      }),
      stop({
        pams_pin: '0248_3502_8.01',
        address: '27 FAWN HILL RD, Ramsey NJ 07446',
        score: 81,
        reason_chip: 'fit_pool',
        walk_minutes: 37.9,
        cumulative_minutes: 42.3,
        talk_track:
          "Hey, I'm with HouseAccount — we're doing work for a few of your neighbors " +
          'here on Fawn Hill Rd this week. Do you have a pool or anything out back?',
        talk_track_branches: [
          {
            trigger: 'Yes',
            line:
              "Oh nice. Who's opening it for you? HouseAccount's one number for the whole " +
              'house — pool openings and filter swaps right up through roofing. Most people ' +
              'are paying three separate people for that. Want me to leave a card?',
          },
          {
            trigger: 'No',
            line:
              "No worries. HouseAccount's one number for the whole house anyway — mounting " +
              'a TV up to fixing the roof. Want me to leave a card?',
          },
        ],
        path: [
          [-74.155, 41.0454],
          [-74.1548, 41.0455],
          [-74.1539, 41.0451],
        ],
      }),
    ],
    total_minutes: 42.3,
    estimate_disclosure:
      'Walking times follow the streets between the parcels, at 3 mph — '
      + 'estimated from parcel geometry, not turn-by-turn directions.',
    // R27/R30: every route payload names the contract its scores came from,
    // and the server's aggregate is arithmetic over the displayed scores.
    score_contract_version: 'v2',
    average_score: (100 + 58 + 81) / 3,
    ...overrides,
  };
}

/**
 * What `POST /api/route` answers when the request declared a stale score
 * contract (R27/R30): a refresh signal and no stops — never a mixed-version
 * route.
 */
export function refreshPayload(overrides = {}) {
  return {
    stops: [],
    total_minutes: 0,
    estimate_disclosure: '',
    score_contract_version: 'v2',
    refresh_required: true,
    ...overrides,
  };
}

/**
 * The same three stops after `requestRoute` has mapped them — the row shape
 * walk mode consumes, so `walk.js` can be tested without a fetch.
 */
export function routeRows() {
  return routePayload().stops.map((item, index) => ({
    n: index + 1,
    pin: item.pams_pin,
    address: item.address,
    score: item.score,
    walkMinutes: item.walk_minutes,
    cumulativeMinutes: item.cumulative_minutes,
    elapsedLabel: `+${Math.round(item.cumulative_minutes)} min`,
    talkTrack: item.talk_track,
    reasonChip: item.reason_chip,
    path: item.path,
  }));
}

/**
 * A `fetch` stand-in. Records every call and answers with `payload`.
 *
 * `node --test` has a real `fetch` but no server to point it at, so every module
 * that talks to the API takes its `fetch` as an injected dependency and the
 * tests hand it this.
 */
export function fakeFetch(payload, { status = 200, ok = true } = {}) {
  const calls = [];
  const impl = async (url, init = {}) => {
    calls.push({ url, init, body: init.body ? JSON.parse(init.body) : null });
    return {
      ok,
      status,
      json: async () => (typeof payload === 'function' ? payload(calls.length) : payload),
    };
  };
  impl.calls = calls;
  return impl;
}

/**
 * A `localStorage` stand-in: the same three methods, backed by a plain object.
 *
 * `node --test` has no `localStorage`, so `walk.js` takes its storage as an
 * injected `{getItem, setItem, removeItem}` and the tests hand it this.
 */
export function memoryStorage(initial = {}) {
  const data = { ...initial };
  return {
    data,
    getItem: (key) => (key in data ? data[key] : null),
    setItem: (key, value) => {
      data[key] = String(value);
    },
    removeItem: (key) => {
      delete data[key];
    },
  };
}

// --- T014 ---------------------------------------------------------------------
// The Data & Ethics page reads two published artifacts. Neither exists in a fresh
// clone, so the tests build them here in the real shape rather than reading disk.

/** `eval/report.json` as the V2 R34 recalculation run writes it today. */
export function evalReport(overrides = {}) {
  return {
    score_contract_version: 'v2',
    as_of: '2026-08-15',
    // Counts are measurements: the page must read them from here, never spell
    // them into prose (regression of V1 ticket 025).
    golden: { fixtures_total: 42 },
    coverage: { doors_total: 540, doors_scored: 540, coverage: 1.0 },
    categories: {
      project: { min: 0.0, max: 0.0, mean: 0.0 },
      capacity: { min: 0.0, max: 20.0, mean: 5.590741 },
      fit: { min: 0.0, max: 13.0, mean: 6.298148 },
    },
    // The distribution the map ramp is restopped against (R38): the ramp's
    // stops are derived from these quantiles, never hand-picked.
    score_distribution: {
      min: 2.0,
      max: 92.0,
      mean: 12.383333,
      quantiles: { q10: 5, q20: 5, q30: 8, q40: 8, q50: 11, q60: 13, q70: 15, q80: 19, q90: 22 },
    },
    missing_signals: { acs_missing: 540, imagery_missing: 540, rental_data_missing: 540 },
    vision: {
      precision: 0.818182,
      recall: 0.9,
      hallucination_rate: 0.05,
      cost_per_door: 0.0,
      // The honest caveat: no hand labels exist yet, so the vision metrics come
      // from a frozen fixture. This string has to reach the reader.
      metrics_source: 'frozen fixture 09 (hand labels not yet collected)',
      banner:
        "NOT A MEASUREMENT: precision/recall/hallucination are frozen fixture "
        + "arithmetic over fixture 09's confusion set, not scores over hand-labeled "
        + 'imagery. Hand labels have not been collected yet.',
    },
    exclusions: {
      stale_open_neutralized: 115,
      non_arms_length_sales: 78,
      voided_or_admin: 0,
      cross_source_dedup_collapsed: 0,
    },
    pii_scan: { ok: true, findings: [] },
    source_freshness: {
      parcel: '2026-08-19',
      permits: '2026-08-19',
      acs: '2026-08-19',
      sales: '2026-08-19',
    },
    ...overrides,
  };
}

/** `data/run_manifest.json` as the published run writes it today. */
export function runManifest(overrides = {}) {
  return {
    run_at: '2026-08-14T08:38:26.522178+00:00',
    as_of: '2026-08-14',
    score_contract_version: 'v2',
    code_version: '125f61e',
    territory_median_value: 743350.0,
    acs_dual_income_threshold: 0.35,
    retrieved: { parcel: '2026-08-14', permits: '2026-08-14', acs: '2026-08-14' },
    cost_usd: 3.2,
    cost_per_door: 0.006,
    doors_total: 540,
    doors_scored: 540,
    doors_unscored: 0,
    coverage: 1.0,
    // T019: measured from the harvest, never hardcoded. The live Ramsey extract's
    // newest deed is ~20 months before `as_of`, so no door is inside the mover
    // window and the run says so rather than leaving a reader to guess whether
    // the rule is broken.
    deed_vintage: {
      latest_deed_date: '2024-12-06',
      mover_window_days: 90,
      doors_in_mover_window: 0,
    },
    degradations: [
      'CENSUS_API_KEY is not set; the ACS API refuses keyless callers, so '
      + 'block-group statistics were not fetched',
      'municipal rental registration not obtained via OPRA',
      'OPENAI_API_KEY is not set, so the vision stage was skipped. Pool, solar '
      + 'and exterior-condition signals are unavailable; every door still scores, '
      + 'but without the R4 imagery terms.',
      'no door in this territory has a deed inside the mover decay window — the '
      + 'newest deed in the extract is dated 2024-12-06, more than 365 days before '
      + 'as_of — so no door carries a mover lift on this extract vintage',
    ],
    resolve: {
      doors_total: 540,
      doors_with_signal: 115,
      coverage: 0.21296296296296297,
      permits_total: 1741,
      permits_in_territory: 232,
      permits_matched: 144,
      permits_unmatched: 88,
      permits_in_window: 1741,
      permits_matched_municipal: 1696,
      permit_match_rate: 0.6206896551724138,
      municipal_match_rate: 0.974152785755313,
      block_lot_match_rate: 0.6206896551724138,
      address_match_rate: 0.0,
      doors_with_block_group: 0,
      acs_available: false,
      acs_reason:
        'CENSUS_API_KEY is not set; the ACS API refuses keyless callers, so '
        + 'block-group statistics were not fetched',
      rental_declination_reason: 'municipal rental registration not obtained via OPRA',
    },
    ...overrides,
  };
}

/** The same run with every provider live — the other half of every degraded path. */
export function healthyManifest(overrides = {}) {
  const manifest = runManifest();
  return {
    ...manifest,
    degradations: [],
    // The other half of the T019 path: an extract fresh enough for the Mover
    // group to fire, so nothing is disclaimed.
    deed_vintage: {
      latest_deed_date: '2026-07-30',
      mover_window_days: 90,
      doors_in_mover_window: 12,
    },
    resolve: {
      ...manifest.resolve,
      doors_with_block_group: 512,
      acs_available: true,
      acs_reason: null,
      rental_declination_reason: null,
      rental_registration_available: true,
      rental_registration_matched: 37,
    },
    ...overrides,
  };
}

// --- T022 ---------------------------------------------------------------------
// `map.js` is the one module that is not DOM-free: it reads elements, talks to
// MapLibre and boots itself on import. The camera bug in ticket 022 lives there
// and nowhere else, so it needs a stand-in for the browser rather than a pure
// unit under test.
//
// This is the same injected-double idea as `fakeFetch` and `memoryStorage`, one
// level up: instead of handing a module its dependency, the harness installs the
// globals `map.js` reaches for (`document`, `window`, `maplibregl`, `fetch`,
// `localStorage`, `requestAnimationFrame`, `ResizeObserver`) and then records
// what the module did to the camera.
//
// Nothing here models MapLibre's rendering. It models exactly two things the
// bug turns on: **when the map container becomes measurable**, and **when the
// style's `load` event fires**.

/* ── Doors with geometry ─────────────────────────────────────────────────── */

/**
 * A square parcel centred on `[lng, lat]`.
 *
 * Square and axis-aligned on purpose: the mean of the four distinct ring
 * vertices is the centre exactly, so a test can state the expected bounds from
 * the centres it passed in without re-implementing the centroid arithmetic
 * `map.js` uses.
 */
export function parcelFeature(properties, [lng, lat], half = 0.0004) {
  return {
    type: 'Feature',
    properties,
    geometry: {
      type: 'Polygon',
      coordinates: [
        [
          [lng - half, lat - half],
          [lng + half, lat - half],
          [lng + half, lat + half],
          [lng - half, lat + half],
          [lng - half, lat - half],
        ],
      ],
    },
  };
}

/* ── A street, built to size ─────────────────────────────────────────────── */

/** Ramsey's latitude, and what a metre is worth in degrees there. */
const FIXTURE_LAT = 41.05;
const DEG_PER_M_LAT = 1 / 110540;
const DEG_PER_M_LNG = 1 / (111320 * Math.cos((FIXTURE_LAT * Math.PI) / 180));

/**
 * A street: two rows of lots facing each other across a roadway.
 *
 * Built in metres and converted, because every rule `streets.js` applies is in
 * metres — how wide a road can be, how far apart two labels of the same street
 * have to be — and a fixture written in degrees would state its case in units
 * neither the module nor a reader thinks in.
 *
 * The defaults describe a plain residential block running due east, which is
 * what makes the expected answer statable: the name belongs in the roadway, and
 * the roadway is level, so the bearing is zero. `lots` is odd on purpose — the
 * middle lot of each row then faces the middle lot of the other, and the
 * placement nearest the street's median parcel is that facing pair exactly,
 * with no coin to toss.
 *
 * The 1 m side gaps are the interesting part of the geometry: they are the one
 * place other than the road where a midpoint between two of the street's own
 * houses lands on nobody's parcel, and a labeller that mistook one for the road
 * would write the street's name across it at right angles.
 */
export function streetBlock({
  name = 'MAIN ST',
  lots = 11,
  start = [-74.16, FIXTURE_LAT],
  width = 12,
  gap = 1,
  depth = 32,
  road = 14,
} = {}) {
  const [startLng, centreLat] = start;
  const features = [];

  for (let index = 0; index < lots; index += 1) {
    const west = (index * (width + gap)) * DEG_PER_M_LNG + startLng;
    const east = west + width * DEG_PER_M_LNG;

    for (const side of [1, -1]) {
      const near = centreLat + side * (road / 2) * DEG_PER_M_LAT;
      const far = centreLat + side * (road / 2 + depth) * DEG_PER_M_LAT;
      const number = index * 2 + (side > 0 ? 2 : 1);

      features.push({
        type: 'Feature',
        properties: {
          PAMS_PIN: `${name.replace(/\W+/g, '_')}_${number}`,
          score: 50,
          situs: `${number} ${name}, Ramsey NJ 07446`,
        },
        geometry: {
          type: 'Polygon',
          coordinates: [
            [
              [west, near],
              [east, near],
              [east, far],
              [west, far],
              [west, near],
            ],
          ],
        },
      });
    }
  }
  return features;
}

/** GeoJSON features as `map.js` holds them once the run is adopted. */
export function asDoors(features) {
  return features.map((feature) => ({
    properties: feature.properties,
    geometry: feature.geometry,
  }));
}

/**
 * The published run as `GET /api/doors.geojson` serves it.
 *
 * `centres` is the whole point: the territory's extent is data, not a constant,
 * and a test that shifts the centres must see the camera move with them.
 */
export function doorsGeojson(centres = TERRITORY_CENTRES) {
  const properties = [scoredDoor(), visionDoor(), noEvidenceDoor(), unscoredDoor()];
  return {
    type: 'FeatureCollection',
    features: centres.map((centre, index) =>
      parcelFeature(properties[index % properties.length], centre)
    ),
  };
}

/** Four corners of a plausible Ramsey territory, spread far enough to need a fit. */
const TERRITORY_CENTRES = [
  [-74.152, 41.048],
  [-74.128, 41.048],
  [-74.128, 41.072],
  [-74.152, 41.072],
];

/** `[[west, south], [east, north]]` over the parcel centres — the fit to expect. */
export function centreBounds(centres = TERRITORY_CENTRES) {
  const lngs = centres.map(([lng]) => lng);
  const lats = centres.map(([, lat]) => lat);
  return [
    [Math.min(...lngs), Math.min(...lats)],
    [Math.max(...lngs), Math.max(...lats)],
  ];
}

/** The default territory, for tests that do not care which one they get. */
export function territoryCentres() {
  return TERRITORY_CENTRES.map((centre) => [...centre]);
}

/* ── A DOM stand-in ──────────────────────────────────────────────────────── */

function fakeElement(tag = 'div', id = '') {
  const listeners = new Map();

  const node = {
    tagName: String(tag).toUpperCase(),
    id,
    className: '',
    type: '',
    title: '',
    value: '',
    src: '',
    alt: '',
    disabled: false,
    hidden: false,
    textContent: '',
    scrollTop: 0,
    offsetWidth: 320,
    clientWidth: 0,
    clientHeight: 0,
    style: {},
    dataset: {},
    attributes: {},
    children: [],
    listeners,
    /**
     * Where this element is on screen. Zero by default, which is what a hidden
     * element reports in a browser too — set it in a test to put something in
     * the way.
     */
    rect: { left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0 },
    getBoundingClientRect() {
      return node.rect;
    },

    get firstChild() {
      return node.children[0] ?? null;
    },
    appendChild(child) {
      if (child && child.isFragment) {
        node.children.push(...child.children);
        child.children = [];
        return child;
      }
      node.children.push(child);
      return child;
    },
    append(...kids) {
      for (const kid of kids) {
        node.appendChild(typeof kid === 'string' ? fakeElement('span') : kid);
      }
    },
    removeChild(child) {
      const at = node.children.indexOf(child);
      if (at >= 0) node.children.splice(at, 1);
      return child;
    },
    remove() {},
    setAttribute(name, value) {
      node.attributes[name] = String(value);
    },
    getAttribute(name) {
      return node.attributes[name] ?? null;
    },
    querySelector() {
      return fakeElement();
    },
    addEventListener(type, handler) {
      if (!listeners.has(type)) listeners.set(type, []);
      listeners.get(type).push(handler);
    },
    removeEventListener() {},
    /** Test-side: fire a listener the module registered. */
    dispatch(type, event = {}) {
      for (const handler of listeners.get(type) ?? []) handler(event);
    },
  };

  return node;
}

/* ── A MapLibre stand-in ─────────────────────────────────────────────────── */

function fakeLngLatBounds() {
  let west = null;
  let south = null;
  let east = null;
  let north = null;

  return {
    extend([lng, lat]) {
      west = west === null ? lng : Math.min(west, lng);
      east = east === null ? lng : Math.max(east, lng);
      south = south === null ? lat : Math.min(south, lat);
      north = north === null ? lat : Math.max(north, lat);
      return this;
    },
    getWest: () => west,
    getSouth: () => south,
    getEast: () => east,
    getNorth: () => north,
    getCenter: () => [(west + east) / 2, (south + north) / 2],
    toArray: () => [
      [west, south],
      [east, north],
    ],
  };
}

/**
 * The map object `map.js` drives, recording every camera command.
 *
 * `fitBounds` follows MapLibre's own refusal rule: a viewport that cannot hold
 * the requested padding yields no camera at all (`cameraForBounds` returns
 * undefined and `fitBounds` quietly returns the map unchanged). That is the
 * behaviour a fit issued against an unmeasured container actually gets — it is
 * silent, which is why the bug reaches a browser with a clean console.
 */
class FakeMap {
  constructor(options, harness) {
    this.options = options;
    this.harness = harness;

    this.center = options.center;
    this.zoom = options.zoom;
    /** What the map believes its viewport is — refreshed only by `resize()`. */
    this.size = harness.containerSize();

    this.sources = new Map();
    this.layers = new Map();
    this.filters = new Map();
    this.listeners = new Map();

    /** Every `fitBounds` call, applied or refused. */
    this.fits = [];
    this.resizes = [];
    this.isLoaded = false;

    this.touchZoomRotate = { disableRotation() {} };
    this.canvas = { style: {}, clientWidth: this.size.width, clientHeight: this.size.height };

    harness.maps.push(this);
  }

  on(type, layerOrHandler, maybeHandler) {
    const handler = typeof layerOrHandler === 'function' ? layerOrHandler : maybeHandler;
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(handler);
    return this;
  }
  off() {
    return this;
  }
  once(type, handler) {
    return this.on(type, handler);
  }
  fire(type, event = {}) {
    for (const handler of [...(this.listeners.get(type) ?? [])]) handler(event);
  }

  resize() {
    this.size = this.harness.containerSize();
    this.canvas.clientWidth = this.size.width;
    this.canvas.clientHeight = this.size.height;
    this.resizes.push({ ...this.size });
    return this;
  }
  getCanvas() {
    return this.canvas;
  }
  getContainer() {
    return this.harness.container;
  }

  addSource(id, spec) {
    const source = { id, data: spec.data, setData: (data) => (source.data = data) };
    this.sources.set(id, source);
  }
  getSource(id) {
    return this.sources.get(id);
  }
  addLayer(spec) {
    this.layers.set(spec.id, spec);
  }
  getLayer(id) {
    return this.layers.get(id);
  }
  setFilter(id, filter) {
    this.filters.set(id, filter);
  }

  fitBounds(bounds, options = {}) {
    const padding = options.padding ?? 0;
    const usableWidth = this.size.width - padding * 2;
    const usableHeight = this.size.height - padding * 2;

    const record = {
      bounds: bounds.toArray(),
      padding,
      viewport: { ...this.size },
      applied: false,
    };
    this.fits.push(record);

    // MapLibre's own guard. No throw, no error — the camera simply stays where
    // the constructor put it.
    if (usableWidth <= 0 || usableHeight <= 0) return this;

    record.applied = true;
    this.center = bounds.getCenter();
    const spanLng = Math.max(bounds.getEast() - bounds.getWest(), 1e-9);
    this.zoom = Math.log2((360 / spanLng) * (usableWidth / 512));
    return this;
  }

  getZoom() {
    return this.zoom;
  }
  getCenter() {
    return this.center;
  }
  getBounds() {
    return { contains: () => true };
  }
  /**
   * Flat and unrotated, but honest about the two things that matter: the
   * viewport's middle is the camera's centre, and the further apart two places
   * are the further apart their pixels. Anything that lays out over the canvas —
   * the score labels, the route pins, the street names deciding which of them
   * collide — is only testable against a projection that answers differently
   * for different points.
   */
  project([lng, lat]) {
    const pixelsPerDegree = (512 * Math.pow(2, this.zoom)) / 360;
    return {
      x: this.size.width / 2 + (lng - this.center[0]) * pixelsPerDegree,
      y: this.size.height / 2 - (lat - this.center[1]) * pixelsPerDegree,
    };
  }
  panBy() {}
  zoomIn() {
    this.zoom += 1;
  }
  zoomOut() {
    this.zoom -= 1;
  }
  queryRenderedFeatures() {
    return [];
  }
  loaded() {
    return this.isLoaded;
  }
  isStyleLoaded() {
    return this.isLoaded;
  }
  remove() {}
}

/**
 * The camera command that actually framed something.
 *
 * A fit computed against a viewport of no size is not a framing — it is the
 * bug. So this only counts a call that both applied and had a real viewport to
 * apply itself to.
 */
export function appliedFit(map) {
  return map.fits.find(
    (fit) => fit.applied && fit.viewport.width > 0 && fit.viewport.height > 0
  );
}

/* ── The harness ─────────────────────────────────────────────────────────── */

let bootSerial = 0;

/**
 * Install a browser for `map.js` to boot into.
 *
 * The two knobs are the whole point:
 *   `harness.layout()`    the container gets its real size (the browser has
 *                         laid the freshly-unhidden map screen out)
 *   `harness.loadStyle()` MapLibre's `load` event fires
 *
 * Tests drive those in either order. A fresh tab lays out first, because the
 * doors fetch went to the network and gave the browser a frame to do it in; a
 * reload serves the doors from cache and the style can be up before the layout
 * is.
 */
export function createMapHarness({ doors = doorsGeojson(), viewport = { width: 1280, height: 780 } } = {}) {
  const harness = {
    maps: [],
    /** rAF callbacks the module has queued. */
    frames: [],
    resizeObservers: [],
    fetchCalls: [],
    viewport,
  };

  let size = { width: 0, height: 0 };
  let elements = new Map();

  const getElement = (id) => {
    if (!elements.has(id)) elements.set(id, fakeElement('div', id));
    return elements.get(id);
  };

  const buildDocument = () => {
    elements = new Map();
    harness.container = getElement('map');
    return {
      getElementById: (id) => getElement(id),
      // Selectors are kept as their own keys, so `#coverage` is the element the
      // page also reaches by id and `.zoombar` is a stable element a test can
      // position. A test that never touches one gets an element measuring zero,
      // which reads as "not in the way".
      querySelector: (selector) => getElement(String(selector).replace(/^#/, '')),
      createElement: (tag) => fakeElement(tag),
      createDocumentFragment: () => {
        const fragment = fakeElement();
        fragment.isFragment = true;
        return fragment;
      },
      addEventListener: () => {},
      removeEventListener: () => {},
      body: fakeElement('body'),
    };
  };

  const windowListeners = new Map();
  const fakeWindow = {
    innerWidth: 1280,
    innerHeight: 780,
    addEventListener: (type, handler) => {
      if (!windowListeners.has(type)) windowListeners.set(type, []);
      windowListeners.get(type).push(handler);
    },
    removeEventListener: () => {},
  };

  const maplibregl = {
    Map: function Map(options) {
      return new FakeMap(options, harness);
    },
    LngLatBounds: function LngLatBounds() {
      return fakeLngLatBounds();
    },
  };
  fakeWindow.maplibregl = maplibregl;

  harness.containerSize = () => ({ ...size });
  harness.element = getElement;
  /** The map instance the current boot created. */
  Object.defineProperty(harness, 'map', { get: () => harness.maps.at(-1) });

  const saved = {};
  const globals = {
    window: fakeWindow,
    document: buildDocument(),
    location: { hash: '', pathname: '/', search: '', href: 'http://localhost:5173/' },
    history: { replaceState: () => {} },
    navigator: {},
    localStorage: memoryStorage(),
    sessionStorage: memoryStorage(),
    maplibregl,
    requestAnimationFrame: (callback) => harness.frames.push(callback),
    cancelAnimationFrame: () => {},
    ResizeObserver: function ResizeObserver(callback) {
      const observer = { callback, targets: [], observe(t) { observer.targets.push(t); }, disconnect() {} };
      harness.resizeObservers.push(observer);
      return observer;
    },
    Image: function Image() {
      return fakeElement('img');
    },
    fetch: async (url, init) => {
      harness.fetchCalls.push(String(url));
      return {
        ok: true,
        status: 200,
        json: async () => doors,
        headers: init?.headers ?? {},
      };
    },
  };

  // Some of these (`navigator`, `location`) are accessor-only on `globalThis` in
  // Node, so they are installed and put back as property descriptors.
  const define = (key, value) =>
    Object.defineProperty(globalThis, key, {
      value,
      writable: true,
      configurable: true,
      enumerable: true,
    });

  harness.install = () => {
    for (const [key, value] of Object.entries(globals)) {
      saved[key] = Object.getOwnPropertyDescriptor(globalThis, key) ?? null;
      define(key, value);
    }
    return harness;
  };

  harness.restore = () => {
    for (const [key, descriptor] of Object.entries(saved)) {
      if (descriptor === null) delete globalThis[key];
      else Object.defineProperty(globalThis, key, descriptor);
    }
  };

  /** Settle promises, then run whatever the module asked for next frame. */
  harness.flush = async () => {
    for (let pass = 0; pass < 6; pass += 1) {
      await new Promise((resolve) => setTimeout(resolve, 0));
      const due = harness.frames.splice(0, harness.frames.length);
      for (const callback of due) callback(pass);
    }
  };

  /**
   * The browser lays the map container out.
   *
   * Deliberately does NOT dispatch a window `resize` event: a container getting
   * its size during the page's own first layout is not a window resize, and
   * pretending otherwise would let the bug pass on the strength of a handler
   * that never runs in the field. Everything that genuinely does happen is
   * offered — ResizeObserver callbacks, MapLibre's continued `render`/`idle`,
   * and the next animation frame — so any honest recovery hook is available.
   */
  harness.layout = async (next = harness.viewport) => {
    size = { ...next };
    harness.container.clientWidth = size.width;
    harness.container.clientHeight = size.height;

    for (const observer of harness.resizeObservers) {
      observer.callback(
        observer.targets.map((target) => ({ target, contentRect: { ...size } })),
        observer
      );
    }
    for (const map of harness.maps) {
      map.fire('render');
      map.fire('idle');
    }
    await harness.flush();
  };

  /** MapLibre finishes the style and fires `load`. */
  harness.loadStyle = async () => {
    for (const map of harness.maps) {
      if (map.isLoaded) continue;
      map.isLoaded = true;
      map.fire('load');
    }
    await harness.flush();
  };

  /** Boot a fresh instance of `map.js` into this browser. */
  harness.boot = async () => {
    bootSerial += 1;
    await import(`./map.js?boot=${bootSerial}`);
    await harness.flush();
    return harness.map;
  };

  /**
   * A reload: same tab, same storage, a brand-new document and a camera back at
   * its starting position, with the container not yet measured.
   */
  harness.reload = () => {
    globals.document = buildDocument();
    define('document', globals.document);
    size = { width: 0, height: 0 };
    harness.maps = [];
    harness.frames = [];
    harness.resizeObservers = [];
  };

  return harness;
}
// --- T023 ---------------------------------------------------------------------
// `runManifest()` and `healthyManifest()` above are older publishes: they carry no
// `vision` block, and the page has to keep reading them. This one is the shape the
// run publishes now, and its numbers are the published run's own, read off
// data/run_manifest.json and data/doors.geojson rather than invented:
// 270 vision requests, 31 answers that could not be parsed, and 119 of 540 doors
// carrying an imagery-backed evidence line. That run is a *partial* loss — the
// stage ran and 119 doors have its evidence — which is the whole distinction the
// page has to be able to draw.

/** `data/run_manifest.json` from a run whose vision stage ran and partly failed. */
export function visionManifest(overrides = {}) {
  const manifest = healthyManifest();
  return {
    ...manifest,
    degradations: [
      '31 vision answers could not be read as detections; the doors they covered '
      + 'scored without their imagery signals',
    ],
    vision: {
      available: true,
      declination_reason: null,
      answers_total: 270,
      answers_lost: 31,
      doors_with_imagery: 119,
    },
    ...overrides,
  };
}

/** The same block for a stage that ran clean — nothing lost, nothing to disclaim. */
export function visionRanClean(overrides = {}) {
  return {
    available: true,
    declination_reason: null,
    answers_total: 270,
    answers_lost: 0,
    doors_with_imagery: 119,
    ...overrides,
  };
}

/** The same block for a stage that never ran, because there was no key for it. */
export function visionDeclined(overrides = {}) {
  return {
    available: false,
    declination_reason: VISION_NO_KEY_REASON,
    answers_total: 0,
    answers_lost: 0,
    doors_with_imagery: 0,
    ...overrides,
  };
}

/** The pipeline's own wording for a vision stage with no key to run on. */
export const VISION_NO_KEY_REASON =
  'OPENAI_API_KEY is not set, so the vision stage was skipped. Pool, solar '
  + 'and exterior-condition signals are unavailable; every door still scores, '
  + 'but without the R4 imagery terms.';
