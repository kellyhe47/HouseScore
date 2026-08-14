/**
 * The score-range filter and the coverage readout (R9.1, R9.4).
 *
 * Both answer the same question from opposite ends: which doors is the rep
 * looking at, and how many doors could be scored at all. They live together
 * because the coverage readout is the honest counterweight to a filter — a
 * range that hides doors and a count that hides its own denominator would let
 * the map imply a completeness the pipeline never claimed.
 *
 * DOM-free by construction so `node --test` can import it.
 */

/**
 * The doors a score range selects.
 *
 * The range is inclusive at both ends: a rep who drags to 40–60 means "40 and
 * 60 too", and an exclusive end would silently drop the doors sitting exactly
 * on a round number the rep just chose.
 *
 * Unscored doors are governed by `showUnscored`, not by the range: they have no
 * score to compare, so any range test on them is a lie in one direction or the
 * other. Off by default — the range is a statement about scores — and the map
 * turns it on so the three excluded parcels stay visible and clickable (R9.4).
 *
 * @param {ReadonlyArray<{score: number|null}>} doors
 * @param {readonly [number, number]} range inclusive [low, high]
 * @param {{showUnscored?: boolean}} [options]
 * @returns {Array<object>} a new array, in input order
 */
export function filterDoors(doors, range, options) {
  const [low, high] = range;
  const showUnscored = Boolean(options && options.showUnscored);

  return doors.filter((door) => {
    if (door.score === null || door.score === undefined) return showUnscored;
    return door.score >= low && door.score <= high;
  });
}

/**
 * The coverage readout, e.g. `537 of 540 scored`.
 *
 * Always both numbers. The denominator is the point: it is what makes the
 * unscored doors an admission rather than an omission.
 *
 * @param {number} scored
 * @param {number} total
 * @returns {string}
 */
export function coverageText(scored, total) {
  return `${scored} of ${total} scored`;
}
