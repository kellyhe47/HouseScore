/**
 * The browser layer: everything that touches the DOM, MapLibre or the network.
 *
 * The rules this screen obeys — the colour ramp, the score filter, the coverage
 * readout, the evidence panel's shape, the load/error/retry machine, the route
 * request, walk-mode bookkeeping, the share format — live in `ramp.js`,
 * `filter.js`, `panel.js`, `state.js`, `route-ui.js`, `walk.js` and `share.js`,
 * which are DOM-free and unit-tested. This file is the wiring: it fetches the
 * published run, hands it to those modules, and renders whatever they say.
 * Nothing here decides what a score looks like, which doors a range selects, or
 * what order a walk goes in.
 *
 * Loaded as `<script type="module">`, no build step, no bundler.
 */

import { scoreColor, UNSCORED_COLOR, RAMP_CSS_GRADIENT } from './ramp.js';
import { coverageText, filterDoors } from './filter.js';
import { buildPanel, copyAddress, evidenceLabel, panelLayout } from './panel.js';
import { createMapState } from './state.js';
import { createRoutePlanner, routeLine, REFRESH_PROMPT } from './route-ui.js';
import { createWalk, readWalk, clearWalk, resumeOffer } from './walk.js';
import { streetLabels } from './streets.js';
import { copyAsText, copyShareLink, readShare, shareTokenVersion } from './share.js';
import { buildStreetView, loadingStreetView } from './streetview.js';

/* ── Configuration ───────────────────────────────────────────────────────── */

/**
 * Where the API lives — one constant, resolved once.
 *
 * R12 deploys the map and the server to different hosts, so this must never be
 * a hardcoded localhost. A deployment that splits them sets
 * `window.HOUSEACCOUNT_API_BASE` (an injected snippet, a one-line config
 * script) to the API's origin; the server's CORS allowlist
 * (`houseaccount.server.app.UI_ORIGINS`) is the other half of that handshake.
 *
 * The fallback is the same-origin `/api`, which is what a single-process or
 * proxied deployment serves — and the only path a local check exercises, so the
 * common case is the one that gets tested.
 */
const API_BASE = String(
  (typeof window !== 'undefined' && window.HOUSEACCOUNT_API_BASE) || '/api'
).replace(/\/$/, '');

/** Above this zoom every visible parcel wears its score (wireframe frame 1). */
const LABEL_ZOOM = 17;

/**
 * How many streets get named in the grey, and how big that name is drawn.
 *
 * With no basemap under it, the map is parcels and gaps — legible as a territory
 * only to someone who already knows the town. Naming the handful of streets the
 * territory is mostly built along is what lets everyone else place themselves.
 * Eight is enough to orient by and few enough to stay out of the way of the
 * thing the screen is actually about, which is the scores.
 */
const STREET_LABEL_LIMIT = 8;
const STREET_LABEL_HEIGHT = 13;
/** Average advance of one uppercase character at the label's size, in pixels. */
const STREET_LABEL_CHAR = 7.6;
/** Empty pixels held around each street name so two never read as one. */
const STREET_LABEL_MARGIN = 6;
/** How far apart on screen the same street's name may be written twice. */
const STREET_REPEAT_PX = 320;

/** Breathing room around the territory, in pixels, whenever the camera is fitted. */
const FIT_PADDING = 48;

/** Out-of-range parcels stay on the map, dimmed, so the territory keeps its shape. */
const DIM_OPACITY = 0.14;

/** With a route on screen, everything not on it recedes but stays legible. */
const OFF_ROUTE_OPACITY = 0.28;

const TOAST_MS = 1800;

/** The hours a rep actually has (frame 4c). Strings, because they are labels. */
const HOURS_OPTIONS = ['0.5', '1', '1.5', '2', '3'];

/**
 * The "why this door" chip (wireframe frame 4d) is the API's `reason_chip`
 * (R30) — assembled on the server from the door's own top evidence under the
 * selection rule, never re-derived in the browser from a label table. The map
 * only formats it for reading.
 */
const chipText = (reasonChip) => (reasonChip ? evidenceLabel(reasonChip) : null);

/* ── Element handles ─────────────────────────────────────────────────────── */

const $ = (id) => document.getElementById(id);

const els = {
  loading: $('loading'),
  loadingProgress: $('loading-progress'),
  screen: $('screen-map'),
  errorBanner: $('error-banner'),
  retry: $('retry'),
  rangeLo: $('range-lo'),
  rangeHi: $('range-hi'),
  rangeLabel: $('range-label'),
  coverage: $('coverage'),
  legendRamp: $('legend-ramp'),
  streets: $('street-labels'),
  labels: $('labels'),
  toast: $('toast'),
  panel: $('panel'),
  panelAddr: $('panel-addr'),
  panelPin: $('panel-pin'),
  panelBody: $('panel-body'),
  panelClose: $('panel-close'),
  copyAddress: $('copy-address'),
  toggleMath: $('toggle-math'),
  panelPhoto: $('panel-photo'),
  photoFull: $('photo-full'),
  photoBack: $('photo-back'),
  photoAddr: $('photo-addr'),
  photoSub: $('photo-sub'),
  photoMaps: $('photo-maps'),
  photoFrame: $('photo-frame'),
  lightbox: $('lightbox'),
  lightboxFrame: $('lightbox-frame'),
  lightboxLabel: $('lightbox-label'),
  lightboxSub: $('lightbox-sub'),

  routePins: $('route-pins'),
  pickHint: $('pick-hint'),
  routeBar: $('route-bar'),
  resumeBanner: $('resume-banner'),
  resumeLabel: $('resume-label'),

  routePanel: $('route-panel'),
  hoursOptions: $('hours-options'),
  routeDoors: $('route-doors'),
  pickStart: $('pick-start'),
  planGo: $('plan-route-go'),
  cancelPlan: $('cancel-plan'),
  routeEmpty: $('route-empty'),
  routeError: $('route-error'),
  routeList: $('route-list'),
  routeFoot: $('route-foot'),
  routeSummary: $('route-summary'),
  routeDisclosure: $('route-disclosure'),

  walk: $('walk'),
  walkProgress: $('walk-progress'),
  walkLeft: $('walk-left'),
  walkBar: $('walk-bar'),
  walkStop: $('walk-stop'),
  walkMeta: $('walk-meta'),
  walkAddr: $('walk-addr'),
  walkChip: $('walk-chip'),
  walkTalk: $('walk-talk'),
  walkActions: $('walk-actions'),
  walkUpcoming: $('walk-upcoming'),
  walkFinished: $('walk-finished'),
  walkSummary: $('walk-summary'),
};

/* ── Application state ───────────────────────────────────────────────────── */

const machine = createMapState();

/** @type {{properties: object, geometry: object|null, centroid: [number, number]|null}[]} */
let doors = [];
/** Doors currently passing the filter, for labelling. */
let visible = [];
/** The main streets' names and where to write them, derived from the parcels. */
let streets = [];
let range = [0, 100];
let selectedPin = null;
let map = null;
let toastTimer = null;
let labelFrame = null;
/** True once the territory has been framed against a viewport that could hold it. */
let territoryFramed = false;
/** The ResizeObserver waiting for that viewport, or null once it is no longer needed. */
let containerWatch = null;

/** The detail body of the selected door, once `/api/door/{pin}` answers. */
let selectedDetail = null;
/** The Street View slot's view model for the selected door (streetview.js). */
let streetView = null;
/** Frame 2b's toggle: the breakdown is opt-in, not the default reading. */
let showMath = false;

/* Route + walk state. */
const planner = createRoutePlanner({ fetch: (...args) => fetch(...args), apiBase: API_BASE });
/** Where the rep parks, as [lng, lat]. Null until they pick it on the map. */
let startPoint = null;
let pickingStart = false;
let planning = false;
let hours = '2';
/** The current `RouteView`, or null when no route is planned. */
let routeView = null;
/** The active walk, or null. */
let walk = null;
/** A `resumeOffer` waiting on the map, or null. */
let pendingResume = null;

