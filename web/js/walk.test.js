import { test } from 'node:test';
import assert from 'node:assert/strict';

import { createWalk, readWalk, clearWalk, resumeOffer } from './walk.js';
import { memoryStorage, routeRows } from './test-fixtures.js';

/**
 * The one key a walk lives under. It is pinned here rather than imported so a
 * rename shows up as a failing test: a rep who backgrounds the app mid-walk and
 * comes back to a new build must still find their walk (R10.4).
 */
const KEY = 'houseaccount.walk.v1';

const PINS = ['0248_01101_00012', '0248_01101_00020', '0248_3502_8.01'];

const saved = (storage) => JSON.parse(storage.getItem(KEY));

// ---------- createWalk: the current stop ----------

test('a fresh walk starts on the first stop', () => {
  const walk = createWalk(routeRows(), { storage: memoryStorage() });

  assert.equal(walk.index, 0);
  assert.equal(walk.current.pin, PINS[0]);
  assert.equal(walk.finished, false);
});

test('the current stop carries the address and talk track the rep reads', () => {
  const walk = createWalk(routeRows(), { storage: memoryStorage() });

  assert.equal(walk.current.address, '12 OAK ST, Ramsey NJ 07446');
  assert.ok(walk.current.talkTrack.trim(), 'the rep needs an opener at the door');
});

test('progress reads as a position in the route', () => {
  const walk = createWalk(routeRows(), { storage: memoryStorage() });
  assert.equal(walk.progressLabel, '1 of 3');

  walk.done();
  assert.equal(walk.progressLabel, '2 of 3');
});

// ---------- done / skip ----------

test('done advances to the next stop and records the knock', () => {
  const walk = createWalk(routeRows(), { storage: memoryStorage() });

  walk.done();

  assert.equal(walk.index, 1);
  assert.equal(walk.current.pin, PINS[1]);
  assert.deepEqual(walk.knocked, [PINS[0]]);
  assert.deepEqual(walk.skipped, []);
});

test('skip advances without recording a knock', () => {
  const walk = createWalk(routeRows(), { storage: memoryStorage() });

  walk.skip();

  assert.equal(walk.index, 1);
  assert.deepEqual(walk.knocked, []);
  assert.deepEqual(walk.skipped, [PINS[0]]);
});

test('done and skip mix across a walk', () => {
  const walk = createWalk(routeRows(), { storage: memoryStorage() });

  walk.done();
  walk.skip();
  walk.done();

  assert.deepEqual(walk.knocked, [PINS[0], PINS[2]]);
  assert.deepEqual(walk.skipped, [PINS[1]]);
});

test('a finished walk has no current stop', () => {
  const walk = createWalk(routeRows(), { storage: memoryStorage() });

  walk.done();
  walk.done();
  walk.done();

  assert.equal(walk.finished, true);
  assert.equal(walk.current, null);
});

test('advancing past the end is a no-op', () => {
  const walk = createWalk(routeRows(), { storage: memoryStorage() });

  for (let i = 0; i < 6; i++) walk.done();

  assert.equal(walk.index, 3);
  assert.deepEqual(walk.knocked, PINS);
});

test('a walk over no stops is finished from the start', () => {
  const walk = createWalk([], { storage: memoryStorage() });

  assert.equal(walk.finished, true);
  assert.equal(walk.current, null);
});

// ---------- the finish summary (R10.4) ----------

test('finishing a walk summarises knocks and skips', () => {
  const walk = createWalk(routeRows(), { storage: memoryStorage() });

  walk.done();
  walk.skip();
  walk.done();

  assert.equal(walk.summary, '2 doors knocked · 1 skipped');
});

test('a walk where every door was skipped says so', () => {
  const walk = createWalk(routeRows(), { storage: memoryStorage() });

  walk.skip();
  walk.skip();
  walk.skip();

  assert.equal(walk.summary, '0 doors knocked · 3 skipped');
});

// ---------- persistence ----------

test('starting a walk writes it under the stable key', () => {
  const storage = memoryStorage();
  createWalk(routeRows(), { storage });

  assert.ok(storage.getItem(KEY), `a walk must persist under ${KEY}`);
});

test('every advance persists the new position', () => {
  const storage = memoryStorage();
  const walk = createWalk(routeRows(), { storage });

  walk.done();

  assert.equal(saved(storage).index, 1);
  assert.deepEqual(saved(storage).knocked, [PINS[0]]);
});

