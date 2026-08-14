import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  appliedFit,
  centreBounds,
  createMapHarness,
  doorsGeojson,
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
