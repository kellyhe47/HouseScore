import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import path from 'node:path';

import {
  WEIGHTS,
  THRESHOLDS,
  CONDITION_ORDER,
  icpTrace,
  evalMetrics,
  signalAvailability,
  absenteeStatement,
  buildEthicsPage,
} from './ethics.js';
import {
  evalReport,
  runManifest,
  healthyManifest,
  visionManifest,
  visionRanClean,
  visionDeclined,
  VISION_NO_KEY_REASON,
} from './test-fixtures.js';

/* ── The anti-drift check ────────────────────────────────────────────────────
 *
 * The page publishes the engine's weights, so a browser constant that quietly
 * disagrees with `src/houseaccount/scoring/weights.py` would make the public
 * rationale a lie. There is no build step here, so `ethics.js` carries a hand
 * written mirror and this block reads the engine's own source and compares.
 *
 * `weights.py` is checked-in source, not a generated artifact, so it is always
 * present — unlike `eval/report.json` and `data/run_manifest.json`, which the
 * tests below build in-module instead of reading.
 *
 * Editing either side alone fails these three tests.
 */

const WEIGHTS_PY = path.join(
  import.meta.dirname,
  '..',
  '..',
  'src',
  'houseaccount',
  'scoring',
  'weights.py'
);

/** The literal assigned to `name` in weights.py, as JS. */
function pythonLiteral(name) {
  const source = readFileSync(WEIGHTS_PY, 'utf8');
  const lines = source.split('\n');

  const start = lines.findIndex((line) => new RegExp(`^${name}\\b.*=`).test(line));
  assert.notEqual(start, -1, `${name} is not defined in weights.py`);

  const head = lines[start];
  const inline = /=\s*\((.*)\)\s*$/.exec(head);
  if (inline) {
    // A tuple on one line: CONDITION_ORDER.
    return JSON.parse(`[${inline[1].replace(/,\s*$/, '')}]`);
  }

  assert.match(head, /\{\s*$/, `${name} is neither a one-line tuple nor an open dict`);
  const body = [];
  for (let i = start + 1; i < lines.length; i++) {
    const line = lines[i];
    if (/^\}/.test(line)) break;
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    body.push(trimmed);
  }
  return JSON.parse(`{${body.join('').replace(/,$/, '')}}`);
}

test('the browser weight table is the engine weight table', () => {
  assert.deepEqual(WEIGHTS, pythonLiteral('WEIGHTS'));
});

test('the browser threshold table is the engine threshold table', () => {
  assert.deepEqual(THRESHOLDS, pythonLiteral('THRESHOLDS'));
});

test('the browser condition scale is the engine condition scale', () => {
  assert.deepEqual(CONDITION_ORDER, pythonLiteral('CONDITION_ORDER'));
});

test('the anti-drift check would notice a changed weight', () => {
  // Guards the guard: a parser that silently returned {} would pass the three
  // tests above against an empty mirror and prove nothing.
  const parsed = pythonLiteral('WEIGHTS');

  assert.ok(Object.keys(parsed).length >= 15, 'weights.py did not parse');
  assert.equal(parsed.mover_30d, 100);
  assert.equal(parsed.absentee_modifier, -15);
  assert.equal(pythonLiteral('THRESHOLDS').capacity_1_5x_multiple, 1.5);
});

/* ── The page ──────────────────────────────────────────────────────────────── */

const page = (overrides = {}) =>
  buildEthicsPage({ report: evalReport(), manifest: runManifest(), ...overrides });

/** Every string anywhere in the view model, for the whole-page scans below. */
function strings(value, found = []) {
  if (typeof value === 'string') found.push(value);
  else if (Array.isArray(value)) value.forEach((item) => strings(item, found));
  else if (value && typeof value === 'object') Object.values(value).forEach((v) => strings(v, found));
  return found;
}

const claimIds = (section) => section.claims.map((claim) => claim.id);

// ---------- ICP and the signal→ICP trace (R11.4) ----------

test('the page states who the score is for', () => {
  const { icp } = page();

  assert.ok(icp.definition.trim(), 'the ICP has to be written down');
  assert.equal(icp.traits.length, 4, 'recently moved, hires out, capacity, near-term need');
});

