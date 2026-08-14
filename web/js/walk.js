// STUB — implementation ticket 013 fills this in. walk.test.js is the spec.

/**
 * @param {object[]} rows
 * @param {{storage?: {getItem: Function, setItem: Function, removeItem: Function}, resume?: object}} [options]
 * @returns {object} walk
 */
export function createWalk(rows, options) {
  throw new Error('not implemented: createWalk');
}

/**
 * @param {{getItem: Function, setItem: Function, removeItem: Function}} [storage]
 * @returns {object|null} the saved walk, or null when there is nothing usable
 */
export function readWalk(storage) {
  throw new Error('not implemented: readWalk');
}

/**
 * @param {{getItem: Function, setItem: Function, removeItem: Function}} [storage]
 * @returns {void}
 */
export function clearWalk(storage) {
  throw new Error('not implemented: clearWalk');
}

/**
 * @param {object|null} saved
 * @returns {object|null} the resume banner's view model
 */
export function resumeOffer(saved) {
  throw new Error('not implemented: resumeOffer');
}
