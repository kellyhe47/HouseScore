/**
 * The Data & Ethics page's view model (R11.4, wireframe frame 5).
 *
 * This page is two deliverables at once: the ethics and ToS position, and the
 * one-page rationale for the House Score. Both are only worth anything if they
 * describe the system that actually ran — so nothing here is decorative prose
 * about an ideal pipeline. The weights come from the engine's own table, the
 * eval numbers come from `eval/report.json` with the caveat that ships with
 * them, and what a provider refused to answer is reported as a refusal rather
 * than quietly rounded to zero.
 *
 * Like `route-ui.js`, this module never fetches: `ethics.html` reads the two
 * artifacts and passes them in, so every degraded shape — a missing report, a
 * manifest with no resolve block, a territory with no doors — is reachable in
 * `node --test`.
 *
 * **Claims are structured, not prose.** Each commitment is `{id, text}`. The id
 * is what a test can assert is present; the text is what a reader gets. That
 * split is what lets the wording improve without a test pinning a sentence, and
 * stops the page losing a commitment during an edit.
 *
 * DOM-free at import time.
 */

/* ── The engine's numbers, mirrored ──────────────────────────────────────────
 *
 * A hand-maintained copy of `src/houseaccount/scoring/weights.py`. There is no
 * build step in this project, so the mirror is checked by test instead: the
 * suite parses the Python source and deep-equals it against these three
 * constants, and editing either side alone fails.
 *
 * The page publishes these as the model's public weights. A browser constant
 * that quietly disagreed with the engine would make the published rationale a
 * lie about the scores on the map next to it.
 */

/** Signed point values, keyed exactly as the engine keys them. */
export const WEIGHTS = {
  // Mover (group max 100) — the strongest single signal in the model.
  mover_30d: 100,
  mover_60d: 85,
  mover_90d: 70,
  // Hires-out (group max 60).
  permit_each: 20,
  permit_cap: 40,
  provider_churn: 20,
  // Capacity (group max 30). The two value bands are alternatives, not a sum.
  capacity_median: 15,
  capacity_1_5x: 25,
  capacity_acs_prior: 5,
  // Need (group max 30).
  need_home_age: 8,
  need_pool: 8,
  need_lot: 4,
  need_condition_decline: 6,
  need_deferred_maintenance: 4,
  // Modifier — mild by design: a registered rental is a demotion, never an
  // exclusion, because tenants and landlords both buy home services.
  absentee_modifier: -15,
};

/** The cut-offs the weights hang on. */
export const THRESHOLDS = {
  mover_30d_days: 30,
  mover_60d_days: 60,
  mover_90d_days: 90,
  permit_window_days: 730,
  provider_churn_min_contractors: 2,
  nominal_sale_price_usd: 100,
  capacity_1_5x_multiple: 1.5,
  need_home_age_years: 30,
  need_lot_acres: 0.5,
  score_floor: 0,
  score_ceiling: 100,
};

/** Exterior condition as an ordinal scale, worst first. */
export const CONDITION_ORDER = ['poor', 'fair', 'good', 'excellent'];

/* ── Formatting ──────────────────────────────────────────────────────────── */

/** What an unmeasured number renders as. Never "0" — zero is a measurement. */
const UNMEASURED = 'not yet measured';

const isNumber = (value) => typeof value === 'number' && Number.isFinite(value);

/** A rate as a percentage, e.g. `97.4%`. */
const asPercent = (value, digits = 1) =>
  isNumber(value) ? `${(value * 100).toFixed(digits)}%` : UNMEASURED;

/** A ratio kept as a decimal, because precision and recall are read as decimals. */
const asRatio = (value) => (isNumber(value) ? value.toFixed(2) : UNMEASURED);

/** Money at whatever precision it actually has: $0.006 is not $0.01. */
const asMoney = (value) =>
  isNumber(value) ? `$${Number(value.toPrecision(2))}` : UNMEASURED;

/* ── The ICP and the signal → ICP trace (R11.4) ──────────────────────────── */

/**
 * The four traits that define the best concierge customer.
 *
 * Every group in the trace below points at one of these. A signal that traces
 * to none of them has no business moving a score, which is the whole discipline
 * this table exists to enforce.
 */
