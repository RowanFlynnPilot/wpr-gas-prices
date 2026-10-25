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
  // Cents mode: whole cents, and a sub-half-cent move is flat, not "▲ 0¢".
  assert.deepEqual(plain(L.delta(4.433, 4.389, { cents: true })), { text: '▲ 4¢', cls: 'up-color' });
  assert.deepEqual(plain(L.delta(4.381, 4.39, { cents: true })), { text: '▼ 1¢', cls: 'down-color' });
  assert.deepEqual(plain(L.delta(4.433, 4.429, { cents: true })), { text: '— 0¢', cls: 'flat-color' });
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

// ── Price Trends story ──────────────────────────────────────────────────
const day = 864e5;
const ser = (startIso, prices, stepDays = 1) => prices.map((price, i) => ({ t: Date.parse(startIso + 'T12:00:00Z') + i * stepDays * day, price }));

test('trendStory: Wisconsin leads, at a six-month high, with the Midwest gap', () => {
  const wi = ser('2026-04-26', [4.33, ...Array(180).fill(4.5), 5.00]);
  const mw = ser('2026-04-27', Array(26).fill(4.4).concat([4.62]), 7);
  const s = L.trendStory(wi, mw, 182, 'mid_grade');
  assert.equal(s.leadIsWI, true);
  assert.equal(s.headline, 'Wisconsin mid-grade is at its six-month high');
  assert.equal(s.dek, 'Up 67¢ since Apr 26, and running 38¢ above the Midwest average.');
  assert.equal(s.low.price, 4.33);
});

test('trendStory: never claims a 12-month high from a shorter history', () => {
  const wi = ser('2026-04-26', [4.33, 4.6, 5.0]);
  const s = L.trendStory(wi, [], 364, 'regular');
  assert.equal(s.headline, 'Wisconsin regular gas is at its highest since Apr 26');
  assert.equal(s.gap, null);
  assert.equal(s.dek, 'Up 67¢ since Apr 26.');
});

test('trendStory: falling-but-not-a-low says the move and names the low', () => {
  const wi = ser('2026-05-01', [4.80, 4.10, 4.40]);
  const s = L.trendStory(wi, ser('2026-05-01', [4.5, 4.6], 7), 182, 'diesel');
  assert.equal(s.headline, 'Wisconsin diesel is down 40¢ since May 1');
  assert.equal(s.dek, 'Running 20¢ below the Midwest average, and the low was $4.10 on May 2.');
});

test('trendStory: falls back to the Midwest when Wisconsin history is missing', () => {
  const s = L.trendStory(null, ser('2026-01-05', [3.0, 2.8, 2.5], 7), 182, 'premium');
  assert.equal(s.leadIsWI, false);
  assert.match(s.headline, /^Midwest premium is at its/);
  assert.equal(s.gap, null);
  assert.equal(L.trendStory(null, [], 182, 'regular'), null);
});

test('valueAt interpolates inside the span only; readingAtOrBefore never looks ahead', () => {
  const mw = ser('2026-01-05', [3.0, 3.7], 7);
  assert.equal(L.valueAt(mw, mw[0].t + 3.5 * day), 3.35);
  assert.equal(L.valueAt(mw, mw[0].t - day), null);
  assert.equal(L.readingAtOrBefore(mw, mw[1].t - 1).price, 3.0);
  assert.equal(L.readingAtOrBefore(mw, mw[0].t - 1), null);
});

test('centsText and shortDate', () => {
  assert.equal(L.centsText(0.674), '67¢');
  assert.equal(L.centsText(-1.524), '$1.52');
  assert.equal(L.shortDate(Date.UTC(2026, 6, 8, 12)), 'Jul 8');
});

// ── Metro map ───────────────────────────────────────────────────────────
test('priceBin: diverging classes around the average, edges in whole cents', () => {
  const avg = 4.43;
  assert.deepEqual(plain([4.28, 4.29, 4.37, 4.38, 4.43, 4.48, 4.49, 4.57, 4.58].map(p => L.priceBin(p, avg))),
    [0, 1, 1, 2, 2, 2, 3, 3, 4]);
  assert.equal(L.PRICE_BINS.length, 5);
});

test('projectWI matches the generated outline projection', () => {
  const proj = { k: 0.71079947, minX: -66.034499, maxY: 47.081595, s: 81.869494, pad: 14 };
  const [x, y] = L.projectWI(44.959, -89.630, proj);      // Wausau sits mid-state
  assert.ok(x > 120 && x < 260 && y > 130 && y < 250, `Wausau at ${x},${y}`);
  const [sx, sy] = L.projectWI(46.721, -92.104, proj);    // Superior: far north-west
  assert.ok(sx < x && sy < y);
});

test('rankMetros + centralShare', () => {
  const m = n => ({ current_avg: { regular: n } });
  const ranked = L.rankMetros({ Wausau: m(4.43), Mosinee: m(4.69), 'Eau Claire': m(4.08), Marshfield: m(4.69), Madison: m(4.33) }, 'regular', 4.43);
  assert.deepEqual(plain(ranked.map(r => r.name)), ['Eau Claire', 'Madison', 'Wausau', 'Marshfield', 'Mosinee']);
  assert.equal(ranked[0].bin, 0);
  assert.deepEqual(plain(L.centralShare(ranked, 2)), { count: 2, of: 2 });
});
