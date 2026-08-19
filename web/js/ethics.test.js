import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import path from 'node:path';

import {
  CATEGORY_CAPS,
  MOVER_BLEND,
  RENTAL_MODIFIER,
  icpTrace,
  buildEthicsPage,
} from './ethics.js';
import {
  evalReport,
  runManifest,
  healthyManifest,
} from './test-fixtures.js';

/* ── T107: the Data & Ethics page describes the V2 contract (R29) ─────────────
 *
 * This page is the published rationale for the House Score, and it is only
 * worth anything if it describes the system that actually ran. V2 replaced
 * the additive V1 model with three capped base categories (project 45,
 * capacity 25, fit 30), a mover blend into a 90–100 priority band with
 * exponential decay (flat to day 90, zero at day 365, the 275-day denominator
 * derived as 365 − 90), and a −25 verified-rental demotion that currently
 * ships dormant. The page must say all of that, must source its evaluation
 * claims from the V2 `eval/report.json`, and must say none of the dead V1
 * things.
 */

// The V2 contract constants the page publishes. Mirrored from the plan's R2,
// R4–R6 — a page constant that disagreed with the engine would make the
// published rationale a lie about the map next to it.
const V2_CAPS = { project: 45, capacity: 25, fit: 48 };

test('the page publishes the exact V2 category caps', () => {
  assert.deepEqual(CATEGORY_CAPS, V2_CAPS);
});

test('the blend constants are the derived ones, not tuned copies', () => {
  assert.equal(MOVER_BLEND.flatDays, 90);
  assert.equal(MOVER_BLEND.zeroDays, 365);
  // 275 = 365 − 90: derived, and the page must present it that way.
  assert.equal(MOVER_BLEND.decayDenominatorDays, 275);
  assert.equal(MOVER_BLEND.decayDenominatorDays, MOVER_BLEND.zeroDays - MOVER_BLEND.flatDays);
  // The divisor 8 maps the 0–80 base range onto the 10-point priority band.
  assert.equal(MOVER_BLEND.baseDivisor, 8);
  assert.deepEqual(MOVER_BLEND.priorityBand, [90, 100]);
});

test('the rental modifier the page discloses is −25', () => {
  assert.equal(RENTAL_MODIFIER, -25);
});

/* ── Building the page ───────────────────────────────────────────────────── */

const page = (overrides = {}) =>
  buildEthicsPage({ report: evalReport(), manifest: runManifest(), ...overrides });

/** Every string anywhere in the view model, for the whole-page scans below. */
function strings(value, found = []) {
  if (typeof value === 'string') found.push(value);
  else if (Array.isArray(value)) value.forEach((item) => strings(item, found));
  else if (value && typeof value === 'object') Object.values(value).forEach((v) => strings(v, found));
  return found;
}

const pageText = (built = page()) => strings(built).join('\n');

// ---------- ICP and the signal→ICP trace (R11.4, retained under R29) ----------

test('the page states who the score is for', () => {
  const { icp } = page();

  assert.ok(icp.definition.trim(), 'the ICP has to be written down');
});

test('the trace covers the three V2 categories, the mover blend and the rental modifier', () => {
  const keys = icpTrace().map((row) => row.key);

  assert.deepEqual(keys, ['mover', 'project', 'capacity', 'fit', 'rental']);
});

test('every traced row names the ICP trait it serves', () => {
  for (const row of icpTrace()) {
    assert.ok(row.trait, `${row.key} traces to no ICP trait`);
    assert.ok(row.body.trim(), `${row.key} has no rationale`);
  }
});

test('each traced category declares its V2 cap', () => {
  const byKey = Object.fromEntries(icpTrace().map((row) => [row.key, row]));

  assert.equal(byKey.project.cap, 45);
  assert.equal(byKey.capacity.cap, 25);
  assert.equal(byKey.fit.cap, 48);
});

test('the mover row is a blend, not points', () => {
  const mover = icpTrace().find((row) => row.key === 'mover');

  assert.equal(mover.cap, null, 'the mover is not a capped point category');
  assert.match(mover.body, /blend|priority band/i);
});

test('the rental row is the only demotion, and says −25', () => {
  const rental = icpTrace().find((row) => row.key === 'rental');

  assert.match(rental.body, /[−-]\s?25/);
  const demotions = icpTrace().filter((row) => /[−-]\s?\d/.test(row.body));
  assert.deepEqual(demotions.map((row) => row.key), ['rental']);
});

test('the trace reaches the page', () => {
  assert.ok(page().trace.length >= 5);
});