const ICP_TRAITS = [
  {
    key: 'recently_moved',
    label: 'Recently moved in',
    body:
      'New owners buy a year of services in their first weeks — the single '
      + 'strongest predictor available, and the one with a real deadline on it.',
  },
  {
    key: 'hires_out',
    label: 'Hires out rather than DIY',
    body:
      'A household that already pays contractors has demonstrated the behaviour '
      + 'the offer depends on. Nobody has to be converted from a weekend habit.',
  },
  {
    key: 'capacity',
    label: 'Capacity to pay for years of service',
    body:
      'Concierge service is a subscription, not a transaction, so the question '
      + 'is not whether one job is affordable but whether the next thirty are.',
  },
  {
    key: 'near_term_need',
    label: 'Near-term service need',
    body:
      'An older house, a pool, a big lot or a visibly slipping exterior all mean '
      + 'work is already due. Need is what turns capacity into a call.',
  },
];

/**
 * Each scoring group, its ceiling, the ICP trait it serves, and every weight
 * inside it.
 *
 * `points` is read from `WEIGHTS` rather than re-typed, so the published table
 * and the engine cannot disagree even by a transcription slip — and every key in
 * `WEIGHTS` appears here exactly once, so a weight can never be added to the
 * engine and quietly left off the public page.
 */
export function icpTrace() {
  const signal = (weightKey, description) => ({
    weightKey,
    points: WEIGHTS[weightKey],
    description,
  });

  return [
    {
      key: 'mover',
      label: 'Mover',
      max: 100,
      icpTrait: 'Recently moved in',
      signals: [
        signal(
          'mover_30d',
          `Deed recorded within ${THRESHOLDS.mover_30d_days} days — the window in which `
            + 'a new owner is still choosing every provider they will keep.'
        ),
        signal(
          'mover_60d',
          `Deed recorded within ${THRESHOLDS.mover_60d_days} days: still deciding, `
            + 'but some choices have already been made.'
        ),
        signal(
          'mover_90d',
          `Deed recorded within ${THRESHOLDS.mover_90d_days} days — the edge of the `
            + 'move-in window, and the last point at which the deadline still helps.'
        ),
      ],
    },
    {
      key: 'hires_out',
      label: 'Hires-out',
      max: 60,
      icpTrait: 'Hires out rather than DIY',
      signals: [
        signal(
          'permit_each',
          `Each contractor permit in a rolling ${THRESHOLDS.permit_window_days}-day `
            + 'window: proof that work here gets bought rather than done in-house.'
        ),
        signal(
          'permit_cap',
          'The ceiling on permit points, so a renovation project cannot outweigh '
            + 'every other trait in the model on its own.'
        ),
        signal(
          'provider_churn',
          `At least ${THRESHOLDS.provider_churn_min_contractors} distinct contractors `
            + 'with no repeat — work is being bought and no incumbent holds the account.'
        ),
      ],
    },
    {
      key: 'capacity',
      label: 'Capacity',
      max: 30,
      icpTrait: 'Capacity to pay for years of service',
      signals: [
        signal(
          'capacity_median',
          'Assessed value at or above the territory median — the band where a '
            + 'recurring service plan is a normal household expense.'
        ),
        signal(
          'capacity_1_5x',
          `Assessed value at or above ${THRESHOLDS.capacity_1_5x_multiple}× the `
            + 'territory median. An alternative to the band above, never added to it.'
        ),
        signal(
          'capacity_acs_prior',
          'A small prior when the ACS reports a high dual-income share for the '
            + 'surrounding block-group — neighbourhood context, never a reading of '
            + 'any address.'
        ),
      ],
    },
    {
      key: 'need',
      label: 'Need',
      max: 30,
      icpTrait: 'Near-term service need',
      signals: [
        signal(
          'need_home_age',
          `A home at least ${THRESHOLDS.need_home_age_years} years old, where original `
            + 'systems are at or past replacement age.'
        ),
        signal(
          'need_pool',
          'A pool visible in public-domain aerial imagery: the longest standing '
            + 'maintenance list in any town.'
        ),
        signal(
          'need_lot',
          `A lot of at least ${THRESHOLDS.need_lot_acres} acres — grounds that make `
            + 'outdoor work a standing job rather than an afternoon.'
        ),
        signal(
          'need_condition_decline',
          `Exterior condition moving down the ${CONDITION_ORDER.join(' → ')} scale `
            + 'between two ortho vintages.'
        ),
        signal(
          'need_deferred_maintenance',
          'The combination that reads as deferred maintenance: an older home, no '
            + 'permits in the window, and a declining exterior.'
        ),
      ],
    },
    {
      key: 'modifier',
      label: 'Modifier',
      max: -15,
      icpTrait: 'Occupancy context (demotion only)',
      signals: [
        signal(
          'absentee_modifier',
          'A match against the municipal rental registration. Mild and negative '
            + 'by design: a rented address still buys home services, so this moves '
            + 'a door down the list rather than off it.'
        ),
      ],
    },
  ];
}

