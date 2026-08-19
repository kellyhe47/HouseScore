/**
 * The Data & Ethics page's view model (R29, wireframe frame 5).
 *
 * This page is two deliverables at once: the ethics and ToS position, and the
 * one-page rationale for the V2 House Score. Both are only worth anything if
 * they describe the system that actually ran — so nothing here is decorative
 * prose about an ideal pipeline. The contract constants are the engine's own
 * (three capped base categories, the mover blend, the rental demotion), the
 * eval numbers come from `eval/report.json` with the caveat that ships with
 * them, and what a provider refused to answer is reported as a refusal rather
 * than quietly rounded to zero.
 *
 * Like `route-ui.js`, this module never fetches: `ethics.html` reads the two
 * artifacts and passes them in, so every degraded shape — a missing report, a
 * manifest with no resolve block, a territory with no doors — is reachable in
 * `node --test`.
 *
 * DOM-free at import time.
 */

/* ── The V2 contract constants, mirrored (R2/R4–R6) ─────────────────────────
 *
 * A hand-maintained mirror of the engine's scoring contract. The page
 * publishes these as the model's public shape; a browser constant that
 * quietly disagreed with the engine would make the published rationale a lie
 * about the scores on the map next to it, so the test suite pins them.
 */

/** The three base categories and their exact caps. */
export const CATEGORY_CAPS = { project: 45, capacity: 25, fit: 48 };

/**
 * The mover blend: a recent valid move blends the score toward the 90–100
 * priority band instead of adding points. The decay denominator is derived —
 * 365 − 90 — not tuned; the divisor 8 maps the 0–80 base range onto the
 * band's 10 points.
 */
export const MOVER_BLEND = {
  flatDays: 90,
  zeroDays: 365,
  decayDenominatorDays: 275,
  baseDivisor: 8,
  priorityBand: [90, 100],
};

/** The one demotion in the model: a current verified rental registration. */
export const RENTAL_MODIFIER = -25;

/** The fixed V2 gap vocabulary a door discloses its missing inputs in. */
export const GAP_TYPES = [
  'rental_data_missing',
  'acs_missing',
  'imagery_missing',
  'sdl_page_unavailable',
  'local_comparables_insufficient',
  'assessed_value_missing',
];

/* ── Formatting ──────────────────────────────────────────────────────────── */

const MINUS = '−';

const isNumber = (value) => typeof value === 'number' && Number.isFinite(value);

/** A rate as a percentage, e.g. `81.8%`. */
const asPercent = (value, digits = 1) =>
  isNumber(value) ? `${(value * 100).toFixed(digits)}%` : 'not yet measured';

/* ── The ICP and the signal → ICP trace (R11.4, retained under R29) ──────── */

/**
 * The four traits that define the best concierge customer.
 *
 * Every row in the trace below points at one of these. A signal that traces
 * to none of them has no business moving a score, which is the whole
 * discipline this table exists to enforce.
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
    key: 'buys_work',
    label: 'Pays professionals rather than DIY',
    body:
      'A household that already pays for home work has demonstrated the '
      + 'behaviour the offer depends on. Nobody has to be converted from a '
      + 'weekend habit.',
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
      'An aging roof, a pool, a big lot or a historically slipping exterior all '
      + 'mean work is already due. Need is what turns capacity into a call.',
  },
];

/**
 * The signal → ICP trace: each V2 scoring term, the ICP trait it serves, and
 * its place in the arithmetic. The three base categories carry their exact
 * caps; the mover blend and the rental modifier are not capped point
 * categories, and say so.
 */