test('the trace covers all five score groups, in scoring order', () => {
  assert.deepEqual(
    icpTrace().map((row) => row.key),
    ['mover', 'hires_out', 'capacity', 'need', 'modifier']
  );
});

test('each traced group declares its ceiling', () => {
  assert.deepEqual(
    icpTrace().map((row) => [row.key, row.max]),
    [
      ['mover', 100],
      ['hires_out', 60],
      ['capacity', 30],
      ['need', 30],
      ['modifier', -15],
    ]
  );
});

test('every traced group names the ICP trait it serves', () => {
  for (const row of icpTrace()) {
    assert.ok(row.icpTrait.trim(), `${row.key} traces to nothing`);
    assert.ok(row.label.trim());
  }
});

test('every traced signal takes its points from the shared weight table', () => {
  for (const row of icpTrace()) {
    assert.ok(row.signals.length > 0, `${row.key} has no signals`);
    for (const signal of row.signals) {
      assert.equal(
        signal.points,
        WEIGHTS[signal.weightKey],
        `${signal.weightKey} was re-typed instead of read`
      );
    }
  }
});

test('the trace accounts for every weight the engine has', () => {
  const traced = icpTrace().flatMap((row) => row.signals.map((signal) => signal.weightKey));

  assert.deepEqual(traced.sort(), Object.keys(WEIGHTS).sort());
});

test('the absentee modifier is the only negative group', () => {
  const negatives = icpTrace().filter((row) => row.max < 0);

  assert.deepEqual(
    negatives.map((row) => row.key),
    ['modifier']
  );
});

// ---------- the weights and thresholds tables ----------

test('the rendered weights table is the shared constant, row for row', () => {
  const { rows } = page().weights;

  assert.deepEqual(
    Object.fromEntries(rows.map((row) => [row.key, row.points])),
    WEIGHTS
  );
});

test('the rendered thresholds carry the engine values', () => {
  const { thresholds } = page().weights;

  assert.deepEqual(
    Object.fromEntries(thresholds.map((row) => [row.key, row.value])),
    THRESHOLDS
  );
});

test('the page publishes the clamp the score is subject to', () => {
  const { formula } = page().weights;

  assert.match(formula, /clamp/i);
  assert.ok(formula.includes(String(THRESHOLDS.score_ceiling)), formula);
});

// ---------- the validation plan (R6.3) ----------

test('the validation plan is written down', () => {
  assert.ok(page().validation.body.trim());
});

test('the validation plan names decile lift and calibration', () => {
  const ids = claimIds(page().validation);

  assert.ok(ids.includes('decile_lift'), ids);
  assert.ok(ids.includes('calibration'), ids);
});

test('the validation plan states the weights were pre-registered, not fitted', () => {
  assert.ok(claimIds(page().validation).includes('pre_registered'));
});

// ---------- live eval numbers (R11.4) ----------

const metric = (view, key) => view.metrics.find((row) => row.key === key);

test('the eval section reports the five measured numbers', () => {
  const view = evalMetrics(evalReport());

  assert.deepEqual(
    view.metrics.map((row) => row.key).sort(),
    ['cost_per_door', 'hallucination_rate', 'precision', 'recall', 'resolve_match_rate'].sort()
  );
});

test('every eval number comes from the report, not the page', () => {
  const report = evalReport();
  const view = evalMetrics(report);

  assert.equal(metric(view, 'precision').value, report.precision);
  assert.equal(metric(view, 'recall').value, report.recall);
  assert.equal(metric(view, 'hallucination_rate').value, report.hallucination_rate);
  assert.equal(metric(view, 'cost_per_door').value, report.cost_per_door);
});

test('a different report renders different numbers', () => {
  const changed = evalMetrics(
    evalReport({ precision: 0.5, recall: 0.25, hallucination_rate: 0.5, cost_per_door: 0.99 })
  );

  assert.equal(metric(changed, 'precision').value, 0.5);
  assert.match(metric(changed, 'precision').display, /0\.5/);
  assert.match(metric(changed, 'cost_per_door').display, /0\.99/);
});

test('the metrics source travels with the numbers', () => {
  const report = evalReport();

  assert.equal(evalMetrics(report).source, report.metrics_source);
});

test('a frozen-fixture source is flagged as provisional and carries a caveat', () => {
  const view = evalMetrics(evalReport());

  assert.equal(view.isProvisional, true, 'unlabelled metrics must not read as measured');
  assert.ok(view.caveat.trim(), 'the reader has to be told these are not hand labels');
});

