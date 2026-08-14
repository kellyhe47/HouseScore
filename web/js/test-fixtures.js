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
