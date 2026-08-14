import { test } from 'node:test';
import assert from 'node:assert/strict';

import { createMapState } from './state.js';

test('a fresh machine starts in loading', () => {
  assert.equal(createMapState().state, 'loading');
});

test('loading → ready when the doors layer arrives', () => {
  const m = createMapState();
  assert.equal(m.ready(), 'ready');
  assert.equal(m.state, 'ready');
});

test('loading → error when the fetch fails (R9.3)', () => {
  const m = createMapState();
  assert.equal(m.fail(), 'error');
  assert.equal(m.state, 'error');
});

test('retry from error returns to loading (wireframe frame 6)', () => {
  const m = createMapState();
  m.fail();
  assert.equal(m.retry(), 'loading');
  assert.equal(m.state, 'loading');
});

test('the full recovery path ends in ready', () => {
  const m = createMapState();
  m.fail();
  m.retry();
  m.ready();
  assert.equal(m.state, 'ready');
});

test('retry from ready is a no-op', () => {
  const m = createMapState();
  m.ready();
  assert.equal(m.retry(), 'ready');
  assert.equal(m.state, 'ready');
});

test('retry from loading is a no-op', () => {
  const m = createMapState();
  assert.equal(m.retry(), 'loading');
  assert.equal(m.state, 'loading');
});

test('a retried load can fail again', () => {
  const m = createMapState();
  m.fail();
  m.retry();
  assert.equal(m.fail(), 'error');
});

test('machines are independent instances', () => {
  const a = createMapState();
  const b = createMapState();
  a.ready();
  assert.equal(a.state, 'ready');
  assert.equal(b.state, 'loading');
});
