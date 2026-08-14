import { test } from 'node:test';
import assert from 'node:assert/strict';

import { streetLabels, streetName } from './streets.js';
import { asDoors, streetBlock } from './test-fixtures.js';

/* ── Street names in the empty grey ──────────────────────────────────────────
 *
 * The map has no basemap, so its gaps are streets by implication only. These
 * cover the two claims a street label makes that a reader will believe without
 * checking: that the name in a gap is the name of that gap, and that a name
 * lying along a corridor means the road runs that way.
 *
 * Both are derived, never given — there is no street layer in the published
 * run, only parcels that each know their own address.
 */

const METRE_IN_DEG_LAT = 1 / 110540;
const METRE_IN_DEG_LNG = 1 / (111320 * Math.cos((41.05 * Math.PI) / 180));

const findLabel = (labels, name) => labels.find((label) => label.name === name);

test('an address gives up its street, however the run spelled the suffix', () => {
  assert.equal(streetName('68 SNYDER AVENUE, Ramsey NJ 07446'), 'SNYDER AVE');
  assert.equal(streetName('41 Snyder Ave, Ramsey NJ 07446'), 'SNYDER AVE');
  assert.equal(streetName('12 W OAK STREET, Ramsey NJ 07446'), 'W OAK ST');
  assert.equal(streetName('9 GOOSE COVE LANE, Ramsey NJ 07446'), 'GOOSE COVE LN');

  // Nothing to name is not the same as a street called nothing.
  assert.equal(streetName(''), null);
  assert.equal(streetName(null), null);
  assert.equal(streetName('Ramsey NJ 07446'), null);
});

test('the same street spelled two ways is one street, not two', () => {
  // The published run genuinely contains both spellings on the same road; split
  // in two, a main street can fall under the threshold and go unnamed.
  const block = streetBlock({ name: 'PINE ST' });
  for (const feature of block.slice(0, 8)) {
    feature.properties.situs = feature.properties.situs.replace('PINE ST,', 'PINE STREET,');
  }

  const labels = streetLabels(asDoors(block));
  assert.deepEqual(
    labels.map((label) => label.name),
    ['PINE ST']
  );
  assert.equal(labels[0].doors, block.length, 'every parcel on it should have counted');
});

test('the name goes in the roadway, lying along the street', () => {
  const centreLat = 41.05;
  const labels = streetLabels(
    asDoors(streetBlock({ name: 'MAIN ST', start: [-74.16, centreLat], road: 14 }))
  );

  const main = findLabel(labels, 'MAIN ST');
  assert.ok(main, 'the one street in the territory went unnamed');

  // Inside the 14m roadway: the label is in the gap, not on somebody's house.
  const offset = Math.abs(main.position[1] - centreLat) / METRE_IN_DEG_LAT;
  assert.ok(
    offset <= 7,
    `the name landed ${offset.toFixed(1)}m from the middle of a 14m road — it is on a parcel`
  );

  // And along it: this street runs due east, so the text does too.
  assert.ok(
    Math.abs(main.bearing) < 5,
    `the name is written at ${main.bearing.toFixed(1)}° across a road that runs at 0°`
  );

  // Mid-street, not at whichever end won: the block is 11 lots of 13m.
  const fromEnd = (main.position[0] + 74.16) / METRE_IN_DEG_LNG;
  assert.ok(fromEnd > 40 && fromEnd < 103, `the name sits ${fromEnd.toFixed(0)}m along the block`);
});

test('a name is never written in the next street over', () => {
  // Two parallel streets, close enough that a pair of houses on one has its
  // midpoint in the other's roadway if nothing checks whose houses front it.
  const doors = asDoors([
    ...streetBlock({ name: 'MAIN ST', start: [-74.16, 41.05] }),
    ...streetBlock({ name: 'BACK ST', start: [-74.16, 41.05 + 90 * METRE_IN_DEG_LAT] }),
  ]);

  const labels = streetLabels(doors);
  for (const label of labels) {
    const expected = label.name === 'MAIN ST' ? 41.05 : 41.05 + 90 * METRE_IN_DEG_LAT;
    const strayed = Math.abs(label.position[1] - expected) / METRE_IN_DEG_LAT;
    assert.ok(strayed <= 7, `${label.name} was written ${strayed.toFixed(0)}m off its own road`);
  }
  assert.equal(labels.length, 2, 'both streets should be named, once each');
});

test('a long street is named more than once; a short one only once', () => {
  const long = streetLabels(asDoors(streetBlock({ name: 'LONG ST', lots: 45 })));
  const short = streetLabels(asDoors(streetBlock({ name: 'SHORT ST', lots: 11 })));

  assert.ok(
    long.length > 1,
    'a street a rep can only see part of at a time needs its name written more than once'
  );
  assert.equal(short.length, 1, 'a block-long street says its name once');

  // Far enough apart to be a second sighting rather than a stutter.
  const [first, second] = long;
  const apart = Math.hypot(
    (second.position[0] - first.position[0]) / METRE_IN_DEG_LNG,
    (second.position[1] - first.position[1]) / METRE_IN_DEG_LAT
  );
  assert.ok(apart >= 180, `two labels of the same street sat ${apart.toFixed(0)}m apart`);
});

test('the busiest streets are named first, and only as many as asked for', () => {
  const doors = asDoors([
    ...streetBlock({ name: 'BIG AVE', lots: 15, start: [-74.16, 41.05] }),
    ...streetBlock({ name: 'MID AVE', lots: 9, start: [-74.16, 41.0518] }),
    ...streetBlock({ name: 'SMALL AVE', lots: 5, start: [-74.16, 41.0536] }),
  ]);

  const named = streetLabels(doors).map((label) => label.name);
  assert.deepEqual(named, ['BIG AVE', 'MID AVE', 'SMALL AVE']);

  // Every street's own label comes before any street's repeat, because that is
  // the order the renderer drops labels in when the screen runs out of room.
  const capped = streetLabels(doors, { limit: 2 });
  assert.deepEqual(
    [...new Set(capped.map((label) => label.name))],
    ['BIG AVE', 'MID AVE'],
    'the limit is a limit on streets named, not on labels drawn'
  );
});

test('a street of four or fewer doors is not a landmark, and gets no name', () => {
  const labels = streetLabels(asDoors(streetBlock({ name: 'STUB CT', lots: 1 })));
  assert.deepEqual(labels, []);
});

test('doors with no geometry, and a run with no doors at all, are survivable', () => {
  assert.deepEqual(streetLabels([]), []);
  assert.deepEqual(streetLabels(undefined), []);
  assert.deepEqual(
    streetLabels([{ properties: { situs: '1 NOWHERE RD, Ramsey NJ 07446' }, geometry: null }]),
    []
  );
});