/* ── Evaluation results (R11.4) ──────────────────────────────────────────── */

/** The five numbers the page reports, in the order a reader reads them. */
const METRIC_ROWS = [
  { key: 'precision', label: 'Pool-detection precision', format: asRatio },
  { key: 'recall', label: 'Pool-detection recall', format: asRatio },
  { key: 'hallucination_rate', label: 'Hallucination rate (verified-negative probe)', format: asPercent },
  { key: 'cost_per_door', label: 'Cost per door', format: asMoney },
  { key: 'resolve_match_rate', label: 'Entity-resolution match rate', format: asPercent },
];

/**
 * Anything that says the numbers did not come from hand labels.
 *
 * Deliberately not "does the source mention hand labels": today's source string
 * is `frozen fixture 09 (hand labels not yet collected)`, which mentions them
 * precisely to say they are absent.
 */
const PROVISIONAL_SOURCE = /frozen|fixture|not yet|provisional|placeholder|synthetic/i;

/**
 * The evaluation section.
 *
 * A missing report leaves every number null and the section unavailable. It
 * does not fall back to zero: an unmeasured rate rendered as `0` is a claim
 * that the measurement was taken and came out badly, which is a different and
 * worse statement than "we have not measured this yet".
 *
 * @param {object|null} report parsed `eval/report.json`
 */
export function evalMetrics(report) {
  const available = Boolean(report);
  const source = (report && report.metrics_source) || null;
  const isProvisional = Boolean(source) && PROVISIONAL_SOURCE.test(source);

  const metrics = METRIC_ROWS.map(({ key, label, format }) => {
    const raw = report ? report[key] : null;
    const value = isNumber(raw) ? raw : null;
    return { key, label, value, display: format(value) };
  });

  return {
    available,
    source,
    isProvisional,
    caveat: caveatFor(available, isProvisional),
    metrics,
  };
}

function caveatFor(available, isProvisional) {
  if (!available) {
    return 'No evaluation report has been published for this run, so this page '
      + 'reports no measurements rather than estimates of them.';
  }
  if (isProvisional) {
    return 'These vision figures come from a frozen scoring fixture, not from a '
      + 'hand-labelled sample — the labels have not been collected yet. Read them '
      + 'as a check that the arithmetic is stable, not as a measurement of how '
      + 'well the detector sees.';
  }
  return 'Measured against a hand-labelled sample.';
}

/* ── What actually ran, and what did not ─────────────────────────────────── */

/** The three signal sources that can decline, and how to recognise each. */
const PROVIDERS = [
  {
    key: 'acs',
    label: 'Census ACS block-group context',
    pattern: /census|\bacs\b/i,
    reasonField: 'acs_reason',
    availableField: 'acs_available',
  },
  {
    key: 'rental_registration',
    label: 'Municipal rental registration',
    pattern: /rental/i,
    reasonField: 'rental_declination_reason',
    availableField: 'rental_registration_available',
  },
  {
    key: 'vision',
    label: 'Aerial-imagery vision stage',
    pattern: /vision|imagery|anthropic/i,
    reasonField: null,
    availableField: null,
  },
];

