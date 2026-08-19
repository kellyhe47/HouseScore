/**
 * Taking a route off the screen: as text a rep can paste, or as a link that
 * reopens the same walk on another device (R10.4).
 *
 * The share token is the server's format, re-implemented rather than requested:
 * `r2` + base64url(zlib deflate) of the comma-joined PINs, padding stripped.
 * Asking the server to encode would put a network round trip between "Share
 * link" and the clipboard, and the browser already has a deflate — so the two
 * sides each write their own and read each other's.
 *
 * The bytes are *not* identical across the two: `zlib.compress(payload, 9)` and
 * `CompressionStream('deflate')` emit the same zlib container at different
 * compression levels. That is fine and deliberately pinned by tests on both
 * sides — the contract is "decodes", not "matches byte for byte".
 *
 * Everything here is async because `CompressionStream` is. DOM-free at import
 * time so `node --test` can import it.
 */

/**
 * Version marker — the score contract the link was minted under (R27/R30).
 * `r2` is the current contract; `r1` links were minted under the dead V1
 * contract and deliberately decode to nothing, so a stale link prompts a
 * refresh rather than replaying as a wrong route.
 */
const SHARE_PREFIX = 'r2';

/** Token prefixes that identify a score contract, current first. */
const TOKEN_VERSIONS = [
  ['r2', 'v2'],
  ['r1', 'v1'],
];

/** Everything a PAMS PIN can contain, and nothing a URL would mind. */
const PIN_PATTERN = /^[A-Za-z0-9._-]+$/;

/** The base64url alphabet, unpadded. */
const TOKEN_PATTERN = /^[A-Za-z0-9_-]*$/;

const COPIED_TEXT_MESSAGE = 'Route copied as text';
const COPIED_LINK_MESSAGE = 'Share link copied';

/**
 * The PIN of a stop, whichever shape it arrived in.
 *
 * `requestRoute` rows say `pin`; the planner's own stops say `pams_pin`. Sharing
 * works from either, so a caller never has to map before it can share.
 */
const pinOf = (stop) => stop.pin ?? stop.pams_pin;

/* ── The wire format ─────────────────────────────────────────────────────── */

async function deflate(text) {
  const stream = new CompressionStream('deflate');
  // Start draining before writing: a stream whose output nobody is reading can
  // block the writer once its internal queue fills.
  const packed = new Response(stream.readable).arrayBuffer();
  const writer = stream.writable.getWriter();
  const written = writer.write(new TextEncoder().encode(text)).then(() => writer.close());

  const [buffer] = await Promise.all([packed, written]);
  return new Uint8Array(buffer);
}

async function inflate(bytes) {
  const stream = new DecompressionStream('deflate');
  const text = new Response(stream.readable).text();
  const writer = stream.writable.getWriter();
  // A corrupt stream rejects on both ends. The read is the one that reports it;
  // the write's rejection is absorbed so it cannot surface as an unhandled one.
  const written = writer.write(bytes).then(() => writer.close());

  const [payload] = await Promise.all([text, written.catch(() => {})]);
  return payload;
}

function toBase64Url(bytes) {
  let binary = '';
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function fromBase64Url(token) {
  const padded = token.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - (token.length % 4)) % 4);
  const binary = atob(padded);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index++) bytes[index] = binary.charCodeAt(index);
  return bytes;
}

/**
 * The token inside whatever the caller passed: a full fragment, a query-ish
 * `route=…`, or the bare token itself.
 *
 * Accepting all three is not politeness — a share link arrives as
 * `location.hash`, a copied token arrives on its own, and the difference is not
 * something the person pasting it should have to think about.
 */
