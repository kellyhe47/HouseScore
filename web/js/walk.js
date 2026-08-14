/**
 * Walk mode: the rep's position in a planned route, and the memory of it
 * (R10.4, wireframe frame 4d).
 *
 * A door-knocking walk is interrupted constantly — a phone locks, a browser tab
 * is evicted, someone actually answers the door. So the walk is not a variable
 * in a page that reloads away; it is a small record in storage that every
 * advance rewrites, and `readWalk` is the question "was I in the middle of
 * something?" asked on boot.
 *
 * Storage is injected, defaulting to `localStorage`, for two reasons that turn
 * out to be the same reason: `node --test` has no `localStorage`, and a browser
 * in private mode has one that *throws*. Both are handled by treating storage
 * as something that might not work — a walk whose persistence fails is a walk
 * that cannot be resumed, not a walk that cannot be walked.
 *
 * DOM-free at import time.
 */

/**
 * Where a walk lives. Pinned by test rather than exported for the tests' use:
 * a rep who backgrounds the app mid-walk and returns to a new build must still
 * find their walk, so this string is a compatibility surface, not a detail.
 */
const KEY = 'houseaccount.walk.v1';

/**
 * The shape version. `readWalk` refuses anything else rather than guessing:
 * a record written by an older build has fields in places this one does not
 * look, and resuming it would put the rep at the wrong door.
 */
const SCHEMA = 1;

const defaultStorage = () =>
  typeof globalThis !== 'undefined' ? globalThis.localStorage : undefined;

/** Storage is best-effort on every call; private browsing throws from all three. */
function read(storage) {
  try {
    return storage ? storage.getItem(KEY) : null;
  } catch {
    return null;
  }
}

function write(storage, value) {
  try {
    if (storage) storage.setItem(KEY, JSON.stringify(value));
  } catch {
    // Persistence is the resume feature, not the walk. Losing it costs the rep
    // a resume banner, and costs them nothing at the door they are standing at.
  }
}

function remove(storage) {
  try {
    if (storage) storage.removeItem(KEY);
  } catch {
    // Same bargain as `write`.
  }
}

/**
 * Start (or resume) a walk over `rows`.
 *
 * The returned object is read through getters, so a caller can hold one
 * reference and re-render from it after `done()` or `skip()` without hunting
 * for a new copy — the walk screen redraws from the same object it acted on.
 *
 * @param {Array<{pin: string, address: string, talkTrack: string}>} rows the route, in planner order
 * @param {{storage?: object, resume?: object}} [options]
 */
export function createWalk(rows, options = {}) {
  const stops = rows ?? [];
  const storage = options.storage === undefined ? defaultStorage() : options.storage;
  const resume = options.resume ?? null;

  // A resumed walk inherits the whole history, not just the position: its
  // summary has to count the doors knocked before the interruption too.
  let index = resume ? Math.min(Math.max(resume.index ?? 0, 0), stops.length) : 0;
  const knocked = resume && Array.isArray(resume.knocked) ? [...resume.knocked] : [];
  const skipped = resume && Array.isArray(resume.skipped) ? [...resume.skipped] : [];

  const persist = () => write(storage, { schema: SCHEMA, rows: stops, index, knocked, skipped });

  /** Move to the next stop, remembering what happened at this one. */
  const advance = (log) => {
    // Past the end is a no-op rather than an error: a double-tap on "Done" at
    // the last door must not push the walk into a state with no stop in it.
    if (index >= stops.length) return walk;
    log.push(stops[index].pin);
    index += 1;
    persist();
    return walk;
  };

  const walk = {
    get index() {
      return index;
    },
    /** The door the rep is standing at, or null once the route is walked. */
    get current() {
      return index < stops.length ? stops[index] : null;
    },
    get finished() {
      return index >= stops.length;
    },
    get knocked() {
      return [...knocked];
    },
    get skipped() {
      return [...skipped];
    },
    /** "2 of 3" — where the rep is, not how much is left. */
    get progressLabel() {
      return `${Math.min(index + 1, stops.length)} of ${stops.length}`;
    },
    /** R10.4's end screen. Always plural, because it is a tally, not a sentence. */
    get summary() {
      return `${knocked.length} doors knocked · ${skipped.length} skipped`;
    },
    done: () => advance(knocked),
    skip: () => advance(skipped),
    /** Leave the walk and forget it: exiting is a decision, not an interruption. */
    exit: () => {
      remove(storage);
      return walk;
    },
  };

  persist();
  return walk;
}

/**
 * The walk in progress, or null when there is nothing to resume.
 *
 * "Nothing to resume" covers more than "nothing stored": garbage, a record from
 * an older schema, and a walk that already reached its end all answer null,
 * because the resume banner must never offer a walk it cannot actually restore.
 *
 * @param {object} [storage]
 * @returns {object|null}
 */
export function readWalk(storage = defaultStorage()) {
  const raw = read(storage);
  if (!raw) return null;

  let saved;
  try {
    saved = JSON.parse(raw);
  } catch {
    return null;
  }

  if (!saved || typeof saved !== 'object') return null;
  if (saved.schema !== SCHEMA) return null;
  if (!Array.isArray(saved.rows) || typeof saved.index !== 'number') return null;
  if (!Array.isArray(saved.knocked) || !Array.isArray(saved.skipped)) return null;
  if (saved.index >= saved.rows.length) return null;

  return saved;
}

/**
 * Forget the stored walk — the Discard half of the resume banner.
 *
 * @param {object} [storage]
 */
export function clearWalk(storage = defaultStorage()) {
  remove(storage);
}

/**
 * The resume banner's view model, or null when there is nothing to offer.
 *
 * Both actions, always: Resume-only traps a rep in a stale walk they no longer
 * want, which is why Discard is beside it (DESIGN-ADDITIONS, R10.4).
 *
 * @param {object|null} saved
 * @returns {{label: string, canResume: boolean, canDiscard: boolean}|null}
 */
export function resumeOffer(saved) {
  if (!saved) return null;
  return {
    label: `stop ${saved.index + 1} of ${saved.rows.length}`,
    canResume: true,
    canDiscard: true,
  };
}
