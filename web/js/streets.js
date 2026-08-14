/**
 * Street names for the map's empty grey, derived from the parcels themselves.
 *
 * The map deliberately has no basemap — no tile provider, no API key (R12) — so
 * the gaps between the parcels are the streets, drawn only by their absence. A
 * rep who knows the town can read that; a reviewer opening the map cold cannot,
 * and neither can a rep dropped into a territory they have not walked. Naming a
 * few of the main streets is the cheapest thing that turns an abstract mosaic
 * into a place.
 *
 * There is no street layer to label, so the streets are inferred. Two facts do
 * the work: every parcel carries its own `situs` address, which names the street
 * it fronts; and two houses facing each other across a street have a midpoint
 * that lands in the roadway, because the roadway is the one place no parcel
 * covers. So a candidate label position is the midpoint of a pair of parcels on
 * the same street that falls outside every parcel in the territory, and the
 * label's angle is the perpendicular of that pair — which is to say, along the
 * street. `corridor` then throws out the midpoints that are merely open ground.
 *
 * This is presentation over data the map already holds: nothing here reads a
 * score, and a street label is never evidence about a door.
 *
 * DOM-free by construction so `node --test` can import it.
 */

/** Long suffixes as the published addresses spell them, and their map form. */
const SUFFIXES = {
  AVENUE: 'AVE',
  STREET: 'ST',
  DRIVE: 'DR',
  ROAD: 'RD',
  PLACE: 'PL',
  COURT: 'CT',
  LANE: 'LN',
  TERRACE: 'TER',
  BOULEVARD: 'BLVD',
  CIRCLE: 'CIR',
  HIGHWAY: 'HWY',
  PARKWAY: 'PKWY',
};

/** Below this a "street" is a handful of parcels — too little to place, and not a landmark. */
const MIN_DOORS = 4;

/** Stands for a parcel whose address could not be read; never equal to a street. */
const UNADDRESSED = Symbol('unaddressed');

/** How many streets get named. Orientation is the goal, not a gazetteer. */
const DEFAULT_LIMIT = 8;

/** A pair closer than this is two neighbours side by side, not two across a street. */
const MIN_WIDTH_M = 15;

/** Beyond this the pair is diagonally across a block, and its midpoint proves nothing. */
const MAX_WIDTH_M = 110;

/** Pairs within this factor of the street's narrowest count as facing each other. */
const TYPICAL_WIDTH_FACTOR = 1.6;

/** How far to walk sideways looking for the parcels that make a gap a street. */
const CORRIDOR_REACH_M = 45;
const CORRIDOR_STEP_M = 3;

/**
 * How far a street has to run in its own direction before it counts as one, and
 * how far is far enough that there is no point measuring further.
 */
const RUN_MIN_M = 24;
const RUN_LIMIT_M = 60;

/**
 * How many places one street may be named, and how far apart those have to be.
 *
 * A single label per street reads well over the whole territory and disappears
 * the moment a rep zooms into the block they are walking — which is exactly when
 * knowing the street matters most. So a long street gets its name written a few
 * times along its length, the way a paper map does.
 *
 * The separation here is in metres and is only about not writing the same name
 * twice in the same breath. How often a repeat is worth drawing depends on how
 * much of the street is on screen, which is a question only the renderer can
 * answer — and it does, by dropping repeats that crowd each other in pixels.
 */
const PLACEMENTS_PER_STREET = 4;
const MIN_SEPARATION_M = 180;

const M_PER_DEG_LAT = 110540;
const M_PER_DEG_LNG = 111320;

/**
 * The street a `situs` address is on, in map form — or null if it has none.
 *
 * `68 SNYDER AVENUE, Ramsey NJ 07446` and `41 SNYDER AVE, Ramsey NJ 07446` are
 * the same street spelled two ways, and the published run contains both, so the
 * suffix is normalised before anything is grouped. Without that, Snyder Avenue
 * is two streets of 29 and 13 doors and neither may make the cut.
 */
export function streetName(situs) {
  const match = /^\s*[\w-]+\s+(.+?)\s*,/.exec(String(situs || ''));
  if (!match) return null;

  const words = match[1].toUpperCase().split(/\s+/).filter(Boolean);
  if (!words.length) return null;

  const last = words[words.length - 1];
  if (SUFFIXES[last]) words[words.length - 1] = SUFFIXES[last];
  return words.join(' ');
}

/** The outer rings of a polygon or multipolygon; holes are not fronted by houses. */
function outerRings(geometry) {
  if (!geometry) return [];
  if (geometry.type === 'Polygon') return [geometry.coordinates[0]].filter(Boolean);
  if (geometry.type === 'MultiPolygon') {
    return geometry.coordinates.map((polygon) => polygon[0]).filter(Boolean);
  }
  return [];
}

function ringMean(ring) {
  const points = ring.length > 1 ? ring.slice(0, -1) : ring;
  let x = 0;
  let y = 0;
  for (const [lng, lat] of points) {
    x += lng;
    y += lat;
  }
  return [x / points.length, y / points.length];
}

