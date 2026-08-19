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
 * R2: the three V2 base categories in contract order, with their caps.
 *
 * The only subtotal vocabulary any surface may use (R30). The caps are the
 * engine's: capped subtotals sum to the base, and any cap adjustment lives in
 * the evidence trail as its own explicit entry (R7).
 */
const V2_CATEGORIES = [
  { key: 'project', label: 'Project', cap: 45 },
  { key: 'capacity', label: 'Capacity', cap: 25 },
  { key: 'fit', label: 'Need', cap: 48 },
];

/**
 * R30: the degradation copy for the six V2 gap types — what the panel says
 * when a signal source could not answer for this door. Every message states a
 * neutral default: a missing input never subtracts points.
 */
export const GAP_MESSAGES = {
  rental_data_missing:
    'No rental registry was available, so rental status is treated as neutral '
    + '— this door is neither promoted nor demoted for it.',
  acs_missing:
    'No Census block-group statistics were available, so the neighborhood '
    + 'prior contributed nothing to this score.',
  imagery_missing:
    'No aerial imagery signals were available for this parcel, so the score '
    + 'is built without pool, solar or exterior-condition terms.',
  sdl_page_unavailable:
    'The permit lifecycle page could not be retrieved, so project activity is '
    + 'read from the records already on hand.',
  local_comparables_insufficient:
    'Too few nearby comparable sales exist, so the local relative-value '
    + 'signal contributed nothing to this score.',
  assessed_value_missing:
    'The county record carries no assessed value for this parcel, so the '
    + 'capacity signals that read it contributed nothing.',
};

/**
 * Reader-facing names for evidence types whose underscore-spelling is jargon.
 * Every other type reads fine as its own words; the fallback is the type with
 * underscores as spaces.
 */
const TYPE_LABELS = {
  mover_recency: 'recent move-in',
};

/** The doorstep name for one evidence type. */
export const evidenceLabel = (type) =>
  TYPE_LABELS[type] ?? String(type).replace(/_/g, ' ');

/**
 * One evidence line, ready to render.
 *
 * `signed` and `hasSign` are separate because a zero-point line is not "+0" —
 * it is context (a data gap, a tenure note) that must not wear a badge implying
 * it moved the score (R7.3). Rendering decides how to show the absence; this
 * decides that there is one.
 */
function toRow(item) {
  // Whole numbers only at the doorstep: the engine's mover blend is fractional,
  // but a rep must never read "+61.23400494288421" off the panel.
  const points = Math.round(item.points);
  const hasSign = points !== 0;
  return {
    type: item.type,
    label: evidenceLabel(item.type),
    points,
    hasSign,
    signed: hasSign ? (points > 0 ? `+${points}` : `${MINUS}${Math.abs(points)}`) : null,
    reason: item.reason,
    // The attribution line: which record produced this evidence, fetched
    // when. Derived entries (category caps) publish no source and fall back
    // to the readable type label.
    source: item.source ?? null,
    retrieved: item.retrieved ?? null,
    imagery: item.imagery ?? null,
  };
}

/**
 * The V2 score's arithmetic, shown (R7/R30, wireframe frame 2b).
 *
 * Every published door carries the reconciliation fields under V2 —
 * `categories{project,capacity,fit}`, `base`, `mover_lift`, `rental_modifier`,
 * `adjustment` — so the breakdown renders from the map's own properties, not
 * only from the door endpoint. The arithmetic the panel shows is the one the
 * engine ran:
 *
 *   base + mover lift + rental modifier + adjustment = score
 *
 * with the three capped category subtotals above it. An unscored door has no
 * arithmetic to show at all.
 */
function buildBreakdown(door, scored) {
  if (!scored || !door.categories) return null;

  // What the trail summed to before the clamp/rounding adjustment: below 0 the
  // published 0 was a floor, above 100 the published 100 was a ceiling. Saying
  // so is what keeps the arithmetic honest for the one reader who checks.
  const unclamped =
    (door.base ?? 0) + (door.mover_lift ?? 0) + (door.rental_modifier ?? 0);

  // Whole numbers only: each displayed term is rounded, and the adjustment is
  // re-derived from the rounded terms — not rounded itself — so the shown
  // arithmetic still sums exactly to the published integer score.
  const base = Math.round(door.base ?? 0);
  const moverLift = Math.round(door.mover_lift ?? 0);
  const rentalModifier = Math.round(door.rental_modifier ?? 0);
  const adjustment = door.score - base - moverLift - rentalModifier;

  return {
    categories: V2_CATEGORIES.map((category) => ({
      ...category,
      points: door.categories[category.key] ?? 0,
    })),
    base,
    moverLift,
    rentalModifier,
    adjustment,
    score: door.score,
    mathLine: mathLine(base, moverLift, rentalModifier, adjustment, door.score),
    clamp: unclamped < 0 ? 'floor' : unclamped > 100 ? 'ceiling' : null,
  };
}

/**
 * The reconciling line under the subtotals, e.g. `39 + 56 + 0 + 0 = 95` — or,
 * for the demoted rental floored at 0, `13 + 0 − 25 + 12 = 0`, where the
 * 12-point adjustment is the clamp made visible. Negative terms wear the
 * typographic minus the rest of the panel uses.
 */
function mathLine(base, ...terms) {
  const score = terms.pop();
  const line = terms
    .map((term) => (term < 0 ? `${MINUS} ${Math.abs(term)}` : `+ ${term}`))
    .join(' ');
  return `${base} ${line} = ${score}`;
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
    // Both arrive only with the door endpoint's detail (R7.2 / R8.1). Null here
    // means "not fetched yet or not applicable", and the panel renders without
    // the section rather than with an empty one.
    talkTrack: door.talk_track ?? null,
    // The opener stops on one open question and waits. What the rep says next
    // depends on the answer, so the branches travel as alternatives to pick
    // from rather than more prose to read out.
    talkTrackBranches: door.talk_track_branches ?? [],
    breakdown: buildBreakdown(door, scored),
    // R30: readable degradation rows in the V2 gap vocabulary. Only a scored
    // door discloses gaps — an unscored door has its own message.
    gaps: scored
      ? (door.data_gaps ?? []).map((gap) => ({
          type: gap.type,
          message: GAP_MESSAGES[gap.type] ?? null,
        }))
      : [],
    // R30: the chip is the API's, never derived in the browser. Null means the
    // detail fetch has not landed, and no chip is shown rather than a guess.
    reasonChip: door.reason_chip ?? null,
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