/**
 * Which signal sources answered on the published run, and why the others did not.
 *
 * A reviewer's first question about a pipeline like this is "which of these
 * numbers is real?" — so the declinations are published beside the model rather
 * than buried in a log. A missing key and an unanswered records request are
 * ordinary states here, not failures to hide.
 *
 * @param {object|null} manifest parsed `data/run_manifest.json`
 */
export function signalAvailability(manifest) {
  const degradations = (manifest && manifest.degradations) || [];
  const resolve = (manifest && manifest.resolve) || {};

  return PROVIDERS.map(({ key, label, pattern, reasonField, availableField }) => {
    if (!manifest) {
      return {
        key,
        label,
        live: false,
        reason: 'No published run to report on, so this page cannot say whether '
          + 'the provider answered.',
      };
    }

    // The manifest states a declination two ways — a per-provider reason in the
    // resolve block, and a human sentence in `degradations` — and either alone
    // is enough to know the signal did not run.
    const stated = reasonField ? resolve[reasonField] : null;
    const logged = degradations.find((note) => pattern.test(note)) || null;
    const reason = stated || logged;

    const declared = availableField ? resolve[availableField] : undefined;
    const live = !reason && declared !== false;

    return { key, label, live, reason: reason || null };
  });
}

/* ── Absentee detection (R11.3) ──────────────────────────────────────────── */

/**
 * The absentee position, and whether the data behind it exists.
 *
 * The commitment holds whether or not the registration was obtained: absentee
 * is a rental-registration match and nothing else. What changes with the data
 * is the count — and with no registration in hand the count is null, because a
 * "0 matched" on this page would be a finding nobody made.
 *
 * @param {object|null} manifest
 */
export function absenteeStatement(manifest) {
  const resolve = (manifest && manifest.resolve) || {};
  const degradations = (manifest && manifest.degradations) || [];

  const reason =
    resolve.rental_declination_reason
    || degradations.find((note) => /rental/i.test(note))
    || (manifest ? null : 'no published run');

  const available = Boolean(manifest) && !reason;
  const matched = available && isNumber(resolve.rental_registration_matched)
    ? resolve.rental_registration_matched
    : null;

  // The recorded reason is a full sentence in its own right, so it is quoted
  // rather than spliced into one — the run's own words, not a paraphrase.
  const body = available
    ? `The municipal rental registration was obtained and matched ${matched} addresses in `
      + `this territory. Each of those doors carries the ${WEIGHTS.absentee_modifier}-point `
      + 'demotion and an evidence line saying exactly why.'
    : manifest
      ? 'No door in this run carries the absentee demotion, because the municipal rental '
        + `registration was not obtained. The run records the reason as: “${reason}”. The `
        + 'rule is published regardless — the honest statement is that this signal is a seam '
        + 'waiting on a records response, not that this territory has no rentals in it.'
      : 'There is no published run to report against, so no door carries the absentee '
        + 'demotion here. The rule stands either way: it is a rental-registration match and '
        + 'nothing else, and that registration was not obtained.';

  return {
    available,
    matched,
    body,
    claims: [
      {
        id: 'rental_registration_only',
        text:
          'Absentee likelihood is decided by one thing only: whether the address '
          + 'appears in the municipality’s own rental-registration list. No '
          + 'mail-forwarding data, no occupancy inference, nothing derived from who '
          + 'lives anywhere.',
      },
      {
        id: 'absentee_is_mild',
        text:
          `The penalty is ${WEIGHTS.absentee_modifier} points and never an exclusion, `
          + 'because a landlord who buys gutter cleaning and a tenant who buys it are '
          + 'both customers.',
      },
    ],
  };
}

/* ── The whole page ──────────────────────────────────────────────────────── */

/**
 * Assemble the page from the rationale (which is always true) and the published
 * run (which may be missing, partial, or degraded).
 *
 * @param {{report: object|null, manifest: object|null}} data
 */
