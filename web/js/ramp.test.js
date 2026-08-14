import { test } from 'node:test';
import assert from 'node:assert/strict';

import { scoreColor } from './ramp.js';

// Ground truth: the prototype's ramp stops, [score, r, g, b].
// docs/prototype-decoded.html line 922.
const STOPS = [
  [0, 237, 239, 242],
  [25, 191, 208, 226],
  [45, 127, 163, 201],
  [65, 65, 114, 159],
  [85, 30, 76, 126],
  [100, 18, 47, 85],
];

// The distinct unscored fill from the prototype (line 962), NOT a ramp colour.
const UNSCORED = '#D6D3CB';

const rgb = (r, g, b) => `rgb(${r},${g},${b})`;

function parse(css) {
  const m = /^rgb\((\d+),(\d+),(\d+)\)$/.exec(css);
  assert.ok(m, `expected "rgb(r,g,b)" with no spaces, got ${JSON.stringify(css)}`);
  return [Number(m[1]), Number(m[2]), Number(m[3])];
}

test('scoreColor pins the low endpoint (score 0)', () => {
  assert.equal(scoreColor(0), rgb(237, 239, 242));
});

test('scoreColor pins the high endpoint (score 100)', () => {
  assert.equal(scoreColor(100), rgb(18, 47, 85));
});

test('scoreColor returns each interior ramp stop exactly', () => {
  for (const [score, r, g, b] of STOPS.slice(1, -1)) {
    assert.equal(scoreColor(score), rgb(r, g, b), `stop ${score}`);
  }
});

test('scoreColor interpolates linearly inside the 0–25 band', () => {
  // t = 10/25 = 0.4 → r 237→191, g 239→208, b 242→226
  assert.equal(scoreColor(10), rgb(219, 227, 236));
});

test('scoreColor interpolates linearly inside the 85–100 band', () => {
  // t = 5/15 = 1/3 → r 30→18, g 76→47, b 126→85
  assert.equal(scoreColor(90), rgb(26, 66, 112));
});

test('scoreColor darkens monotonically across the whole range', () => {
  let prev = parse(scoreColor(0));
  for (let s = 1; s <= 100; s++) {
    const cur = parse(scoreColor(s));
    for (let ch = 0; ch < 3; ch++) {
      assert.ok(cur[ch] <= prev[ch], `channel ${ch} rose from ${prev[ch]} to ${cur[ch]} at score ${s}`);
    }
    prev = cur;
  }
});

test('scoreColor(null) returns the distinct unscored grey', () => {
  assert.equal(scoreColor(null), UNSCORED);
});

test('the unscored grey is not any colour on the ramp', () => {
  for (let s = 0; s <= 100; s++) {
    assert.notEqual(scoreColor(s), UNSCORED, `score ${s} collided with the unscored grey`);
  }
});

test('scoreColor clamps scores below 0 to the low endpoint', () => {
  assert.equal(scoreColor(-5), scoreColor(0));
});

test('scoreColor clamps scores above 100 to the high endpoint', () => {
  assert.equal(scoreColor(150), scoreColor(100));
});
