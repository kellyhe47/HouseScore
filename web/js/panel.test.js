import { test } from 'node:test';
import assert from 'node:assert/strict';

import { buildPanel, copyAddress, panelLayout } from './panel.js';
import { scoredDoor, visionDoor, unscoredDoor, noEvidenceDoor } from './test-fixtures.js';

// U+2212 MINUS SIGN — the typographic minus the prototype's panel uses, not ASCII '-'.
const MINUS = '−';

// Copy that is user-visible contract (R9.4 / R9.3, docs/prototype-decoded.html:541,510).
const EXCLUSION_COPY = 'Not scored — parcel record incomplete in county data.';
const NO_IMAGERY_FOOTER = 'No imagery signals for this parcel';

// ---------- buildPanel: evidence rows ----------

test('buildPanel orders evidence rows by descending absolute points', () => {
  const panel = buildPanel(scoredDoor()); // input order: +15, +8, -20, 0
  assert.deepEqual(
    panel.rows.map((r) => r.points),
    [-20, 15, 8, 0]
  );
});

test('buildPanel keeps input order for rows with equal absolute points', () => {
  const door = scoredDoor({
    evidence: [
      { type: 'deed_recency', points: 15, sentence: 'first', source: 's', retrieved: '2026-08-14', imagery: null },
      { type: 'assessed_value', points: -15, sentence: 'second', source: 's', retrieved: '2026-08-14', imagery: null },
    ],
  });
  assert.deepEqual(buildPanel(door).rows.map((r) => r.sentence), ['first', 'second']);
});

test('buildPanel renders a positive row with a leading plus', () => {
  const row = buildPanel(scoredDoor()).rows.find((r) => r.points === 15);
  assert.equal(row.signed, '+15');
  assert.equal(row.hasSign, true);
});

test('buildPanel renders a negative row with the typographic minus', () => {
  const row = buildPanel(scoredDoor()).rows.find((r) => r.points === -20);
  assert.equal(row.signed, MINUS + '20');
  assert.equal(row.signed.charCodeAt(0), 0x2212, 'must be U+2212 MINUS SIGN, not ASCII hyphen');
  assert.equal(row.hasSign, true);
});

test('buildPanel renders a zero-point context row without a sign badge (R7.3)', () => {
  const row = buildPanel(scoredDoor()).rows.find((r) => r.points === 0);
  assert.equal(row.hasSign, false);
  assert.equal(row.signed, null);
});

test('buildPanel passes evidence fields through unchanged', () => {
  const door = scoredDoor();
  const source = door.evidence.find((e) => e.type === 'tenure');
  const row = buildPanel(door).rows.find((r) => r.type === 'tenure');
  assert.equal(row.sentence, source.sentence);
  assert.equal(row.source, source.source);
  assert.equal(row.retrieved, source.retrieved);
  assert.equal(row.imagery, null);
});

test('buildPanel carries the imagery attachment onto its row', () => {
  const door = visionDoor();
  const row = buildPanel(door).rows.find((r) => r.type === 'pool');
  assert.deepEqual(row.imagery, door.evidence.find((e) => e.type === 'pool').imagery);
});

test('buildPanel does not mutate the door it is given', () => {
  const door = scoredDoor();
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
  const panel = buildPanel(scoredDoor({ score: 0 }));
  assert.equal(panel.state, 'scored');
  assert.equal(panel.score, 0);
  assert.equal(panel.exclusionMessage, null);
});

test('buildPanel handles a score of 100 as a scored door', () => {
  const panel = buildPanel(scoredDoor({ score: 100 }));
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