export function buildEthicsPage({ report = null, manifest = null } = {}) {
  return {
    icp: icpSection(),
    trace: icpTrace(),
    weights: weightsSection(),
    validation: validationSection(),
    evaluation: evalMetrics(report),
    sources: sourcesSection(manifest),
    ethics: {
      danielsLaw: danielsLawSection(),
      census: censusSection(manifest),
      streetView: streetViewSection(),
      absentee: absenteeStatement(manifest),
    },
    // The machine keys stay in `signalAvailability`, which is where they are
    // read; the page carries the sentences a reviewer actually reads.
    availability: signalAvailability(manifest).map(({ label, live, reason }) => ({
      label,
      live,
      reason,
    })),
    resolution: resolutionSection(manifest),
    coverage: coverageSection(manifest),
    demo: {
      isDemoOnly: true,
      label: 'Simulate a data-fetch error on the map',
      body:
        'A demo-only trigger for the map’s degraded state, so a reviewer can see '
        + 'how the page behaves when the doors layer fails rather than taking the '
        + 'claim on trust.',
    },
  };
}

function icpSection() {
  return {
    definition:
      'The best concierge customer has recently moved in, hires work out rather '
      + 'than doing it themselves, can pay for years of service rather than one '
      + 'job, and has work already coming due. Every signal in the score traces to '
      + 'one of those four traits — nothing scores because it was available.',
    traits: ICP_TRAITS,
  };
}

const WEIGHT_LABELS = {
  mover_30d: 'Deed within 30 days',
  mover_60d: 'Deed within 60 days',
  mover_90d: 'Deed within 90 days',
  permit_each: 'Each permit in the 2-year window',
  permit_cap: 'Permit points ceiling',
  provider_churn: 'Provider churn, no repeat contractor',
  capacity_median: 'Assessed at or above territory median',
  capacity_1_5x: 'Assessed at or above 1.5× the median',
  capacity_acs_prior: 'ACS block-group dual-income prior',
  need_home_age: 'Home age at or over 30 years',
  need_pool: 'Pool detected in aerial imagery',
  need_lot: 'Lot at or over half an acre',
  need_condition_decline: 'Exterior condition declined between vintages',
  need_deferred_maintenance: 'Deferred-maintenance combination',
  absentee_modifier: 'Registered rental (demotion)',
};

const THRESHOLD_LABELS = {
  mover_30d_days: 'Deed age for the top mover band (days)',
  mover_60d_days: 'Deed age for the middle mover band (days)',
  mover_90d_days: 'Deed age for the last mover band (days)',
  permit_window_days: 'Rolling permit window (days)',
  provider_churn_min_contractors: 'Distinct contractors before churn counts',
  nominal_sale_price_usd: 'Sale price at or below which a deed reads as non-arm’s-length (USD)',
  capacity_1_5x_multiple: 'Multiple of the median for the upper capacity band',
  need_home_age_years: 'Home age that counts as service need (years)',
  need_lot_acres: 'Lot size that counts as service need (acres)',
  score_floor: 'Score floor',
  score_ceiling: 'Score ceiling',
};

function weightsSection() {
  return {
    rows: Object.entries(WEIGHTS).map(([key, points]) => ({
      key,
      points,
      label: WEIGHT_LABELS[key] || key,
    })),
    thresholds: Object.entries(THRESHOLDS).map(([key, value]) => ({
      key,
      value,
      label: THRESHOLD_LABELS[key] || key,
    })),
    formula:
      'score = clamp(mover + hires-out + capacity + need + modifier, '
      + `${THRESHOLDS.score_floor}, ${THRESHOLDS.score_ceiling})`,
    note:
      'Deterministic and integer-valued. The same door and the same inputs produce '
      + 'the same score on every run, and twelve golden fixtures pin the arithmetic.',
    conditionScale: CONDITION_ORDER,
  };
}

