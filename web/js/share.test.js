import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  encodeShare,
  readShare,
  shareUrl,
  copyAsText,
  copyShareLink,
  shareTokenVersion,
} from './share.js';
import { routeRows, routePayload } from './test-fixtures.js';

const PINS = ['0248_01101_00012', '0248_01101_00020', '0248_3502_8.01'];

/**
 * Produced by the server's own encoder:
 *
 *   .venv/bin/python -c "from houseaccount.route import encode_share, Stop; ..."
 *
 * `houseaccount.route.encode_share` is `r2` + base64url(zlib deflate) with the
 * padding stripped. The browser has to read what the server writes, so this
 * exact token is the compatibility pin — not a value the JS produced for itself.
 */
const SERVER_TOKEN = 'r2eNozMDKxiDcwNDQwjDcwMDA00jFAETAygAgYmxoYxVvoGRgCAPz0Clk';

/** The same route encoded by the server with an empty stop list. */
const SERVER_EMPTY_TOKEN = 'r2eNoDAAAAAAE';

const HREF = 'https://houseaccount.example/map#stale';

// ---------- encodeShare / readShare ----------

test('encodeShare writes a route fragment', async () => {
  const fragment = await encodeShare(routeRows());

  assert.match(fragment, /^#route=r2/, 'the fragment carries a current-contract r2 token');
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
    '#route=r2!!!!not-base64!!!!',
    '#route=r2AAAAAAAA',
    '#route=',
    '#something-else=r2abc',
    '#',
    '',
  ]) {
    assert.deepEqual(await readShare(fragment), [], `fragment ${JSON.stringify(fragment)}`);
  }
});

test('a token without the r2 prefix restores nothing', async () => {
  const fragment = await encodeShare(routeRows());
  const withoutPrefix = fragment.replace('r2', '');

  assert.deepEqual(await readShare(withoutPrefix), []);
});

test('readShare with no argument restores nothing outside a browser', async () => {
  assert.deepEqual(await readShare(), []);
});

// ---------- shareUrl ----------

test('shareUrl hangs the route off the page it is shared from', async () => {
  const url = await shareUrl(routeRows(), HREF);

  assert.match(url, /^https:\/\/houseaccount\.example\/map#route=r2/);
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

/* ── T106: the share link carries the score contract (R27/R30) ────────────────
 *
 * A share token minted under the dead V1 contract must never replay as a V2
 * route. The token self-identifies by prefix — `r2` is the current contract,
 * `r1` the dead one — mirroring `houseaccount.route.share_token_version`, and
 * the route view uses the answer to prompt a refresh instead of planning.
 */

// The V1 encoder's own output for the three fixture PINs — a real stale link.
const V1_TOKEN = 'r1eNozMDKxiDcwNDQwjDcwMDA00jFAETAygAgYmxoYxVvoGRgCAPz0Clk';

test('a current token reads as the current contract version', async () => {
  const fragment = await encodeShare(routeRows());
  assert.equal(shareTokenVersion(fragment.replace('#route=', '')), 'v2');
});

test('shareTokenVersion reads a full fragment as well as a bare token', async () => {
  assert.equal(shareTokenVersion(await encodeShare(routeRows())), 'v2');
  assert.equal(shareTokenVersion(`#route=${V1_TOKEN}`), 'v1');
});

test('a V1-era token identifies itself as v1', () => {
  assert.equal(shareTokenVersion(V1_TOKEN), 'v1');
});

test('a V1-era token decodes to no pins — a refresh, never a wrong route', async () => {
  assert.deepEqual(await readShare(`#route=${V1_TOKEN}`), []);
});

test('an unrecognizable token has no version', () => {
  assert.equal(shareTokenVersion('zzznotatoken'), null);
  assert.equal(shareTokenVersion(''), null);
  assert.equal(shareTokenVersion(null), null);
});
