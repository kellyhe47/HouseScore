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
    walk_minutes: 0.0,
    cumulative_minutes: 0.0,
    talk_track: "Hi, I'm working OAK ST today. Is now a bad time?",
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
        walk_minutes: 4.4,
        cumulative_minutes: 4.4,
        talk_track: "Hi, I'm working MAPLE AVE today. Is now a bad time?",
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
        walk_minutes: 37.9,
        cumulative_minutes: 42.3,
        talk_track: "Hi, I'm working FAWN HILL RD today. Is now a bad time?",
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

/** `eval/report.json` as `make eval` writes it today. */
export function evalReport(overrides = {}) {
  return {
    // T025: the published `eval/report.json` reports thirteen fixtures, and the
    // harness pins that count (`tests/test_harness.py`). This fixture said twelve,
    // which is the same stale number the page had spelled into its prose.
    fixtures_total: 13,
    fixtures_passed: 13,
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
      'no door in this territory has a deed inside the 90-day mover window — the '
      + 'newest deed in the MOD-IV extract is dated 2024-12-06 — so the Mover group '
      + 'could not fire on this extract vintage',
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