// ---------- The model section: caps, blend, decay, rental (R29) ----------

test('the model section renders one row per category with the exact caps', () => {
  const { model } = page();

  assert.deepEqual(
    model.caps.map((row) => [row.key, row.cap]),
    [['project', 45], ['capacity', 25], ['fit', 48]]
  );
});

test('the blend formula is written down with the priority band', () => {
  const { model } = page();

  assert.ok(model.blend.formula.trim(), 'the blend formula must be published');
  assert.match(model.blend.formula.replace(/\s/g, ''), /90\+B\/8/);
  assert.deepEqual(model.blend.priorityBand, [90, 100]);
});

test('the decay is stated as exponential with the flat band and the zero', () => {
  const { model } = page();

  assert.match(model.decay.text, /exponential/i);
  assert.match(model.decay.text, /90/);
  assert.match(model.decay.text, /365/);
});

test('the 275-day denominator is presented as derived, not tunable', () => {
  const { model } = page();

  assert.match(model.decay.text, /275/);
  assert.match(model.decay.text, /derived|365\s?[−-]\s?90/i);
});

test('the rental precedence names −25 and its dormancy', () => {
  const { model } = page();

  assert.match(model.rental.text, /[−-]\s?25/);
  // Honest disclosure: no verified municipal registry has been supplied, so
  // the demotion path ships dormant and fires only in fixtures.
  assert.match(model.rental.text, /dormant/i);
  assert.match(model.rental.text, /municipal|registry|OPRA/i);
});

test('a stale registration is neutral, and missing rental data is neutral with a gap', () => {
  const { model } = page();

  assert.match(model.rental.text, /stale|older registration/i);
  assert.match(model.rental.text, /neutral/i);
});

test('unknown signals stay neutral — the page commits to it', () => {
  const { model } = page();
  const text = strings(model.unknowns).join(' ');

  assert.match(text, /neutral/i);
  assert.match(text, /missing|unknown/i);
  assert.doesNotMatch(text, /penali[sz]e/i);
});

// ---------- Project and condition evidence rules (R19/R24 phrasing) ----------

test('exterior condition is described as historical decline, never current state', () => {
  const text = pageText();

  assert.match(text, /historical decline|decline between the 2015 and 2020/i);
  const conditionClaims = strings(page()).filter((s) => /exterior[- ]condition|condition signal/i.test(s));
  for (const claim of conditionClaims) {
    assert.doesNotMatch(claim, /current condition|is currently in/i, claim);
  }
});

test('a later completed exterior permit supersedes the observation', () => {
  assert.match(pageText(), /completed (exterior )?permit.*(supersede|neutral)/is);
});

// ---------- ACS: a neighborhood-level prior, never a household claim (R15) ----------

test('the ACS prior is described as neighborhood-level', () => {
  assert.match(pageText(), /neighborhood-level|block[- ]group/i);
});

test('every sentence about the dual-income prior stays off the household', () => {
  for (const text of strings(page())) {
    if (!/dual[- ]income/i.test(text)) continue;
    assert.doesNotMatch(text, /household is dual-income|this household/i, text);
    assert.match(text, /block[- ]group|neighborhood/i, text);
  }
});

// ---------- The R36 validation plan (R29) ----------

test('the validation plan states the primary outcome', () => {
  const { validation } = page();

  assert.match(validation.primary, /receptive|qualified conversation/i);
  assert.match(validation.primary, /answered door|per door/i);
});

test('the validation plan states both secondary outcomes', () => {
  const { validation } = page();

  assert.match(validation.secondary, /follow[- ]up/i);
  assert.match(validation.secondary, /booked/i);
});

test('the success test is directional ranking lift, not a conversion number', () => {
  const { validation } = page();

  assert.match(validation.successTest, /ranking lift/i);
  assert.match(validation.successTest, /band|quantile/i);
});

test('the plan promises no absolute target before field outcomes exist', () => {
  const { validation } = page();
  const text = strings(validation).join(' ');

  assert.match(text, /no absolute|not.*absolute (conversion )?target|must not be invented/i);
  assert.doesNotMatch(text, /\d+%\s?(conversion|close rate)/i, 'an invented conversion target');
});

// ---------- Evaluation claims come from the V2 report (R29) ----------

test('the eval section is available when a report is', () => {
  assert.equal(page().evaluation.available, true);
  assert.equal(buildEthicsPage({ report: null, manifest: runManifest() }).evaluation.available, false);
});

