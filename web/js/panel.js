/**
 * The evidence panel's view model (R9.1–R9.5).
 *
 * `buildPanel` turns one published door into plain data — no HTML, no
 * callbacks, no DOM — so the panel's rules (what order the evidence reads in,
 * which state a door is in, what copy the exclusion shows) are testable without
 * a browser, and `map.js` is left with nothing but rendering. Everything it
 * returns survives a JSON round trip; that is the contract that keeps logic
 * from leaking back in as a closure.
 *
 * DOM-free at import time so `node --test` can import it.
 */

/** U+2212 MINUS SIGN — the panel's numbers are typography, not code. */
const MINUS = '−';

/** R9.4: what a door with no score says, verbatim from the approved prototype. */
export const EXCLUSION_MESSAGE =
  'Not scored — parcel record incomplete in county data.';

/** R9.3: shown when no evidence line came from imagery. */
export const NO_IMAGERY_FOOTER = 'No imagery signals for this parcel';

/** R9.2: below this viewport width the panel is a bottom sheet. */
export const SHEET_BREAKPOINT = 768;

/** The toast `copyAddress` emits (R9.5). */
export const COPIED_MESSAGE = 'Address copied';

/**
 * One evidence line, ready to render.
 *
 * `signed` and `hasSign` are separate because a zero-point line is not "+0" —
 * it is context (a data gap, a tenure note) that must not wear a badge implying
 * it moved the score (R7.3). Rendering decides how to show the absence; this
 * decides that there is one.
 */
function toRow(item) {
  const points = item.points;
  const hasSign = points !== 0;
  return {
    type: item.type,
    points,
    hasSign,
    signed: hasSign ? (points > 0 ? `+${points}` : `${MINUS}${Math.abs(points)}`) : null,
    sentence: item.sentence,
    source: item.source,
    retrieved: item.retrieved,
    imagery: item.imagery ?? null,
  };
}

/**
 * The panel for one door.
 *
 * Evidence is ordered by how much it moved the score, largest first, regardless
 * of direction: the rep's first question at the doorstep is "why this door",
 * and a −20 answers that as loudly as a +20. Ties keep the engine's order,
 * which is the order it built the trail in, so the same door always reads the
 * same way.
 *
 * @param {{PAMS_PIN: string, score: number|null, confidence: string|null,
 *          situs: string, exclusion_reason: string|null, evidence: object[]}} door
 * @returns {object} plain-data view model
 */
export function buildPanel(door) {
  const scored = door.score !== null && door.score !== undefined;

  const rows = (door.evidence ?? [])
    .map((item, index) => ({ item, index }))
    .sort((a, b) => Math.abs(b.item.points) - Math.abs(a.item.points) || a.index - b.index)
    .map(({ item }) => toRow(item));

  const hasImagery = rows.some((row) => row.imagery !== null);

  return {
    state: scored ? 'scored' : 'unscored',
    pin: door.PAMS_PIN,
    situs: door.situs,
    score: scored ? door.score : null,
    confidence: door.confidence ?? null,
    rows,
    hasImagery,
    // The footer explains an absence in the evidence trail, so it only makes
    // sense where a trail was attempted. An unscored door has its own message.
    footer: scored && !hasImagery ? NO_IMAGERY_FOOTER : null,
    exclusionMessage: scored ? null : EXCLUSION_MESSAGE,
  };
}

/**
 * Put a door's address on the clipboard and confirm it (R9.5).
 *
 * Returns the full situs string — the whole address, because a rep pasting it
 * into a phone's map app needs the town, not just the house number.
 *
 * The clipboard write is best-effort and guarded: this module must import
 * cleanly under `node --test`, and a browser without clipboard permission
 * should still show the address rather than throw. The toast is emitted either
 * way, through an injected emitter, so the confirmation stays the caller's
 * concern and this function stays testable.
 *
 * @param {{situs: string}} door
 * @param {(event: {type: string, message: string}) => void} [emit]
 * @returns {string}
 */
export function copyAddress(door, emit) {
  const address = door.situs;

  try {
    if (typeof navigator !== 'undefined' && navigator.clipboard) {
      // `writeText` rejects rather than throws when permission is refused, so
      // the rejection is swallowed here too. A refused clipboard is not a
      // failed copy from the rep's point of view — the address is on screen and
      // selectable either way — and it must not surface as an unhandled
      // rejection in the console.
      Promise.resolve(navigator.clipboard.writeText(address)).catch(() => {});
    }
  } catch {
    // Some environments throw synchronously (no secure context, no permission).
  }

  if (emit) emit({ type: 'toast', message: COPIED_MESSAGE });
  return address;
}

/**
 * Which shape the evidence panel takes at a given viewport width (R9.2).
 *
 * A side panel needs room beside the map; below the breakpoint there is none,
 * and a rep standing on a sidewalk gets a bottom sheet they can thumb.
 *
 * @param {number} viewportWidth
 * @returns {'sheet'|'side'}
 */
export function panelLayout(viewportWidth) {
  return viewportWidth < SHEET_BREAKPOINT ? 'sheet' : 'side';
}
