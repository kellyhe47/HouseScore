import { test } from 'node:test';
import assert from 'node:assert/strict';

import { encodeShare, readShare, shareUrl, copyAsText, copyShareLink } from './share.js';
import { routeRows, routePayload } from './test-fixtures.js';

const PINS = ['0248_01101_00012', '0248_01101_00020', '0248_3502_8.01'];

/**
 * Produced by the server's own encoder:
 *
 *   .venv/bin/python -c "from houseaccount.route import encode_share, Stop; ..."
 *
 * `houseaccount.route.encode_share` is `r1` + base64url(zlib deflate) with the
 * padding stripped. The browser has to read what the server writes, so this
 * exact token is the compatibility pin — not a value the JS produced for itself.
 */
const SERVER_TOKEN = 'r1eNozMDKxiDcwNDQwjDcwMDA00jFAETAygAgYmxoYxVvoGRgCAPz0Clk';

/** The same route encoded by the server with an empty stop list. */
const SERVER_EMPTY_TOKEN = 'r1eNoDAAAAAAE';

const HREF = 'https://houseaccount.example/map#stale';

// ---------- encodeShare / readShare ----------

test('encodeShare writes a route fragment', async () => {
  const fragment = await encodeShare(routeRows());

  assert.match(fragment, /^#route=r1/, 'the fragment carries an r1 token');
});

test('readShare restores exactly the order encodeShare wrote', async () => {
  const fragment = await encodeShare(routeRows());

  assert.deepEqual(await readShare(fragment), PINS);
});

test('the order survives a route the planner did not sort by score', async () => {
  const reversed = routeRows().reverse();
  const fragment = await encodeShare(reversed);

  assert.deepEqual(await readShare(fragment), [...PINS].reverse());
});

test('readShare reads a token the server encoded', async () => {
  assert.deepEqual(await readShare(`#route=${SERVER_TOKEN}`), PINS);
});

test('readShare reads a bare token as well as a full fragment', async () => {
  assert.deepEqual(await readShare(SERVER_TOKEN), PINS);
  assert.deepEqual(await readShare(`route=${SERVER_TOKEN}`), PINS);
});

test('readShare reads the empty route the server encodes', async () => {
  assert.deepEqual(await readShare(`#route=${SERVER_EMPTY_TOKEN}`), []);
});

test('encodeShare accepts raw planner stops as well as rows', async () => {
  const fragment = await encodeShare(routePayload().stops);

  assert.deepEqual(await readShare(fragment), PINS);
});

test('encoding an empty route round-trips to nothing', async () => {
  assert.deepEqual(await readShare(await encodeShare([])), []);
});

// ---------- readShare: sad paths ----------

test('garbage in the fragment restores nothing rather than throwing', async () => {
  for (const fragment of [
    '#route=not-a-token',
    '#route=r1!!!!not-base64!!!!',
    '#route=r1AAAAAAAA',
    '#route=',
    '#something-else=r1abc',
    '#',
    '',
  ]) {
    assert.deepEqual(await readShare(fragment), [], `fragment ${JSON.stringify(fragment)}`);
  }
});

test('a token without the r1 prefix restores nothing', async () => {
  const fragment = await encodeShare(routeRows());
  const withoutPrefix = fragment.replace('r1', '');

  assert.deepEqual(await readShare(withoutPrefix), []);
});

test('readShare with no argument restores nothing outside a browser', async () => {
  assert.deepEqual(await readShare(), []);
});

// ---------- shareUrl ----------

test('shareUrl hangs the route off the page it is shared from', async () => {
  const url = await shareUrl(routeRows(), HREF);

  assert.match(url, /^https:\/\/houseaccount\.example\/map#route=r1/);
  assert.deepEqual(await readShare(new URL(url).hash), PINS);
});

test('shareUrl replaces an existing fragment rather than appending to it', async () => {
  const url = await shareUrl(routeRows(), HREF);

  assert.equal(url.split('#').length, 2);
  assert.ok(!url.includes('stale'));
});

// ---------- copyAsText ----------

test('copyAsText writes one line per stop', () => {
  const text = copyAsText(routeRows(), () => {});

  assert.equal(text.split('\n').length, 3);
});

test('each line carries the address and the talk track', () => {
  const lines = copyAsText(routeRows(), () => {}).split('\n');
  const rows = routeRows();

  for (const [index, line] of lines.entries()) {
    assert.ok(line.includes(rows[index].address), `line ${index} is missing the address`);
    assert.ok(line.includes(rows[index].talkTrack), `line ${index} is missing the talk track`);
  }
});

test('a copied line is numbered and shows the elapsed offset, not a clock time', () => {
  const line = copyAsText(routeRows(), () => {}).split('\n')[2];

  assert.match(line, /^3\. /);
  assert.ok(line.includes('+42 min'), line);
  assert.doesNotMatch(line, /\d{1,2}:\d{2}/);
});

test('copyAsText fires a toast', () => {
  const events = [];
  copyAsText(routeRows(), (event) => events.push(event));

  assert.equal(events.length, 1);
  assert.equal(events[0].type, 'toast');
  assert.ok(events[0].message.trim());
});

test('copyAsText works without an emitter', () => {
  assert.ok(copyAsText(routeRows()).includes('12 OAK ST'));
});

test('copyAsText on an empty route copies nothing and says nothing', () => {
  const events = [];

  assert.equal(copyAsText([], (event) => events.push(event)), '');
  assert.deepEqual(events, [], 'there is nothing to confirm');
});

// ---------- copyShareLink ----------

test('copyShareLink returns the shareable URL and fires a toast', async () => {
  const events = [];
  const url = await copyShareLink(routeRows(), HREF, (event) => events.push(event));

  assert.equal(url, await shareUrl(routeRows(), HREF));
  assert.equal(events.length, 1);
  assert.equal(events[0].type, 'toast');
});

test('copyShareLink on an empty route says nothing', async () => {
  const events = [];
  await copyShareLink([], HREF, (event) => events.push(event));

  assert.deepEqual(events, []);
});