/**
 * The Data & Ethics page's demo-only trigger links here with `#demo-error`, so
 * the degraded state it describes is one click from the description.
 *
 * Read and cleared at boot rather than checked on each load: leaving it in the
 * URL would make every Retry re-fail, which is the opposite of a demo. Clearing
 * it here is also why `openSharedRoute` below never sees it — and a share token
 * is never this fragment, so nothing is lost.
 */
let demoErrorRequested =
  typeof location !== 'undefined' && location.hash === '#demo-error';
if (demoErrorRequested) {
  history.replaceState(null, '', location.pathname + location.search);
}

/* ── Small DOM helpers ───────────────────────────────────────────────────── */

/** Build an element. Text always goes in as text — never as markup. */
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

function showToast(message) {
  els.toast.textContent = message;
  els.toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    els.toast.hidden = true;
  }, TOAST_MS);
}

/** The single sink for `{type: 'toast'}` events from the logic modules. */
function emit(event) {
  if (event && event.type === 'toast') showToast(event.message);
}

/* ── Loading, error and retry (R9.3, wireframe frame 6) ──────────────────── */

function renderMachine() {
  const state = machine.state;
  els.loading.hidden = state !== 'loading';
  els.screen.hidden = state === 'loading';
  els.errorBanner.hidden = state !== 'error';
  if (state === 'ready' && map) map.resize();
}

async function loadDoors() {
  renderMachine();
  els.loadingProgress.textContent = 'loading doors…';

  try {
    const response = await fetch(`${API_BASE}/doors.geojson`, {
      headers: { accept: 'application/geo+json, application/json' },
    });
    if (!response.ok) throw new Error(`doors.geojson responded ${response.status}`);

    const collection = await response.json();
    const features = Array.isArray(collection.features) ? collection.features : [];
    els.loadingProgress.textContent = `loading ${features.length} doors…`;

    adoptDoors(features);
    machine.ready();
    renderMachine();
    renderMap();
    renderFilter();

    // Both need the territory in hand: a resume banner has to name stops, and a
    // share link has to resolve its PINs to doors that exist.
    offerResume();
    openSharedRoute();

    if (demoErrorRequested) {
      // One-shot: the flag was consumed at boot, so Retry recovers instead of
      // re-reading the fragment and failing the same load forever.
      demoErrorRequested = false;
      machine.fail();
      renderMachine();
    }
  } catch (error) {
    console.error('[houseaccount] doors layer failed to load:', error);
    machine.fail();
    renderMachine();
  }
}

els.retry.addEventListener('click', () => {
  // Retry only means anything from `error`; the machine is what enforces that,
  // so a stray click cannot start a second load over a healthy map.
  if (machine.retry() === 'loading') loadDoors();
});

// Demo-only (DESIGN-ADDITIONS): the error state is otherwise undemoable, so a
// reviewer gets a trigger for it, marked as demo-only in the UI. It drops the
// map into `error` without breaking the API, so Retry is a real recovery.
$('simulate-error').addEventListener('click', (event) => {
  event.preventDefault();
  closePanel();
  machine.fail();
  renderMachine();
});

/* ── The published run ───────────────────────────────────────────────────── */

/** The average of a polygon's outer ring — good enough to hang a label on. */
function centroidOf(geometry) {
  if (!geometry) return null;
  const ring =
    geometry.type === 'Polygon'
      ? geometry.coordinates[0]
      : geometry.type === 'MultiPolygon'
        ? geometry.coordinates[0][0]
        : null;
  if (!ring || !ring.length) return null;

  // The closing vertex repeats the first, so it is dropped from the mean.
  const points = ring.length > 1 ? ring.slice(0, -1) : ring;
  let x = 0;
  let y = 0;
  for (const [lng, lat] of points) {
    x += lng;
    y += lat;
  }
  return [x / points.length, y / points.length];
}

function adoptDoors(features) {
  doors = features.map((feature) => ({
    properties: feature.properties,
    geometry: feature.geometry,
    centroid: centroidOf(feature.geometry),
  }));

  // Derived once per load, not per frame: the placements are in lng/lat, so
  // panning and zooming only re-project them.
  streets = streetLabels(doors, { limit: STREET_LABEL_LIMIT });

  const scored = doors.filter((door) => door.properties.score !== null).length;
  els.coverage.textContent = coverageText(scored, doors.length);
}

const byPin = (pin) => doors.find((door) => door.properties.PAMS_PIN === pin);

/* ── MapLibre ────────────────────────────────────────────────────────────── */

/**
 * The parcels as a GeoJSON source, carrying their own paint values.
 *
 * `_color` comes from `scoreColor`, so the fill, the panel's big number and the
 * legend are all the same ramp by construction rather than by coincidence.
 * `_opacity` carries the filter, which is why filtering is a `setData` and not
 * a layer swap: an out-of-range parcel is dimmed, never removed, so the rep can
 * still see the shape of the territory they are narrowing.
 */
function sourceData() {
  const inRange = new Set(
    filterDoors(
      doors.map((door) => door.properties),
      range,
      { showUnscored: true }
    ).map((properties) => properties.PAMS_PIN)
  );

  visible = doors.filter(
    (door) => door.properties.score !== null && inRange.has(door.properties.PAMS_PIN)
  );

  // With a route planned, the walk is the subject and the rest of the territory
  // is context — so off-route doors recede rather than vanish (frame 4).
  const onRoute = routeView ? new Set(routeView.rows.map((row) => row.pin)) : null;
  const knocked = walk ? new Set(walk.knocked) : null;

  return {
    type: 'FeatureCollection',
    features: doors
      .filter((door) => door.geometry)
      .map((door) => {
        const { PAMS_PIN, score } = door.properties;
        const done = knocked ? knocked.has(PAMS_PIN) : false;

        let opacity = inRange.has(PAMS_PIN) ? 1 : DIM_OPACITY;
        if (onRoute && !onRoute.has(PAMS_PIN)) opacity = Math.min(opacity, OFF_ROUTE_OPACITY);

        return {
          type: 'Feature',
          geometry: door.geometry,
          properties: {
            PAMS_PIN,
            // A knocked door goes grey: the map is the rep's record of where
            // they have been, not just of where they were sent.
            _color: done ? '#B9BCC2' : scoreColor(score),
            _opacity: done ? 0.6 : opacity,
            _scored: score === null ? 0 : 1,
          },
        };
      }),
  };
}

/**
 * The dashed walking path: the parking spot, then the walk the planner planned.
 *
 * `routeLine` assembles the legs; this only wraps them for the source. The
 * vertices are the server's, because a rep walks along streets and the planner
 * is the only thing here that knows where those are (R10.3).
 */
function routeLineData() {
  const coordinates = routeLine(routeView ? routeView.rows : [], startPoint, (pin) => {
    const door = byPin(pin);
    return door && door.centroid ? door.centroid : null;
  });

  return {
    type: 'FeatureCollection',
    features: coordinates.length
      ? [{ type: 'Feature', geometry: { type: 'LineString', coordinates }, properties: {} }]
      : [],
  };
}

function refreshMapData() {
  if (!map || !map.getSource('doors')) return;
  map.getSource('doors').setData(sourceData());
  map.getSource('route').setData(routeLineData());
  scheduleLabels();
}

function boundsOfDoors() {
  const bounds = new maplibregl.LngLatBounds();
  let any = false;
  for (const door of doors) {
    if (!door.centroid) continue;
    bounds.extend(door.centroid);
    any = true;
  }
  return any ? bounds : null;
}

