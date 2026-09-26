// The data contract: does the JSON the scraper actually produced satisfy what the
// widgets and the digest card rely on? widget-logic.test.mjs proves the logic on
// synthetic data; this proves the *real* files feed it — a renamed field or a
// missing list would pass every other test and only break on the live site.
//
// Runs two ways: in the Tests workflow against the committed docs/*.json, and in
// the update workflow against the scraper's fresh output *before* it is
// committed (a failure there restores the previous files and alerts).
//
// Run: npm run test:widget
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const docs = new URL('../docs/', import.meta.url);
const load = name => JSON.parse(readFileSync(new URL(name, docs), 'utf8'));
const sandbox = { self: {} };
vm.runInNewContext(readFileSync(new URL('widget-logic.js', docs), 'utf8'), sandbox);
const L = sandbox.self.WprGasLogic;

const data = load('gas_prices.json');
const history = load('gas_prices_history.json');
const FUELS = ['regular', 'mid_grade', 'premium', 'diesel'];
const isPrice = v => typeof v === 'number' && v > 1 && v < 10;
const ISO_DAY = /^\d{4}-\d{2}-\d{2}$/;

test('gas_prices.json: top-level shape the widgets read on every render', () => {
  for (const key of ['source', 'price_date', 'scraped_at', 'statewide', 'metros', 'priority_metros', 'summary']) {
    assert.ok(key in data, `missing top-level "${key}"`);
  }
  assert.match(data.price_date, /^\d{2}\/\d{2}\/\d{2}$/, 'price_date is mm/dd/yy');
  assert.ok(!isNaN(L.historyKeyTime(data.price_date)), 'price_date parses as a date');
  assert.ok(!isNaN(Date.parse(data.scraped_at)), 'scraped_at is an ISO timestamp');
  assert.ok(isPrice(data.statewide.current_avg.regular), 'statewide regular average is a plausible price');
  assert.ok(isPrice(data.statewide.low.regular) && isPrice(data.statewide.high.regular));
  assert.ok(data.summary.blurb.length > 80 && data.summary.headline.includes('$'), 'newsroom blurb + headline present');
});

test('gas_prices.json: every metro carries what the Metro tab and cards need', () => {
  const names = Object.keys(data.metros);
  assert.equal(names.length, 22, 'all 22 cities present (fresh or carried forward)');
  for (const [name, m] of Object.entries(data.metros)) {
    assert.ok(isPrice(m.current_avg?.regular), `${name}: current_avg.regular`);
    assert.ok(isPrice(m.low?.regular) && isPrice(m.high?.regular), `${name}: low/high.regular`);
    assert.ok(m.low.regular <= m.current_avg.regular && m.current_avg.regular <= m.high.regular, `${name}: low ≤ avg ≤ high`);
    assert.ok(Array.isArray(m.stations) && m.stations.length > 0, `${name}: cheapest-stations list`);
    for (const st of m.stations) {
      assert.ok(typeof st.name === 'string' && st.name.trim(), `${name}: station has a name`);
      assert.ok(isPrice(st.prices?.regular), `${name}: station "${st.name}" has a regular price`);
    }
    if (m.stale) assert.match(m.stale_from, /^\d{2}\/\d{2}\/\d{2}$/, `${name}: stale entries say since when`);
  }
  for (const p of data.priority_metros) assert.ok(names.includes(p), `priority metro "${p}" exists`);
});

test('gas_prices.json: AAA and neighbor blocks the Statewide tab, blurb and nudge read', () => {
  assert.match(data.aaa.as_of, /^\d{2}\/\d{2}\/\d{2}$/);
  for (const period of ['current', 'yesterday', 'week_ago', 'month_ago', 'year_ago']) {
    assert.ok(isPrice(data.aaa[period]?.regular), `aaa.${period}.regular`);
  }
  for (const code of ['MN', 'IA', 'IL', 'MI']) {
    assert.ok(isPrice(data.neighbors.states[code]?.current?.regular), `neighbors.${code}`);
    assert.ok(data.neighbors.states[code].name, `neighbors.${code}.name`);
  }
});

