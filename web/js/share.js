// STUB — implementation ticket 013 fills this in. share.test.js is the spec.

/**
 * @param {object[]} stops
 * @returns {Promise<string>} the URL fragment carrying the ordered PINs
 */
export async function encodeShare(stops) {
  throw new Error('not implemented: encodeShare');
}

/**
 * @param {string} [fragment] defaults to the current location hash
 * @returns {Promise<string[]>} the PINs the fragment carries, in order
 */
export async function readShare(fragment) {
  throw new Error('not implemented: readShare');
}

/**
 * @param {object[]} stops
 * @param {string} href
 * @returns {Promise<string>} a full URL that reopens this route
 */
export async function shareUrl(stops, href) {
  throw new Error('not implemented: shareUrl');
}

/**
 * @param {object[]} stops
 * @param {(event: {type: string, message: string}) => void} [emit]
 * @returns {string} the copied text
 */
export function copyAsText(stops, emit) {
  throw new Error('not implemented: copyAsText');
}

/**
 * @param {object[]} stops
 * @param {string} href
 * @param {(event: {type: string, message: string}) => void} [emit]
 * @returns {Promise<string>} the copied URL
 */
export async function copyShareLink(stops, href, emit) {
  throw new Error('not implemented: copyShareLink');
}