export function icpTrace() {
  return [
    {
      key: 'mover',
      label: 'Mover',
      cap: null,
      // Not a capped point category, but its lift has a true ceiling: at an
      // empty base the blend adds the full 90; richer bases gain less and
      // land inside the 90-100 band.
      capLabel: 'max 90',
      trait: 'Recently moved in',
      body:
        'A recent valid arm’s-length move does not add points: it blends '
        + 'the score toward the priority band, with a strength that decays as '
        + 'the move ages. The strongest trait gets the strongest mechanism.',
      signals: [
        {
          points: '→ 90+',
          description:
            'Deed recorded within 90 days — the window in which a new owner is '
            + 'still choosing every provider they will keep. The score blends '
            + 'toward the low-90s priority band, whatever the base.',
        },
        {
          points: 'fades',
          description:
            'From day 91 the pull decays on an exponential, reaching zero at '
            + 'day 365 — a year in, a mover is a resident.',
        },
      ],
    },
    {
      key: 'project',
      label: 'Project',
      cap: CATEGORY_CAPS.project,
      capLabel: `max ${CATEGORY_CAPS.project}`,
      trait: 'Pays professionals rather than DIY',
      body:
        'Permit lifecycle activity: every qualifying permit shows this '
        + 'household already pays professionals for home work. Read '
        + 'conservatively from the permit record alone.',
      signals: [
        {
          points: '+15',
          description:
            'Each qualifying permit — open with lifecycle activity in the '
            + 'last 12 months, or completed within 24 — earns 15. A boiler '
            + 'replacement counts the same as an addition: both are proof that '
            + 'work here gets bought rather than done in-house. Three permits '
            + 'reach the 45 cap.',
        },
      ],
    },
    {
      key: 'capacity',
      label: 'Capacity',
      cap: CATEGORY_CAPS.capacity,
      capLabel: `max ${CATEGORY_CAPS.capacity}`,
      trait: 'Capacity to pay for years of service',
      body:
        'Assessed value against the territory and against the nearest local '
        + 'comparables, plus the small neighborhood dual-income prior read at '
        + 'the Census block group.',
      signals: [
        {
          points: '+10',
          description:
            'Assessed at 1.5× or more of the median of the nearest 20 '
            + 'single-family comparables (+7 from 1.2×, +3 above the median — '
            + 'bands, never added together).',
        },
        {
          points: '+10',
          description:
            'Assessed value at or above the 90th percentile of the territory’s '
            + 'single-family homes (+7 from the 75th, +3 from the 50th).',
        },
        {
          points: '+5',
          description:
            'A small prior when the ACS reports a dual-income share of 35% or '
            + 'more for the surrounding block group — neighbourhood context, '
            + 'never a reading of any address.',
        },
      ],
    },
    {
      key: 'fit',
      label: 'Need',
      cap: CATEGORY_CAPS.fit,
      capLabel: `max ${CATEGORY_CAPS.fit}`,
      trait: 'Near-term service need',
      body:
        'Roof age, home age, a pool, solar, lot size, and historical exterior '
        + 'decline between the public imagery vintages: the properties where '
        + 'work is already coming due.',
      signals: [
        {
          points: '+12',
          description:
            'A completed roof installation at least 20 years old (+8 at 15, '
            + '+4 at 10) — at or past replacement age.',
        },
        {
          points: '+10',
          description:
            'A home at least 75 years old (+8 at 50, +5 at 30), where original '
            + 'systems are at or past replacement age.',
        },
        {
          points: '+8',
          description:
            'Exterior condition moving down the poor → fair → good → excellent '
            + 'scale between the 2015 and 2020 ortho vintages.',
        },
        {
          points: '+8',
          description:
            'A pool, established by a completed permit or public-domain aerial '
            + 'imagery — a standing maintenance commitment.',
        },
        {
          points: '+5',
          description:
            'Solar panels, established by a completed permit or aerial imagery.',
        },
        {
          points: '+5',
          description:
            'A lot of at least 0.5 acres — grounds that make outdoor work a '
            + 'standing job rather than an afternoon.',
        },
      ],
    },
    {
      key: 'rental',
      label: 'Rental modifier',
      cap: null,
      capLabel: `max ${MINUS}25`,
      trait: 'Owner-occupier context (demotion only)',
      body:
        'A current verified municipal rental registration is the model’s '
        + `only demotion: ${MINUS}25 points, applied after the blend. A rented `
        + 'door still buys home services, so it moves down the list, never off it.',
      signals: [
        {
          points: `${MINUS}25`,
          description:
            'A current verified match against the municipal rental '
            + 'registration. Negative by design and dormant until the registry '
            + 'is obtained: a rented address still buys home services, so this '
            + 'moves a door down the list rather than off it.',
        },
      ],
    },
  ];
}

