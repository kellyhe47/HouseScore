import { test } from 'node:test';
import assert from 'node:assert/strict';

import { buildPanel, copyAddress, panelLayout, GAP_MESSAGES } from './panel.js';
import {
  scoredDoor,
  visionDoor,
  unscoredDoor,
  noEvidenceDoor,
  detailedDoor,
  clampedDoor,
} from './test-fixtures.js';

// U+2212 MINUS SIGN — the typographic minus the prototype's panel uses, not ASCII '-'.
const MINUS = '−';

// Copy that is user-visible contract (R9.4 / R9.3, docs/prototype-decoded.html:541,510).
const EXCLUSION_COPY = 'Not scored — parcel record incomplete in county data.';
const NO_IMAGERY_FOOTER = 'No imagery signals for this parcel';

// R2: the three V2 categories and their caps — the only subtotal vocabulary
// any surface may use (R30).
const V2_CATEGORIES = [
  ['project', 45],
  ['capacity', 25],
  ['fit', 48],
];

// ---------- buildPanel: evidence rows ----------

test('buildPanel orders evidence rows by descending absolute points', () => {
  // scoredDoor input order: 15, 15, 8, 5, −3, 5, 0 — the −3 cap adjustment
  // sinks below both 5s, the zero context row reads last.
  const panel = buildPanel(scoredDoor());
  assert.deepEqual(
    panel.rows.map((r) => r.points),
    [15, 15, 8, 5, 5, -3, 0]
  );
});

test('buildPanel keeps input order for rows with equal absolute points', () => {
  const door = scoredDoor({
    evidence: [
      { type: 'project_active', points: 15, reason: 'first', imagery: null },
      { type: 'capacity_territory_percentile', points: -15, reason: 'second', imagery: null },
    ],
  });
  assert.deepEqual(buildPanel(door).rows.map((r) => r.reason), ['first', 'second']);
});

test('buildPanel renders a positive row with a leading plus', () => {
  const row = buildPanel(scoredDoor()).rows.find((r) => r.points === 8);
  assert.equal(row.signed, '+8');
  assert.equal(row.hasSign, true);
});

test('buildPanel renders a negative row with the typographic minus', () => {
  const row = buildPanel(scoredDoor()).rows.find((r) => r.points === -3);
  assert.equal(row.signed, MINUS + '3');
  assert.equal(row.signed.charCodeAt(0), 0x2212, 'must be U+2212 MINUS SIGN, not ASCII hyphen');
  assert.equal(row.hasSign, true);
});

test('buildPanel renders a zero-point context row without a sign badge (R7.3)', () => {
  const row = buildPanel(scoredDoor()).rows.find((r) => r.points === 0);
  assert.equal(row.hasSign, false);
  assert.equal(row.signed, null);
});

test('buildPanel carries the V2 evidence reason onto its row', () => {
  const door = scoredDoor();
  const source = door.evidence.find((e) => e.type === 'fit_lot');
  const row = buildPanel(door).rows.find((r) => r.type === 'fit_lot');
  assert.equal(row.reason, source.reason);
  assert.equal(row.imagery, null);
});

test('buildPanel carries the imagery attachment onto its row', () => {
  const door = visionDoor();
  const row = buildPanel(door).rows.find((r) => r.type === 'fit_pool');
  assert.deepEqual(row.imagery, door.evidence.find((e) => e.type === 'fit_pool').imagery);
});

test('buildPanel does not mutate the door it is given', () => {
  const door = detailedDoor();
  const before = JSON.parse(JSON.stringify(door));
  buildPanel(door);
  assert.deepEqual(door, before);
});

test('buildPanel returns plain data, not HTML or callbacks', () => {
  const panel = buildPanel(scoredDoor());
  assert.equal(typeof panel, 'object');
  assert.deepEqual(JSON.parse(JSON.stringify(panel)), panel, 'view model must survive a JSON round trip');
});

// ---------- buildPanel: scored header ----------

test('buildPanel exposes the scored state and door identity', () => {
  const door = scoredDoor();
  const panel = buildPanel(door);
  assert.equal(panel.state, 'scored');
  assert.equal(panel.pin, door.PAMS_PIN);
  assert.equal(panel.situs, door.situs);
  assert.equal(panel.score, door.score);
  assert.equal(panel.confidence, 'normal');
  assert.equal(panel.exclusionMessage, null);
});