test('a hand-labelled source is not flagged provisional', () => {
  const view = evalMetrics(evalReport({ metrics_source: 'hand labels' }));

  assert.equal(view.isProvisional, false);
});

test('an entity-resolution rate that was never measured reads as unmeasured', () => {
  const view = evalMetrics(evalReport({ resolve_match_rate: null }));
  const row = metric(view, 'resolve_match_rate');

  assert.equal(row.value, null, 'null must not become 0');
  assert.doesNotMatch(row.display, /^0(\.0+)?$/, `an unmeasured rate rendered as ${row.display}`);
});

test('a measured entity-resolution rate is rendered', () => {
  const view = evalMetrics(evalReport({ resolve_match_rate: 0.9741 }));

  assert.equal(metric(view, 'resolve_match_rate').value, 0.9741);
  assert.match(metric(view, 'resolve_match_rate').display, /97|0\.97/);
});

test('a missing eval report leaves the section unavailable and invents nothing', () => {
  const view = evalMetrics(null);

  assert.equal(view.available, false);
  for (const row of view.metrics) {
    assert.equal(row.value, null, `${row.key} was fabricated`);
  }
});

test('a present eval report marks the section available', () => {
  assert.equal(evalMetrics(evalReport()).available, true);
});

// ---------- Daniel's Law (R11.1) ----------

test("the page has a Daniel's Law section", () => {
  const { danielsLaw } = page().ethics;

  assert.match(`${danielsLaw.heading} ${danielsLaw.body}`, /Daniel/);
});

test("the Daniel's Law section makes all three commitments", () => {
  const ids = claimIds(page().ethics.danielsLaw);

  assert.ok(ids.includes('redacted_at_source'), ids);
  assert.ok(ids.includes('never_requested'), ids);
  assert.ok(ids.includes('no_identity_reconstruction'), ids);
});

test('each identity commitment is written out for a reader', () => {
  for (const claim of page().ethics.danielsLaw.claims) {
    assert.ok(claim.text.trim().length > 20, `${claim.id} is not a sentence`);
  }
});

// ---------- Street View and ToS (R11.2) ----------

test('the imagery section cites the Google Maps Platform ToS clause', () => {
  const { streetView } = page().ethics;

  assert.ok(`${streetView.heading} ${streetView.body}`.includes('3.2.3'), streetView.body);
});

test('the imagery section states where bulk signals actually come from', () => {
  const ids = claimIds(page().ethics.streetView);

  assert.ok(ids.includes('bulk_from_public_domain_orthos'), ids);
  assert.ok(ids.includes('street_view_demo_scale'), ids);
});

test('the imagery section states the Zillow and Redfin exclusion', () => {
  assert.ok(claimIds(page().ethics.streetView).includes('zillow_redfin_excluded'));
});

// ---------- ACS is neighbourhood-level (R6.2) ----------

test('the census section disclaims household inference', () => {
  assert.ok(claimIds(page().ethics.census).includes('no_household_inference'));
});

test('every sentence about Census data on the page stays at block-group level', () => {
  const censusTalk = strings(page()).filter((text) => /\bACS\b|Census/i.test(text));

  assert.ok(censusTalk.length > 0, 'the page has to mention the ACS at all');
  for (const text of censusTalk) {
    assert.match(
      text,
      /block[- ]group|neighbou?rhood/i,
      `an ACS claim with no neighbourhood framing: ${text}`
    );
  }
});

test('nothing on the page claims to know a household', () => {
  for (const text of strings(page())) {
    assert.doesNotMatch(
      text,
      /\bthis household\b|\bthe household (is|has|earns|makes)\b/i,
      `household-level claim: ${text}`
    );
  }
});

// ---------- absentee detection (R11.3) ----------

test('a declined rental registration is stated as a declination', () => {
  const statement = absenteeStatement(runManifest());

  assert.equal(statement.available, false);
  assert.ok(
    statement.body.includes('OPRA') || statement.body.includes('not obtained'),
    statement.body
  );
});

test('a declined rental registration claims no matches', () => {
  const statement = absenteeStatement(runManifest());

  assert.equal(statement.matched, null, 'a count with no data behind it is a fabrication');
});