/* ── The model section: caps, blend, decay, rental, unknowns (R29) ───────── */

function modelSection(report) {
  return {
    caps: [
      {
        key: 'project',
        label: 'Project',
        cap: CATEGORY_CAPS.project,
        body: `Permit lifecycle evidence of bought work, capped at ${CATEGORY_CAPS.project}.`,
      },
      {
        key: 'capacity',
        label: 'Capacity',
        cap: CATEGORY_CAPS.capacity,
        body: `Value and neighborhood-prior evidence of means, capped at ${CATEGORY_CAPS.capacity}.`,
      },
      {
        key: 'fit',
        label: 'Need',
        cap: CATEGORY_CAPS.fit,
        body: `Property evidence of near-term need, capped at ${CATEGORY_CAPS.fit}.`,
      },
    ],
    blend: {
      formula:
        `blended target = 90 + B/${MOVER_BLEND.baseDivisor}, where B is the `
        + 'capped base (0–80). A fresh mover moves toward that target with '
        + 'full strength; the published integer is the blended result.',
      priorityBand: [...MOVER_BLEND.priorityBand],
      body:
        'The blend maps the whole base range onto the priority band, so among '
        + 'fresh movers the better door still ranks higher — a blend preserves '
        + 'order where a flat bonus would flatten it.',
    },
    decay: {
      text:
        `Mover strength is flat for the first ${MOVER_BLEND.flatDays} days `
        + 'after a valid move, then decays exponentially, reaching zero at day '
        + `${MOVER_BLEND.zeroDays}. The ${MOVER_BLEND.decayDenominatorDays}-day `
        + `denominator in the exponent is derived as ${MOVER_BLEND.zeroDays} `
        + `${MINUS} ${MOVER_BLEND.flatDays}, not a tunable knob — there is no `
        + 'hard edge on which a door falls off a cliff.',
    },
    rental: {
      text:
        `A current verified rental registration applies the ${MINUS}25 modifier `
        + 'after the blend — the only demotion in the model, and it takes '
        + 'precedence over everything the blend did. The demotion currently '
        + 'ships dormant: the municipal registry has not been obtained (the '
        + 'OPRA records request is outstanding), so no live door carries it and '
        + 'it fires only in fixtures. A stale registration — one older than the '
        + 'verification window — is neutral, and missing rental data is neutral '
        + 'with a disclosed rental_data_missing gap.',
    },
    unknowns: [
      'Unknown means neutral: a signal that could not be measured never '
      + 'subtracts points and never promotes a door. Missing data is not '
      + 'evidence of anything.',
      'Every unmeasured input is disclosed on the door itself, in the fixed V2 '
      + `gap vocabulary: ${GAP_TYPES.join(', ')}.`,
    ],
    note: determinismNote(report),
  };
}

/**
 * The determinism claim, plus the fixture count when a report published one.
 *
 * The count is read from `eval/report.json` because this page once spelled a
 * stale count into its prose — a measurement stated as a literal is how that
 * drift happened, and an unmeasured number is stated as absent, never guessed.
 */
function determinismNote(report) {
  const total =
    report && report.golden && isNumber(report.golden.fixtures_total)
      ? report.golden.fixtures_total
      : null;
  const claim =
    'Deterministic and integer-valued: the same door and the same inputs '
    + 'produce the same score on every run';

  return total === null
    ? `${claim}. The golden-fixture count is read from the published evaluation `
      + 'report, and this page has none to read, so it states no count rather '
      + 'than a remembered one.'
    : `${claim}, and ${total} golden fixtures pin the arithmetic.`;
}