test('buildPanel handles a score of 0 as a scored door', () => {
  const panel = buildPanel(clampedDoor());
  assert.equal(panel.state, 'scored');
  assert.equal(panel.score, 0);
  assert.equal(panel.exclusionMessage, null);
});

test('buildPanel handles a score of 100 as a scored door', () => {
  // A full-cap base of 80 blended to the top of the priority band: 80 + 20 = 100.
  const panel = buildPanel(
    scoredDoor({
      score: 100,
      base: 80,
      categories: { project: 25, capacity: 25, fit: 30 },
      mover: { eligible: true, days_since_move: 30, strength: 1.0 },
      mover_lift: 20,
      adjustment: 0,
      evidence: [],
    })
  );
  assert.equal(panel.state, 'scored');
  assert.equal(panel.score, 100);
});

// ---------- buildPanel: exclusion state (R9.4) ----------

test('a door with score null yields the exclusion panel state', () => {
  const panel = buildPanel(unscoredDoor());
  assert.equal(panel.state, 'unscored');
  assert.equal(panel.score, null);
});

test('the exclusion state carries the prototype copy verbatim', () => {
  assert.equal(buildPanel(unscoredDoor()).exclusionMessage, EXCLUSION_COPY);
});

test('the exclusion state still identifies the door', () => {
  const door = unscoredDoor();
  const panel = buildPanel(door);
  assert.equal(panel.pin, door.PAMS_PIN);
  assert.equal(panel.situs, door.situs);
  assert.ok(Array.isArray(panel.rows), 'rows stays an array in the exclusion state');
});

test('the exclusion state shows no imagery footer', () => {
  assert.equal(buildPanel(unscoredDoor()).footer, null);
});

// ---------- buildPanel: imagery footer (R9.3) ----------

test('a door with no vision-derived evidence gets the no-imagery footer', () => {
  const panel = buildPanel(scoredDoor());
  assert.equal(panel.hasImagery, false);
  assert.equal(panel.footer, NO_IMAGERY_FOOTER);
});

test('a scored door with no evidence at all gets the no-imagery footer', () => {
  const panel = buildPanel(noEvidenceDoor());
  assert.deepEqual(panel.rows, []);
  assert.equal(panel.footer, NO_IMAGERY_FOOTER);
});

test('a door with vision-derived evidence gets no footer', () => {
  const panel = buildPanel(visionDoor());
  assert.equal(panel.hasImagery, true);
  assert.equal(panel.footer, null);
});

// ---------- copyAddress (R9.5) ----------

test('copyAddress returns the door full situs string', () => {
  const door = scoredDoor();
  assert.equal(copyAddress(door, () => {}), '128 SNYDER AVE, Ramsey NJ 07446');
  assert.equal(copyAddress(door, () => {}), door.situs);
});

test('copyAddress emits exactly one toast event', () => {
  const events = [];
  copyAddress(scoredDoor(), (e) => events.push(e));
  assert.equal(events.length, 1);
  assert.deepEqual(events[0], { type: 'toast', message: 'Address copied' });
});

test('copyAddress works without an emitter', () => {
  const door = scoredDoor();
  assert.equal(copyAddress(door), door.situs);
});

test('copyAddress works on an unscored door', () => {
  const door = unscoredDoor();
  const events = [];
  assert.equal(copyAddress(door, (e) => events.push(e)), door.situs);
  assert.equal(events.length, 1);
});

// ---------- panelLayout (R9.2) ----------

test('panelLayout is a bottom sheet at 375px', () => {
  assert.equal(panelLayout(375), 'sheet');
});

test('panelLayout is a bottom sheet just below the breakpoint', () => {
  assert.equal(panelLayout(767), 'sheet');
});

test('panelLayout is a side panel at the breakpoint', () => {
  assert.equal(panelLayout(768), 'side');
});

test('panelLayout is a side panel on desktop', () => {
  assert.equal(panelLayout(1280), 'side');
});

/* ── T106: the V2 score breakdown (R7/R30) ────────────────────────────────────
 *
 * Under V2 every published door carries the reconciliation fields —
 * `categories{project,capacity,fit}`, `base`, `mover_lift`, `rental_modifier`,
 * `adjustment` — so the breakdown renders from the map's own properties, not
 * only from the door endpoint. The panel shows the arithmetic that lands on
 * the displayed integer:
 *
 *   base + mover lift + rental modifier + adjustment = score
 *
 * with the three capped category subtotals above it, and any cap adjustment
 * living in the evidence trail as its own entry.
 */