/**
 * Frame the whole territory — once, and only once the container can hold it.
 *
 * `fitBounds` is silent about failure: MapLibre asks `cameraForBounds` for a
 * camera, and a viewport too small to hold the padding yields none, so the call
 * returns the map untouched with nothing logged. A reload serves
 * `doors.geojson` from cache, which means the style can be up before the
 * browser has laid the just-unhidden map screen out — the one fit fired against
 * a container of no size, was discarded, and left the camera on the
 * constructor's opening centre. That is the blank grey map QA saw on every
 * reload, with a clean console and a readout still claiming 540 doors.
 *
 * Re-measuring harder does not help, because there is nothing to measure yet.
 * So the fit is attempted, and if the viewport cannot carry it the attempt is
 * simply not counted — `watchContainer` will bring it back when the container
 * has a size. Once it lands, `territoryFramed` closes the door: the rep's own
 * panning and zooming is theirs to keep.
 */
function frameTerritory() {
  if (territoryFramed || !map || !map.isStyleLoaded()) return;

  // The container may only just have become measurable; the map still believes
  // whatever it measured at construction time.
  map.resize();
  const canvas = map.getCanvas();
  if (canvas.clientWidth <= FIT_PADDING * 2 || canvas.clientHeight <= FIT_PADDING * 2) return;

  const bounds = boundsOfDoors();
  if (!bounds) return;

  map.fitBounds(bounds, { padding: FIT_PADDING, duration: 0 });
  territoryFramed = true;
  stopWatchingContainer();
}

/**
 * Retry the opening fit whenever the map container changes size.
 *
 * A container getting its first real size during the page's own layout is not a
 * window `resize` — no such event is ever dispatched — which is why the resize
 * handler at the bottom of this file cannot stand in for this. A
 * `ResizeObserver` is the hook that genuinely fires for it, including for the
 * `display: none` → laid-out transition `renderMachine` triggers one statement
 * before the map is built.
 */
function watchContainer(container) {
  if (containerWatch || typeof ResizeObserver !== 'function' || !container) return;
  containerWatch = new ResizeObserver(() => frameTerritory());
  containerWatch.observe(container);
}

function stopWatchingContainer() {
  if (!containerWatch) return;
  containerWatch.disconnect();
  containerWatch = null;
}

function renderMap() {
  if (!window.maplibregl) {
    console.error('[houseaccount] MapLibre GL JS did not load.');
    machine.fail();
    renderMachine();
    return;
  }

  if (map) {
    // A retry after the map already exists is a data swap, not a rebuild. The
    // source can still be missing if the style has not finished loading, in
    // which case its own `load` handler will read the fresh doors anyway.
    const source = map.getSource('doors');
    if (source) source.setData(sourceData());
    scheduleLabels();
    return;
  }

  map = new maplibregl.Map({
    container: 'map',
    // No basemap: a plain ground plus the parcels, exactly as the approved
    // prototype draws it — and no tile provider, so no API key (R12).
    style: {
      version: 8,
      sources: {},
      layers: [{ id: 'ground', type: 'background', paint: { 'background-color': '#E8E6E0' } }],
    },
    center: [-74.14, 41.06],
    zoom: 13,
    attributionControl: false,
    dragRotate: false,
    pitchWithRotate: false,
  });
  map.touchZoomRotate.disableRotation();
  map.getCanvas().style.cursor = 'grab';
  // Registered before the style can possibly be up, so whichever of the two
  // arrives second — the layout or the `load` — carries the fit.
  watchContainer(map.getContainer());
  watchChrome();

  map.on('load', () => {
    map.addSource('doors', { type: 'geojson', data: sourceData() });

    map.addLayer({
      id: 'doors-fill',
      type: 'fill',
      source: 'doors',
      paint: { 'fill-color': ['get', '_color'], 'fill-opacity': ['get', '_opacity'] },
    });
    map.addLayer({
      id: 'doors-edge',
      type: 'line',
      source: 'doors',
      paint: {
        'line-color': 'rgba(255,255,255,0.7)',
        'line-width': 0.7,
        'line-opacity': ['get', '_opacity'],
      },
    });
    // The unscored parcels get a hatched edge as well as their off-ramp grey:
    // "no score" has to be legible as a different kind of thing, not a low one.
    map.addLayer({
      id: 'doors-unscored',
      type: 'line',
      source: 'doors',
      filter: ['==', ['get', '_scored'], 0],
      paint: { 'line-color': '#B4B1A8', 'line-width': 1, 'line-dasharray': [2, 2] },
    });
    map.addLayer({
      id: 'doors-selected',
      type: 'line',
      source: 'doors',
      filter: ['==', ['get', 'PAMS_PIN'], ''],
      paint: { 'line-color': '#E8791A', 'line-width': 2.4 },
    });

    map.addSource('route', { type: 'geojson', data: routeLineData() });
    map.addLayer({
      id: 'route-line',
      type: 'line',
      source: 'route',
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: { 'line-color': '#E8791A', 'line-width': 2.5, 'line-dasharray': [3, 2] },
    });

    // The map is built the moment the doors arrive, which can be before the
    // browser has laid the freshly-unhidden map screen out — so this is an
    // attempt, not the guarantee. `watchContainer` below is the guarantee.
    frameTerritory();

    scheduleLabels();
  });

  map.on('click', (event) => {
    // While picking a start the whole canvas is one control, so a parcel click
    // means "park here", not "tell me about this door" (frame 4c).
    if (pickingStart) {
      setStartPoint([event.lngLat.lng, event.lngLat.lat]);
      return;
    }
    const hits = map.queryRenderedFeatures(event.point, { layers: ['doors-fill'] });
    if (hits.length) selectDoor(hits[0].properties.PAMS_PIN);
    else closePanel();
  });
  map.on('mouseenter', 'doors-fill', () => {
    map.getCanvas().style.cursor = 'pointer';
  });
  map.on('mouseleave', 'doors-fill', () => {
    map.getCanvas().style.cursor = 'grab';
  });
  map.on('move', scheduleLabels);
  map.on('zoom', scheduleLabels);
}

$('zoom-in').addEventListener('click', () => map && map.zoomIn());
$('zoom-out').addEventListener('click', () => map && map.zoomOut());

/* ── Score labels at high zoom ───────────────────────────────────────────── */

function scheduleLabels() {
  if (labelFrame) return;
  labelFrame = requestAnimationFrame(() => {
    labelFrame = null;
    renderStreets();
    renderLabels();
    renderRoutePins();
  });
}

/**
 * The street names, written in the grey where the streets are.
 *
 * HTML over the canvas, like the score labels and the route pins: a MapLibre
 * symbol layer would need a glyph server, and this page has no external font or
 * tile dependency to lose (R12).
 *
 * Nothing is ever allowed to overlap a street name: not another street name,
 * and not the chrome floating over the map, which is read as being in front of
 * the map rather than on it and so does not tolerate type sliding under it.
 * `streetLabels` returns its placements best-first, so the loop simply takes
 * what fits and drops what does not — which at territory zoom means one name per
 * street, and as the rep zooms in means the repeats along the longer streets
 * come back as room appears.
 */
function renderStreets() {
  if (!map) return;

  // Measured before the container is touched, so a frame is one read pass
  // followed by one write pass rather than a layout per label.
  const taken = chromeBoxes();
  clear(els.streets);

  const canvas = map.getCanvas();
  const width = canvas.clientWidth;
  const height = canvas.clientHeight;
  const fragment = document.createDocumentFragment();

  for (const street of streets) {
    const point = map.project(street.position);
    const box = labelBox(point, street);
    if (box.right < 0 || box.left > width || box.bottom < 0 || box.top > height) continue;
    if (taken.some((other) => overlaps(other, box) || tooSoonAgain(other, street, point))) {
      continue;
    }
    taken.push({ ...box, name: street.name, x: point.x, y: point.y });

    const label = el('span', 'street-label', street.name);
    label.style.left = `${Math.round(point.x)}px`;
    label.style.top = `${Math.round(point.y)}px`;
    // Screen y grows downward and CSS rotates clockwise, so the geographic
    // bearing is applied negated — the label lies along the street either way.
    label.style.transform = `translate(-50%, -50%) rotate(${(-street.bearing).toFixed(1)}deg)`;
    fragment.appendChild(label);
  }

  els.streets.appendChild(fragment);
}

