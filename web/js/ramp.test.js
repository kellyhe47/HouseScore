import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import {
  scoreColor,
  RAMP_STOP_SCORES,
  RAMP_CSS_GRADIENT,
  UNSCORED_COLOR,
} from './ramp.js';

/* ── R38: the ramp is restopped to the V2 distribution, derived not hand-picked ──
 *
 * ~538 of 540 doors now sit in 0–80 with a q50 of 11, so the V1 hand-picked
 * stops (25/45/65/85) render the territory as one pale wash. The recalibrated
 * ramp takes its interior stops from the documented quantiles of the R34
 * recalculation report — `eval/report.json` `score_distribution.quantiles` —
 * and this test compares the runtime constants against that report, so a
 * re-run that shifts the distribution fails the build until the stops are
 * re-derived. Scores themselves stay 0–100 integers (R1).
 */

const REPORT = JSON.parse(
  readFileSync(new URL('../../eval/report.json', import.meta.url), 'utf8')
);

/** q10..q90 in order, from the published report — the documented quantiles. */
const QUANTILES = ['q10', 'q20', 'q30', 'q40', 'q50', 'q60', 'q70', 'q80', 'q90'].map(
  (key) => REPORT.score_distribution.quantiles[key]
);

/** The derived interior stops: the distinct quantile values, ascending. */
const INTERIOR = [...new Set(QUANTILES)].sort((a, b) => a - b);

test('the report publishes nine quantiles for the current run', () => {
  for (const value of QUANTILES) {
    assert.ok(Number.isFinite(value), `a quantile is missing from eval/report.json`);
  }
});

test('the ramp stop scores are the report quantiles with the 0 and 100 endpoints', () => {
  assert.deepEqual(RAMP_STOP_SCORES, [0, ...INTERIOR, 100]);
});

test('every ramp stop is an integer score in 0–100', () => {
  for (const stop of RAMP_STOP_SCORES) {
    assert.ok(Number.isInteger(stop), `stop ${stop} is not an integer`);
    assert.ok(stop >= 0 && stop <= 100, `stop ${stop} is out of the 0–100 contract`);
  }
});

test('the stops ascend strictly — deduped, never repeated', () => {
  for (let i = 1; i < RAMP_STOP_SCORES.length; i++) {
    assert.ok(
      RAMP_STOP_SCORES[i] > RAMP_STOP_SCORES[i - 1],
      `stops must strictly ascend: ${RAMP_STOP_SCORES[i - 1]} then ${RAMP_STOP_SCORES[i]}`
    );
  }
});

test('the ramp is not the V1 hand-picked ramp', () => {
  assert.notDeepEqual(RAMP_STOP_SCORES, [0, 25, 45, 65, 85, 100]);
});

test('the legend gradient positions each stop at its score percentage', () => {
  // The legend is drawn from the same constant the fills use: one colour stop
  // per ramp stop, positioned at `${score}%`, so the legend and the parcels
  // cannot disagree about where the ramp turns.
  for (const stop of RAMP_STOP_SCORES) {
    assert.match(
      RAMP_CSS_GRADIENT,
      new RegExp(`rgb\\(\\d+,\\d+,\\d+\\)\\s+${stop}%`),
      `the gradient carries no colour stop at ${stop}%`
    );
  }
});

/* ── The colour function's contract, unchanged from V1 ─────────────────────── */

function parse(css) {
  const m = /^rgb\((\d+),(\d+),(\d+)\)$/.exec(css);
  assert.ok(m, `expected "rgb(r,g,b)" with no spaces, got ${JSON.stringify(css)}`);
  return [Number(m[1]), Number(m[2]), Number(m[3])];
}

test('scoreColor answers rgb() for every integer score', () => {
  for (let s = 0; s <= 100; s++) parse(scoreColor(s));
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

test('the quantile stops actually spread the observed distribution', () => {
  // The point of the restop: q20 and q80 of the observed scores must land on
  // visibly different colours, which the V1 ramp failed (5 and 19 were nearly
  // the same near-white). "Visibly different" pinned as ≥ 40 units of summed
  // channel distance.
  const [q20, q80] = [QUANTILES[1], QUANTILES[7]];
  const a = parse(scoreColor(q20));
  const b = parse(scoreColor(q80));
  const distance = a.reduce((sum, v, i) => sum + Math.abs(v - b[i]), 0);
  assert.ok(
    distance >= 40,
    `scores ${q20} and ${q80} render ${distance} channel units apart — the middle of the distribution is a wash`
  );
});

test('scoreColor(null) returns the distinct unscored grey', () => {
  assert.equal(scoreColor(null), UNSCORED_COLOR);
});

test('the unscored grey is not any colour on the ramp', () => {
  for (let s = 0; s <= 100; s++) {
    assert.notEqual(scoreColor(s), UNSCORED_COLOR, `score ${s} collided with the unscored grey`);
  }
});

test('scoreColor clamps scores below 0 to the low endpoint', () => {
  assert.equal(scoreColor(-5), scoreColor(0));
});

test('scoreColor clamps scores above 100 to the high endpoint', () => {
  assert.equal(scoreColor(150), scoreColor(100));
});
