/**
 * The panel's Street View slot, as plain data.
 *
 * The photo under the address is *visual context only* — "this is the house
 * you're walking to" — and never evidence: nothing here reads or feeds the
 * score, and the label the model carries says so on the image itself.
 *
 * Like `panel.js`, this module is DOM-free at import time so `node --test`
 * can exercise the four states (loading / available / unavailable / error)
 * without a browser, leaving `map.js` nothing but rendering. Everything
 * returned survives a JSON round trip.
 *
 * Images are never stored: the server proxies them with `no-store`, and the
 * model only ever carries the proxy URL, never bytes.
 */

/** What the slot says while the metadata fetch is in flight. */
export const LOADING_MESSAGE = 'loading Street View…';

/** R-ethics: the designed absence, shown when Google has no imagery here. */
export const UNAVAILABLE_MESSAGE = 'No Street View imagery for this address';

/** Shown when the lookup itself failed — retryable, unlike unavailable. */
export const ERROR_MESSAGE = 'Street View couldn’t load';

/** Google's terms require attribution wherever the image is shown. */
export const ATTRIBUTION = 'Imagery © Google';

/** The badge that keeps the photo honest about its role in the panel. */
export const CONTEXT_LABEL = 'Visual context only — not used in the score';

const MONTHS = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
];

/**
 * Google reports capture month as "YYYY-MM" (sometimes just "YYYY").
 * Rendered as words — "Captured Jul 2019" — because "2019-07" under a photo
 * reads as a file name, not a date. An unparseable date is dropped rather
 * than shown raw: the photo stands without it.
 *
 * @param {string|null|undefined} date
 * @returns {string|null}
 */
export function captureLabel(date) {
  if (typeof date !== 'string') return null;
  const match = date.match(/^(\d{4})(?:-(\d{2}))?/);
  if (!match) return null;
  const year = match[1];
  const month = match[2] ? MONTHS[Number(match[2]) - 1] : null;
  if (match[2] && !month) return null;
  return month ? `Captured ${month} ${year}` : `Captured ${year}`;
}

/** The slot before (and while) the metadata fetch answers. */
export function loadingStreetView() {
  return { state: 'loading', message: LOADING_MESSAGE };
}

/**
 * The slot once `GET /api/streetview/{pin}` has answered.
 *
 * The three server shapes map onto three render states:
 *  - `status: "available"`  → the photo, its capture date, and the Maps link;
 *  - `status: "unavailable"` → the designed absence (no imagery here, or a
 *    keyless deployment — the panel does not distinguish, because in both
 *    cases there is simply nothing to show);
 *  - anything else (a 502 body, a shape we don't recognise) → the error
 *    state, which keeps the Maps link when the server managed to send one.
 *
 * @param {object|null} body  parsed response body, or null if unparseable
 * @returns {object} plain-data view model for the slot
 */
export function buildStreetView(body) {
  const mapsUrl = body && typeof body.maps_url === 'string' ? body.maps_url : null;

  if (body && body.status === 'available' && typeof body.image_url === 'string') {
    return {
      state: 'available',
      imageUrl: body.image_url,
      captureLabel: captureLabel(body.capture_date),
      mapsUrl,
      attribution: ATTRIBUTION,
      contextLabel: CONTEXT_LABEL,
    };
  }
  if (body && body.status === 'unavailable') {
    return { state: 'unavailable', message: UNAVAILABLE_MESSAGE, mapsUrl };
  }
  return { state: 'error', message: ERROR_MESSAGE, mapsUrl };
}