/**
 * Where the map's own furniture is sitting right now, in canvas pixels.
 *
 * The readout, the legend, the zoom buttons, the evidence panel and the route
 * chrome all float over the map, and every one of them can move or appear
 * without the camera moving at all — the panel opens, a route bar arrives, the
 * viewport narrows and the legend shifts. So they are measured on the frame
 * rather than assumed, and handed to the same collision test the labels use on
 * each other. Anything hidden measures zero and is dropped, which is exactly
 * right: a hidden panel is not in the way.
 */
function chromeBoxes() {
  const container = map.getContainer().getBoundingClientRect();
  const boxes = [];

  for (const node of chrome()) {
    const rect = node.getBoundingClientRect();
    if (!rect || rect.width <= 0 || rect.height <= 0) continue;
    boxes.push({
      left: rect.left - container.left - STREET_LABEL_MARGIN,
      right: rect.right - container.left + STREET_LABEL_MARGIN,
      top: rect.top - container.top - STREET_LABEL_MARGIN,
      bottom: rect.bottom - container.top + STREET_LABEL_MARGIN,
      name: null,
      x: NaN,
      y: NaN,
    });
  }
  return boxes;
}

/**
 * The floating chrome, resolved once.
 *
 * Everything drawn over the canvas rather than in it — including walk mode's
 * sheet, which lives outside `.maparea` but is measured in the same viewport
 * coordinates as everything else and covers the bottom of the map while a rep is
 * walking. The canvas and the three label layers are not in the list, because
 * they are the map.
 */
const CHROME_SELECTORS = [
  '#coverage',
  '#pick-hint',
  '#route-bar',
  '#resume-banner',
  '.maparea__bottomleft',
  '.zoombar',
  '#toast',
  '#panel',
  '#route-panel',
  '#walk',
];
let chromeNodes = null;

/**
 * Re-lay the street names whenever the furniture over the map moves.
 *
 * The panel opens, the route bar arrives, a toast comes and goes — none of which
 * is a camera move, so none of which would otherwise reach `scheduleLabels`, and
 * the names underneath would sit there under the new panel until the next pan. A
 * `ResizeObserver` catches all of it, including the appearing and disappearing:
 * an element going `hidden` is a box collapsing to nothing, which is a resize.
 */
function watchChrome() {
  if (typeof ResizeObserver !== 'function') return;
  const observer = new ResizeObserver(() => scheduleLabels());
  for (const node of chrome()) observer.observe(node);
}

function chrome() {
  if (!chromeNodes) {
    chromeNodes = CHROME_SELECTORS.map((selector) => document.querySelector(selector)).filter(
      Boolean
    );
  }
  return chromeNodes;
}

/** The screen box a rotated street name occupies, margin included. */
function labelBox(point, street) {
  const radians = (street.bearing * Math.PI) / 180;
  const sin = Math.abs(Math.sin(radians));
  const cos = Math.abs(Math.cos(radians));
  const textWidth = street.name.length * STREET_LABEL_CHAR;

  const halfWidth = (textWidth * cos + STREET_LABEL_HEIGHT * sin) / 2 + STREET_LABEL_MARGIN;
  const halfHeight = (textWidth * sin + STREET_LABEL_HEIGHT * cos) / 2 + STREET_LABEL_MARGIN;

  return {
    left: point.x - halfWidth,
    right: point.x + halfWidth,
    top: point.y - halfHeight,
    bottom: point.y + halfHeight,
  };
}

const overlaps = (a, b) =>
  a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;

/**
 * A street's name written again too soon after the last time.
 *
 * `streetLabels` offers several places to write a long street's name so that
 * one of them is on screen whatever the rep has zoomed into. How far apart those
 * read is a matter of pixels, not metres: the same two anchors that are a
 * welcome second sighting across the whole territory are a stutter once the map
 * is down to one block.
 */
const tooSoonAgain = (other, street, point) =>
  other.name === street.name &&
  Math.hypot(other.x - point.x, other.y - point.y) < STREET_REPEAT_PX;

function renderLabels() {
  if (!map) return;
  clear(els.labels);
  if (map.getZoom() < LABEL_ZOOM) return;

  const bounds = map.getBounds();
  const fragment = document.createDocumentFragment();

  for (const door of visible) {
    if (!door.centroid || !bounds.contains(door.centroid)) continue;
    const point = map.project(door.centroid);
    const score = door.properties.score;
    const label = el('span', 'parcel-label', score);
    // Dark fills need light type; the ramp crosses over around 55.
    label.style.color = score > 55 ? '#FFFFFF' : '#1B1E23';
    label.style.left = `${Math.round(point.x)}px`;
    label.style.top = `${Math.round(point.y)}px`;
    fragment.appendChild(label);
  }
  els.labels.appendChild(fragment);
}

/**
 * The numbered stop markers and the parking spot.
 *
 * HTML over the canvas rather than a symbol layer: numbers in a MapLibre symbol
 * layer need a glyph server, and this page deliberately has no external tile or
 * font dependency it could lose.
 */
function renderRoutePins() {
  if (!map) return;
  clear(els.routePins);

  const fragment = document.createDocumentFragment();
  const knocked = walk ? new Set(walk.knocked) : null;

  if (startPoint) {
    const point = map.project(startPoint);
    const marker = el('span', 'route-pin is-start', 'P');
    marker.style.left = `${Math.round(point.x)}px`;
    marker.style.top = `${Math.round(point.y)}px`;
    fragment.appendChild(marker);
  }

  for (const row of routeView ? routeView.rows : []) {
    const door = byPin(row.pin);
    if (!door || !door.centroid) continue;
    const point = map.project(door.centroid);
    const done = knocked ? knocked.has(row.pin) : false;
    const marker = el('span', `route-pin${done ? ' is-knocked' : ''}`, row.n);
    marker.style.left = `${Math.round(point.x)}px`;
    marker.style.top = `${Math.round(point.y - 16)}px`;
    fragment.appendChild(marker);
  }

  els.routePins.appendChild(fragment);
}

/* ── Score filter ────────────────────────────────────────────────────────── */

// The legend is painted from the same two constants the parcels are, so it can
// never drift out of step with what the map is actually showing.
els.legendRamp.style.background = RAMP_CSS_GRADIENT;
document.querySelector('.legend__swatch').style.background = UNSCORED_COLOR;

function renderFilter() {
  const [lo, hi] = range;
  els.rangeLo.value = String(lo);
  els.rangeHi.value = String(hi);
  els.rangeLabel.textContent = `score ${lo}–${hi}`;
  if (map && map.getSource('doors')) {
    refreshMapData();
  } else {
    // Keeps `visible` in step before the map exists.
    sourceData();
  }
}

// The thumbs keep a one-point gap so neither can be buried under the other.
els.rangeLo.addEventListener('input', () => {
  range = [Math.min(Number(els.rangeLo.value), range[1] - 1), range[1]];
  renderFilter();
});
els.rangeHi.addEventListener('input', () => {
  range = [range[0], Math.max(Number(els.rangeHi.value), range[0] + 1)];
  renderFilter();
});

/* ── Evidence panel (R9.1–R9.5) ──────────────────────────────────────────── */

function selectDoor(pin) {
  const door = byPin(pin);
  if (!door) return;

  selectedPin = pin;
  selectedDetail = null;
  showMath = false;
  if (map && map.getLayer('doors-selected')) {
    map.setFilter('doors-selected', ['==', ['get', 'PAMS_PIN'], pin]);
  }

  // Drawn twice on purpose. The map already holds every published property, so
  // the panel opens on the click with the evidence trail intact; the talk track
  // and the group math live only on the door endpoint (R11.1 keeps them out of
  // the 540-door download), so they arrive a moment later and fill in.
  renderPanel(buildPanel(door.properties));
  // The photo slot goes to its skeleton immediately — the panel must not
  // reflow when the metadata answer lands.
  streetView = loadingStreetView();
  renderStreetView();
  revealSelected(door);
  loadDoorDetail(pin);
  loadStreetView(pin);
}