function validationSection() {
  return {
    heading: 'Validation plan',
    body:
      'No knock outcomes exist yet, so there is nothing to fit the weights to — and '
      + 'weights fitted to nothing are how a model ends up confidently wrong. The '
      + 'table above is therefore pre-registered rather than tuned: it is published, '
      + 'dated, and every term is argued from the ICP. When outcomes accrue, the '
      + 'model gets validated rather than defended.',
    claims: [
      {
        id: 'pre_registered',
        text:
          'The weights are pre-registered. They were argued from the ICP and '
          + 'published before any outcome data existed, so they cannot have been '
          + 'quietly fitted to a result after the fact.',
      },
      {
        id: 'decile_lift',
        text:
          'First test once outcomes exist: a score-decile lift curve. A working '
          + 'score shows monotonically higher engagement as the deciles climb, and a '
          + 'flat curve means the model is not finding anything.',
      },
      {
        id: 'calibration',
        text:
          'Second test: calibration. A door scored 80 should convert about twice as '
          + 'often as one scored 40 — ranking correctly is not the same as being '
          + 'right about the size of the difference.',
      },
      {
        id: 'no_outcome_data_yet',
        text:
          'Until both curves can be drawn, every number on this page is an input '
          + 'measurement or a pipeline statistic. None of them is evidence that the '
          + 'score predicts revenue.',
      },
    ],
  };
}

function danielsLawSection() {
  return {
    heading: 'Daniel’s Law and personal identity',
    body:
      'New Jersey’s Daniel’s Law restricts the disclosure of home addresses '
      + 'and personal identity for covered persons. This pipeline is built so that '
      + 'compliance is a property of what it never holds, not of a filter at the end: '
      + 'the identifying columns are dropped at the source adapter, before anything '
      + 'reaches storage, scoring, or the map.',
    claims: [
      {
        id: 'redacted_at_source',
        text:
          'Owner identity and mailing-address columns are dropped in the source '
          + 'adapter, before the parcel record reaches the database. Nothing '
          + 'downstream can leak a field it was never handed.',
      },
      {
        id: 'never_requested',
        text:
          'No request this pipeline makes asks for personal identity. The absence '
          + 'is in the query, not only in the parsing of the answer.',
      },
      {
        id: 'no_identity_reconstruction',
        text:
          'No attempt is made to reconstruct who lives at a door — no deed-book '
          + 'name mining, no broker or people-search data, no cross-referencing '
          + 'toward a person. The score describes a property, and the UI shows only '
          + 'the property.',
      },
    ],
  };
}

function censusSection(manifest) {
  const threshold = manifest && isNumber(manifest.acs_dual_income_threshold)
    ? asPercent(manifest.acs_dual_income_threshold, 0)
    : null;

  return {
    heading: 'Census data stays at the neighbourhood',
    body:
      'The ACS contribution is a small prior drawn from block-group tables'
      + (threshold ? ` at a ${threshold} dual-income share` : '')
      + `, worth ${WEIGHTS.capacity_acs_prior} points of a possible ${THRESHOLDS.score_ceiling}. `
      + 'A block group covers hundreds of addresses, and a statistic about hundreds of '
      + 'addresses says nothing about any one of them.',
    claims: [
      {
        id: 'no_household_inference',
        text:
          'ACS figures are never treated as a fact about an address. Every '
          + 'evidence line built from them is phrased as block-group context — '
          + '"this neighbourhood reports a high dual-income share" — and never as a '
          + 'claim about the people behind the door.',
      },
      {
        id: 'acs_is_a_small_prior',
        text:
          `The block-group prior is worth ${WEIGHTS.capacity_acs_prior} points, `
          + 'deliberately too small to move a door between bands on its own. '
          + 'Neighbourhood context nudges; it does not decide.',
      },
    ],
  };
}

function streetViewSection() {
  return {
    heading: 'Imagery, and the Street View ToS',
    body:
      'Google Maps Platform Terms of Service §3.2.3 prohibits creating derived '
      + 'datasets from Street View content, and a per-parcel signal stored in a '
      + 'database is exactly that. So the bulk imagery signals do not come from '
      + 'Street View at all — they come from public-domain state orthophotography, '
      + 'which carries no such restriction.',
    claims: [
      {
        id: 'bulk_from_public_domain_orthos',
        text:
          'Every imagery signal that reaches a score — pool, exterior condition, '
          + 'lot context — is derived from public-domain NJ orthophotos and NAIP '
          + 'coverage, which may lawfully be analysed and stored.',
      },
      {
        id: 'street_view_demo_scale',
        text:
          'Street View is used only at demo scale, for a handful of doors, without '
          + 'building a stored derived dataset — and that position is published '
          + 'here rather than left implicit.',
      },
      {
        id: 'zillow_redfin_excluded',
        text:
          'Zillow and Redfin are excluded outright. Their terms forbid scraping '
          + 'and redistribution, and no listing-portal data of any kind is in this '
          + 'pipeline.',
      },
      {
        id: 'tos_over_convenience',
        text:
          'Where a licence and a better signal conflict, the licence wins and the '
          + 'gap is disclosed. That is why the imagery terms are absent rather than '
          + 'approximated when a provider declines.',
      },
    ],
  };
}