/* ── Evidence interpretation rules (R19/R24 phrasing) ────────────────────── */

const EVIDENCE_RULES = [
  'Project evidence is read conservatively from the permit lifecycle alone: a '
  + 'qualifying record in a live or recently completed state, nothing inferred '
  + 'beyond what the register says.',
  'Roof evidence is the latest explicit completed roof installation on record '
  + '— old enough to need service counts toward Need, and absence of a record '
  + 'is treated as unknown, not as an old roof.',
  'The exterior-condition signal is read as historical decline between the '
  + '2015 and 2020 orthophoto vintages — never a claim about the present state '
  + 'of the house, because the imagery cannot see the present.',
  'A later completed exterior permit supersedes the observation: the condition '
  + 'term goes neutral, because the owner has already addressed what the older '
  + 'imagery saw.',
];

/* ── ACS: a neighborhood-level prior, never a household claim (R15) ──────── */

const ACS_SECTION = {
  heading: 'Census data stays at the neighborhood',
  body:
    'The ACS dual-income prior is neighborhood-level context read at the '
    + 'Census block group. A block group covers hundreds of addresses, and a '
    + 'statistic about hundreds of addresses is never treated as a fact about '
    + 'any one door.',
  claims: [
    {
      id: 'no_household_inference',
      text:
        'ACS figures are never a claim about the people behind a door. Every '
        + 'evidence line built from the dual-income share is phrased as '
        + 'block-group context and nothing more.',
    },
    {
      id: 'acs_is_a_small_prior',
      text:
        'The block-group prior is deliberately too small to move a door '
        + 'between bands on its own. Neighborhood context nudges; it does not '
        + 'decide.',
    },
  ],
};

/* ── Source limitations (R29) ────────────────────────────────────────────── */

const LIMITATIONS = [
  'SDL permit lifecycle pages are point-in-time snapshots: a state read today '
  + 'can change tomorrow, and each run records when it looked rather than '
  + 'claiming currency.',
  'The SR1A sales register lags county recording, so the freshest sale a run '
  + 'can see is already weeks old — the top of the mover window may be '
  + 'unreachable on any given extract vintage.',
  'Permit records carry no contractor identity, so nothing here can say who '
  + 'did the work — only that permitted work happened at the parcel.',
  'Imagery vintage is a hard limit: the public orthophotos are the 2015 and '
  + '2020 flights, so every imagery signal describes those years, not today.',
];

/* ── The R36 validation plan (R29 / PRD R11.4) ───────────────────────────── */

function validationSection() {
  return {
    heading: 'Validation plan (R36)',
    primary:
      'Primary outcome: the receptive-conversation rate per answered door — a '
      + 'qualified conversation, counted against doors that actually opened.',
    secondary:
      'Secondary outcomes: follow-up appointments accepted, and jobs booked '
      + 'within the tracking window.',
    successTest:
      'The success test is directional ranking lift: higher score bands must '
      + 'out-perform lower bands on the primary outcome, quantile against '
      + 'quantile, in walk-order field tests.',
    claims: [
      {
        id: 'no_absolute_target',
        text:
          'No absolute conversion target is promised. One must not be invented '
          + 'before field outcomes exist — every number on this page today is '
          + 'an input measurement or a pipeline statistic, and none of it is '
          + 'evidence that the score predicts revenue.',
      },
      {
        id: 'pre_registered',
        text:
          'The contract is pre-registered: published, dated, and argued from '
          + 'the ICP before any outcome data existed, so it cannot have been '
          + 'quietly fitted to a result after the fact.',
      },
    ],
  };
}

/* ── Evaluation claims, sourced from the V2 report (R29) ─────────────────── */

