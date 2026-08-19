/**
 * The Street View slot's four states, exercised as data (streetview.js).
 *
 * What matters here: the slot is context and never evidence — nothing in the
 * model references points or the score; the capture date renders as words;
 * unavailability is a designed state rather than an error; and a body the
 * server never promised (null, a 502 shape, junk) lands in `error` without
 * throwing.
 */

import test from 'node:test';
import assert from 'node:assert/strict';

import {
  ATTRIBUTION,
  CONTEXT_LABEL,
  ERROR_MESSAGE,
  UNAVAILABLE_MESSAGE,
  buildStreetView,
  captureLabel,
  loadingStreetView,
} from './streetview.js';

test('the slot starts in a loading state with a message to show', () => {
  const model = loadingStreetView();
  assert.equal(model.state, 'loading');
  assert.ok(model.message.length > 0);
});

test('an available answer carries the image, the date, the link and the labels', () => {
  const model = buildStreetView({
    status: 'available',
    capture_date: '2019-07',
    image_url: '/streetview/0248_1534_13/image',
    maps_url: 'https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=41,-74',
  });
  assert.equal(model.state, 'available');
  assert.equal(model.imageUrl, '/streetview/0248_1534_13/image');
  assert.equal(model.captureLabel, 'Captured Jul 2019');
  assert.equal(model.mapsUrl.includes('google.com/maps'), true);
  assert.equal(model.attribution, ATTRIBUTION);
  assert.equal(model.contextLabel, CONTEXT_LABEL);
});

test('the context label says the photo is not part of the score', () => {
  assert.match(CONTEXT_LABEL, /not used in the score/);
});

test('unavailable is a designed absence, not an error', () => {
  const model = buildStreetView({
    status: 'unavailable',
    reason: 'no_imagery',
    maps_url: 'https://maps.example',
  });
  assert.equal(model.state, 'unavailable');
  assert.equal(model.message, UNAVAILABLE_MESSAGE);
  // The rep can still jump to Google Maps and look around.
  assert.equal(model.mapsUrl, 'https://maps.example');
});

test('a keyless deployment renders exactly like missing imagery', () => {
  const model = buildStreetView({ status: 'unavailable', reason: 'not_configured', maps_url: 'x' });
  assert.equal(model.state, 'unavailable');
});

for (const body of [
  null,
  {},
  { error: 'streetview_unavailable', message: 'nope', maps_url: 'https://maps.example' },
  { status: 'available' }, // available without an image_url is not renderable
]) {
  test(`an unrecognised body lands in error: ${JSON.stringify(body)}`, () => {
    const model = buildStreetView(body);
    assert.equal(model.state, 'error');
    assert.equal(model.message, ERROR_MESSAGE);
  });
}

test('the error state keeps the Maps link when the server sent one', () => {
  const model = buildStreetView({ error: 'streetview_unavailable', maps_url: 'https://maps.example' });
  assert.equal(model.mapsUrl, 'https://maps.example');
});

test('capture dates render as words, and bad ones are dropped rather than shown raw', () => {
  assert.equal(captureLabel('2019-07'), 'Captured Jul 2019');
  assert.equal(captureLabel('2023-12'), 'Captured Dec 2023');
  assert.equal(captureLabel('2021'), 'Captured 2021');
  assert.equal(captureLabel('2021-00'), null);
  assert.equal(captureLabel('soon'), null);
  assert.equal(captureLabel(null), null);
  assert.equal(captureLabel(undefined), null);
});

test('the model survives a JSON round trip', () => {
  const model = buildStreetView({
    status: 'available',
    capture_date: '2019-07',
    image_url: '/streetview/p/image',
    maps_url: 'https://maps.example',
  });
  assert.deepEqual(JSON.parse(JSON.stringify(model)), model);
});
