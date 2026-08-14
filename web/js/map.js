/**
 * The browser layer: everything that touches the DOM, MapLibre or the network.
 *
 * The rules this screen obeys — the colour ramp, the score filter, the coverage
 * readout, the evidence panel's shape, the load/error/retry machine — live in
 * `ramp.js`, `filter.js`, `panel.js` and `state.js`, which are DOM-free and
 * unit-tested. This file is the wiring: it fetches the published run, hands it
 * to those modules, and renders whatever they say. Nothing here decides what a
 * score looks like or which doors a range selects.
 *
 * Loaded as `<script type="module">`, no build step, no bundler.
 */

import { scoreColor, UNSCORED_COLOR, RAMP_CSS_GRADIENT } from './ramp.js';
import { coverageText, filterDoors } from './filter.js';
import { buildPanel, copyAddress, panelLayout } from './panel.js';
import { createMapState } from './state.js';

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

/** Out-of-range parcels stay on the map, dimmed, so the territory keeps its shape. */
const DIM_OPACITY = 0.14;

const TOAST_MS = 1800;

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
  labels: $('labels'),
  toast: $('toast'),
  panel: $('panel'),
  panelAddr: $('panel-addr'),
  panelPin: $('panel-pin'),
  panelBody: $('panel-body'),
  panelClose: $('panel-close'),
  copyAddress: $('copy-address'),
  lightbox: $('lightbox'),
  lightboxFrame: $('lightbox-frame'),
  lightboxLabel: $('lightbox-label'),
  lightboxSub: $('lightbox-sub'),
};

/* ── Application state ───────────────────────────────────────────────────── */

const machine = createMapState();

/** @type {{properties: object, geometry: object|null, centroid: [number, number]|null}[]} */
let doors = [];
/** Doors currently passing the filter, for labelling. */
let visible = [];
let range = [0, 100];
let selectedPin = null;
let map = null;
let toastTimer = null;
let labelFrame = null;

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

  return {
    type: 'FeatureCollection',
    features: doors
      .filter((door) => door.geometry)
      .map((door) => {
        const { PAMS_PIN, score } = door.properties;
        return {
          type: 'Feature',
          geometry: door.geometry,
          properties: {
            PAMS_PIN,
            _color: scoreColor(score),
            _opacity: inRange.has(PAMS_PIN) ? 1 : DIM_OPACITY,
            _scored: score === null ? 0 : 1,
          },
        };
      }),
  };
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

  map.on('load', () => {
    // The map is built the moment the doors arrive, which can be before the
    // browser has laid the freshly-unhidden map screen out. Measuring again
    // here is what keeps `fitBounds` below fitting the real viewport rather
    // than MapLibre's 400×300 fallback.
    map.resize();

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

    const bounds = boundsOfDoors();
    if (bounds) map.fitBounds(bounds, { padding: 48, duration: 0 });

    scheduleLabels();
  });

  map.on('click', (event) => {
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
    renderLabels();
  });
}

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
    map.getSource('doors').setData(sourceData());
    scheduleLabels();
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
  if (map && map.getLayer('doors-selected')) {
    map.setFilter('doors-selected', ['==', ['get', 'PAMS_PIN'], pin]);
  }
  renderPanel(buildPanel(door.properties));
  revealSelected(door);
}

function closePanel() {
  selectedPin = null;
  els.panel.hidden = true;
  if (map && map.getLayer('doors-selected')) {
    map.setFilter('doors-selected', ['==', ['get', 'PAMS_PIN'], '']);
  }
}

els.panelClose.addEventListener('click', closePanel);
document.addEventListener('keydown', (event) => {
  if (event.key !== 'Escape') return;
  if (!els.lightbox.hidden) closeLightbox();
  else closePanel();
});

els.copyAddress.addEventListener('click', () => {
  const door = byPin(selectedPin);
  if (door) copyAddress(door.properties, emit);
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
  els.panelAddr.textContent = panel.situs;
  els.panelPin.textContent = `PIN ${panel.pin}`;

  const body = els.panelBody;
  clear(body);

  if (panel.state === 'unscored') {
    renderExclusion(body, panel);
  } else {
    renderScored(body, panel);
  }

  els.panel.hidden = false;
  body.scrollTop = 0;
}

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
}

function evidenceRow(row) {
  const wrapper = el('div', 'evidence__row');

  const kind = row.hasSign ? (row.points > 0 ? 'is-pos' : 'is-neg') : 'is-context';
  // A zero-point line is context, not a contribution: it wears a dot, not a badge.
  wrapper.appendChild(el('div', `evidence__pts ${kind}`, row.hasSign ? row.signed : '·'));

  const text = el('div', 'evidence__text');
  text.appendChild(el('div', 'evidence__sentence', row.sentence));
  text.appendChild(el('div', 'evidence__source', `${row.source} · fetched ${row.retrieved}`));
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

  const meta = el('div', 'thumb__meta', row.type.replace(/_/g, ' '));
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
  image.alt = `${row.type.replace(/_/g, ' ')} imagery`;
  image.src = row.imagery.image_url;
  image.addEventListener('error', () => {
    clear(els.lightboxFrame);
    els.lightboxFrame.textContent = 'imagery tile unavailable';
  });
  els.lightboxFrame.appendChild(image);

  els.lightboxLabel.textContent = row.sentence;
  els.lightboxSub.textContent = [row.source, imageryMeta(row.imagery)]
    .filter(Boolean)
    .join(' · ');
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

/* ── Next-ticket surfaces, present because the approved design has them ──── */

$('plan-route').addEventListener('click', () =>
  showToast('Route planning arrives in the next build')
);
$('open-about').addEventListener('click', () =>
  showToast('Data & Ethics page arrives in the next build')
);

/* ── Boot ────────────────────────────────────────────────────────────────── */

window.addEventListener('resize', () => {
  if (selectedPin) els.panel.dataset.layout = panelLayout(window.innerWidth);
  scheduleLabels();
});

loadDoors();