async function loadStreetView(pin) {
  let body = null;
  try {
    const response = await fetch(`${API_BASE}/streetview/${encodeURIComponent(pin)}`);
    // A 502 still carries a body (and often a usable Maps link); parse it
    // either way and let the view model sort available from error.
    body = await response.json().catch(() => null);
  } catch {
    // Network down: body stays null, which buildStreetView renders as error.
  }
  // The rep may have clicked another door while this was in flight.
  if (selectedPin !== pin) return;
  streetView = buildStreetView(body);
  renderStreetView();
}

async function loadDoorDetail(pin) {
  try {
    const response = await fetch(`${API_BASE}/door/${encodeURIComponent(pin)}`);
    if (!response.ok) return;
    const detail = await response.json();
    // The rep may have clicked another door while this was in flight; the late
    // answer belongs to a panel that is no longer open.
    if (selectedPin !== pin) return;
    selectedDetail = detail;
    renderPanel(buildPanel(detail));
  } catch {
    // The panel is already useful without the detail. A failed lookup costs the
    // talk track and the breakdown, not the door.
  }
}

function closePanel() {
  selectedPin = null;
  selectedDetail = null;
  showMath = false;
  streetView = null;
  els.panelPhoto.hidden = true;
  clear(els.panelPhoto);
  closePhotoFull();
  els.panel.hidden = true;
  if (map && map.getLayer('doors-selected')) {
    map.setFilter('doors-selected', ['==', ['get', 'PAMS_PIN'], '']);
  }
}

els.panelClose.addEventListener('click', closePanel);
// Escape unwinds one layer at a time, outermost first.
document.addEventListener('keydown', (event) => {
  if (event.key !== 'Escape') return;
  if (!els.photoFull.hidden) closePhotoFull();
  else if (!els.lightbox.hidden) closeLightbox();
  else if (pickingStart) {
    pickingStart = false;
    openRoutePanel();
  } else if (!els.panel.hidden) closePanel();
  else if (!els.routePanel.hidden) closeRoutePanel();
});

els.copyAddress.addEventListener('click', () => {
  const door = byPin(selectedPin);
  if (door) copyAddress(door.properties, emit);
});

// Frame 2b: the arithmetic is opt-in. The evidence trail is what the rep reads
// at the door; the group math is what they open when someone asks "why 62?".
els.toggleMath.addEventListener('click', () => {
  showMath = !showMath;
  if (selectedDetail) renderPanel(buildPanel(selectedDetail));
});

/**
 * Nudge the map so the panel is not sitting on top of the parcel just clicked.
 *
 * Which way to nudge is the layout question `panelLayout` answers (R9.2): a
 * side panel eats the right edge, a bottom sheet eats the bottom.
 */
function revealSelected(door) {
  if (!map || !door.centroid) return;
  const layout = panelLayout(window.innerWidth);
  const canvas = map.getCanvas();
  const width = canvas.clientWidth;
  const height = canvas.clientHeight;
  const point = map.project(door.centroid);

  let dx = 0;
  let dy = 0;
  if (layout === 'side') {
    const rightEdge = width - els.panel.offsetWidth - 24;
    if (point.x > rightEdge) dx = point.x - rightEdge;
  } else {
    const bottomEdge = height * 0.28;
    if (point.y > bottomEdge) dy = point.y - bottomEdge;
  }
  if (dx || dy) map.panBy([dx, dy], { duration: 260 });
}

function renderPanel(panel) {
  els.panel.dataset.layout = panelLayout(window.innerWidth);
  const reopening = els.panelPin.textContent !== `PIN ${panel.pin}`;
  const scrollTop = reopening ? 0 : els.panelBody.scrollTop;

  els.panelAddr.textContent = panel.situs;
  els.panelPin.textContent = `PIN ${panel.pin}`;

  const body = els.panelBody;
  clear(body);

  if (panel.state === 'unscored') {
    renderExclusion(body, panel);
  } else {
    renderScored(body, panel);
  }

  // The math toggle only means something once the breakdown has arrived.
  els.toggleMath.hidden = !panel.breakdown;
  els.toggleMath.textContent = showMath ? 'Hide math' : 'Show math';

  els.panel.hidden = false;
  // Re-rendering in place when the detail lands must not throw the reader back
  // to the top of a panel they have already started scrolling.
  body.scrollTop = scrollTop;
}

/* ── Street View slot ────────────────────────────────────────────────────── */

/**
 * The photo under the address: context, never evidence.
 *
 * Four states, one slot, one height — the frame is fixed in CSS so the panel
 * does not jump as the skeleton becomes a photo (or a designed absence). The
 * image element is created fresh per render and never persisted anywhere:
 * the server proxies with `no-store`, and this side keeps no copy either.
 */
function renderStreetView() {
  const slot = els.panelPhoto;
  clear(slot);
  if (!streetView) {
    slot.hidden = true;
    return;
  }
  slot.hidden = false;
  slot.dataset.state = streetView.state;

  const frame = el('div', 'photo__frame');

  if (streetView.state === 'loading') {
    frame.appendChild(el('div', 'photo__skeleton', streetView.message));
  } else if (streetView.state === 'available') {
    const image = new Image();
    image.className = 'photo__img';
    image.alt = 'Street View of the selected house';
    image.src = `${API_BASE}${streetView.imageUrl}`;
    // The metadata said OK but the image call can still fail; that failure is
    // the error state, not a broken-image glyph.
    image.onerror = () => {
      streetView = { ...streetView, state: 'error', message: 'Street View couldn’t load' };
      renderStreetView();
    };
    frame.appendChild(image);

    const view = el('button', 'photo__expand', 'View house ⤢');
    view.type = 'button';
    view.addEventListener('click', openPhotoFull);
    frame.appendChild(view);
  } else {
    // `unavailable` and `error` are both a message in the frame; only the
    // wording (and the retry-worthiness it implies) differs.
    frame.appendChild(el('div', 'photo__absent', streetView.message));
  }
  slot.appendChild(frame);

  const meta = el('div', 'photo__meta');
  const left = el(
    'div',
    'photo__caption',
    [streetView.captureLabel, streetView.attribution].filter(Boolean).join(' · ')
  );
  meta.appendChild(left);
  if (streetView.mapsUrl) {
    const link = el('a', 'photo__maps', 'Open in Google Maps ↗');
    link.href = streetView.mapsUrl;
    link.target = '_blank';
    link.rel = 'noopener';
    meta.appendChild(link);
  }
  if (left.textContent || streetView.mapsUrl) slot.appendChild(meta);
}

/** Frame the photo full-screen over the map; Back (or Escape) unwinds it. */
function openPhotoFull() {
  if (!streetView || streetView.state !== 'available') return;
  clear(els.photoFrame);
  const image = new Image();
  image.className = 'photofull__img';
  image.alt = 'Street View of the selected house';
  image.src = `${API_BASE}${streetView.imageUrl}?view=full`;
  els.photoFrame.appendChild(image);

  els.photoAddr.textContent = els.panelAddr.textContent;
  els.photoSub.textContent = [streetView.captureLabel].filter(Boolean).join('');
  if (streetView.mapsUrl) {
    els.photoMaps.href = streetView.mapsUrl;
    els.photoMaps.hidden = false;
  } else {
    els.photoMaps.hidden = true;
  }
  els.photoFull.hidden = false;
}

function closePhotoFull() {
  if (els.photoFull.hidden) return;
  els.photoFull.hidden = true;
  clear(els.photoFrame);
}

els.photoBack.addEventListener('click', closePhotoFull);

/** R9.4: the exclusion state, tied back to the coverage count it explains. */
function renderExclusion(body, panel) {
  body.appendChild(el('div', 'exclusion', panel.exclusionMessage));

  const excluded = doors.filter((door) => door.properties.score === null).length;
  const scored = doors.length - excluded;
  if (excluded > 0) {
    body.appendChild(
      el(
        'div',
        'exclusion',
        `This door is one of the ${excluded} excluded from the ${scored}-door coverage count.`
      )
    );
  }
}

