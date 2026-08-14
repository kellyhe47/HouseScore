import { test } from 'node:test';
import assert from 'node:assert/strict';

import { requestRoute, createRoutePlanner } from './route-ui.js';
import { fakeFetch, routePayload } from './test-fixtures.js';

const START = [-74.156, 41.0447];
const REQUEST = { hours: 2, start: START, maxDoors: 20 };

const options = (payload = routePayload(), init) => {
  const fetch = fakeFetch(payload, init);
  return { fetch, apiBase: '/api' };
};

// ---------- requestRoute: the request itself ----------

test('requestRoute posts to /api/route', async () => {
  const opts = options();
  await requestRoute(REQUEST, opts);

  assert.equal(opts.fetch.calls.length, 1);
  assert.equal(opts.fetch.calls[0].url, '/api/route');
  assert.equal(opts.fetch.calls[0].init.method, 'POST');
});

test('requestRoute sends the planner the body it validates', async () => {
  const opts = options();
  await requestRoute(REQUEST, opts);

  const body = opts.fetch.calls[0].body;
  assert.equal(body.hours, 2);
  assert.deepEqual(body.start_point, START);
  assert.equal(body.max_doors, 20);
});

test('requestRoute honours a custom API base', async () => {
  const fetch = fakeFetch(routePayload());
  await requestRoute(REQUEST, { fetch, apiBase: 'https://api.example.invalid/api' });

  assert.equal(fetch.calls[0].url, 'https://api.example.invalid/api/route');
});

test('requestRoute passes excluded PINs through to the planner', async () => {
  const opts = options();
  await requestRoute({ ...REQUEST, exclude: ['0248_01101_00012'] }, opts);

  assert.deepEqual(opts.fetch.calls[0].body.exclude, ['0248_01101_00012']);
});

// ---------- requestRoute: the view model ----------

test('requestRoute maps every stop into a row', async () => {
  const view = await requestRoute(REQUEST, options());
  assert.equal(view.rows.length, 3);
});

test('rows keep the planner order, never a score sort (R10.3)', async () => {
  const view = await requestRoute(REQUEST, options());

  assert.deepEqual(
    view.rows.map((row) => row.pin),
    ['0248_01101_00012', '0248_01101_00020', '0248_3502_8.01']
  );
  assert.deepEqual(
    view.rows.map((row) => row.score),
    [100, 58, 81],
    'a descending score order here would mean the browser re-planned'
  );
});

test('rows are numbered from one, in route order', async () => {
  const view = await requestRoute(REQUEST, options());
  assert.deepEqual(
    view.rows.map((row) => row.n),
    [1, 2, 3]
  );
});

test('a row carries the address, score and talk track the planner sent', async () => {
  const view = await requestRoute(REQUEST, options());
  const row = view.rows[2];
  const source = routePayload().stops[2];

  assert.equal(row.pin, source.pams_pin);
  assert.equal(row.address, source.address);
  assert.equal(row.score, source.score);
  assert.equal(row.talkTrack, source.talk_track);
  assert.equal(row.cumulativeMinutes, source.cumulative_minutes);
  assert.equal(row.walkMinutes, source.walk_minutes);
});

test('a row shows an elapsed offset, not a clock time (R10.2)', async () => {
  const view = await requestRoute(REQUEST, options());

  assert.equal(view.rows[0].elapsedLabel, '+0 min');
  assert.equal(view.rows[2].elapsedLabel, '+42 min');
});

test('no row label anywhere reads as a time of day (R10.2)', async () => {
  const view = await requestRoute(REQUEST, options());

  for (const row of view.rows) {
    for (const value of Object.values(row)) {
      if (typeof value !== 'string') continue;
      assert.doesNotMatch(value, /\d{1,2}:\d{2}/, `clock time in ${value}`);
      assert.doesNotMatch(value, /\b\d{1,2}\s?(am|pm|AM|PM)\b/, `clock time in ${value}`);
    }
  }
});

test('the view carries the total and the estimate disclosure', async () => {
  const payload = routePayload();
  const view = await requestRoute(REQUEST, options(payload));

  assert.equal(view.totalMinutes, payload.total_minutes);
  assert.equal(view.estimateDisclosure, payload.estimate_disclosure);
});

test('the view model is plain data', async () => {
  const view = await requestRoute(REQUEST, options());
  assert.deepEqual(JSON.parse(JSON.stringify(view)), view);
});

// ---------- requestRoute: sad paths ----------