/**
 * The evaluation section. A missing report leaves the section unavailable and
 * states no measurements: an unmeasured rate rendered as `0` would claim the
 * measurement was taken and came out badly, which is a different and worse
 * statement than "not measured yet".
 *
 * @param {object|null} report parsed `eval/report.json`
 */
export function evalSection(report) {
  if (!report) {
    return {
      available: false,
      caveat:
        'No published evaluation report is available, so this page states no '
        + 'measurements rather than estimates of them.',
      lines: [],
    };
  }

  const coverage = report.coverage || {};
  const vision = report.vision || {};
  const lines = [];

  if (isNumber(coverage.doors_scored) && isNumber(coverage.doors_total)) {
    lines.push(
      `${coverage.doors_scored} of ${coverage.doors_total} doors scored `
      + `(${asPercent(coverage.coverage)} coverage) on the published run.`
    );
  }
  if (isNumber(vision.precision)) {
    lines.push(
      `Vision spot-check: pool-detection precision ${asPercent(vision.precision)}, `
      + `recall ${asPercent(vision.recall)}, hallucination rate `
      + `${asPercent(vision.hallucination_rate)}.`
    );
  }

  return {
    available: true,
    contractVersion: report.score_contract_version ?? null,
    asOf: report.as_of ?? null,
    lines,
    // The honest caveat travels with the numbers it qualifies, verbatim.
    banner: vision.banner ?? null,
    source: vision.metrics_source ?? null,
    caveat: null,
  };
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
    pattern: /vision|imagery|openai/i,
    reasonField: null,
    availableField: null,
    measured: visionState,
  },
];

/** The deed-vintage row's identity, kept beside the providers it is listed with. */
const MOVER_VINTAGE = {
  key: 'mover_deed_vintage',
  label: 'Deed recency (mover blend)',
};

/**
 * Which signal sources answered on the published run, and why the others did
 * not — the declinations published beside the model rather than buried in a
 * log. Each row carries a `status` of `live`, `partial` or `declined`.
 *
 * @param {object|null} manifest parsed `data/run_manifest.json`
 */
export function signalAvailability(manifest) {
  const degradations = (manifest && manifest.degradations) || [];
  const resolve = (manifest && manifest.resolve) || {};

  const providers = PROVIDERS.map((provider) => {
    const { key, label, pattern, reasonField, availableField, measured } = provider;
    if (!manifest) {
      return {
        key,
        label,
        status: 'declined',
        reason:
          'No published run to report on, so this page cannot say whether the '
          + 'provider answered.',
      };
    }

    const state = measured ? measured(manifest, degradations) : null;
    if (state) return { key, label, ...state };

    const stated = reasonField ? resolve[reasonField] : null;
    const logged = degradations.find((note) => pattern.test(note)) || null;
    const reason = stated || logged;

    const declared = availableField ? resolve[availableField] : undefined;
    const live = !reason && declared !== false;

    return { key, label, status: live ? 'live' : 'declined', reason: reason || null };
  });

  return [...providers, moverVintageRow(manifest, degradations)].map((row) => ({
    ...row,
    live: row.status !== 'declined',
  }));
}

/**
 * The vision stage's own state, as the run measured it — or null for a run
 * that never measured it, which hands the row back to the degradation-string
 * reading it has always had.
 */
function visionState(manifest, degradations) {
  const vision = manifest.vision;
  if (!vision || !('available' in vision)) return null;

  if (vision.available === false) {
    const recorded = degradations.find((note) => /vision|imagery|openai/i.test(note)) || null;
    return {
      status: 'declined',
      reason:
        vision.declination_reason
        || recorded
        || 'The vision stage did not run on this publish, and the run recorded no reason.',
    };
  }

  const lost = isNumber(vision.answers_lost) ? vision.answers_lost : 0;
  if (lost <= 0) return { status: 'live', reason: null };

  return { status: 'partial', reason: describeVisionLoss(vision, manifest) };
}

