// Tests for docs/widget-logic.js — the exact file the browser loads, evaluated in
// a bare sandbox (it only needs `self`). Run: npm run test:widget
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const sandbox = { self: {} };
vm.runInNewContext(readFileSync(new URL('../docs/widget-logic.js', import.meta.url), 'utf8'), sandbox);
const L = sandbox.self.WprGasLogic;
const plain = v => JSON.parse(JSON.stringify(v)); // sandbox objects have a foreign prototype

const city = (avg, low, high, stations) => ({
  current_avg: { regular: avg }, low: { regular: low }, high: { regular: high },
  ...(stations ? { stations } : {}),
});

test('extremeStations names the city that owns the station price, not the average', () => {
  // The 2026-09-19 bug, reduced: Rhinelander has the highest AVERAGE, but the
  // priciest single station is in Merrill.
  const metros = {
    Milwaukee:   city(4.192, 3.79, 4.35),
    Rhinelander: city(4.506, 4.47, 4.59),
    Merrill:     city(4.495, 4.43, 4.79),
  };
  const { low, high } = L.extremeStations(metros, 'regular');
  assert.deepEqual(plain(low), { city: 'Milwaukee', price: 3.79 });
  assert.deepEqual(plain(high), { city: 'Merrill', price: 4.79 });
});

test('extremeStations names the station when it is in the stored list', () => {
  const metros = {
    Wausau: city(4.4, 4.19, 4.49, [
      { name: 'BP', address: '401 State Rd, Hatley', prices: { regular: 4.19 } },
      { name: 'Kolbe', prices: { regular: 4.3 } },
    ]),
  };
  const { low, high } = L.extremeStations(metros, 'regular');
  assert.equal(low.name, 'BP');
  assert.equal(high.name, undefined); // priciest stations are never in the cheapest-8 list
});

test('extremeStations skips metros missing the fuel and tolerates empty input', () => {
  const metros = { A: city(4, 3.9, 4.1), B: { current_avg: {}, low: {}, high: {} } };
  assert.equal(L.extremeStations(metros, 'regular').low.city, 'A');
  assert.deepEqual(plain(L.extremeStations({}, 'regular')), { low: null, high: null });
  assert.deepEqual(plain(L.extremeStations(null, 'diesel')), { low: null, high: null });
});

test('history keys sort by date, never as strings', () => {
  const history = { '7/24/26': {}, '3/18/26': {}, '12/01/25': {}, '07/25/26': {}, junk: {} };
  assert.deepEqual(plain(L.sortedHistoryKeys(history)), ['12/01/25', '3/18/26', '7/24/26', '07/25/26']);
});

test('previousReading skips today and gaps, and reports the key it used', () => {
  const history = {
    '07/22/26': { statewide: { regular: 3.5 } },
    '07/23/26': { Wausau: { regular: 3.6 } },          // no statewide that day
    '07/25/26': { statewide: { regular: 3.9 } },        // today
  };
  assert.deepEqual(plain(L.previousReading(history, '07/25/26', 'statewide', 'regular')),
    { price: 3.5, key: '07/22/26' });
  assert.equal(L.previousReading(history, '07/25/26', 'Madison', 'regular'), null);
  assert.equal(L.previousReading(null, '07/25/26', 'statewide', 'regular'), null);
});

test('comparisonLabel says "yesterday" only when it is', () => {
  assert.equal(L.comparisonLabel('07/25/26', '07/24/26'), 'vs yesterday');
  assert.equal(L.comparisonLabel('07/25/26', '07/22/26'), 'vs Jul 22');
  assert.equal(L.comparisonLabel('01/01/27', '12/31/26'), 'vs yesterday'); // across a year
  assert.equal(L.comparisonLabel('07/25/26', 'nonsense'), 'vs previous');
});

test('delta direction, sign and dead band', () => {
  assert.deepEqual(plain(L.delta(3.5, 3.4)), { text: '▲ +0.100', cls: 'up-color' });
  assert.deepEqual(plain(L.delta(3.4, 3.5)), { text: '▼ -0.100', cls: 'down-color' });
  assert.equal(L.delta(3.5, 3.5).cls, 'flat-color');
  assert.equal(L.delta(3.5004, 3.5).cls, 'flat-color'); // sub-0.001 is noise
  assert.deepEqual(plain(L.delta(null, 3.5)), { text: '—', cls: 'flat-color' });
});

test('freshness wording and the 26h stale threshold', () => {
  const at = '2026-09-19T07:50:00+00:00';
  const t = Date.parse(at);
  assert.equal(L.freshness(at, '09/19/26', t + 20 * 60e3).text, 'Updated just now');
  assert.equal(L.freshness(at, '09/19/26', t + 7 * 36e5).text, 'Updated 7h ago');
  assert.equal(L.freshness(at, '09/19/26', t + 25 * 36e5).stale, false);
  const old = L.freshness(at, '09/19/26', t + 50 * 36e5);
  assert.equal(old.text, 'Updated 2 days ago');
  assert.equal(old.stale, true);
  // No scraped_at: fall back to the price date rather than claiming freshness
  assert.match(L.freshness(undefined, '09/19/26', t).text, /^Updated /);
  assert.equal(L.freshness(undefined, '', t).text, 'Updated');
});