test('hours 0 yields an empty route, not an error', async () => {
  const empty = routePayload({ stops: [], total_minutes: 0 });
  const view = await requestRoute({ ...REQUEST, hours: 0 }, options(empty));

  assert.deepEqual(view.rows, []);
  assert.equal(view.isEmpty, true);
  assert.equal(view.totalMinutes, 0);
});

test('a route with stops is not empty', async () => {
  const view = await requestRoute(REQUEST, options());
  assert.equal(view.isEmpty, false);
});

test('a server error rejects and carries the status', async () => {
  const opts = options({ error: 'planner_failed', message: 'nope' }, { ok: false, status: 500 });

  const failure = await requestRoute(REQUEST, opts).then(
    () => null,
    (error) => error
  );

  assert.ok(failure instanceof Error, 'a failed plan must reject');
  assert.equal(failure.status, 500);
});

test('a network failure rejects', async () => {
  const fetch = async () => {
    throw new TypeError('Failed to fetch');
  };

  await assert.rejects(() => requestRoute(REQUEST, { fetch, apiBase: '/api' }));
});

// ---------- createRoutePlanner: live adjust (frame 4c) ----------

test('the planner exposes the view its last plan produced', async () => {
  const planner = createRoutePlanner(options());
  const view = await planner.plan(REQUEST);

  assert.equal(planner.view, view);
  assert.equal(view.rows.length, 3);
});

test('changing hours re-requests with the same start and cap', async () => {
  const opts = options();
  const planner = createRoutePlanner(opts);

  await planner.plan(REQUEST);
  await planner.plan({ hours: 3 });

  assert.equal(opts.fetch.calls.length, 2, 'a live adjust asks the planner again');
  assert.equal(opts.fetch.calls[1].body.hours, 3);
  assert.deepEqual(opts.fetch.calls[1].body.start_point, START);
  assert.equal(opts.fetch.calls[1].body.max_doors, 20);
});

test('changing the door count re-requests with the same hours and start', async () => {
  const opts = options();
  const planner = createRoutePlanner(opts);

  await planner.plan(REQUEST);
  await planner.plan({ maxDoors: 8 });

  assert.equal(opts.fetch.calls.length, 2);
  assert.equal(opts.fetch.calls[1].body.max_doors, 8);
  assert.equal(opts.fetch.calls[1].body.hours, 2);
});

// ---------- createRoutePlanner: excludeStop (frame 4c) ----------

test('excludeStop re-plans without that door', async () => {
  const opts = options();
  const planner = createRoutePlanner(opts);

  await planner.plan(REQUEST);
  await planner.excludeStop('0248_01101_00020');

  assert.equal(opts.fetch.calls.length, 2, 'excluding a door asks the planner again');
  assert.deepEqual(opts.fetch.calls[1].body.exclude, ['0248_01101_00020']);
  assert.equal(opts.fetch.calls[1].body.hours, 2, 'the budget survives the exclusion');
});

test('excludeStop accumulates exclusions across calls', async () => {
  const opts = options();
  const planner = createRoutePlanner(opts);

  await planner.plan(REQUEST);
  await planner.excludeStop('0248_01101_00020');
  await planner.excludeStop('0248_3502_8.01');

  assert.deepEqual(opts.fetch.calls[2].body.exclude, [
    '0248_01101_00020',
    '0248_3502_8.01',
  ]);
  assert.deepEqual(planner.excluded, ['0248_01101_00020', '0248_3502_8.01']);
});

test('excluding the same door twice does not duplicate it', async () => {
  const opts = options();
  const planner = createRoutePlanner(opts);

  await planner.plan(REQUEST);
  await planner.excludeStop('0248_01101_00020');
  await planner.excludeStop('0248_01101_00020');

  assert.deepEqual(planner.excluded, ['0248_01101_00020']);
});

test('the same exclusions produce the same request every time', async () => {
  const first = options();
  const second = options();

  for (const opts of [first, second]) {
    const planner = createRoutePlanner(opts);
    await planner.plan(REQUEST);
    await planner.excludeStop('0248_3502_8.01');
    await planner.excludeStop('0248_01101_00020');
  }

  assert.deepEqual(first.fetch.calls.at(-1).body, second.fetch.calls.at(-1).body);
});

test('the planner returns the server order after an exclusion, unsorted', async () => {
  const opts = options();
  const planner = createRoutePlanner(opts);

  await planner.plan(REQUEST);
  const view = await planner.excludeStop('nobody');

  assert.deepEqual(
    view.rows.map((row) => row.score),
    [100, 58, 81]
  );
});

test('a planner that has never planned has no view and no exclusions', () => {
  const planner = createRoutePlanner(options());

  assert.equal(planner.view, null);
  assert.deepEqual(planner.excluded, []);
});