test('an available rental registration reports the matched count', () => {
  const statement = absenteeStatement(healthyManifest());

  assert.equal(statement.available, true);
  assert.equal(statement.matched, 37);
  assert.ok(statement.body.includes('37'), statement.body);
});

test('absentee detection is stated as rental-registration only', () => {
  assert.ok(claimIds(page().ethics.absentee).includes('rental_registration_only'));
});

test('a manifest with no resolve block still states the absentee position', () => {
  const statement = absenteeStatement(runManifest({ resolve: undefined }));

  assert.equal(statement.available, false);
  assert.ok(statement.body.trim());
});

// ---------- what is live and what is a seam ----------

test('every declined provider is listed with the reason it declined', () => {
  const rows = signalAvailability(runManifest());
  const declined = rows.filter((row) => !row.live);

  // T019 widened this list: the MOD-IV deed vintage is reported here too, in the
  // same voice as a provider that refused, because "the Mover group could not
  // fire on this extract" is the same kind of fact as "the ACS declined".
  assert.deepEqual(
    declined.map((row) => row.key).sort(),
    ['acs', 'mover_deed_vintage', 'rental_registration', 'vision']
  );
  for (const row of declined) {
    assert.ok(row.reason.trim(), `${row.key} declined without saying why`);
  }
});

test('a run with every provider live says so', () => {
  const rows = signalAvailability(healthyManifest());

  assert.ok(
    rows.every((row) => row.live),
    rows.filter((row) => !row.live)
  );
});

test('a manifest with no resolve block does not crash', () => {
  const rows = signalAvailability(runManifest({ resolve: undefined, degradations: [] }));

  assert.ok(Array.isArray(rows));
});

test('a missing manifest does not crash', () => {
  assert.ok(Array.isArray(signalAvailability(null)));
});

/* ── The vision stage ran, and the page has to say so (T023) ─────────────────
 *
 * On the published run the vision stage answered 270 of 270 requests and put
 * imagery evidence on 119 of 540 doors — and the page printed DECLINED beside
 * it, because the only thing it had to go on was a degradation *string*, and any
 * matching string read as a refusal. A reviewer two clicks away finds pool
 * evidence on the map, which makes the section a lie about the product beside
 * it.
 *
 * So there are three states here, not two, and they are read from the run's own
 * `vision` block rather than inferred from prose:
 *
 *   ran clean          → live      (nothing to disclaim)
 *   ran, lost answers  → partial   (a quantified loss: "31 of 270 answers")
 *   never ran          → declined  (no key; the refusal is printed)
 *
 * `live` stays the boolean the rest of the page already reads, and is exactly
 * "not declined": a stage that answered for 119 doors is live with a stated
 * limit, the same shape the mover row below already uses.
 */

const visionRow = (manifest) => signalAvailability(manifest).find((row) => row.key === 'vision');

test('a vision stage that ran with partial loss is not called a declination', () => {
  const row = visionRow(visionManifest());

  assert.equal(row.status, 'partial');
  assert.notEqual(row.status, 'declined', '119 doors carry this stage’s evidence');
  assert.equal(row.live, true);
});

test('the partial vision row quantifies the loss against the whole run', () => {
  const row = visionRow(visionManifest());

  assert.ok(row.reason.includes('31 of 270'), row.reason);
  assert.ok(row.reason.includes('119 of 540'), row.reason);
});

test('a vision stage that ran clean is live and disclaims nothing', () => {
  const row = visionRow(visionManifest({ vision: visionRanClean(), degradations: [] }));

  assert.equal(row.status, 'live');
  assert.equal(row.live, true);
  assert.equal(row.reason, null);
});

test('a vision stage that never ran is still reported as declined', () => {
  // The distinction the whole ticket rests on: no key configured is a refusal,
  // and it has to keep reading as one.
  const row = visionRow(
    visionManifest({ vision: visionDeclined(), degradations: [VISION_NO_KEY_REASON] })
  );

  assert.equal(row.status, 'declined');
  assert.equal(row.live, false);
  assert.ok(row.reason.includes('OPENAI_API_KEY'), row.reason);
});

