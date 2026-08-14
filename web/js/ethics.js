// STUB — implementation ticket 014 fills this in. ethics.test.js is the spec.
//
// These three constants are the browser's copy of
// `src/houseaccount/scoring/weights.py`. `ethics.test.js` parses that file and
// deep-equals it against these, so the mirror cannot drift from the engine.

/** @type {Record<string, number>} */
export const WEIGHTS = {};

/** @type {Record<string, number>} */
export const THRESHOLDS = {};

/** @type {string[]} */
export const CONDITION_ORDER = [];

/**
 * @returns {object[]} one row per score group, tracing its signals to the ICP
 */
export function icpTrace() {
  throw new Error('not implemented: icpTrace');
}

/**
 * @param {object|null} report parsed `eval/report.json`
 * @returns {object} the evaluation section's view model
 */
export function evalMetrics(report) {
  throw new Error('not implemented: evalMetrics');
}

/**
 * @param {object|null} manifest parsed `data/run_manifest.json`
 * @returns {object[]} one row per signal source: live, or declined and why
 */
export function signalAvailability(manifest) {
  throw new Error('not implemented: signalAvailability');
}

/**
 * @param {object|null} manifest
 * @returns {object} the absentee section (R11.3)
 */
export function absenteeStatement(manifest) {
  throw new Error('not implemented: absenteeStatement');
}

/**
 * @param {{report: object|null, manifest: object|null}} data
 * @returns {object} the whole page as plain data
 */
export function buildEthicsPage(data) {
  throw new Error('not implemented: buildEthicsPage');
}