function tokenFrom(fragment) {
  const raw =
    fragment ?? (typeof location !== 'undefined' && location ? location.hash : '');
  const text = String(raw || '').trim();

  // Anchored so `#myroute=…` is not mistaken for `#route=…`.
  const match = /(?:^|[#&])route=([^&]*)/.exec(text);
  return match ? match[1] : text.replace(/^#/, '');
}

/* ── Public surface ──────────────────────────────────────────────────────── */

/**
 * Which score contract a share token was minted under (R27/R30).
 *
 * Mirrors `houseaccount.route.share_token_version`: `'v2'` for a current
 * token, `'v1'` for one minted under the dead contract, `null` for anything
 * unrecognizable. Accepts a full fragment or a bare token, like `readShare`.
 *
 * @param {string|null} [fragmentOrToken]
 * @returns {'v2'|'v1'|null}
 */
export function shareTokenVersion(fragmentOrToken) {
  if (fragmentOrToken === null || fragmentOrToken === undefined) return null;
  const token = tokenFrom(fragmentOrToken);
  for (const [prefix, version] of TOKEN_VERSIONS) {
    if (token.startsWith(prefix)) return version;
  }
  return null;
}

/**
 * Pack a route's PINs into a URL fragment.
 *
 * Order is the payload. The planner's order is the walk, so the fragment
 * preserves it exactly — a share link that re-sorted would hand the recipient a
 * different route from the one that was shared.
 *
 * @param {Array<{pin?: string, pams_pin?: string}>} stops
 * @returns {Promise<string>} e.g. `#route=r2eJwz…`
 */
export async function encodeShare(stops) {
  const payload = (stops ?? []).map(pinOf).join(',');
  return `#route=${SHARE_PREFIX}${toBase64Url(await deflate(payload))}`;
}

/**
 * The PINs a fragment carries, in order.
 *
 * Every failure mode — wrong prefix, bad base64, corrupt deflate stream,
 * contents that are not PINs — lands on the same empty answer, because a
 * mangled link should open an empty route rather than a broken page.
 *
 * @param {string} [fragment] defaults to the current location hash
 * @returns {Promise<string[]>}
 */
export async function readShare(fragment) {
  const token = tokenFrom(fragment);
  if (!token.startsWith(SHARE_PREFIX)) return [];

  const body = token.slice(SHARE_PREFIX.length);
  if (!TOKEN_PATTERN.test(body)) return [];

  let payload;
  try {
    payload = await inflate(fromBase64Url(body));
  } catch {
    return [];
  }

  const pins = payload ? payload.split(',') : [];
  return pins.every((pin) => PIN_PATTERN.test(pin)) ? pins : [];
}

/**
 * A full URL that reopens this route, hung off the page it was shared from.
 *
 * The fragment is replaced rather than appended: sharing twice from a page that
 * already carries a route must not produce a URL with two of them.
 *
 * @param {Array<object>} stops
 * @param {string} href
 * @returns {Promise<string>}
 */
export async function shareUrl(stops, href) {
  const url = new URL(href);
  url.hash = await encodeShare(stops);
  return url.toString();
}

/**
 * The route as pasteable text — one numbered line per stop.
 *
 * Elapsed offsets, never clock times (R10.2): the rep does not start when the
 * plan says they will, and a line reading "10:40" would be wrong by however
 * long they spent finding parking.
 *
 * @param {Array<object>} stops
 * @param {(event: {type: string, message: string}) => void} [emit]
 * @returns {string}
 */
export function copyAsText(stops, emit) {
  const rows = stops ?? [];
  // Nothing copied, nothing to confirm: a toast over an empty clipboard is a lie.
  if (rows.length === 0) return '';

  const text = rows
    .map((stop, index) => {
      const elapsed = stop.elapsedLabel ?? `+${Math.round(stop.cumulative_minutes ?? 0)} min`;
      const talk = stop.talkTrack ?? stop.talk_track ?? '';
      return `${index + 1}. ${stop.address} — ${elapsed} — ${talk}`;
    })
    .join('\n');

  writeClipboard(text);
  if (emit) emit({ type: 'toast', message: COPIED_TEXT_MESSAGE });
  return text;
}

/**
 * Put a share link on the clipboard and confirm it.
 *
 * @param {Array<object>} stops
 * @param {string} href
 * @param {(event: {type: string, message: string}) => void} [emit]
 * @returns {Promise<string>}
 */
export async function copyShareLink(stops, href, emit) {
  if (!stops || stops.length === 0) return '';

  const url = await shareUrl(stops, href);
  writeClipboard(url);
  if (emit) emit({ type: 'toast', message: COPIED_LINK_MESSAGE });
  return url;
}

/**
 * Best-effort clipboard write.
 *
 * Guarded on both axes — absent in `node --test`, and rejecting rather than
 * throwing when a browser refuses permission — because a refused clipboard must
 * not take the route list down with it.
 */
function writeClipboard(text) {
  try {
    if (typeof navigator !== 'undefined' && navigator.clipboard) {
      Promise.resolve(navigator.clipboard.writeText(text)).catch(() => {});
    }
  } catch {
    // No secure context, no permission: the text is on screen either way.
  }
}