test('the breakdown carries the three V2 categories in contract order', () => {
  const { breakdown } = buildPanel(detailedDoor());
  assert.deepEqual(
    breakdown.categories.map((c) => c.key),
    ['project', 'capacity', 'fit']
  );
});

test('each category declares its V2 cap: 45 / 25 / 48', () => {
  const { breakdown } = buildPanel(detailedDoor());
  assert.deepEqual(
    breakdown.categories.map((c) => [c.key, c.cap]),
    V2_CATEGORIES
  );
});

test('each category carries its capped subtotal from the payload', () => {
  const { breakdown } = buildPanel(detailedDoor());
  assert.deepEqual(
    breakdown.categories.map((c) => c.points),
    [15, 12, 12]
  );
});

test('the category labels are the V2 names, capitalised for the reader', () => {
  const { breakdown } = buildPanel(detailedDoor());
  assert.deepEqual(
    breakdown.categories.map((c) => c.label),
    ['Project', 'Capacity', 'Need']
  );
});

test('the category subtotals sum to the base', () => {
  const { breakdown } = buildPanel(detailedDoor());
  const sum = breakdown.categories.reduce((total, c) => total + c.points, 0);
  assert.equal(sum, breakdown.base);
  assert.equal(breakdown.base, 39);
});

test('the breakdown carries the blend arithmetic fields, rounded for display', () => {
  // The envelope's mover_lift is 55.875 and its adjustment 0.125; the panel
  // shows whole numbers, with the adjustment re-derived from the rounded
  // terms so the arithmetic still lands exactly on the published score.
  const { breakdown } = buildPanel(detailedDoor());
  assert.equal(breakdown.moverLift, 56);
  assert.equal(breakdown.rentalModifier, 0);
  assert.equal(breakdown.adjustment, 0);
  assert.equal(breakdown.score, 95);
});

test('the breakdown arithmetic reconciles to the displayed integer', () => {
  for (const door of [detailedDoor(), clampedDoor(), scoredDoor()]) {
    const { breakdown } = buildPanel(door);
    assert.equal(
      breakdown.base + breakdown.moverLift + breakdown.rentalModifier + breakdown.adjustment,
      breakdown.score,
      `door ${door.PAMS_PIN} does not reconcile`
    );
    assert.ok(Number.isInteger(breakdown.score), 'the displayed score is an integer');
  }
});

test('the evidence trail sums to base + mover lift + rental modifier (R7)', () => {
  // Including the cap-adjustment entry: scoredDoor's capacity signals exceed
  // the 25 cap, and the explicit −3 entry is what makes the trail add up.
  // Both sides round per-term for display; the only fractional term (the
  // mover blend) appears once in each, so the rounded sums still agree.
  for (const door of [detailedDoor(), clampedDoor(), scoredDoor()]) {
    const total = door.evidence.reduce((sum, item) => sum + Math.round(item.points), 0);
    const { breakdown } = buildPanel(door);
    assert.equal(
      total,
      breakdown.base + breakdown.moverLift + breakdown.rentalModifier,
      `door ${door.PAMS_PIN}: evidence does not reconcile`
    );
  }
});

test('the math line shows the blend arithmetic for a fresh mover, in whole numbers', () => {
  const { breakdown } = buildPanel(detailedDoor());
  assert.equal(breakdown.mathLine, '39 + 56 + 0 + 0 = 95');
});

test('rows carry the source attribution for the subline, label as fallback', () => {
  const { rows } = buildPanel(detailedDoor());
  const active = rows.find((row) => row.type === 'project_active');
  assert.equal(active.source, 'Ramsey municipal permits (SDL portal)');
  assert.equal(active.retrieved, '2026-08-19');
  // An entry the engine publishes without a source (e.g. a category cap)
  // still renders: the readable type label stands in.
  const capless = rows.find((row) => row.source === null || row.source === undefined);
  if (capless) assert.ok(capless.label.length > 0);
});