test('the vision state comes from the manifest, not from the degradation text', () => {
  // The run recorded a refusal-shaped sentence *and* a block saying the stage
  // ran. The block is the measurement; the sentence is prose.
  const row = visionRow(
    visionManifest({
      vision: visionRanClean(),
      degradations: ['the vision stage was skipped and no imagery was read'],
    })
  );

  assert.equal(row.status, 'live');
  assert.equal(row.live, true);
});

test('the quantified loss is read from the manifest, not written into the page', () => {
  const row = visionRow(
    visionManifest({
      vision: {
        available: true,
        declination_reason: null,
        answers_total: 100,
        answers_lost: 4,
        doors_with_imagery: 7,
      },
      doors_total: 200,
    })
  );

  assert.equal(row.status, 'partial');
  assert.ok(row.reason.includes('4 of 100'), row.reason);
  assert.ok(row.reason.includes('7 of 200'), row.reason);
  assert.doesNotMatch(row.reason, /31|270|119|540/, 'a count written into the page');
});

test('every availability row carries a status, and `live` agrees with it', () => {
  for (const manifest of [runManifest(), healthyManifest(), visionManifest(), null]) {
    for (const row of signalAvailability(manifest)) {
      assert.ok(
        ['live', 'partial', 'declined'].includes(row.status),
        `${row.key} has status ${JSON.stringify(row.status)}`
      );
      assert.equal(row.live, row.status !== 'declined', `${row.key} disagrees with itself`);
    }
  }
});

test('a manifest with no vision block still reports what the run recorded', () => {
  // Older publishes carry no `vision` block at all. A recorded skip still reads
  // as a declination, and a clean run still reads as live — no page that reads
  // an older artifact may start claiming a state nobody measured.
  assert.equal(visionRow(runManifest()).status, 'declined');
  assert.ok(visionRow(runManifest()).reason.trim());
  assert.equal(visionRow(healthyManifest()).status, 'live');
});

test('the page carries the vision state into its what-ran section', () => {
  const built = buildEthicsPage({ report: evalReport(), manifest: visionManifest() });
  const vision = built.availability.find((row) => /vision|imagery/i.test(row.label));

  assert.ok(vision, built.availability.map((row) => row.label));
  assert.equal(vision.status, 'partial');
  assert.ok(vision.reason.includes('119 of 540'), vision.reason);
});

test('the what-ran badge is rendered from the row’s status, not a two-state boolean', () => {
  // The badge is the word a reviewer actually reads, and it is built in
  // ethics.html. A two-state `row.live ? 'live' : 'declined'` cannot print the
  // third state however correct the view model is.
  const markup = readFileSync(ETHICS_HTML, 'utf8');

  assert.match(markup, /row\.status/);
  assert.match(markup, /partial/);
});

/* ── The MOD-IV deed vintage (T019) ──────────────────────────────────────────
 *
 * The Mover group is the heaviest signal in the model and it is unearnable on
 * the published extract: the newest deed in the feed is ~20 months before
 * `as_of`, so no door is inside the 90-day window. A reviewer looking at the map
 * cannot otherwise tell "no movers here right now" from "the mover rule is
 * broken".
 *
 * Where it goes, and why: the vintage is a fourth row of `signalAvailability`
 * rather than a section of its own. The row shape — {key, label, live, reason} —
 * already says exactly what has to be said, the "What ran, and what declined"
 * section already renders that list without knowing what is in it, and a source
 * limitation belongs in the same list as a declined provider precisely because a
 * reader is asking one question of both: which of these numbers is real?
 */

const moverRow = (manifest) =>
  signalAvailability(manifest).find((row) => row.key === 'mover_deed_vintage');

test('the availability list reports the deed vintage beside the providers', () => {
  const keys = (manifest) => signalAvailability(manifest).map((row) => row.key);

  assert.deepEqual(keys(runManifest()), [
    'acs',
    'rental_registration',
    'vision',
    'mover_deed_vintage',
  ]);
  assert.deepEqual(keys(null), keys(runManifest()), 'a missing run drops no row');
});

test('an extract with no door in the mover window reports the mover signal as not live', () => {
  const row = moverRow(runManifest());

  assert.equal(row.live, false);
  assert.match(row.label, /mover|deed/i);
  assert.match(row.reason, /mover/i);
  assert.ok(row.reason.includes('2024-12-06'), row.reason);
});

