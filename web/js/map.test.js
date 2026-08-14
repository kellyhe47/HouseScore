import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  appliedFit,
  centreBounds,
  createMapHarness,
  doorsGeojson,
  streetBlock,
  territoryCentres,
} from './test-fixtures.js';

/* ── T022: the initial camera has to frame the territory, every time ─────────
 *
 * Manual QA, flow 7: the first load in a fresh tab drew all 540 parcels, and
 * every reload of the same tab showed empty grey. The chrome was fine, the
 * readout still said `540 of 540 scored`, and two clicks of zoom-out brought the
 * whole territory back — so the data and the layer were both there and only the
 * opening camera was wrong.
 *
 * What differs between those two loads is timing, not state (localStorage and
 * sessionStorage were empty). A fresh tab fetches `doors.geojson` over the
 * network, which hands the browser a frame to lay the freshly-unhidden map
 * screen out before MapLibre is asked to frame anything. A reload serves the
 * same file from cache, so the style can be up while the container is still
 * unmeasured — and a `fitBounds` issued against a viewport of no size is a
 * silent no-op in MapLibre, which is exactly the clean-console symptom QA saw.
 *
 * So these tests hold the two events the bug turns on — the container becoming
 * measurable, and the style's `load` firing — and drive them in both orders.
 * The requirement is the same either way: by the time the map is on screen, the
 * camera is fitted to the bounds of the doors that loaded.
 *
 * `map.js` boots itself on import, so each case imports it under a fresh
 * specifier. That is what makes a reload testable: new module state, same tab.
 */

/** Boot `map.js` into a harness, run `drive`, then put the globals back. */
async function withBrowser(options, drive) {
  const harness = createMapHarness(options).install();
  try {
    return await drive(harness);
  } finally {
    harness.restore();
  }
}

test('T022 · reload ordering: the style loads before the container is measured, and the territory is still framed', async () => {
  await withBrowser({}, async (harness) => {
    // A cached `doors.geojson` wins the race: the map is built and its style is
    // up while the map screen has only just been unhidden and not yet laid out.
    const map = await harness.boot();
    await harness.loadStyle();
    await harness.layout();

    const fit = appliedFit(map);
    assert.ok(
      fit,
      'the map never framed the territory against a real viewport — this is the blank ' +
        'grey map QA saw on every reload. fitBounds calls: ' +
        JSON.stringify(map.fits)
    );
    assert.deepEqual(fit.bounds, centreBounds());
  });
});

test('T022 · fresh-tab ordering: the container is measured before the style loads, and the territory is framed', async () => {
  await withBrowser({}, async (harness) => {
    // The slow-network path that always worked by hand. It must keep working.
    const map = await harness.boot();
    await harness.layout();
    await harness.loadStyle();

    const fit = appliedFit(map);
    assert.ok(fit, 'the territory was not framed on a first load');
    assert.deepEqual(fit.bounds, centreBounds());
  });
});

test('T022 · every initialisation frames the territory, not only the first one', async () => {
  await withBrowser({}, async (harness) => {
    // Load one, in a fresh tab: the doors come over the network, so the screen
    // is laid out before the style is up.
    const first = await harness.boot();
    await harness.layout();
    await harness.loadStyle();
    assert.ok(appliedFit(first), 'the first load did not frame the territory');

    // Reload: same tab and same storage, fresh document and fresh module state —
    // and now the doors come from cache, so the style wins the race.
    harness.reload();
    const second = await harness.boot();
    await harness.loadStyle();
    await harness.layout();

    assert.notEqual(second, first, 'the reload did not build a new map');
    assert.ok(
      appliedFit(second),
      'the territory was framed on first paint but not on reload — R9.1 says all ~540 ' +
        'parcels are on screen, and a refresh is ordinary use'
    );
    assert.deepEqual(appliedFit(second).bounds, centreBounds());
  });
});

test('T022 · the fit follows the doors that loaded, not a hardcoded centre and zoom', async () => {
  // A territory nowhere near the constructor's opening centre. If the camera is
  // framed from the data, the fit says so; if it is framed from a constant, the
  // map opens on empty ground exactly as QA described.
  const centres = territoryCentres().map(([lng, lat]) => [lng + 0.4, lat - 0.3]);

  await withBrowser({ doors: doorsGeojson(centres) }, async (harness) => {
    const map = await harness.boot();
    await harness.layout();
    await harness.loadStyle();

    const fit = appliedFit(map);
    assert.ok(fit, 'the territory was not framed');
    assert.deepEqual(
      fit.bounds,
      centreBounds(centres),
      'the camera was fitted to something other than the loaded doors'
    );
    assert.notDeepEqual(
      map.center,
      map.options.center,
      'the camera never moved off the hardcoded opening centre'
    );
  });
});