/**
 * The territory as flat metres.
 *
 * Degrees of longitude are shorter than degrees of latitude by the cosine of the
 * latitude, and at Ramsey's 41° that is a quarter — enough that an angle
 * measured in raw degrees would have every label visibly off the street it
 * names. One local scale for the whole territory is plenty: it spans about two
 * kilometres, over which the error in that cosine is nothing.
 */
function projector(latitude) {
  const kx = M_PER_DEG_LNG * Math.cos((latitude * Math.PI) / 180);
  return {
    toMetres: ([lng, lat]) => [lng * kx, lat * M_PER_DEG_LAT],
    toLngLat: ([x, y]) => [x / kx, y / M_PER_DEG_LAT],
  };
}

/** Ray casting, with the ring's own bounding box as the cheap rejection. */
function inRing(x, y, ring, box) {
  if (x < box[0] || x > box[2] || y < box[1] || y > box[3]) return false;

  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i];
    const [xj, yj] = ring[j];
    if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi + 1e-12) + xi) {
      inside = !inside;
    }
  }
  return inside;
}

function median(values) {
  const sorted = [...values].sort((a, b) => a - b);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
}

/**
 * Place one street's labels: midpoints of facing pairs, angled along the road.
 *
 * Every pair of the street's parcels is a candidate. A pair survives if it is
 * neither too close (two neighbours side by side, whose midpoint is a shared lot
 * line) nor too far (diagonally across a block), if its midpoint lands on no
 * parcel at all, and if that midpoint has parcels close by on both sides — the
 * corridor test, which is what separates a road from the open ground at the edge
 * of the territory or the middle of a bend.
 *
 * Of the survivors, the narrowest crossings are the ones genuinely facing each
 * other, and among those the first label goes to the one nearest the street's
 * median parcel: mid-street, where a name reads as belonging to the whole road
 * rather than to whichever end happened to win. Any further labels are taken
 * outwards from there, each far enough from the ones already placed to be a
 * second sighting of the street rather than a stutter.
 */
function placeStreet(name, centres, coverAt) {
  const candidates = [];

  for (let i = 0; i < centres.length; i += 1) {
    for (let j = i + 1; j < centres.length; j += 1) {
      const [ax, ay] = centres[i];
      const [bx, by] = centres[j];
      const dx = bx - ax;
      const dy = by - ay;
      const span = Math.hypot(dx, dy);
      if (span < MIN_WIDTH_M || span > MAX_WIDTH_M) continue;

      const mid = [(ax + bx) / 2, (ay + by) / 2];
      if (coverAt(mid[0], mid[1])) continue;

      // Across the street, as a unit vector; the label runs at right angles to it.
      const across = [dx / span, dy / span];
      const along = [-across[1], across[0]];
      if (!facesStreet(mid, across, name, coverAt)) continue;
      if (freeRun(mid, along, coverAt) < RUN_MIN_M) continue;

      candidates.push({ span, mid, along });
    }
  }
  if (!candidates.length) return [];

  const narrowest = Math.min(...candidates.map((candidate) => candidate.span));
  const facing = candidates.filter(
    (candidate) => candidate.span <= narrowest * TYPICAL_WIDTH_FACTOR
  );

  // Nearest the middle of the street first, so the name reads as the whole
  // road's rather than as whichever end happened to win. Deliberately not "the
  // longest free run": that objective quietly prefers the widest corridor in
  // sight, which for a short street is the main road it joins, and a label that
  // maximises its own elbow room by drifting onto the next street over is worse
  // than no label at all.
  const mx = median(centres.map(([x]) => x));
  const my = median(centres.map(([, y]) => y));
  facing.sort(
    (a, b) =>
      Math.hypot(a.mid[0] - mx, a.mid[1] - my) - Math.hypot(b.mid[0] - mx, b.mid[1] - my)
  );

  const placed = [];
  for (const candidate of facing) {
    if (placed.length >= PLACEMENTS_PER_STREET) break;
    const clear = placed.every(
      (taken) =>
        Math.hypot(candidate.mid[0] - taken.mid[0], candidate.mid[1] - taken.mid[1]) >=
        MIN_SEPARATION_M
    );
    if (clear) placed.push(candidate);
  }
  return placed;
}

/**
 * Is this gap this street? Step out sideways and read the addresses either side.
 *
 * A midpoint outside every parcel is necessary but not sufficient: so is the
 * field past the last house, the space inside a bend, and — the one that
 * actually misleads — the next road over, which two houses on a corner can
 * easily have their midpoint land in. A road is bounded on both sides within a
 * few seconds' walk, and the parcels doing the bounding front the street whose
 * name is about to be written there. Anything else gets no label rather than a
 * label in the wrong place.
 */