test('the mover row quotes the run’s own note rather than one written here', () => {
  const manifest = runManifest();
  const recorded = manifest.degradations.find((note) => /mover/i.test(note));

  assert.equal(moverRow(manifest).reason, recorded);
});

test('a run with doors inside the mover window reports the signal live', () => {
  const row = moverRow(healthyManifest());

  assert.equal(row.live, true);
  assert.equal(row.reason, null);
});

test('a live mover signal still carries the limit the run recorded', () => {
  // The T020 case. The sales register makes the group fire, but a deed reaches
  // that register only after county recording and the state's next release, so
  // the top band can still be unreachable. The signal is live *and* limited, and
  // a green badge that dropped the sentence would be the T019 regression back
  // again in a nicer colour.
  const note =
    'no door is inside the 30-day top mover band: the freshest sale in the SR1A '
    + 'register closed 2026-06-15, 60 days ago, because a deed reaches the published '
    + 'register only after county recording and the state\u2019s next file release.';
  const row = moverRow(
    runManifest({
      degradations: [note],
      deed_vintage: {
        latest_deed_date: '2026-06-15',
        mover_window_days: 90,
        doors_in_mover_window: 2,
        doors_in_top_band: 0,
        top_band_days: 30,
      },
    })
  );

  assert.equal(row.live, true, 'the group did fire, so the row is live');
  assert.equal(row.reason, note, 'and the limit the run measured is still printed');
});

test('the vintage is read from the manifest, not from a date written into the page', () => {
  // Same page, a different extract — and the note stripped, so the only place
  // the date can come from is the recorded block.
  const row = moverRow(
    runManifest({
      degradations: [],
      deed_vintage: {
        latest_deed_date: '2025-03-04',
        mover_window_days: 90,
        doors_in_mover_window: 0,
      },
    })
  );

  assert.equal(row.live, false);
  assert.ok(row.reason.includes('2025-03-04'), row.reason);
  assert.doesNotMatch(row.reason, /2024-12-06/);
});

test('an older manifest with no deed vintage claims nothing and does not break', () => {
  const older = runManifest({ deed_vintage: undefined, degradations: [] });

  const row = moverRow(older);
  assert.equal(row.live, false, 'a run that recorded no vintage cannot be called live');
  assert.ok(row.reason.trim());
  assert.doesNotMatch(row.reason, /undefined|null|NaN/);
  assert.ok(buildEthicsPage({ report: evalReport(), manifest: older }).availability.length);
});

test('the page surfaces the vintage in its what-ran section', () => {
  const rows = page().availability;
  const mover = rows.find((row) => /mover|deed/i.test(row.label));

  assert.equal(rows.length, 4);
  assert.ok(mover, rows.map((row) => row.label));
  assert.equal(mover.live, false);
  assert.ok(mover.reason.includes('2024-12-06'), mover.reason);
});

test('the municipal and permit match rates are not confused for each other', () => {
  const { rows } = page().resolution;
  const byKey = Object.fromEntries(rows.map((row) => [row.key, row]));

  assert.equal(byKey.municipal_match_rate.rate, 0.974152785755313);
  assert.equal(byKey.permit_match_rate.rate, 0.6206896551724138);
  assert.match(byKey.municipal_match_rate.label, /municipal/i);
  assert.match(byKey.permit_match_rate.label, /permit/i);
  assert.notEqual(byKey.municipal_match_rate.label, byKey.permit_match_rate.label);
});

// ---------- the demo trigger and the remaining sad paths ----------

test('the demo-only error trigger is present and marked as demo-only', () => {
  const { demo } = page();

  assert.equal(demo.isDemoOnly, true);
  assert.ok(demo.label.trim());
});

test('the data sources are listed with their retrieval dates', () => {
  const { sources } = page();

  assert.ok(sources.length >= 4, 'the audited source list is a deliverable');
  for (const source of sources) {
    assert.ok(source.name.trim());
    assert.ok(source.retrieved, `${source.name} has no retrieval date`);
  }
});

test('a territory with no doors does not divide by zero', () => {
  const { coverage } = page({
    manifest: runManifest({ doors_total: 0, doors_scored: 0, coverage: 0 }),
  });

  assert.doesNotMatch(coverage.text, /NaN|Infinity/);
});