function renderScored(body, panel) {
  const line = el('div', 'scoreline');
  const value = el('div', 'scoreline__value', panel.score);
  // The ramp's low end is nearly white, so the number is drawn no lighter than
  // score 30 — the fill can be pale, the figure the rep reads cannot.
  value.style.color = scoreColor(Math.max(30, panel.score));
  line.appendChild(value);

  const conf = el('div', 'scoreline__conf', 'confidence: ');
  const confValue = el('b', panel.confidence === 'low' ? 'is-low' : null, panel.confidence ?? 'unknown');
  conf.appendChild(confValue);
  line.appendChild(conf);
  body.appendChild(line);

  if (panel.confidence === 'low') {
    body.appendChild(
      el(
        'div',
        'notice',
        'Low confidence — part of the county record is missing for this parcel, ' +
          'so the score is built from partial signals. The data-gap line below says which.'
      )
    );
  }

  body.appendChild(el('div', 'sectionhead', 'Why this score'));
  if (panel.rows.length === 0) {
    body.appendChild(
      el('div', 'evidence__empty', 'No qualifying signals were found for this parcel.')
    );
  } else {
    const list = el('div', 'evidence');
    for (const row of panel.rows) list.appendChild(evidenceRow(row));
    body.appendChild(list);
  }

  const imagery = panel.rows.filter((row) => row.imagery);
  if (imagery.length) {
    body.appendChild(el('div', 'sectionhead', 'Imagery evidence'));
    const strip = el('div', 'thumbs');
    for (const row of imagery) strip.appendChild(thumbnail(row));
    body.appendChild(strip);
  }

  // R9.3: say so when nothing came from imagery, rather than leaving a gap the
  // rep has to interpret.
  if (panel.footer) body.appendChild(el('div', 'nofooter', panel.footer));

  // R30: what could not be measured for this door, in the V2 gap vocabulary —
  // each one a neutral default said out loud, never a silent hole.
  if (panel.gaps && panel.gaps.length) {
    body.appendChild(el('div', 'sectionhead', 'Data gaps'));
    const gaps = el('div', 'evidence');
    for (const gap of panel.gaps) {
      const row = el('div', 'evidence__row');
      row.appendChild(el('div', 'evidence__pts is-context', '·'));
      const text = el('div', 'evidence__text');
      text.appendChild(el('div', 'evidence__sentence', gap.message ?? gap.type.replace(/_/g, ' ')));
      row.appendChild(text);
      gaps.appendChild(row);
    }
    body.appendChild(gaps);
  }

  // R7.2: what the rep says out loud — the same words the route list shows,
  // because both come from the server's `talk_track_for`.
  //
  // The opener and the branches are drawn as two different things because they
  // are read at two different moments. The opener runs until the homeowner
  // speaks; the branches are alternatives to glance at once they have, and only
  // one of them ever gets said. Setting them as one paragraph would hand the
  // rep a script to recite over the person they knocked for.
  if (panel.talkTrack) {
    const block = el('div', 'talktrack');
    block.appendChild(el('div', 'talktrack__head', 'Rep talk track'));
    block.appendChild(el('div', 'talktrack__line', `“${panel.talkTrack}”`));

    const branches = panel.talkTrackBranches ?? [];
    if (branches.length) {
      block.appendChild(el('div', 'talktrack__cue', 'Then, depending on the answer'));
      const list = el('div', 'talktrack__branches');
      for (const branch of branches) {
        const row = el('div', 'talktrack__branch');
        row.appendChild(el('div', 'talktrack__trigger', branch.trigger));
        row.appendChild(el('div', 'talktrack__reply', `“${branch.line}”`));
        list.appendChild(row);
      }
      block.appendChild(list);
    }
    body.appendChild(block);
  }

  if (panel.breakdown && showMath) body.appendChild(renderBreakdown(panel.breakdown));
}

/** Frame 2b: each V2 category as a bar against its cap, then the arithmetic. */
function renderBreakdown(breakdown) {
  const block = el('div', 'breakdown');
  block.appendChild(el('div', 'sectionhead', 'Score breakdown'));

  const categories = el('div', 'breakdown__groups');
  for (const category of breakdown.categories) {
    const row = el('div');

    const head = el('div', 'breakdown__row-head');
    head.appendChild(el('span', 'breakdown__name', category.label));
    head.appendChild(el('span', 'breakdown__value', `${category.points} / ${category.cap}`));
    row.appendChild(head);

    const track = el('div', 'breakdown__track');
    const fill = el('div', 'breakdown__fill');
    // Proportion of the category's own cap, so a 12/25 capacity and a 15/30
    // fit read as comparably full bars.
    const share = category.cap === 0 ? 0 : Math.abs(category.points / category.cap);
    fill.style.width = `${Math.round(Math.min(1, share) * 100)}%`;
    fill.style.background = scoreColor(40 + share * 60);
    track.appendChild(fill);
    row.appendChild(track);

    categories.appendChild(row);
  }
  block.appendChild(categories);

  // The reconciling arithmetic: base + mover lift + rental modifier +
  // adjustment = score — with the clamp said out loud when one applied.
  block.appendChild(el('div', 'breakdown__math', breakdown.mathLine));
  if (breakdown.clamp) {
    block.appendChild(
      el(
        'div',
        'breakdown__math',
        breakdown.clamp === 'floor'
          ? 'floored at 0 — the adjustment above is the clamp made visible'
          : 'capped at 100 — the adjustment above is the clamp made visible'
      )
    );
  }
  return block;
}

function evidenceRow(row) {
  const wrapper = el('div', 'evidence__row');

  const kind = row.hasSign ? (row.points > 0 ? 'is-pos' : 'is-neg') : 'is-context';
  // A zero-point line is context, not a contribution: it wears a dot, not a badge.
  wrapper.appendChild(el('div', `evidence__pts ${kind}`, row.hasSign ? row.signed : '·'));

  const text = el('div', 'evidence__text');
  text.appendChild(el('div', 'evidence__sentence', row.reason));
  text.appendChild(
    el(
      'div',
      'evidence__source',
      row.source ? `${row.source} · fetched ${row.retrieved}` : row.label
    )
  );
  wrapper.appendChild(text);

  return wrapper;
}

function thumbnail(row) {
  const card = el('button', 'thumb');
  card.type = 'button';

  const frame = el('div', 'thumb__img');
  const image = new Image();
  image.alt = '';
  image.src = row.imagery.image_url;
  // A tile that will not load must not leave an empty box: the hatch behind it
  // is the placeholder, so failure degrades to the prototype's own stand-in.
  image.addEventListener('error', () => image.remove());
  frame.appendChild(image);
  card.appendChild(frame);

  const meta = el('div', 'thumb__meta', row.label);
  meta.appendChild(document.createElement('br'));
  meta.appendChild(el('span', null, imageryMeta(row.imagery)));
  card.appendChild(meta);

  card.addEventListener('click', () => openLightbox(row));
  return card;
}

function imageryMeta(imagery) {
  const parts = [];
  if (imagery.model_confidence !== null && imagery.model_confidence !== undefined) {
    parts.push(`conf ${Number(imagery.model_confidence).toFixed(2)}`);
  }
  if (imagery.capture_date) parts.push(`captured ${imagery.capture_date}`);
  return parts.join(' · ');
}

/* ── Imagery lightbox (R7.1) ─────────────────────────────────────────────── */

function openLightbox(row) {
  clear(els.lightboxFrame);
  const image = new Image();
  image.alt = `${row.label} imagery`;
  image.src = row.imagery.image_url;
  image.addEventListener('error', () => {
    clear(els.lightboxFrame);
    els.lightboxFrame.textContent = 'imagery tile unavailable';
  });
  els.lightboxFrame.appendChild(image);

  els.lightboxLabel.textContent = row.reason;
  els.lightboxSub.textContent = imageryMeta(row.imagery);
  els.lightbox.hidden = false;
}