/** What a partial vision run cost, as fractions read from the run itself. */
function describeVisionLoss(vision, manifest) {
  const total = isNumber(vision.answers_total) ? vision.answers_total : null;
  const withImagery = isNumber(vision.doors_with_imagery) ? vision.doors_with_imagery : null;
  const doors = isNumber(manifest.doors_total) ? manifest.doors_total : null;

  const lostLine =
    total === null
      ? `${vision.answers_lost} vision answers could not be read as detections`
      : `${vision.answers_lost} of ${total} vision answers could not be read as detections`;

  const ranLine =
    withImagery !== null && doors !== null
      ? ` The stage itself ran: ${withImagery} of ${doors} doors carry an `
        + 'imagery-backed evidence line, re-openable on the map.'
      : ' The stage itself ran; the answers it lost are the only part missing.';

  return `${lostLine}, so the doors they covered scored without their imagery signals.${ranLine}`;
}

/**
 * The deed-vintage row: whether any door was inside the mover window, and why
 * not — the run's own note quoted rather than paraphrased wherever one exists.
 */
function moverVintageRow(manifest, degradations) {
  const vintage = (manifest && manifest.deed_vintage) || null;

  if (!vintage) {
    return {
      ...MOVER_VINTAGE,
      status: 'declined',
      reason: manifest
        ? 'This run did not record how old the deed data behind the mover blend '
          + 'was, so this page cannot say whether any door was inside the mover window.'
        : 'There is no published run to report on, so this page cannot say '
          + 'whether any door was inside the mover window.',
    };
  }

  const recorded = degradations.find((note) => /mover/i.test(note)) || null;

  if (isNumber(vintage.doors_in_mover_window) && vintage.doors_in_mover_window > 0) {
    return { ...MOVER_VINTAGE, status: 'live', reason: recorded };
  }

  return { ...MOVER_VINTAGE, status: 'declined', reason: recorded || describeVintage(vintage) };
}

/** A sentence for a recorded vintage the run left uncommented. */
function describeVintage(vintage) {
  const days = isNumber(vintage.mover_window_days)
    ? vintage.mover_window_days
    : MOVER_BLEND.flatDays;

  return vintage.latest_deed_date
    ? `No door in this territory has a deed dated inside the ${days}-day mover `
      + `window: the newest deed anywhere in the county extract is `
      + `${vintage.latest_deed_date}, so no door carries a mover lift on this `
      + 'run. That is the vintage of the extract, not a rule that failed.'
    : `This run found no readable deed date, so no door could be placed inside `
      + `the ${days}-day mover window and no door carries a mover lift on this run.`;
}

/* ── The whole page ──────────────────────────────────────────────────────── */

/**
 * Assemble the page from the rationale (which is always true) and the
 * published run (which may be missing, partial, or degraded).
 *
 * @param {{report?: object|null, manifest?: object|null}} data
 */
export function buildEthicsPage({ report = null, manifest = null } = {}) {
  return {
    icp: {
      definition:
        'The best concierge customer has recently moved in, pays professionals '
        + 'rather than doing the work themselves, can pay for years of service '
        + 'rather than one job, and has work already coming due. Every term in '
        + 'the score traces to one of those four traits — nothing scores '
        + 'because it happened to be available.',
      traits: ICP_TRAITS,
    },
    trace: icpTrace(),
    model: modelSection(report),
    evidenceRules: EVIDENCE_RULES,
    acs: ACS_SECTION,
    limitations: LIMITATIONS,
    validation: validationSection(),
    evaluation: evalSection(report),
    sources: sourcesSection(manifest),
    ethics: {
      danielsLaw: danielsLawSection(),
      streetView: streetViewSection(),
    },
    availability: signalAvailability(manifest).map(({ label, status, live, reason }) => ({
      label,
      status,
      live,
      reason,
    })),
    resolution: resolutionSection(manifest),
    coverage: coverageSection(manifest),
    demo: {
      isDemoOnly: true,
      label: 'Simulate a data-fetch error on the map',
      body:
        'A demo-only trigger for the map’s degraded state, so a reviewer '
        + 'can see how the page behaves when the doors layer fails rather than '
        + 'taking the claim on trust.',
    },
  };
}