test('the eval numbers are read from the report, never restated', () => {
  const report = evalReport();
  const { evaluation } = page();
  const text = strings(evaluation).join(' ');

  assert.ok(text.includes(String(report.coverage.doors_scored)), 'doors scored missing');
  assert.match(text, /0\.81|81\.8/, 'vision precision missing');
});

test('a different report renders different numbers', () => {
  const other = evalReport({ coverage: { doors_total: 12, doors_scored: 9, coverage: 0.75 } });
  const text = strings(buildEthicsPage({ report: other, manifest: runManifest() }).evaluation).join(' ');

  assert.ok(text.includes('9'), text);
  assert.ok(!text.includes('540'), 'a stale count survived the report changing');
});

test('the frozen-fixture banner travels with the vision numbers', () => {
  const { evaluation } = page();
  const text = strings(evaluation).join(' ');

  assert.match(text, /NOT A MEASUREMENT|frozen fixture/i);
});

test('a missing report invents no measurements', () => {
  const { evaluation } = buildEthicsPage({ report: null, manifest: null });
  const text = strings(evaluation).join(' ');

  assert.doesNotMatch(text, /\b540\b|\b0\.81\b|\b42\b/);
  assert.doesNotMatch(text, /undefined|NaN|null/);
});

// ---------- Counts are measurements, not prose (regression of V1 T025) ----------

const fixtureNote = (report) => buildEthicsPage({ report, manifest: runManifest() }).model.note;

test('the golden-fixture count is read from report.golden, not spelled in', () => {
  const note = fixtureNote(evalReport());

  assert.ok(note.includes('42'), note);
  assert.match(note, /golden fixtures/);
});

test('a different published count changes the sentence', () => {
  const note = fixtureNote(evalReport({ golden: { fixtures_total: 7 } }));

  assert.ok(note.includes('7'), note);
  assert.doesNotMatch(note, /\b42\b|forty-two/i);
});

test('no fixture count is spelled into the page as a word', () => {
  for (const text of strings(page())) {
    assert.doesNotMatch(text, /\b(twelve|thirteen|forty-two)\s+golden\b/i, text);
  }
});

test('with no published report the page states no fixture count', () => {
  const note = buildEthicsPage({ report: null, manifest: null }).model.note;

  assert.ok(note.trim());
  assert.match(note, /deterministic/i);
  assert.doesNotMatch(note, /undefined|NaN|null/);
  assert.doesNotMatch(note, /\d+ golden fixtures|\b42\b/);
});

test('the door counts on the page come from the artifacts, not literals', () => {
  const built = buildEthicsPage({ report: null, manifest: null });

  for (const text of strings(built)) {
    assert.doesNotMatch(text, /\b540\b/, `a hardcoded door count survives: ${text}`);
    assert.doesNotMatch(text, /\b42\b/, `a hardcoded fixture count survives: ${text}`);
  }
});

test('the coverage figure is computed from the manifest it was given', () => {
  const built = buildEthicsPage({
    report: evalReport(),
    manifest: runManifest({ doors_total: 10, doors_scored: 8 }),
  });
  const text = pageText(built);

  assert.match(text, /8 of 10 doors/);
});

// ---------- The forbidden V1 claims (R29's remove-list) ----------

const FORBIDDEN_V1 = [
  ['the old additive formula', /raw total|raw_total|sum of (the )?five groups/i],
  ['the hires-out group', /hires[\s_-]?out/i],
  ['the old group maxima', /group max(imum)? (100|60)|mover.{0,12}max 100/i],
  ['the provider-churn claim', /(provider|contractor)[\s_-]?churn|no repeat contractor/i],
  ['the deferred-maintenance bonus', /deferred[\s_-]?maintenance/i],
  ['the −15 rental language', /(^|[^\d])[−-]15\b/],
  ['the 90-day hard cutoff as current behavior', /90[\s-]day[s]?\s?(hard\s)?(cutoff|cut-off)/i],
  ['V1 weight keys', /mover_(30|60|90)d|permit_each|absentee_modifier/],
];

test('the page makes none of the dead V1 claims — whole-page scan', () => {
  const offences = [];
  for (const text of strings(page())) {
    for (const [name, pattern] of FORBIDDEN_V1) {
      if (pattern.test(text)) offences.push(`${name}: ${JSON.stringify(text.slice(0, 120))}`);
    }
  }
  assert.deepEqual(offences, []);
});

test('the healthy-manifest page is clean too', () => {
  const built = buildEthicsPage({ report: evalReport(), manifest: healthyManifest() });
  for (const text of strings(built)) {
    for (const [name, pattern] of FORBIDDEN_V1) {
      assert.doesNotMatch(text, pattern, `${name} in ${text.slice(0, 120)}`);
    }
  }
});

