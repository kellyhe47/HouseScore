/**
 * The map's load/error/retry machine (R9.3, wireframe frame 6).
 *
 * Three states and three events, kept as an explicit transition table rather
 * than a scatter of booleans, because the degraded states are the ones nobody
 * exercises by hand: `isLoading && hasError` is representable with flags and
 * meaningless on screen, and "retry" firing while a load is already in flight
 * is how a map ends up fetching twice and rendering the slower answer.
 *
 * Unknown transitions are no-ops that report the current state, so a caller can
 * always use the return value as "what to render now" without checking first.
 *
 * DOM-free by construction so `node --test` can import it.
 */

/**
 * state → event → next state. Anything absent is a no-op.
 *
 * `ready → error` is here because the doors layer can stop being available
 * after it has drawn — a refetch that 500s, the demo trigger — and R9.3's
 * banner is the answer in either case. `ready → loading` is deliberately absent:
 * once the map is drawn there is nothing to show a skeleton over, so a reload
 * goes through `error` or not at all.
 */
const TRANSITIONS = {
  loading: { ready: 'ready', fail: 'error' },
  ready: { fail: 'error' },
  error: { retry: 'loading' },
};

/**
 * A fresh machine, in `loading` — the state the page is in before its first
 * byte of door data arrives, which is the state it must be able to render.
 *
 * @returns {{state: string, ready(): string, fail(): string, retry(): string}}
 */
export function createMapState() {
  const machine = {
    state: 'loading',
  };

  const send = (event) => {
    const next = TRANSITIONS[machine.state][event];
    if (next) machine.state = next;
    return machine.state;
  };

  /** The doors layer arrived and drew. */
  machine.ready = () => send('ready');
  /** The doors fetch failed — show the banner, keep the chrome (R9.3). */
  machine.fail = () => send('fail');
  /** The rep pressed Retry. Only means anything from `error`. */
  machine.retry = () => send('retry');

  return machine;
}