function closeLightbox() {
  els.lightbox.hidden = true;
  clear(els.lightboxFrame);
}

$('lightbox-close').addEventListener('click', closeLightbox);
els.lightbox.addEventListener('click', (event) => {
  if (event.target === els.lightbox) closeLightbox();
});

/* ── Route planner (wireframe frames 4–4c, R10.1–R10.3) ──────────────────── */

/** "1h54m" — a duration, never a time of day (R10.2). */
function formatDuration(minutes) {
  const total = Math.max(0, Math.round(minutes ?? 0));
  return `${Math.floor(total / 60)}h${String(total % 60).padStart(2, '0')}m`;
}

function openRoutePanel() {
  els.routePanel.hidden = false;
  // Two bottom sheets cannot share the bottom of a phone, so the evidence panel
  // yields to the planner and the parcel outline is what keeps the door findable.
  if (panelLayout(window.innerWidth) === 'sheet') closePanel();
  renderRouteChrome();
}

function closeRoutePanel() {
  els.routePanel.hidden = true;
  pickingStart = false;
  renderRouteChrome();
}

/** The floating furniture that depends on route state, in one place. */
function renderRouteChrome() {
  els.pickHint.hidden = !pickingStart;
  els.routeBar.hidden = !(routeView && !routeView.isEmpty && els.routePanel.hidden && !walk);
  els.resumeBanner.hidden = !pendingResume || Boolean(walk);
  if (map) map.getCanvas().style.cursor = pickingStart ? 'crosshair' : 'grab';
}

els.hoursOptions.append(
  ...HOURS_OPTIONS.map((value) => {
    const button = el('button', 'segmented__btn', value);
    button.type = 'button';
    button.dataset.hours = value;
    button.setAttribute('aria-pressed', String(value === hours));
    button.addEventListener('click', () => {
      hours = value;
      renderHours();
      // Frame 4c: the sliders stay live — changing the budget re-asks the
      // planner rather than trimming the route the browser already has.
      if (routeView) runPlan({ hours: Number(hours) });
    });
    return button;
  })
);

function renderHours() {
  for (const button of els.hoursOptions.children) {
    button.setAttribute('aria-pressed', String(button.dataset.hours === hours));
  }
}

els.routeDoors.addEventListener('change', () => {
  const target = Math.max(1, Math.min(60, Number(els.routeDoors.value) || 20));
  els.routeDoors.value = String(target);
  if (routeView) runPlan({ maxDoors: target });
});

els.pickStart.addEventListener('click', () => {
  pickingStart = true;
  // The map is the control now, so the panel gets out of its way.
  els.routePanel.hidden = true;
  renderRouteChrome();
});

/**
 * Accept a parking spot, or explain why not.
 *
 * A start miles from the territory plans an empty walk and looks like a bug, so
 * a click outside the parcels' own bounds (plus a short walk's grace) is
 * refused with a reason rather than planned from.
 */
function setStartPoint(point) {
  const bounds = boundsOfDoors();
  if (bounds) {
    const grace = 0.02; // ~2 km, further than anyone parks from their territory
    const [west, south] = [bounds.getWest() - grace, bounds.getSouth() - grace];
    const [east, north] = [bounds.getEast() + grace, bounds.getNorth() + grace];
    if (point[0] < west || point[0] > east || point[1] < south || point[1] > north) {
      showToast('Start must be near the territory');
      return;
    }
  }

  startPoint = point;
  pickingStart = false;
  els.pickStart.textContent = 'Start ✓ (move)';
  els.planGo.disabled = false;
  openRoutePanel();
  refreshMapData();
}

let planToken = 0;

/**
 * Ask the planner, and keep the panel honest about what it is doing.
 *
 * `planner.plan` remembers the inputs the rep did not touch, so this passes
 * only what changed — and the token is what makes Cancel mean something: a
 * superseded answer is dropped rather than rendered over a newer one.
 */
async function runPlan(changes) {
  if (!startPoint) return;

  const token = ++planToken;
  planning = true;
  els.routeError.hidden = true;
  renderPlanButton();

  try {
    const view = await planner.plan(changes);
    if (token !== planToken) return;
    routeView = view;
  } catch (error) {
    if (token !== planToken) return;
    els.routeError.textContent =
      `Could not plan a route — ${error.message}. The doors are still on the map.`;
    els.routeError.hidden = false;
  } finally {
    if (token === planToken) {
      planning = false;
      renderPlanButton();
      renderRouteList();
      refreshMapData();
      renderRouteChrome();
    }
  }
}

function renderPlanButton() {
  els.planGo.textContent = planning ? 'Routing 540 candidates…' : 'Plan';
  els.planGo.disabled = planning || !startPoint;
  els.cancelPlan.hidden = !planning;
}

els.planGo.addEventListener('click', () =>
  runPlan({
    hours: Number(hours),
    start: startPoint,
    maxDoors: Math.max(1, Math.min(60, Number(els.routeDoors.value) || 20)),
  })
);

els.cancelPlan.addEventListener('click', () => {
  // Abandon the answer, keep whatever route was already on screen.
  planToken += 1;
  planning = false;
  renderPlanButton();
});

$('route-close').addEventListener('click', closeRoutePanel);
$('reopen-route').addEventListener('click', openRoutePanel);
$('clear-route').addEventListener('click', () => {
  routeView = null;
  startPoint = null;
  els.pickStart.textContent = 'Set start on map';
  els.planGo.disabled = true;
  renderRouteList();
  refreshMapData();
  renderRouteChrome();
});

function renderRouteList() {
  const list = els.routeList;
  clear(list);

  els.routeEmpty.hidden = true;
  els.routeFoot.hidden = true;
  if (!routeView) return;

  if (routeView.isEmpty) {
    // Frame 4b: never a blank panel — say what to change.
    els.routeEmpty.textContent =
      `No doors reachable in ${hours}h from this start — widen hours or move the start point.`;
    els.routeEmpty.hidden = false;
    return;
  }

  for (const row of routeView.rows) list.appendChild(routeRow(row));

  const scores = routeView.rows.map((row) => row.score);
  const average = Math.round(scores.reduce((sum, score) => sum + score, 0) / scores.length);
  els.routeSummary.textContent =
    routeView.totalMinutes === null
      ? `doors ${routeView.rows.length} · shared route · avg score ${average}`
      : `doors ${routeView.rows.length} · est total ${formatDuration(routeView.totalMinutes)}` +
        ` (walking estimate) · avg score ${average}`;
  els.routeDisclosure.textContent =
    routeView.estimateDisclosure ||
    'Shared link — plan from a start point for walking estimates.';
  els.routeFoot.hidden = false;
}

function routeRow(row) {
  const item = el('button', 'routerow');
  item.type = 'button';

  item.appendChild(el('div', 'routerow__n', row.n));

  const body = el('div', 'routerow__body');
  const title = el('div', 'routerow__title');
  title.appendChild(el('span', 'routerow__addr', row.address));
  const score = el('span', 'routerow__score', row.score);
  score.style.color = scoreColor(Math.max(35, row.score));
  title.appendChild(score);
  body.appendChild(title);

  const chip = chipText(row.reasonChip);
  if (chip) body.appendChild(el('div', 'routerow__chip', chip));

  if (row.talkTrack) body.appendChild(el('div', 'routerow__talk', row.talkTrack));

  if (typeof row.cumulativeMinutes === 'number') {
    body.appendChild(
      el(
        'div',
        'routerow__timing',
        `${row.elapsedLabel} into route · ${Math.max(1, Math.round(row.walkMinutes))} min walk from prev`
      )
    );
  }
  item.appendChild(body);

  // Frame 4c: "the rep knows a house is vacant or hostile."
  const exclude = el('button', 'routerow__exclude', '✕');
  exclude.type = 'button';
  exclude.title = 'Exclude this door and re-plan';
  exclude.setAttribute('aria-label', `Exclude ${row.address} and re-plan`);
  exclude.addEventListener('click', async (event) => {
    event.stopPropagation();
    planning = true;
    renderPlanButton();
    try {
      routeView = await planner.excludeStop(row.pin);
    } finally {
      planning = false;
      renderPlanButton();
      renderRouteList();
      refreshMapData();
    }
  });
  item.appendChild(exclude);

  // Frame 4d: tapping a row opens the door's evidence without leaving the route.
  item.addEventListener('click', () => selectDoor(row.pin));
  return item;
}