test('the stored walk keeps the stops so a resume can render them', () => {
  const storage = memoryStorage();
  createWalk(routeRows(), { storage });

  assert.deepEqual(
    saved(storage).rows.map((row) => row.pin),
    PINS
  );
});

test('the stored walk carries a schema version', () => {
  const storage = memoryStorage();
  createWalk(routeRows(), { storage });

  assert.equal(typeof saved(storage).schema, 'number');
});

test('exit clears the stored walk', () => {
  const storage = memoryStorage();
  const walk = createWalk(routeRows(), { storage });

  walk.exit();

  assert.equal(storage.getItem(KEY), null);
});

test('a storage that throws does not break the walk', () => {
  const hostile = {
    getItem: () => {
      throw new Error('denied');
    },
    setItem: () => {
      throw new Error('denied');
    },
    removeItem: () => {
      throw new Error('denied');
    },
  };

  const walk = createWalk(routeRows(), { storage: hostile });
  walk.done();

  assert.equal(walk.index, 1, 'private browsing must not end the walk');
});

// ---------- readWalk / clearWalk ----------

test('readWalk returns the walk in progress', () => {
  const storage = memoryStorage();
  const walk = createWalk(routeRows(), { storage });
  walk.done();

  const found = readWalk(storage);

  assert.equal(found.index, 1);
  assert.deepEqual(
    found.rows.map((row) => row.pin),
    PINS
  );
});

test('readWalk returns null when nothing is stored', () => {
  assert.equal(readWalk(memoryStorage()), null);
});

test('readWalk returns null for garbage in storage', () => {
  assert.equal(readWalk(memoryStorage({ [KEY]: 'not json {' })), null);
});

test('readWalk returns null for a walk from an older schema', () => {
  const stale = memoryStorage({
    [KEY]: JSON.stringify({ schema: 0, pins: PINS, idx: 1, done: [] }),
  });

  assert.equal(readWalk(stale), null, 'an old shape must not be resumed as if it fits');
});

test('readWalk returns null for a walk that already finished', () => {
  const storage = memoryStorage();
  const walk = createWalk(routeRows(), { storage });
  walk.done();
  walk.done();
  walk.done();

  assert.equal(readWalk(storage), null, 'a finished walk is not a walk in progress');
});

test('clearWalk removes the stored walk', () => {
  const storage = memoryStorage();
  createWalk(routeRows(), { storage });

  clearWalk(storage);

  assert.equal(storage.getItem(KEY), null);
  assert.equal(readWalk(storage), null);
});

// ---------- the resume banner (R10.4) ----------

test('a walk in progress offers both Resume and Discard', () => {
  const storage = memoryStorage();
  const walk = createWalk(routeRows(), { storage });
  walk.done();

  const offer = resumeOffer(readWalk(storage));

  assert.equal(offer.canResume, true);
  assert.equal(offer.canDiscard, true);
});

test('the resume banner says where the walk left off', () => {
  const storage = memoryStorage();
  const walk = createWalk(routeRows(), { storage });
  walk.done();

  assert.equal(resumeOffer(readWalk(storage)).label, 'stop 2 of 3');
});

test('there is no offer when nothing was stored', () => {
  assert.equal(resumeOffer(null), null);
});

// ---------- resuming ----------

test('a resumed walk carries on from the stored position', () => {
  const storage = memoryStorage();
  const first = createWalk(routeRows(), { storage });
  first.done();
  first.skip();

  const resumed = createWalk(routeRows(), { storage, resume: readWalk(storage) });

  assert.equal(resumed.index, 2);
  assert.equal(resumed.current.pin, PINS[2]);
  assert.deepEqual(resumed.knocked, [PINS[0]]);
  assert.deepEqual(resumed.skipped, [PINS[1]]);
});

test('a resumed walk summarises the whole walk, not just what came after', () => {
  const storage = memoryStorage();
  const first = createWalk(routeRows(), { storage });
  first.done();
  first.skip();

  const resumed = createWalk(routeRows(), { storage, resume: readWalk(storage) });
  resumed.done();

  assert.equal(resumed.summary, '2 doors knocked · 1 skipped');
});

test('discarding a walk leaves nothing to resume', () => {
  const storage = memoryStorage();
  const walk = createWalk(routeRows(), { storage });
  walk.done();

  clearWalk(storage);

  assert.equal(resumeOffer(readWalk(storage)), null);
});
