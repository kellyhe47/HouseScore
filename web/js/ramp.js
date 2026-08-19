/**
 * The one 0–100 colour ramp every score on the map is drawn on (R9.1).
 *
 * The map has exactly one visual encoding of the House Score, so the ramp lives
 * in one place and everything that needs a score colour — parcel fills, the
 * evidence panel's big number, the legend gradient — asks this module. A second
 * ramp anywhere would mean a parcel and its panel could disagree about what 62
 * looks like.
 *
 * The stops are derived, not hand-picked (R38): the interior stops are the
 * documented quantiles of the R34 recalculation report, generated into
 * `ramp-stops.js` by `web/scripts/gen-ramp-stops.mjs` (run on every
 * `npm test` via pretest). The colours interpolate linearly in RGB. Unlike
 * the prototype's own `ramp()`, this one clamps: the published scores are
 * already clamped to 0–100, but a colour function that returns
 * `rgb(NaN,NaN,NaN)` for an out-of-range input fails silently as an invisible
 * parcel, and clamping is the cheaper contract.
 *
 * DOM-free by construction so `node --test` can import it.
 */

import { STOPS } from './ramp-stops.js';

/** The scores the ramp turns at — the report quantiles plus the endpoints. */
export const RAMP_STOP_SCORES = STOPS.map(([score]) => score);

/**
 * The fill for a door the pipeline could not score (R9.4).
 *
 * Deliberately off-ramp — a warm grey no interpolation of the blues can produce
 * — because "not scored" must not read as "scored low". The map pairs it with a
 * diagonal hairline for the same reason.
 */
export const UNSCORED_COLOR = '#D6D3CB';

/**
 * The ramp as a CSS gradient, for the legend and the filter track.
 *
 * One colour stop per ramp stop, positioned at its score percentage, so the
 * legend and the parcel fills cannot disagree about where the ramp turns.
 */
export const RAMP_CSS_GRADIENT = `linear-gradient(90deg, ${STOPS.map(
  ([score, r, g, b]) => `rgb(${r},${g},${b}) ${score}%`
).join(', ')})`;

const clamp = (value, low, high) => Math.min(high, Math.max(low, value));

/**
 * The colour for one House Score.
 *
 * @param {number|null|undefined} score 0–100, or null for an unscored door.
 * @returns {string} a CSS colour: `rgb(r,g,b)` on the ramp, or the unscored grey.
 */
export function scoreColor(score) {
  if (score === null || score === undefined) return UNSCORED_COLOR;

  const s = clamp(Number(score), STOPS[0][0], STOPS[STOPS.length - 1][0]);
  for (let i = 0; i < STOPS.length - 1; i++) {
    const [low, ...from] = STOPS[i];
    const [high, ...to] = STOPS[i + 1];
    if (s <= high) {
      const t = (s - low) / (high - low);
      const channels = from.map((v, j) => Math.round(v + (to[j] - v) * t));
      return `rgb(${channels.join(',')})`;
    }
  }
  const [, ...top] = STOPS[STOPS.length - 1];
  return `rgb(${top.join(',')})`;
}
