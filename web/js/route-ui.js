/**
 * The route request and its view model (R10.1–R10.3, wireframe frames 4–4c).
 *
 * **The order is the server's.** `plan_route` chooses the walk; this module maps
 * its stops into rows and does not touch their sequence. That is not a
 * convention, it is R10.3: the MCP tool and the map must hand a rep the same
 * walk, and a browser that re-sorted — even "helpfully", by score — would have
 * re-planned. The fixture's scores deliberately do not descend so a sort here
 * fails loudly.
 *
 * **`fetch` is injected.** `node --test` has a real `fetch` and no server to
 * point it at, so the caller supplies one; `map.js` passes the browser's.
 *
 * DOM-free at import time.
 */

/** Same-origin by default; a split deployment overrides it (R12). */
const DEFAULT_API_BASE = '/api';

/**
 * One stop, as the route list and walk mode read it.
 *
 * `elapsedLabel` is an offset from the start of the walk, never a clock time
 * (R10.2): the plan does not know when the rep will actually set off, and
 * "10:40" would be wrong by however long they spent parking.
 *
 * `path` is the leg the planner measured — the line the map draws. It arrives
 * as a list of `[lon, lat]` and is carried through untouched; a payload without
 * one leaves it empty and the map falls back to the door's centroid.
 */
function toRow(stop, index) {
  return {
    n: index + 1,
    pin: stop.pams_pin,
    address: stop.address,
    score: stop.score,
    walkMinutes: stop.walk_minutes,
    cumulativeMinutes: stop.cumulative_minutes,
    elapsedLabel: `+${Math.round(stop.cumulative_minutes)} min`,
    talkTrack: stop.talk_track,
    path: stop.path ?? [],
  };
}

/**
 * The whole walk as one line: the parking spot, then every leg the planner
 * measured, end to end.
 *
 * The vertices are the server's. A rep walks along streets, and the planner is
 * the only thing here that knows where those are — it measured the minutes
 * along them. Joining the doors' centroids in the browser instead would draw a
 * walk through the middle of a block beside a time that assumed otherwise,
 * which is the same R10.3 rule that keeps the *order* on the server.
 *
 * `centroidOf` supplies a door's point for a leg that arrived without a path,
 * so a route payload from an older server still draws something honest.
 *
 * @param {{pin: string, path?: number[][]}[]} rows
 * @param {[number, number]|null} startPoint
 * @param {(pin: string) => [number, number]|null} centroidOf
 * @returns {number[][]} the line's coordinates, `[]` when there is nothing to draw
 */
export function routeLine(rows, startPoint, centroidOf = () => null) {
  const coordinates = [];
  const push = (point) => {
    const last = coordinates[coordinates.length - 1];
    // Each leg starts where the previous one ended; drawing that seam twice
    // costs a duplicate vertex on every stop.
    if (!last || last[0] !== point[0] || last[1] !== point[1]) coordinates.push(point);
  };

  if (startPoint) push(startPoint);
  for (const row of rows ?? []) {
    const leg = row.path && row.path.length ? row.path : [centroidOf(row.pin)];
    for (const point of leg) if (point) push(point);
  }

  return coordinates.length > 1 ? coordinates : [];
}

/**
 * Ask the planner for a walk.
 *
 * @param {{hours: number, start: [number, number], maxDoors?: number, exclude?: string[]}} request
 * @param {{fetch?: Function, apiBase?: string}} [options]
 * @returns {Promise<{rows: object[], totalMinutes: number, estimateDisclosure: string, isEmpty: boolean}>}
 */
export async function requestRoute(request, options = {}) {
  const fetchImpl = options.fetch ?? globalThis.fetch;
  const apiBase = options.apiBase ?? DEFAULT_API_BASE;

  const response = await fetchImpl(`${apiBase}/route`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({
      hours: request.hours,
      // GeoJSON order, as everywhere in this codebase and as the map's own
      // coordinates already are.
      start_point: request.start,
      max_doors: request.maxDoors ?? null,
      exclude: request.exclude ?? null,
    }),
  });

  if (!response.ok) {
    // The API's failures are shapes — `{error, message}` — but a proxy or a
    // crash can answer with neither, so the status is the part always carried.
    const detail = await response.json().catch(() => null);
    const error = new Error(
      (detail && detail.message) || `route request failed (${response.status})`
    );
    error.status = response.status;
    error.code = detail && detail.error;
    throw error;
  }

  const payload = await response.json();
  const rows = (payload.stops ?? []).map(toRow);

  return {
    rows,
    totalMinutes: payload.total_minutes,
    estimateDisclosure: payload.estimate_disclosure,
    // An empty route is an answer, not a failure: "no doors reachable in 0.5h"
    // is what frame 4b renders, and it is never a blank map.
    isEmpty: rows.length === 0,
  };
}

/**
 * A planner that remembers its own inputs, so the rep can adjust one of them.
 *
 * Frame 4c's whole behaviour is "change hours 2 → 3 and the route recomputes":
 * `plan({hours: 3})` keeps the start and the door cap, because the rep changed
 * one control, not all three. That memory is the live-adjust feature — without
 * it every caller would have to re-supply the full request and would eventually
 * re-supply a stale one.
 *
 * @param {{fetch?: Function, apiBase?: string}} [options]
 */
export function createRoutePlanner(options = {}) {
  const request = { hours: undefined, start: undefined, maxDoors: undefined };
  const excluded = [];
  let view = null;

  const run = async () => {
    view = await requestRoute(
      {
        hours: request.hours,
        start: request.start,
        maxDoors: request.maxDoors,
        // Always sent as a list — including the empty one — so the body of a
        // given set of inputs is the same every time it is built.
        exclude: [...excluded],
      },
      options
    );
    return view;
  };

  return {
    /**
     * Plan, or re-plan with some inputs changed. Omitted fields keep their
     * last value.
     */
    async plan(changes = {}) {
      for (const field of ['hours', 'start', 'maxDoors']) {
        if (changes[field] !== undefined) request[field] = changes[field];
      }
      return run();
    },

    /**
     * Drop a door and re-plan (frame 4c's ✕: "the rep knows that house is
     * vacant"). The exclusion goes to the planner rather than filtering the
     * rows here — a filtered route keeps its detours around a house nobody is
     * visiting any more, and re-ordering in JS would break R10.3.
     */
    async excludeStop(pin) {
      if (!excluded.includes(pin)) excluded.push(pin);
      return run();
    },

    /** The view the last plan produced, or null before the first one. */
    get view() {
      return view;
    },

    get excluded() {
      return [...excluded];
    },
  };
}
