import { test } from 'node:test';
import assert from 'node:assert/strict';

import { filterDoors, coverageText } from './filter.js';
import { doorWithScore } from './test-fixtures.js';

const pins = (doors) => doors.map((d) => d.PAMS_PIN);

const sample = () => [
  doorWithScore(0, 'a'),
  doorWithScore(39, 'b'),
  doorWithScore(40, 'c'),
  doorWithScore(50, 'd'),
  doorWithScore(60, 'e'),
  doorWithScore(61, 'f'),
  doorWithScore(100, 'g'),
  doorWithScore(null, 'u1'),
  doorWithScore(null, 'u2'),
];

// ---------- filterDoors ----------

test('filterDoors is inclusive at both ends of the range', () => {
  const got = filterDoors(sample(), [40, 60], { showUnscored: false });
  assert.deepEqual(pins(got), ['c', 'd', 'e']);
});

test('filterDoors excludes scores just outside the range', () => {
  const got = filterDoors(sample(), [40, 60], { showUnscored: false });
  assert.ok(!pins(got).includes('b'), '39 must be excluded from [40,60]');
  assert.ok(!pins(got).includes('f'), '61 must be excluded from [40,60]');
});

test('filterDoors excludes unscored doors by default', () => {
  const got = filterDoors(sample(), [0, 100], { showUnscored: false });
  assert.deepEqual(pins(got), ['a', 'b', 'c', 'd', 'e', 'f', 'g']);
});

test('filterDoors excludes unscored doors when options are omitted entirely', () => {
  const got = filterDoors(sample(), [0, 100]);
  assert.deepEqual(pins(got), ['a', 'b', 'c', 'd', 'e', 'f', 'g']);
});

test('filterDoors excludes unscored doors when the option is absent from options', () => {
  const got = filterDoors(sample(), [0, 100], {});
  assert.ok(!pins(got).includes('u1'));
});

test('filterDoors includes unscored doors when showUnscored is set', () => {
  const got = filterDoors(sample(), [0, 100], { showUnscored: true });
  assert.deepEqual(pins(got), ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'u1', 'u2']);
});

test('showUnscored keeps unscored doors regardless of how narrow the range is', () => {
  const got = filterDoors(sample(), [90, 95], { showUnscored: true });
  assert.deepEqual(pins(got), ['u1', 'u2']);
});

test('showUnscored still applies the range to scored doors', () => {
  const got = filterDoors(sample(), [40, 60], { showUnscored: true });
  assert.deepEqual(pins(got), ['c', 'd', 'e', 'u1', 'u2']);
});

test('a score of 0 is inside the full range', () => {
  const got = filterDoors([doorWithScore(0, 'z')], [0, 100], { showUnscored: false });
  assert.deepEqual(pins(got), ['z']);
});

test('a score of 0 is outside a range starting at 1', () => {
  const got = filterDoors([doorWithScore(0, 'z')], [1, 100], { showUnscored: false });
  assert.deepEqual(got, []);
});

test('a score of 100 is inside the full range', () => {
  const got = filterDoors([doorWithScore(100, 'z')], [0, 100], { showUnscored: false });
  assert.deepEqual(pins(got), ['z']);
});

test('filterDoors on an empty door list returns an empty array', () => {
  assert.deepEqual(filterDoors([], [0, 100], { showUnscored: true }), []);
});

test('a range that matches nothing returns an empty array, not null', () => {
  const got = filterDoors(sample(), [70, 75], { showUnscored: false });
  assert.deepEqual(got, []);
});

test('a range whose ends collapse to a single value matches that exact score', () => {
  const got = filterDoors(sample(), [50, 50], { showUnscored: false });
  assert.deepEqual(pins(got), ['d']);
});

test('filterDoors does not mutate the input list', () => {
  const doors = sample();
  const before = pins(doors);
  filterDoors(doors, [40, 60], { showUnscored: false });
  assert.deepEqual(pins(doors), before);
});

test('filterDoors returns a new array, not the input array', () => {
  const doors = sample();
  const got = filterDoors(doors, [0, 100], { showUnscored: true });
  assert.notEqual(got, doors);
});

// ---------- coverageText ----------

test('coverageText renders the prototype readout', () => {
  assert.equal(coverageText(537, 540), '537 of 540 scored');
});

test('coverageText renders full coverage', () => {
  assert.equal(coverageText(540, 540), '540 of 540 scored');
});

test('coverageText renders zero coverage', () => {
  assert.equal(coverageText(0, 0), '0 of 0 scored');
});
