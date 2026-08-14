// Test-only fixtures. NOT app code — nothing in web/js/*.js (other than *.test.js)
// should import this. Shapes mirror data/doors.geojson feature `properties` exactly:
//   { PAMS_PIN, score, confidence, evidence[], situs, exclusion_reason }
// and each evidence item:
//   { type, points, sentence, source, retrieved, imagery }
// Every export is a FACTORY so tests never share mutable state.

/** A normal-confidence scored door, no vision-derived evidence. */
export function scoredDoor(overrides = {}) {
  return {
    PAMS_PIN: '0248_1503_7',
    score: 62,
    confidence: 'normal',
    situs: '128 SNYDER AVE, Ramsey NJ 07446',
    exclusion_reason: null,
    evidence: [
      {
        type: 'assessed_value',
        points: 15,
        sentence: 'Assessed at $880,700, at or above the $743,350 territory median.',
        source: 'NJ MOD-IV parcel record',
        retrieved: '2026-08-14',
        imagery: null,
      },
      {
        type: 'home_age',
        points: 8,
        sentence: 'Built 1983, so roughly 43 years old.',
        source: 'NJ MOD-IV parcel record',
        retrieved: '2026-08-14',
        imagery: null,
      },
      {
        type: 'non_arms_length_transfer',
        points: -20,
        sentence: 'Deed recorded as a non-arms-length transfer between related parties.',
        source: 'NJ deed record',
        retrieved: '2026-08-14',
        imagery: null,
      },
      {
        type: 'tenure',
        points: 0,
        sentence: '28-year tenure with no permits on record.',
        source: 'NJ MOD-IV parcel record',
        retrieved: '2026-08-14',
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
    score: 81,
    confidence: 'normal',
    situs: '27 FAWN HILL RD, Ramsey NJ 07446',
    exclusion_reason: null,
    evidence: [
      {
        type: 'pool',
        points: 12,
        sentence: 'In-ground pool visible in 2020 aerial imagery.',
        source: 'NJ 2020 orthoimagery',
        retrieved: '2026-08-14',
        imagery: {
          image_url: 'https://example.invalid/tiles/0248_3502_8.01_2020.jpg',
          bbox: [-74.14, 41.05, -74.139, 41.051],
          model_confidence: 0.88,
          capture_date: '2020-04-11',
        },
      },
      {
        type: 'permit_history',
        points: 22,
        sentence: 'Three permits since 2021 with no repeat contractor.',
        source: 'NJ construction permit dataset',
        retrieved: '2026-08-14',
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
    ...overrides,
  };
}

/** A scored door with an empty evidence list. */
export function noEvidenceDoor(overrides = {}) {
  return {
    PAMS_PIN: '0248_2201_1',
    score: 4,
    confidence: 'low',
    situs: '412 MAIN ST, Ramsey NJ 07446',
    exclusion_reason: null,
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
    evidence: [],
  };
}

// --- T013 ---------------------------------------------------------------------
// The route/walk surfaces read two more shapes: the widened `GET /api/door/{pin}`
// body (published properties + `groups`, `raw_total`, `talk_track`) and the
// `POST /api/route` payload, whose stops are `houseaccount.route.Stop` verbatim.

/**
 * A door as `GET /api/door/{pin}` serves it after the T013 amendment: the same
 * published properties plus the group math and the rep's opener.
 */
export function detailedDoor(overrides = {}) {
  return {
    ...scoredDoor(),
    // The group math reconciles with `scoredDoor()`'s evidence trail:
    // 15 + 8 − 20 + 0 = 3, which is also the sum of these five groups.
    groups: { mover: 0, hires_out: 0, capacity: 15, need: 8, modifier: -20 },
    raw_total: 3,
    score: 3,
    talk_track:
      "Hi, I'm working SNYDER AVE today. Quick reason I knocked: " +
      'assessed at $880,700, at or above the $743,350 territory median. Is now a bad time?',
    ...overrides,
  };
}

/** One stop exactly as the server serializes `houseaccount.route.Stop`. */
export function stop(overrides = {}) {
  return {
    pams_pin: '0248_01101_00012',
    address: '12 OAK ST, Ramsey NJ 07446',
    score: 100,
    walk_minutes: 0.0,
    cumulative_minutes: 0.0,
    talk_track: "Hi, I'm working OAK ST today. Is now a bad time?",
    ...overrides,
  };
}

/**
 * A three-stop `POST /api/route` body.
 *
 * The scores deliberately do NOT descend: the planner orders on score per
 * walking minute, and anything that re-sorts this list in the browser has
 * re-implemented the planner (R10.3).
 */
export function routePayload(overrides = {}) {
  return {
    stops: [
      stop(),
      stop({
        pams_pin: '0248_01101_00020',
        address: '20 MAPLE AVE, Ramsey NJ 07446',
        score: 58,
        walk_minutes: 4.4,
        cumulative_minutes: 4.4,
        talk_track: "Hi, I'm working MAPLE AVE today. Is now a bad time?",
      }),
      stop({
        pams_pin: '0248_3502_8.01',
        address: '27 FAWN HILL RD, Ramsey NJ 07446',
        score: 81,
        walk_minutes: 37.9,
        cumulative_minutes: 42.3,
        talk_track: "Hi, I'm working FAWN HILL RD today. Is now a bad time?",
      }),
    ],
    total_minutes: 42.3,
    estimate_disclosure:
      'Walking times are straight-line estimates (x1.3 detour at 3 mph), '
      + 'not turn-by-turn directions.',
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

/** `eval/report.json` as `make eval` writes it today. */
export function evalReport(overrides = {}) {
  return {
    fixtures_total: 12,
    fixtures_passed: 12,
    fixture_failures: [],
    precision: 0.8181818181818182,
    recall: 0.9,
    hallucination_rate: 0.05,
    // The honest caveat: no hand labels exist yet, so the vision metrics come
    // from a frozen fixture. This string has to reach the reader.
    metrics_source: 'frozen fixture 09 (hand labels not yet collected)',
    cost_total_usd: 3.2,
    doors_scored: 540,
    cost_per_door: 0.006,
    resolve_match_rate: null,
    resolve_match_rate_threshold: 0.95,
    ok: true,
    ...overrides,
  };
}

/** `data/run_manifest.json` as the published run writes it today. */
export function runManifest(overrides = {}) {
  return {
    run_at: '2026-08-14T08:38:26.522178+00:00',
    as_of: '2026-08-14',
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
    degradations: [
      'CENSUS_API_KEY is not set; the ACS API refuses keyless callers, so '
      + 'block-group statistics were not fetched',
      'municipal rental registration not obtained via OPRA',
      'ANTHROPIC_API_KEY is not set, so the vision stage was skipped. Pool, solar '
      + 'and exterior-condition signals are unavailable; every door still scores, '
      + 'but without the R4 imagery terms.',
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