test('no fractional number ever reaches the panel (whole-number display)', () => {
  for (const door of [detailedDoor(), clampedDoor(), scoredDoor()]) {
    const { rows, breakdown } = buildPanel(door);
    for (const row of rows) {
      assert.ok(Number.isInteger(row.points), `row ${row.type} shows ${row.points}`);
      if (row.signed !== null) {
        assert.doesNotMatch(row.signed, /\./, `row ${row.type} shows ${row.signed}`);
      }
    }
    for (const key of ['base', 'moverLift', 'rentalModifier', 'adjustment']) {
      assert.ok(Number.isInteger(breakdown[key]), `${key} shows ${breakdown[key]}`);
    }
    assert.doesNotMatch(breakdown.mathLine, /\./, breakdown.mathLine);
  }
});

test('the math line shows the rental demotion and the floor clamp', () => {
  // 13 + 0 − 25 = −12, floored to 0 — the 12-point clamp is shown, with the
  // typographic minus on the demotion.
  const { breakdown } = buildPanel(clampedDoor());
  assert.equal(breakdown.mathLine, `13 + 0 ${MINUS} 25 + 12 = 0`);
});

test('a clamped door says it was clamped', () => {
  assert.equal(buildPanel(clampedDoor()).breakdown.clamp, 'floor');
  assert.equal(buildPanel(detailedDoor()).breakdown.clamp, null);
});

test('a door with no blend and no modifier still reads as plain arithmetic', () => {
  const { breakdown } = buildPanel(scoredDoor());
  assert.equal(breakdown.mathLine, '45 + 0 + 0 + 0 = 45');
});

test('an unscored door has no breakdown', () => {
  assert.equal(buildPanel(unscoredDoor()).breakdown, null);
});

test('the breakdown speaks no V1 vocabulary', () => {
  const panel = buildPanel(detailedDoor());
  const json = JSON.stringify(panel);
  for (const forbidden of ['raw_total', 'rawTotal', 'hires_out', 'hires-out', 'groups']) {
    assert.ok(!json.includes(forbidden), `panel view model still carries "${forbidden}"`);
  }
});

/* ── T106: degradation copy uses the V2 gap vocabulary (R30) ─────────────────── */

const V2_GAP_TYPES = [
  'rental_data_missing',
  'acs_missing',
  'imagery_missing',
  'sdl_page_unavailable',
  'local_comparables_insufficient',
  'assessed_value_missing',
];

test('GAP_MESSAGES covers exactly the six V2 gap types', () => {
  assert.deepEqual(Object.keys(GAP_MESSAGES).sort(), [...V2_GAP_TYPES].sort());
});

test('every gap message is real copy', () => {
  for (const [type, message] of Object.entries(GAP_MESSAGES)) {
    assert.ok(typeof message === 'string' && message.trim().length >= 20, `${type} has no copy`);
  }
});

test('the rental gap message states neutrality, not a penalty', () => {
  assert.match(GAP_MESSAGES.rental_data_missing, /neutral/i);
  assert.match(GAP_MESSAGES.rental_data_missing, /rental/i);
});

test('buildPanel maps the door data gaps onto readable rows', () => {
  const panel = buildPanel(clampedDoor());
  assert.deepEqual(
    panel.gaps,
    [
      { type: 'acs_missing', message: GAP_MESSAGES.acs_missing },
      { type: 'assessed_value_missing', message: GAP_MESSAGES.assessed_value_missing },
    ]
  );
});

test('a door with no gaps renders no gap rows', () => {
  assert.deepEqual(buildPanel(detailedDoor()).gaps, []);
});

test('an unscored door renders no gap rows', () => {
  assert.deepEqual(buildPanel(unscoredDoor()).gaps, []);
});

/* ── T106: the reason chip is the API's, never derived in the browser (R30) ──── */

test('the panel carries the API reason chip when the detail has one', () => {
  assert.equal(buildPanel(detailedDoor()).reasonChip, 'mover_recency');
  assert.equal(buildPanel(clampedDoor()).reasonChip, 'project_completed');
});

test('a door without the detail fetch has no chip rather than a guessed one', () => {
  assert.equal(buildPanel(scoredDoor()).reasonChip, null);
});

// ---------- talk track (R7.2) ----------

test('the door endpoint payload carries its talk track into the panel', () => {
  const door = detailedDoor();
  const panel = buildPanel(door);
  assert.equal(panel.talkTrack, door.talk_track);
  assert.deepEqual(panel.talkTrackBranches, door.talk_track_branches);
});

test('a map-properties door has no talk track section yet', () => {
  const panel = buildPanel(scoredDoor());
  assert.equal(panel.talkTrack, null);
  assert.deepEqual(panel.talkTrackBranches, []);
});