test('widget logic produces sensible output from the real file', () => {
  for (const fuel of FUELS) {
    const { low, high } = L.extremeStations(data.metros, fuel);
    assert.ok(low && high, `${fuel}: extremes found`);
    assert.ok(low.city in data.metros && high.city in data.metros, `${fuel}: extreme cities are real metros`);
    assert.ok(low.price <= high.price);
  }
  assert.equal(L.sortMetroNames(data.metros, data.priority_metros, 'regular', 'cheapest').length, 22);
  assert.equal(L.sortMetroNames(data.metros, data.priority_metros, 'regular', 'featured')[0], 'Wausau');
  const fresh = L.freshness(data.scraped_at, data.price_date, Date.now());
  assert.match(fresh.text, /^Updated /);
  for (const st of data.metros.Wausau.stations) {
    assert.match(L.mapsUrl(st), /^https:\/\/www\.google\.com\/maps\/search\/\?api=1&query=/);
  }
});

test('gas_prices_history.json: dated keys, statewide series, enough depth for the widgets', () => {
  const keys = Object.keys(history);
  assert.ok(keys.length >= 60, `history has ${keys.length} days (the blurb milestone needs 60+)`);
  for (const k of keys) assert.ok(!isNaN(L.historyKeyTime(k)), `history key "${k}" is a date`);
  const sorted = L.sortedHistoryKeys(history);
  assert.equal(sorted.length, keys.length, 'no history key was dropped as unparseable');
  assert.ok(isPrice(history[sorted.at(-1)].statewide?.regular), 'latest day has statewide.regular');
  const prev = L.previousReading(history, data.price_date, 'statewide', 'regular');
  assert.ok(prev && isPrice(prev.price), 'hero "vs yesterday" has something to compare against');
  const daysBack = (L.historyKeyTime(sorted.at(-1)) - L.historyKeyTime(sorted[0])) / 864e5;
  assert.ok(daysBack <= 400, 'history cap of 400 days is respected');
});

test('eia_weekly.json + eia_context.json: the Trends chart and context strip', () => {
  const weekly = load('eia_weekly.json');
  for (const fuel of FUELS) {
    assert.ok(Array.isArray(weekly[fuel]) && weekly[fuel].length >= 52, `eia_weekly.${fuel} has a year+`);
    const last = weekly[fuel].at(-1);
    assert.match(last.date, ISO_DAY);
    assert.ok(isPrice(last.price));
  }
  const ctx = load('eia_context.json');
  assert.ok(isPrice(ctx.national_regular) && ISO_DAY.test(ctx.national_as_of), 'national average + date');
  assert.ok(typeof ctx.wti === 'number' && ctx.wti > 10 && ctx.wti < 300 && ISO_DAY.test(ctx.wti_as_of), 'WTI + date');
});

test('eia_heating.json: the Home Heating tab and digest line', () => {
  const heat = load('eia_heating.json');
  for (const fuel of ['propane', 'heating_oil']) {
    assert.ok(Array.isArray(heat[fuel]) && heat[fuel].length >= 100, `${fuel}: multi-season history`);
    const s = L.heatingSummary(heat[fuel], Date.now());
    assert.ok(s && isPrice(s.latest.price), `${fuel}: summary from real data`);
    assert.ok(L.seasonPoints(heat[fuel], s.season).length + L.seasonPoints(heat[fuel], s.season - 1).length >= 20,
      `${fuel}: enough points to draw a season`);
  }
  const gas = L.heatingSummary(heat.natural_gas, Date.now());
  assert.ok(gas && typeof gas.latest.price === 'number' && /^\d{4}-\d{2}$/.test(gas.latest.date), 'natural gas monthly');
});