function facesStreet(point, across, name, coverAt) {
  for (const sign of [1, -1]) {
    let found = null;
    for (let d = CORRIDOR_STEP_M; d <= CORRIDOR_REACH_M; d += CORRIDOR_STEP_M) {
      found = coverAt(point[0] + across[0] * sign * d, point[1] + across[1] * sign * d);
      if (found) break;
    }
    if (found !== name) return false;
  }
  return true;
}

/**
 * How far the label could run in its own direction before it hits a parcel —
 * the shorter of the two ways, so it is room either side of the anchor.
 *
 * This is the measurement that decides which pair a street's name is hung on,
 * and it is the same question a reader asks of the finished map: does this name
 * lie along the road? A pair truly facing across the street answers with the
 * length of the block. Two pairs that are merely diagonal neighbours, or two
 * houses either side of a side-yard gap, point somewhere that leaves the
 * roadway almost immediately and answer with a few metres — which is what keeps
 * a street's name from being written at right angles to the street.
 */
function freeRun(point, along, coverAt) {
  let shortest = RUN_LIMIT_M;
  for (const sign of [1, -1]) {
    let run = RUN_LIMIT_M;
    for (let d = CORRIDOR_STEP_M; d <= RUN_LIMIT_M; d += CORRIDOR_STEP_M) {
      if (coverAt(point[0] + along[0] * sign * d, point[1] + along[1] * sign * d)) {
        run = d - CORRIDOR_STEP_M;
        break;
      }
    }
    shortest = Math.min(shortest, run);
  }
  return shortest;
}

/**
 * Where to write the main streets' names, in the order they earn their place.
 *
 * Streets are ranked by how many doors front them, so the ones a rep would
 * orient by — the ones the territory is mostly made of — are the ones named.
 * The returned order is what the renderer drops labels in when two collide on
 * screen: every street's best placement first, in rank order, then the repeats
 * along the longer streets. A crowded view therefore loses the second sighting
 * of Wyckoff Avenue before it loses the only sighting of Pine Street.
 *
 * @param {{properties: object, geometry: object|null, centroid?: [number, number]|null}[]} doors
 * @param {{limit?: number}} [options] `limit` caps how many streets are named.
 * @returns {{name: string, doors: number, position: [number, number], bearing: number}[]}
 */
export function streetLabels(doors, { limit = DEFAULT_LIMIT } = {}) {
  const parcels = [];
  for (const door of doors || []) {
    const rings = outerRings(door.geometry);
    if (!rings.length) continue;
    parcels.push({
      name: streetName(door.properties && door.properties.situs),
      rings,
      centre: door.centroid || ringMean(rings[0]),
    });
  }
  if (!parcels.length) return [];

  const { toMetres, toLngLat } = projector(parcels[0].centre[1]);

  // Every ring in metres with its bounding box, so "what is under this point" is
  // a box test for all but the few parcels actually near it.
  const rings = [];
  for (const parcel of parcels) {
    for (const ring of parcel.rings) {
      const flat = ring.map(toMetres);
      const xs = flat.map(([x]) => x);
      const ys = flat.map(([, y]) => y);
      rings.push({
        // A parcel with no readable address still covers ground; it just cannot
        // vouch for a street, so it answers with a name no street can equal.
        name: parcel.name || UNADDRESSED,
        ring: flat,
        box: [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)],
      });
    }
  }

  /** The street of the parcel under a point in metres, or null for open ground. */
  const coverAt = (x, y) => {
    for (const entry of rings) {
      if (inRing(x, y, entry.ring, entry.box)) return entry.name;
    }
    return null;
  };

  const groups = new Map();
  for (const parcel of parcels) {
    if (!parcel.name) continue;
    if (!groups.has(parcel.name)) groups.set(parcel.name, []);
    groups.get(parcel.name).push(toMetres(parcel.centre));
  }

  const ranked = [...groups.entries()]
    .filter(([, centres]) => centres.length >= MIN_DOORS)
    .sort((a, b) => b[1].length - a[1].length || a[0].localeCompare(b[0]));

  /** Each named street's placements, best first — a row per street. */
  const rows = [];
  for (const [name, centres] of ranked) {
    if (rows.length >= limit) break;

    const placements = placeStreet(name, centres, coverAt);
    if (!placements.length) continue;

    rows.push(
      placements.map((placement) => ({
        name,
        doors: centres.length,
        position: toLngLat(placement.mid),
        // Text reads left to right, so a street running up-left is labelled
        // running down-right: the same line, never upside down.
        bearing: uprightBearing(placement.along),
      }))
    );
  }

  // Read the rows column by column: best placements first, repeats afterwards.
  const labels = [];
  for (let column = 0; column < PLACEMENTS_PER_STREET; column += 1) {
    for (const row of rows) {
      if (row[column]) labels.push(row[column]);
    }
  }
  return labels;
}

function uprightBearing([x, y]) {
  let bearing = (Math.atan2(y, x) * 180) / Math.PI;
  if (bearing > 90) bearing -= 180;
  if (bearing < -90) bearing += 180;
  return bearing;
}
