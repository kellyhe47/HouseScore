import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import path from 'node:path';

/* ── T106: no V1 scoring vocabulary survives in the shipped browser code (R32) ──
 *
 * The V2 cutover replaced the score contract: the five V1 groups
 * (mover/hires-out/capacity/need/modifier), `raw_total`, the −15 rental
 * modifier, the 90-day mover cutoff, provider churn and the
 * deferred-maintenance bonus are all dead. A runtime file still carrying any
 * of those strings is either rendering V1 copy over V2 numbers or holding a
 * label table for evidence types the pipeline no longer emits — both lies
 * about the map. This scan covers every file the browser actually ships:
 * web/js runtime modules plus web/*.html and web/css.
 *
 * Test files and fixtures are exempt: they may name the dead vocabulary in
 * order to forbid it.
 */

const WEB = path.join(import.meta.dirname, '..');

/** Every shipped file: web/js/*.js minus tests/fixtures, web/*.html, web/css. */
function shippedFiles() {
  const files = [];
  for (const entry of readdirSync(path.join(WEB, 'js'))) {
    if (!entry.endsWith('.js')) continue;
    if (entry.endsWith('.test.js') || entry === 'test-fixtures.js') continue;
    files.push(path.join(WEB, 'js', entry));
  }
  for (const entry of readdirSync(WEB)) {
    if (entry.endsWith('.html') || entry.endsWith('.css')) files.push(path.join(WEB, entry));
  }
  const css = path.join(WEB, 'css');
  try {
    for (const entry of readdirSync(css)) files.push(path.join(css, entry));
  } catch {
    // No css directory is fine.
  }
  return files;
}

/** [name, pattern] — each pattern is a V1-only string, worded to avoid false hits. */
const FORBIDDEN = [
  ['raw_total / rawTotal', /raw_total|rawTotal|raw total/i],
  ['hires-out group', /hires[_\s-]?out/i],
  ['deferred maintenance bonus', /deferred[_\s-]?maintenance/i],
  ['provider / contractor churn', /(provider|contractor)[_\s-]?churn/i],
  ['no-repeat-contractor copy', /no repeat contractor/i],
  // −15 as a modifier (ASCII or U+2212), not the "-15" inside a date or pin:
  // the character before the sign may not be a digit.
  ['the −15 rental modifier', /(^|[^\d])[−-]15\b/],
  ['the 90-day cutoff as current behavior', /90[\s-]day[s]?\s?(hard\s)?(cutoff|cut-off)/i],
  ['V1 mover band keys', /mover_(30|60|90)d/],
  ['V1 weight keys', /permit_each|absentee_modifier|need_home_age|capacity_1_5x/],
];

test('no shipped web file carries V1 scoring vocabulary', () => {
  const offences = [];
  for (const file of shippedFiles()) {
    const text = readFileSync(file, 'utf8');
    for (const [name, pattern] of FORBIDDEN) {
      const match = pattern.exec(text);
      if (match) {
        const line = text.slice(0, match.index).split('\n').length;
        offences.push(`${path.relative(WEB, file)}:${line} — ${name} (${JSON.stringify(match[0].trim())})`);
      }
    }
  }
  assert.deepEqual(offences, [], `V1 strings survive in shipped files:\n${offences.join('\n')}`);
});

test('the scan actually covers the runtime modules', () => {
  const names = shippedFiles().map((file) => path.basename(file));
  for (const expected of ['map.js', 'panel.js', 'route-ui.js', 'ramp.js', 'ethics.js', 'share.js']) {
    assert.ok(names.includes(expected), `${expected} escaped the scan`);
  }
  assert.ok(!names.some((name) => name.endsWith('.test.js')), 'test files are exempt');
});