function danielsLawSection() {
  return {
    heading: 'Daniel’s Law and personal identity',
    body:
      'New Jersey’s Daniel’s Law restricts the disclosure of home '
      + 'addresses and personal identity for covered persons. This pipeline is '
      + 'built so that compliance is a property of what it never holds, not of '
      + 'a filter at the end: the identifying columns are dropped at the source '
      + 'adapter, before anything reaches storage, scoring, or the map.',
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
          'No request this pipeline makes asks for personal identity. The '
          + 'absence is in the query, not only in the parsing of the answer.',
      },
      {
        id: 'no_identity_reconstruction',
        text:
          'No attempt is made to reconstruct who lives at a door — no deed-book '
          + 'name mining, no broker or people-search data, no cross-referencing '
          + 'toward a person. The score describes a property, and the UI shows '
          + 'only the property.',
      },
    ],
  };
}

function streetViewSection() {
  return {
    heading: 'Imagery, and the Street View ToS',
    body:
      'Google Maps Platform Terms of Service §3.2.3 prohibits creating '
      + 'derived datasets from Street View content, and a per-parcel signal '
      + 'stored in a database is exactly that. So the bulk imagery signals do '
      + 'not come from Street View at all — they come from public-domain state '
      + 'orthophotography, which carries no such restriction.',
    claims: [
      {
        id: 'bulk_from_public_domain_orthos',
        text:
          'Every imagery signal that reaches a score — pool, exterior '
          + 'condition, lot context — is derived from public-domain NJ '
          + 'orthophotos and NAIP coverage, which may lawfully be analysed and stored.',
      },
      {
        id: 'street_view_demo_scale',
        text:
          'Street View is used only at demo scale, for a handful of doors, '
          + 'without building a stored derived dataset — and that position is '
          + 'published here rather than left implicit.',
      },
      {
        id: 'zillow_redfin_excluded',
        text:
          'Zillow and Redfin are excluded outright. Their terms forbid scraping '
          + 'and redistribution, and no listing-portal data of any kind is in '
          + 'this pipeline.',
      },
      {
        id: 'tos_over_convenience',
        text:
          'Where a licence and a better signal conflict, the licence wins and '
          + 'the gap is disclosed. That is why the imagery terms are absent '
          + 'rather than approximated when a provider declines.',
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
      name: 'NJ SR1A sales register',
      detail:
        'Year-to-date statewide sales flat file (nj.gov/treasury/taxation), fixed-width, '
        + 'joined to parcels by block, lot and condominium qualifier. Supplies the deed '
        + 'recency the mover blend reads whenever it is fresher than MOD-IV’s. The '
        + 'download is statewide and its layout reserves grantor/grantee identity columns; '
        + 'only non-identity columns are ever read, and the raw file is never cached.',
      retrieved: date('sales'),
    },
    {
      name: 'NJ construction permits (SDL lifecycle)',
      detail:
        'Socrata (data.nj.gov), dataset w9se-dmra, matched to parcels by block and lot. '
        + 'Lifecycle pages are point-in-time reads; no contractor identity is collected.',
      retrieved: date('permits'),
    },
    {
      name: 'Census ACS 5-year block-group tables',
      detail: 'B23007, B19013 and B08303 at block-group geography — neighborhood context only.',
      retrieved: date('acs'),
    },
    {
      name: 'NJ orthophotography, 2015 and 2020 vintages',
      detail: 'maps.nj.gov, public domain. The source of every bulk imagery signal.',
      retrieved: date('imagery'),
    },
    {
      name: 'Ramsey municipal rental registration',
      detail:
        'Municipal record requested under OPRA. The only input to the rental modifier, '
        + 'which ships dormant until it arrives.',
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