function sourcesSection(manifest) {
  const retrieved = (manifest && manifest.retrieved) || {};
  const asOf = (manifest && manifest.as_of) || null;
  const date = (key) => retrieved[key] || asOf || 'not recorded';

  return [
    {
      name: 'NJ statewide parcels and MOD-IV assessment records',
      detail: 'ArcGIS FeatureServer, municipality code 0248. Identity columns dropped at ingest.',
      retrieved: date('parcel'),
    },
    {
      name: 'NJ construction permits',
      detail: 'Socrata (data.nj.gov), dataset w9se-dmra, matched to parcels by block and lot.',
      retrieved: date('permits'),
    },
    {
      name: 'Census ACS 5-year block-group tables',
      detail: 'B23007, B19013 and B08303 at block-group geography — neighbourhood context only.',
      retrieved: date('acs'),
    },
    {
      name: 'NJ orthophotography, 2015 and 2020 vintages',
      detail: 'maps.nj.gov, public domain. The source of every bulk imagery signal.',
      retrieved: date('imagery'),
    },
    {
      name: 'Ramsey municipal rental registration',
      detail: 'Municipal record requested under OPRA. The only input to the absentee modifier.',
      retrieved: date('rental'),
    },
  ];
}

function resolutionSection(manifest) {
  const resolve = manifest && manifest.resolve;
  if (!resolve) {
    return {
      rows: [],
      note: 'No published run, so there are no entity-resolution rates to report.',
    };
  }

  return {
    rows: [
      {
        key: 'municipal_match_rate',
        label: 'Municipal match rate (permits resolved to this municipality)',
        rate: resolve.municipal_match_rate ?? null,
        display: asPercent(resolve.municipal_match_rate),
        body:
          'How many permit records carry a municipality this pipeline could resolve. '
          + 'This is the R3.2 figure, and it is a statement about the permit feed.',
      },
      {
        key: 'permit_match_rate',
        label: 'Permit match rate (in-territory permits joined to a door)',
        rate: resolve.permit_match_rate ?? null,
        display: asPercent(resolve.permit_match_rate),
        body:
          'Of the permits inside this territory, how many landed on a specific parcel '
          + 'by block and lot. Lower than the municipal rate, and measuring a different '
          + 'thing — a permit can belong to Ramsey and still name no parcel this run holds.',
      },
    ],
    note:
      'The two rates are reported separately on purpose. Quoting the municipal rate '
      + 'as though it were the parcel-level join would overstate how much of the '
      + 'permit evidence actually reached a door.',
  };
}

function coverageSection(manifest) {
  if (!manifest) {
    return {
      total: null,
      scored: null,
      ratio: null,
      text: 'No published run, so this page reports no coverage figure.',
    };
  }

  const total = isNumber(manifest.doors_total) ? manifest.doors_total : 0;
  const scored = isNumber(manifest.doors_scored) ? manifest.doors_scored : 0;
  // A territory with no doors is a real state — an empty publish — and dividing
  // into it must not put NaN in front of a reviewer.
  const ratio = total > 0 ? scored / total : null;

  return {
    total,
    scored,
    ratio,
    text:
      total > 0
        ? `${scored} of ${total} doors scored (${asPercent(ratio)}). Any door the county `
          + 'record cannot support is published unscored with its reason, rather than '
          + 'scored on assumptions.'
        : 'No doors were published in this run, so there is no coverage to report.',
  };
}