$('copy-route').addEventListener('click', () => {
  if (routeView) copyAsText(routeView.rows, emit);
});
$('share-route').addEventListener('click', () => {
  if (routeView) copyShareLink(routeView.rows, location.href, emit);
});

/* ── Walk mode (wireframe frame 4d, R10.4) ───────────────────────────────── */

function startWalk(rows, resume) {
  walk = createWalk(rows, resume ? { resume } : {});
  pendingResume = null;
  els.routePanel.hidden = true;
  closePanel();
  els.walk.hidden = false;
  refreshMapData();
  renderWalk();
  renderRouteChrome();
}

function leaveWalk() {
  if (walk) walk.exit();
  walk = null;
  els.walk.hidden = true;
  refreshMapData();
  renderRouteChrome();
}

function renderWalk() {
  if (!walk) return;

  const rows = routeView ? routeView.rows : [];
  els.walkProgress.textContent = walk.finished ? 'Route complete' : walk.progressLabel;
  els.walkBar.style.width = `${rows.length ? Math.round((walk.index / rows.length) * 100) : 0}%`;

  const finished = walk.finished;
  els.walkStop.hidden = finished;
  els.walkActions.hidden = finished;
  els.walkUpcoming.hidden = finished;
  els.walkFinished.hidden = !finished;

  if (finished) {
    els.walkLeft.textContent = '';
    els.walkSummary.textContent = walk.summary;
    return;
  }

  const current = walk.current;
  // Minutes left is what remains of the plan from here, not a clock (R10.2).
  const done = walk.index > 0 ? rows[walk.index - 1].cumulativeMinutes : 0;
  const remaining =
    typeof routeView?.totalMinutes === 'number' && typeof done === 'number'
      ? Math.max(0, Math.round(routeView.totalMinutes - done))
      : null;
  els.walkLeft.textContent = remaining === null ? '' : `${remaining} min left`;

  clear(els.walkMeta);
  els.walkMeta.append(`STOP ${current.n} · score `);
  const score = el('b', null, current.score);
  score.style.color = scoreColor(Math.max(35, current.score));
  els.walkMeta.appendChild(score);
  if (typeof current.walkMinutes === 'number') {
    els.walkMeta.append(` · ${Math.max(1, Math.round(current.walkMinutes))} min walk`);
  }

  els.walkAddr.textContent = current.address;

  const chip = chipText(current.reasonChip);
  els.walkChip.hidden = !chip;
  els.walkChip.textContent = chip ?? '';

  els.walkTalk.textContent = current.talkTrack ? `“${current.talkTrack}”` : '';

  clear(els.walkUpcoming);
  const upcoming = rows.slice(walk.index + 1, walk.index + 6);
  if (upcoming.length) {
    els.walkUpcoming.appendChild(el('div', 'walk__upcoming-head', 'Up next'));
    for (const row of upcoming) {
      const next = el('div', 'walk__next');
      next.appendChild(el('span', 'walk__next-n', row.n));
      next.appendChild(el('span', 'walk__next-addr', row.address));
      const score = el('span', 'walk__next-score', row.score);
      score.style.color = scoreColor(Math.max(35, row.score));
      next.appendChild(score);
      els.walkUpcoming.appendChild(next);
    }
  }
}

$('start-walk').addEventListener('click', () => {
  if (routeView && !routeView.isEmpty) startWalk(routeView.rows);
});
$('walk-done').addEventListener('click', () => {
  walk.done();
  refreshMapData();
  renderWalk();
});
$('walk-skip').addEventListener('click', () => {
  walk.skip();
  refreshMapData();
  renderWalk();
});
$('walk-exit').addEventListener('click', leaveWalk);
$('walk-back').addEventListener('click', leaveWalk);

/* ── Resuming an interrupted walk (R10.4) ────────────────────────────────── */

/**
 * Offer to pick a walk back up — with Discard beside Resume, because a rep who
 * finished yesterday must not be trapped in yesterday's route.
 */
function offerResume() {
  const saved = readWalk();
  pendingResume = resumeOffer(saved);
  if (!pendingResume) return;

  els.resumeLabel.textContent = pendingResume.label;
  renderRouteChrome();

  $('resume-walk').onclick = () => {
    // The stored rows are the route: a resumed walk must not depend on the
    // planner still being able to reproduce it from the same start.
    routeView = {
      rows: saved.rows,
      totalMinutes: saved.rows.at(-1)?.cumulativeMinutes ?? null,
      estimateDisclosure: '',
      isEmpty: saved.rows.length === 0,
    };
    startWalk(saved.rows, saved);
  };
  $('discard-walk').onclick = () => {
    clearWalk();
    pendingResume = null;
    renderRouteChrome();
  };
}

/* ── A shared route, opened from the URL (R10.4) ─────────────────────────── */

/**
 * Rebuild the route a share link names.
 *
 * The link carries the order and nothing else, so the walking estimates are
 * genuinely unknown until someone plans from a start point — and the panel says
 * so rather than inventing legs. Talk tracks come from the door endpoint, which
 * is the only thing entitled to write them (R7.2).
 */
async function openSharedRoute() {
  // R27/R30: a link minted under the dead V1 contract must never replay as a
  // V2 route — the token says which vintage it is, and a stale one prompts a
  // refresh instead of opening a wrong walk.
  if (shareTokenVersion(location.hash) === 'v1') {
    showToast(REFRESH_PROMPT);
    return;
  }

  const pins = (await readShare(location.hash)).slice(0, 60);
  if (pins.length === 0) return;

  const details = await Promise.all(
    pins.map(async (pin) => {
      try {
        const response = await fetch(`${API_BASE}/door/${encodeURIComponent(pin)}`);
        return response.ok ? await response.json() : null;
      } catch {
        return null;
      }
    })
  );

  const rows = details
    .map((detail, index) =>
      detail
        ? {
            n: index + 1,
            pin: detail.PAMS_PIN,
            address: detail.situs,
            score: detail.score ?? 0,
            walkMinutes: null,
            cumulativeMinutes: null,
            elapsedLabel: null,
            talkTrack: detail.talk_track ?? null,
            talkTrackBranches: detail.talk_track_branches ?? [],
            reasonChip: detail.reason_chip ?? null,
          }
        : null
    )
    .filter(Boolean)
    .map((row, index) => ({ ...row, n: index + 1 }));

  if (rows.length === 0) return;

  routeView = { rows, totalMinutes: null, estimateDisclosure: '', isEmpty: false };
  renderRouteList();
  refreshMapData();
  openRoutePanel();
  showToast('Shared route opened');
}

/* ── Header actions ──────────────────────────────────────────────────────── */

$('plan-route').addEventListener('click', () => {
  if (els.routePanel.hidden) openRoutePanel();
  else closeRoutePanel();
});
// "Data & Ethics" is a plain link to ethics.html — no handler needed.

/* ── Boot ────────────────────────────────────────────────────────────────── */

window.addEventListener('resize', () => {
  if (selectedPin) els.panel.dataset.layout = panelLayout(window.innerWidth);

  // `fitBounds` runs once on load, so a desktop↔mobile resize used to leave the
  // territory small and off-centre until a reload. Re-measure and re-fit — but
  // only while the rep is looking at the whole territory: refitting under a
  // planned route or an open door would throw away the view they chose.
  if (map) {
    map.resize();
    if (!routeView && !selectedPin) {
      const bounds = boundsOfDoors();
      if (bounds) map.fitBounds(bounds, { padding: FIT_PADDING, duration: 0 });
    }
  }
  scheduleLabels();
});

loadDoors();