/* ── Street names over the canvas ────────────────────────────────────────────
 *
 * `streets.js` decides where a street's name belongs and which way it reads;
 * these are about the other half — that the names reach the screen at all, and
 * that two of them never end up written over each other, which is the failure a
 * derived label is most likely to produce and the one a reader cannot untangle.
 */

test('the main streets are named over the map once the doors are in', async () => {
  const doors = {
    type: 'FeatureCollection',
    features: streetBlock({ name: 'MAIN ST', lots: 15, start: [-74.152, 41.048] }),
  };

  await withBrowser({ doors }, async (harness) => {
    await harness.boot();
    await harness.layout();
    await harness.loadStyle();

    const drawn = harness.element('street-labels').children;
    assert.ok(drawn.length, 'the map drew no street names at all');
    assert.ok(
      drawn.every((label) => label.textContent === 'MAIN ST'),
      'a name appeared for a street that is not in the territory'
    );
    assert.ok(
      drawn.every((label) => /rotate\(-?\d+(\.\d+)?deg\)/.test(label.style.transform)),
      'a street name was laid down flat rather than along its street'
    );
  });
});

test('a long street says its name again further along, but not twice in a breath', async () => {
  // Six hundred metres of one street: too much of it is off screen at walking
  // zoom for a single label in the middle to be any use.
  const doors = {
    type: 'FeatureCollection',
    features: streetBlock({ name: 'LONG ST', lots: 45, start: [-74.152, 41.048] }),
  };

  await withBrowser({ doors }, async (harness) => {
    const map = await harness.boot();
    await harness.layout();
    await harness.loadStyle();

    const spots = harness.element('street-labels').children.map((label) => ({
      x: Number.parseFloat(label.style.left),
      y: Number.parseFloat(label.style.top),
    }));
    assert.ok(spots.length > 1, 'a long street was named only once');
    for (const [a, b] of pairs(spots)) {
      const apart = Math.hypot(a.x - b.x, a.y - b.y);
      assert.ok(apart >= 320, `the same street said its name twice ${apart.toFixed(0)}px apart`);
    }

    // Zoomed out, those same repeats are a stutter, and are dropped.
    map.zoom -= 3;
    map.fire('zoom');
    await harness.flush();
    assert.equal(harness.element('street-labels').children.length, 1);
  });
});

test('two street names are never written on top of each other', async () => {
  // Three parallel streets 55 metres apart. Close in, each name has its own
  // roadway to sit in; far enough out, all three want the same few pixels.
  const doors = {
    type: 'FeatureCollection',
    features: [
      ...streetBlock({ name: 'FIRST ST', lots: 15, start: [-74.152, 41.048] }),
      ...streetBlock({ name: 'SECOND ST', lots: 15, start: [-74.152, 41.0485] }),
      ...streetBlock({ name: 'THIRD ST', lots: 15, start: [-74.152, 41.049] }),
    ],
  };

  const drawn = (harness) =>
    harness.element('street-labels').children.map((label) => ({
      name: label.textContent,
      x: Number.parseFloat(label.style.left),
      y: Number.parseFloat(label.style.top),
    }));

  await withBrowser({ doors }, async (harness) => {
    const map = await harness.boot();
    await harness.layout();
    await harness.loadStyle();

    const close = drawn(harness);
    assert.equal(close.length, 3, 'with room for all three names, all three should be drawn');

    // Pull back until the three roadways are a few pixels apart.
    map.zoom -= 5;
    map.fire('zoom');
    await harness.flush();

    const far = drawn(harness);
    assert.ok(far.length, 'zooming out dropped every street name');
    assert.ok(far.length < close.length, 'three names cannot fit where one fits');
    for (const [a, b] of pairs(far)) {
      assert.ok(
        Math.abs(a.x - b.x) > 20 || Math.abs(a.y - b.y) > 12,
        `${a.name} and ${b.name} were drawn on the same few pixels`
      );
    }

    // And the dropped names come back as the map makes room for them again.
    map.zoom += 5;
    map.fire('zoom');
    await harness.flush();
    assert.equal(drawn(harness).length, close.length);
  });
});

function pairs(items) {
  const out = [];
  for (let i = 0; i < items.length; i += 1) {
    for (let j = i + 1; j < items.length; j += 1) out.push([items[i], items[j]]);
  }
  return out;
}