test('sortMetroNames: featured, cheapest, A–Z', () => {
  const metros = {
    Wausau: city(4.44), Antigo: city(4.44), Milwaukee: city(4.19),
    Madison: city(4.3), NoReg: { current_avg: {} },
  };
  assert.deepEqual(plain(L.sortMetroNames(metros, ['Wausau', 'Ghost', 'Madison'], 'regular', 'featured')),
    ['Wausau', 'Madison', 'Antigo', 'Milwaukee', 'NoReg']);
  assert.deepEqual(plain(L.sortMetroNames(metros, [], 'regular', 'cheapest')),
    ['Milwaukee', 'Madison', 'Antigo', 'Wausau', 'NoReg']); // ties A–Z, missing fuel last
  assert.deepEqual(plain(L.sortMetroNames(metros, [], 'regular', 'az')),
    ['Antigo', 'Madison', 'Milwaukee', 'NoReg', 'Wausau']);
});

test('mapsUrl builds an encoded Wisconsin-pinned search', () => {
  const url = L.mapsUrl({ name: "Casey's", address: '423 N 17th Ave, Wausau' });
  assert.equal(url,
    'https://www.google.com/maps/search/?api=1&query=' + encodeURIComponent("Casey's, 423 N 17th Ave, Wausau, WI"));
  assert.ok(L.mapsUrl({ name: 'BP' }).endsWith(encodeURIComponent('BP, WI')));
});

test('esc neutralises markup in scraped names', () => {
  assert.equal(L.esc('<img src=x onerror=alert(1)>'), '&lt;img src=x onerror=alert(1)&gt;');
  assert.equal(L.esc(`Casey's "General" & Co`), 'Casey&#39;s &quot;General&quot; &amp; Co');
  assert.equal(L.esc(null), '');
});

test('seasonOf / seasonLabel: July is the boundary', () => {
  assert.equal(L.seasonOf('2026-03-30'), 2025);
  assert.equal(L.seasonOf('2025-10-06'), 2025);
  assert.equal(L.seasonOf('2026-06'), 2025);   // month-only (natural gas)
  assert.equal(L.seasonOf('2026-07'), 2026);
  assert.equal(L.seasonOf('junk'), null);
  assert.equal(L.seasonLabel(2025), '2025–26');
  assert.equal(L.seasonLabel(2029), '2029–30');
});

test('heatingSummary: latest, previous, nearest year-ago within tolerance, in-season flag', () => {
  const series = [
    { date: '2025-03-31', price: 1.90 },   // 364 days before latest → year-ago match
    { date: '2025-03-24', price: 1.95 },
    { date: '2026-03-16', price: 2.059 },
    { date: '2026-03-23', price: 2.069 },
    { date: '2026-03-30', price: 2.066 },
    { date: '2026-03-02', price: 9.99, junk: true },
  ].sort(() => 0);
  const s = L.heatingSummary(series, Date.parse('2026-04-05T00:00:00Z'));
  assert.equal(s.latest.date, '2026-03-30');
  assert.equal(s.prev.date, '2026-03-23');
  assert.equal(s.yearAgo.date, '2025-03-31');
  assert.equal(s.inSeason, true);
  assert.equal(s.season, 2025);
  // Late September: the March reading is 25 weeks old → off-season
  assert.equal(L.heatingSummary(series, Date.parse('2026-09-25T00:00:00Z')).inSeason, false);
});

test('heatingSummary: no year-ago when the nearest point is outside ±10 days', () => {
  const series = [{ date: '2025-02-24', price: 1.8 }, { date: '2026-03-30', price: 2.0 }];
  assert.equal(L.heatingSummary(series, 0).yearAgo, null);
  assert.equal(L.heatingSummary([], 0), null);
  assert.equal(L.heatingSummary(null, 0), null);
  assert.equal(L.heatingSummary([{ date: 'bad', price: 1 }], 0), null);
});

test('seasonPoints keys each winter by days since 1 October', () => {
  const series = [
    { date: '2025-10-06', price: 1.7 }, { date: '2026-03-30', price: 2.0 },
    { date: '2024-10-07', price: 1.6 }, { date: '2025-03-31', price: 1.9 },
  ];
  const s25 = L.seasonPoints(series, 2025);
  assert.deepEqual(plain(s25.map(p => [p.day, p.price])), [[5, 1.7], [180, 2.0]]);
  const s24 = L.seasonPoints(series, 2024);
  assert.deepEqual(plain(s24.map(p => p.day)), [6, 181]);
  assert.deepEqual(plain(L.seasonPoints(series, 2023)), []);
});

test('fillCost rounds to whole dollars and rejects bad input', () => {
  assert.equal(L.fillCost(2.066, 400), 826);
  assert.equal(L.fillCost(4.323, 275), 1189);
  assert.equal(L.fillCost(null, 400), null);
  assert.equal(L.fillCost(2.0, undefined), null);
});