test('a page built with neither artifact still renders', () => {
  const built = buildEthicsPage({ report: null, manifest: null });

  assert.equal(built.evaluation.available, false);
  assert.ok(built.trace.length, 'the rationale does not depend on a published run');
  assert.ok(built.weights.rows.length);
});

test('the page is plain data', () => {
  const built = page();

  assert.deepEqual(JSON.parse(JSON.stringify(built)), built);
});

/* ── The map header, now that both features ship ─────────────────────────────
 *
 * Not view-model logic, but the two assertions below are the testable half of
 * a user-visible defect: the header still tells a reviewer that Plan Route and
 * Data & Ethics are coming in a later build. Both shipped.
 */

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

/* ── The golden-fixture count is a measurement, not prose (T025) ──────────────
 *
 * The weights table two paragraphs above sets this page's own standard: every
 * point value is read from the engine's weight table rather than restated here,
 * because a published weight that disagreed with the engine would make the page
 * a lie about the map next to it. The fixture count was hardcoded prose — it
 * said "twelve" while the harness reported thirteen — so it gets the same
 * treatment: read from `eval/report.json`, which this page already fetches for
 * the eval figures. That removes the class of drift rather than resetting the
 * counter.
 */

const fixtureNote = (report) => buildEthicsPage({ report, manifest: runManifest() }).weights.note;

test('the fixture count is the count the published report reports', () => {
  const note = fixtureNote(evalReport({ fixtures_total: 13 }));

  assert.ok(note.includes('13'), note);
  assert.match(note, /golden fixtures/);
});

test('a different published count changes the sentence', () => {
  const note = fixtureNote(evalReport({ fixtures_total: 7 }));

  assert.ok(note.includes('7'), note);
  assert.doesNotMatch(note, /\b12\b|\b13\b|twelve|thirteen/i);
});

test('no fixture count is spelled into the page as a word', () => {
  for (const text of strings(page())) {
    assert.doesNotMatch(text, /\b(twelve|thirteen)\s+golden\b/i, text);
  }
});

test('with no published report the page states no fixture count', () => {
  // The established sad path: an unmeasured number is reported as unmeasured
  // rather than as a zero, or as a stale literal that survives the report going
  // missing. The determinism claim is true regardless and stays.
  const note = buildEthicsPage({ report: null, manifest: null }).weights.note;

  assert.ok(note.trim());
  assert.match(note, /deterministic/i);
  assert.doesNotMatch(note, /undefined|NaN|null/);
  assert.doesNotMatch(note, /\d+ golden fixtures|twelve|thirteen/i);
});

test('a report that published no fixture count claims none', () => {
  const note = fixtureNote(evalReport({ fixtures_total: null }));

  assert.doesNotMatch(note, /undefined|NaN|null/);
  assert.doesNotMatch(note, /\d+ golden fixtures/);
});

/* ── The MCP endpoint the page publishes (T024) ──────────────────────────────
 *
 * The MCP surface is a graded deliverable and this page is where a reviewer
 * finds its URL. The page advertised a `houseaccount-mcp` app that no step in
 * `docs/DEPLOY.md` creates, while `fly.toml` declares one app — `houseaccount` —
 * and the server mounts the MCP transport at `/mcp` on it. After a by-the-book
 * deploy the published endpoint did not resolve.
 *
 * (The scan below reads every file under `web/`, this one included, so the
 * wrong hostname is deliberately not spelled out above.)
 *
 * The host is read out of `fly.toml` rather than typed here, so this test says
 * "the page advertises the app the deploy creates" rather than re-pinning a
 * literal that can drift again. `tests/test_deploy_config.py` states the same
 * contract from the Python side, where the rest of the deploy config is parsed.
 */

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

  // The transport is mounted at `/mcp` on whichever server answers the API, and
  // `HOUSEACCOUNT_API_BASE` names that server on both topologies. Deriving from
  // it is what makes the published endpoint correct on Railway (one origin) and
  // on Fly + Vercel (two) without a per-deploy edit.
  assert.match(
    markup,
    /HOUSEACCOUNT_API_BASE/,
    'the endpoint must be derived from the configured API base'
  );
  assert.match(markup, /new URL\(\s*'\/mcp'/, 'the endpoint must resolve /mcp against that base');

  // A published absolute endpoint is a hostname that can rot. There is exactly
  // one deploy this repo can no longer guess, so there must be no literal.
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