test('the shipped ethics.html makes none of the dead V1 claims either', () => {
  const markup = readFileSync(path.join(import.meta.dirname, '..', 'ethics.html'), 'utf8');
  for (const [name, pattern] of FORBIDDEN_V1) {
    assert.doesNotMatch(markup, pattern, name);
  }
});

// ---------- Source limitations (R29) ----------

test('the source limitations name the V2 realities', () => {
  const text = pageText();

  assert.match(text, /point[- ]in[- ]time|SDL/i, 'SDL point-in-time limitation');
  assert.match(text, /SR1A/i, 'SR1A lag');
  assert.match(text, /contractor identity|no contractor/i, 'no contractor identity');
  assert.match(text, /imagery vintage|2015.*2020/is, 'imagery vintages');
});

// ---------- Degraded shapes still render ----------

test('a manifest with no resolve block does not crash', () => {
  const manifest = runManifest();
  delete manifest.resolve;

  assert.ok(buildEthicsPage({ report: evalReport(), manifest }));
});

test('a missing manifest does not crash', () => {
  const built = buildEthicsPage({ report: evalReport(), manifest: null });

  assert.ok(built.trace.length, 'the rationale does not depend on a published run');
});

test('a page built with neither artifact still renders the rationale', () => {
  const built = buildEthicsPage({});

  assert.equal(built.evaluation.available, false);
  assert.ok(built.trace.length);
  assert.deepEqual(
    built.model.caps.map((row) => row.cap),
    [45, 25, 48],
    'the contract is the page, with or without a run to report on'
  );
});

test('the page is plain data', () => {
  const built = page();

  assert.deepEqual(JSON.parse(JSON.stringify(built)), built);
});

/* ── The map header, now that both features ship ───────────────────────────── */

const INDEX_HTML = path.join(import.meta.dirname, '..', 'index.html');
const ETHICS_HTML = path.join(import.meta.dirname, '..', 'ethics.html');

test('the header buttons no longer advertise shipped features as upcoming', () => {
  const markup = readFileSync(INDEX_HTML, 'utf8');

  assert.doesNotMatch(markup, /(ships|arrives) in the next build/i);
});

test('the Data & Ethics button points at the ethics page', () => {
  const markup = readFileSync(INDEX_HTML, 'utf8');

  assert.match(markup, /ethics\.html/);
});

/* ── The MCP endpoint the page publishes (T024) ─────────────────────────────── */

const REPO_ROOT = path.join(import.meta.dirname, '..', '..');
const WEB_DIR = path.join(REPO_ROOT, 'web');

/** The one app `fly.toml` declares. */
function flyAppName() {
  const toml = readFileSync(path.join(REPO_ROOT, 'fly.toml'), 'utf8');
  const match = toml.match(/^\s*app\s*=\s*["']([^"']+)["']/m);
  assert.ok(match, 'fly.toml declares no app name');
  return match[1];
}

/** Every file `web/` ships, as `[relative path, text]`. */
function webFiles(dir = WEB_DIR, trail = 'web') {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name);
    const shown = `${trail}/${entry.name}`;
    if (entry.isDirectory()) return webFiles(full, shown);
    if (!/\.(html|js|css|json)$/.test(entry.name)) return [];
    return [[shown, readFileSync(full, 'utf8')]];
  });
}

test('the MCP section derives its endpoint instead of pinning a host', () => {
  const markup = readFileSync(ETHICS_HTML, 'utf8');

  assert.match(
    markup,
    /HOUSEACCOUNT_API_BASE/,
    'the endpoint must be derived from the configured API base'
  );
  assert.match(markup, /new URL\(\s*'\/mcp'/, 'the endpoint must resolve /mcp against that base');

  const pinned = [...markup.matchAll(/https?:\/\/[A-Za-z0-9.-]+\/mcp/g)].map((m) => m[0]);
  assert.deepEqual(pinned, [], 'the MCP endpoint is hardcoded to a host that may not exist');
});

test('no file under web/ still names a Fly app the deploy never creates', () => {
  const app = flyAppName();
  const stray = webFiles().flatMap(([where, text]) =>
    [...text.matchAll(/([A-Za-z0-9][A-Za-z0-9-]*)\.fly\.dev/g)]
      .filter((match) => match[1] !== app)
      .map((match) => `${where}: ${match[1]}.fly.dev`)
  );

  assert.deepEqual(stray, [], `fly.toml declares only "${app}"`);
});
