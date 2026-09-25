// Pure logic shared by index.html and index-compact.html — no DOM, no fetch, no
// clock (callers pass `now`), so tests/widget-logic.test.mjs can run this exact
// file under Node. It exists because an untested inline helper once paired the
// state's cheapest STATION price with the city that had the lowest AVERAGE, and
// the widget named the wrong city for weeks.
//
// Both widgets load it as `widget-logic.js?v=N`. Bump N in both whenever a
// function is added or its contract changes: Pages caches this file for ten
// minutes, and a fresh index.html must never meet a stale copy of its logic.
(function () {
  'use strict';

  const FUELS = ['regular', 'mid_grade', 'premium', 'diesel'];

  function esc(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, ch => (
      { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]
    ));
  }

  // History keys are 'm/d/yy' and older ones aren't zero-padded, so a plain string
  // sort interleaves months and years (it once put March after July and compared
  // the hero against a four-month-old price). Always order by parsed date.
  function historyKeyTime(key) {
    const m = /^(\d{1,2})\/(\d{1,2})\/(\d{2})$/.exec(key || '');
    return m ? Date.UTC(2000 + +m[3], +m[1] - 1, +m[2]) : NaN;
  }

  function sortedHistoryKeys(history) {
    return Object.keys(history || {})
      .filter(k => !isNaN(historyKeyTime(k)))
      .sort((a, b) => historyKeyTime(a) - historyKeyTime(b));
  }

  // Most recent reading before today for one series ('statewide' or a metro name),
  // with the key it came from. Runs can miss days, so callers label the real gap.
  function previousReading(history, todayKey, series, fuel) {
    const keys = sortedHistoryKeys(history);
    for (let i = keys.length - 1; i >= 0; i--) {
      if (keys[i] === todayKey) continue;
      const v = history[keys[i]]?.[series]?.[fuel];
      if (typeof v === 'number') return { price: v, key: keys[i] };
    }
    return null;
  }

  // "vs yesterday" only when it really is yesterday; otherwise name the date.
  function comparisonLabel(todayKey, prevKey) {
    const todayT = historyKeyTime(todayKey);
    const prevT = historyKeyTime(prevKey);
    if (isNaN(todayT) || isNaN(prevT)) return 'vs previous';
    if (Math.round((todayT - prevT) / 864e5) === 1) return 'vs yesterday';
    const d = new Date(prevT);
    return `vs ${d.toLocaleString('en-US', { month: 'short', timeZone: 'UTC' })} ${d.getUTCDate()}`;
  }

  function delta(current, previous) {
    if (current == null || previous == null) return { text: '—', cls: 'flat-color' };
    const diff = current - previous;
    const up = diff > 0.001, down = diff < -0.001;
    return {
      text: `${up ? '▲' : down ? '▼' : '—'} ${diff > 0 ? '+' : ''}${diff.toFixed(3)}`,
      cls: up ? 'up-color' : down ? 'down-color' : 'flat-color',
    };
  }

  // Relative "Updated N ago"; `stale` past ~26h turns the header amber.
  function freshness(scrapedAt, priceDate, nowMs) {
    let ts = scrapedAt ? new Date(scrapedAt).getTime() : NaN;
    if (isNaN(ts)) ts = historyKeyTime(priceDate);
    if (isNaN(ts)) return { text: `Updated ${priceDate || ''}`.trim(), stale: false, title: '' };
    const hours = (nowMs - ts) / 36e5;
    let rel;
    if (hours < 1) rel = 'just now';
    else if (hours < 24) rel = `${Math.max(1, Math.round(hours))}h ago`;
    else { const days = Math.round(hours / 24); rel = `${days} day${days === 1 ? '' : 's'} ago`; }
    return { text: `Updated ${rel}`, stale: hours > 26, title: new Date(ts).toLocaleString() };
  }

  // Where the state's cheapest and priciest single STATION prices actually are.
  // These are station-level extremes (metros[city].low / .high), so the city must
  // come from the same field — never from the averages. `name` is filled in when
  // the station is among that city's stored cheapest-stations list.
  function extremeStations(metros, fuel) {
    let low = null, high = null;
    for (const [city, m] of Object.entries(metros || {})) {
      const lo = m?.low?.[fuel], hi = m?.high?.[fuel];
      if (typeof lo === 'number' && (!low || lo < low.price)) low = { city, price: lo };
      if (typeof hi === 'number' && (!high || hi > high.price)) high = { city, price: hi };
    }
    for (const end of [low, high]) {
      if (!end) continue;
      const hit = (metros[end.city].stations || []).find(st => st?.prices?.[fuel] === end.price);
      if (hit) end.name = hit.name;
    }
    return { low, high };
  }

  // Metro tab ordering. 'featured' = WPR's priority list first, then the rest as
  // scraped; 'cheapest' = by the active fuel's average (cities without that fuel
  // last); 'az' = alphabetical.
  function sortMetroNames(metros, priority, fuel, mode) {
    const names = Object.keys(metros || {});
    if (mode === 'az') return names.sort((a, b) => a.localeCompare(b));
    if (mode === 'cheapest') {
      const price = n => metros[n]?.current_avg?.[fuel];
      return names.sort((a, b) => {
        const pa = price(a), pb = price(b);
        if (pa == null || pb == null) return (pa == null) - (pb == null) || a.localeCompare(b);
        return pa - pb || a.localeCompare(b);
      });
    }
    const featured = (priority || []).filter(n => metros[n]);
    return [...featured, ...names.filter(n => !featured.includes(n))];
  }

  // Google Maps search link for a station. GasBuddy gives no coordinates, so the
  // query is name + street + locality, pinned to Wisconsin.
  function mapsUrl(station) {
    const query = [station?.name, station?.address, 'WI'].filter(Boolean).join(', ');
    return 'https://www.google.com/maps/search/?api=1&query=' + encodeURIComponent(query);
  }

  // ── Home Heating tab (EIA weekly propane / heating oil, monthly natural gas) ──
  // Survey weeks run October–March; a "season" is named by its starting year, and
  // the boundary is July so a late-March point and the next October's point never
  // share one. ISO dates ('2026-03-30') and months ('2026-06') both parse.
  const MS_DAY = 864e5;
  function isoTime(date) {
    const m = /^(\d{4})-(\d{2})(?:-(\d{2}))?$/.exec(date || '');
    return m ? Date.UTC(+m[1], +m[2] - 1, m[3] ? +m[3] : 1) : NaN;
  }
  function seasonOf(date) {
    const m = /^(\d{4})-(\d{2})/.exec(date || '');
    if (!m) return null;
    const y = +m[1], mo = +m[2];
    return mo >= 7 ? y : y - 1;
  }
  function seasonLabel(startYear) {
    return `${startYear}–${String(startYear + 1).slice(-2)}`;
  }

  // Latest reading plus the comparisons the newsroom quotes. `yearAgo` is the
  // reading nearest 52 weeks earlier, within ±10 days, so a skipped survey week
  // doesn't silently compare against a different month. `inSeason` is whether the
  // latest reading is recent (≤ 21 days), i.e. the survey is currently running.
  function heatingSummary(series, nowMs) {
    if (!Array.isArray(series) || !series.length) return null;
    const pts = series.filter(p => p && typeof p.price === 'number' && !isNaN(isoTime(p.date)))
      .sort((a, b) => isoTime(a.date) - isoTime(b.date));
    if (!pts.length) return null;
    const latest = pts[pts.length - 1];
    const t = isoTime(latest.date);
    const prev = pts.length > 1 ? pts[pts.length - 2] : null;
    const target = t - 364 * MS_DAY;
    let yearAgo = null;
    for (const p of pts) {
      const gap = Math.abs(isoTime(p.date) - target);
      if (gap <= 10 * MS_DAY && (!yearAgo || gap < Math.abs(isoTime(yearAgo.date) - target))) yearAgo = p;
    }
    return {
      latest, prev, yearAgo,
      inSeason: nowMs - t <= 21 * MS_DAY,
      season: seasonOf(latest.date),
    };
  }

  // Points for one season, with `day` = days since 1 October of that season so two
  // winters can share an x-axis.
  function seasonPoints(series, startYear) {
    const oct1 = Date.UTC(startYear, 9, 1);
    return (series || [])
      .filter(p => p && typeof p.price === 'number' && seasonOf(p.date) === startYear)
      .map(p => ({ day: Math.round((isoTime(p.date) - oct1) / MS_DAY), price: p.price, date: p.date }))
      .sort((a, b) => a.day - b.day);
  }

  // "$828" style cost of a delivery. A 500-gallon propane tank is filled to 80%,
  // so the everyday "fill the tank" number is 400 gallons.
  function fillCost(pricePerGallon, gallons) {
    if (typeof pricePerGallon !== 'number' || typeof gallons !== 'number') return null;
    return Math.round(pricePerGallon * gallons);
  }

  self.WprGasLogic = {
    FUELS, esc, historyKeyTime, sortedHistoryKeys, previousReading, comparisonLabel,
    delta, freshness, extremeStations, sortMetroNames, mapsUrl,
    isoTime, seasonOf, seasonLabel, heatingSummary, seasonPoints, fillCost,
  };
})();
