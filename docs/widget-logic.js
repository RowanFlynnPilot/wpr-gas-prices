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

  // `cents: true` gives whole cents ("▲ 2¢") for rows that show prices to the cent;
  // a change that rounds to 0¢ is flat there, so the arrow never disagrees with the text.
  function delta(current, previous, { cents = false } = {}) {
    if (current == null || previous == null) return { text: '—', cls: 'flat-color' };
    const diff = current - previous;
    if (cents) {
      const c = Math.round(diff * 100);
      return {
        text: c > 0 ? `▲ ${c}¢` : c < 0 ? `▼ ${-c}¢` : '— 0¢',
        cls: c > 0 ? 'up-color' : c < 0 ? 'down-color' : 'flat-color',
      };
    }
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

  // Common residential tank sizes and the delivery that "fills" each. Propane tanks
  // are filled to 80% (the rest is vapor space); heating-oil deliveries are quoted
  // as the tank's capacity, as before.
  const HEATING_TANKS = {
    propane: [{ gallons: 250, fill: 200 }, { gallons: 500, fill: 400 }, { gallons: 1000, fill: 800 }],
    heating_oil: [{ gallons: 275, fill: 275 }, { gallons: 330, fill: 330 }],
  };

  // Last winter's reading nearest the same day of the season (± `tol` days): the
  // honest "this week last year" for a chart that overlays two winters.
  function sameWeekLastSeason(prevPoints, day, tol = 10) {
    let best = null;
    for (const p of prevPoints || []) {
      const gap = Math.abs(p.day - day);
      if (gap <= tol && (!best || gap < Math.abs(best.day - day))) best = p;
    }
    return best;
  }

  // Headline for the Home Heating tab: the fuel whose year-over-year move is
  // largest in percent leads; the other fuel goes in the dek. `items` are
  // [{ label, sum }] with `sum` from heatingSummary().
  function heatingStory(items) {
    const ok = (items || []).filter(i => i && i.sum);
    if (!ok.length) return null;
    const pct = i => (i.sum.yearAgo ? Math.abs(i.sum.latest.price / i.sum.yearAgo.price - 1) : -1);
    const lead = ok.slice().sort((a, b) => pct(b) - pct(a))[0];
    const month = new Date(isoTime(lead.sum.latest.date)).getUTCMonth() + 1;
    const phase = !lead.sum.inSeason ? '' : month >= 10 ? ' heading into winter' : month <= 2 ? ' this winter' : ' as winter winds down';
    const move = i => {
      const d = i.sum.latest.price - i.sum.yearAgo.price;
      return Math.abs(d) < 0.005 ? 'about where it was a year ago' : `${centsText(d)} ${d > 0 ? 'higher' : 'lower'} than a year ago`;
    };
    const headline = lead.sum.yearAgo
      ? (lead.sum.inSeason ? `${lead.label} is ${move(lead)}${phase}` : `${lead.label} ended last winter ${move(lead).replace('than a year ago', 'than the winter before')}`)
      : `${lead.label} is $${lead.sum.latest.price.toFixed(2)} a gallon${phase}`;
    const other = ok.find(i => i !== lead && i.sum.yearAgo);
    const dek = [
      other ? `${other.label} is ${move(other)}.` : '',
      `Statewide dealer averages, week of ${shortDate(isoTime(lead.sum.latest.date))}.`,
    ].filter(Boolean).join(' ');
    return { lead: lead.label, headline, dek };
  }

  // ── Shared formatting ──
  const MONTHS =['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  function shortDate(ms) {
    const d = new Date(ms);
    return `${MONTHS[d.getUTCMonth()]} ${d.getUTCDate()}`;
  }
  // A price difference the way a reporter would write it: "67¢", "$1.52".
  function centsText(diff) {
    const c = Math.round(Math.abs(diff) * 100);
    return c >= 100 ? '$' + (c / 100).toFixed(2) : c + '¢';
  }

  // ── Price Trends chart ──
  // Series are [{ t: ms, price }] sorted ascending. Wisconsin is WPR's own daily
  // statewide tracking (the reason the widget exists) and leads the chart; the
  // weekly EIA Midwest series is the benchmark. The chart used to do the reverse,
  // and its loudest number — "−5.3%" in green — described the Midwest from its May
  // peak while Wisconsin sat at a six-month high.
  const FUEL_PHRASE = { regular: 'regular gas', mid_grade: 'mid-grade', premium: 'premium', diesel: 'diesel' };

  // Value of a series at time t by linear interpolation; null outside its span.
  function valueAt(series, t) {
    if (!series || !series.length || t < series[0].t || t > series[series.length - 1].t) return null;
    let lo = 0, hi = series.length - 1;
    while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (series[mid].t <= t) lo = mid; else hi = mid; }
    const a = series[lo], b = series[hi];
    return b.t === a.t ? a.price : a.price + (b.price - a.price) * (t - a.t) / (b.t - a.t);
  }

  // Latest reading at or before t — the honest value for a weekly series in a tooltip.
  function readingAtOrBefore(series, t) {
    let best = null;
    for (const p of series || []) { if (p.t <= t) best = p; else break; }
    return best;
  }

  // Everything the chart says in words and tiles, computed once. `windowDays` is
  // the range shown (182 or 364); a series that doesn't cover it is described by
  // its first date rather than claiming a "12-month high" from five months of data.
  function trendStory(wi, mw, windowDays, fuel) {
    const leadIsWI = Array.isArray(wi) && wi.length >= 2;
    const lead = leadIsWI ? wi : mw;
    if (!Array.isArray(lead) || lead.length < 2) return null;
    const first = lead[0], last = lead[lead.length - 1];
    let low = first, high = first;
    for (const p of lead) { if (p.price < low.price) low = p; if (p.price > high.price) high = p; }
    const change = last.price - first.price;
    const mwLast = leadIsWI && Array.isArray(mw) && mw.length ? mw[mw.length - 1] : null;
    const gap = mwLast ? last.price - mwLast.price : null;
    const covers = (last.t - first.t) >= (windowDays - 10) * MS_DAY;
    const span = windowDays <= 200 ? 'six-month' : '12-month';
    const isHigh = last.price >= high.price - 0.0005 && change > 0.005;
    const isLow = last.price <= low.price + 0.0005 && change < -0.005;
    const subject = `${leadIsWI ? 'Wisconsin' : 'Midwest'} ${FUEL_PHRASE[fuel] || fuel}`;
    const since = shortDate(first.t);
    const dir = change > 0 ? 'up' : 'down';

    let headline;
    if (isHigh) headline = covers ? `${subject} is at its ${span} high` : `${subject} is at its highest since ${since}`;
    else if (isLow) headline = covers ? `${subject} is at its ${span} low` : `${subject} is at its lowest since ${since}`;
    else if (Math.abs(change) < 0.005) headline = `${subject} is about where it was on ${since}`;
    else headline = `${subject} is ${dir} ${centsText(change)} since ${since}`;

    const parts = [];
    if (isHigh || isLow) parts.push(`${dir} ${centsText(change)} since ${since}`);
    if (gap != null) parts.push(Math.abs(gap) < 0.005
      ? 'level with the Midwest average'
      : `running ${centsText(gap)} ${gap > 0 ? 'above' : 'below'} the Midwest average`);
    if (!isHigh && !isLow) parts.push(`the low was $${low.price.toFixed(2)} on ${shortDate(low.t)}`);
    let dek = parts.length > 1 ? parts.slice(0, -1).join(', ') + ', and ' + parts[parts.length - 1] : (parts[0] || '');
    dek = dek ? dek.charAt(0).toUpperCase() + dek.slice(1) + '.' : '';

    return { leadIsWI, first, last, low, high, change, mwLast, gap, covers, isHigh, isLow, headline, dek };
  }

  // ── Metro map ──
  // City coordinates for the 22 scraped metros (CITIES in scrape_gas_prices.py).
  // The data contract test fails if the scraper gains a city that isn't here.
  const CITY_COORDS = {
    'Wausau': [44.959, -89.630], 'Stevens Point': [44.524, -89.574],
    'Wisconsin Rapids': [44.384, -89.817], 'Marshfield': [44.669, -90.172],
    'Rhinelander': [45.637, -89.412], 'Merrill': [45.180, -89.683],
    'Mosinee': [44.793, -89.703], 'Antigo': [45.140, -89.152],
    'Eau Claire': [44.811, -91.498], 'Green Bay': [44.519, -88.020],
    'Appleton': [44.262, -88.415], 'Madison': [43.073, -89.401],
    'Milwaukee': [43.039, -87.906], 'La Crosse': [43.801, -91.240],
    'Fond du Lac': [43.775, -88.439], 'Janesville': [42.683, -89.019],
    'Kenosha': [42.585, -87.821], 'Oshkosh': [44.025, -88.543],
    'Racine': [42.726, -87.783], 'Sheboygan': [43.751, -87.714],
    'Superior': [46.721, -92.104], 'Waukesha': [43.012, -88.232],
  };
  // The towns ringed as "Central Wisconsin" on the map — WPR's home region.
  const CENTRAL_WI = ['Wausau', 'Mosinee', 'Merrill', 'Stevens Point', 'Wisconsin Rapids', 'Marshfield', 'Antigo'];

  // The projection docs/wi-outline.js was drawn with (scripts/build-wi-outline.py).
  function projectWI(lat, lon, proj) {
    return [proj.pad + (lon * proj.k - proj.minX) * proj.s, proj.pad + (proj.maxY - lat) * proj.s];
  }

  // Diverging classes around the state average: one question, above or below.
  // Gray means "about the same" (within 5¢); never a hue at the midpoint.
  const PRICE_BINS = ['15¢+ below', '5–15¢ below', 'within 5¢', '5–15¢ above', '15¢+ above'];
  function priceBin(price, average) {
    const c = Math.round((price - average) * 100);   // whole cents, so edges don't wobble on float noise
    if (c <= -15) return 0;
    if (c < -5) return 1;
    if (c <= 5) return 2;
    if (c < 15) return 3;
    return 4;
  }

  // Metros for one fuel, cheapest first, each with its bin.
  function rankMetros(metros, fuel, average) {
    return Object.entries(metros || {})
      .filter(([, m]) => typeof m?.current_avg?.[fuel] === 'number')
      .map(([name, m]) => ({ name, price: m.current_avg[fuel], bin: priceBin(m.current_avg[fuel], average) }))
      .sort((a, b) => a.price - b.price || a.name.localeCompare(b.name));
  }

  // How many of the n priciest metros sit in the ringed central region.
  function centralShare(ranked, n = 6) {
    const top = ranked.slice(-n);
    return { count: top.filter(m => CENTRAL_WI.includes(m.name)).length, of: top.length };
  }

  // ── Newsroom notes on the trend chart ──
  // docs/annotations.json is edited by hand: [{ "date": "YYYY-MM-DD", "text": "...",
  // "fuels": ["regular", ...] (optional, default all) }]. Anything malformed is
  // skipped here (and fails the data contract test), never drawn.
  const NOTE_MAX = 80;
  function validNote(n) {
    // The round trip rejects impossible dates like 2026-02-30, which Date.UTC would roll over.
    return !!n && typeof n.date === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(n.date)
      && new Date(isoTime(n.date)).toISOString().slice(0, 10) === n.date
      && typeof n.text === 'string' && n.text.trim().length > 0 && n.text.length <= NOTE_MAX
      && (n.fuels === undefined || (Array.isArray(n.fuels) && n.fuels.every(f => FUELS.includes(f))));
  }
  function notesInWindow(notes, startMs, endMs, fuel) {
    return (Array.isArray(notes) ? notes : [])
      .filter(validNote)
      .filter(n => !n.fuels || n.fuels.includes(fuel))
      .map(n => ({ t: isoTime(n.date), text: n.text.trim() }))
      .filter(n => n.t >= startMs && n.t <= endMs)
      .sort((a, b) => a.t - b.t);
  }

  // ── Fill-up calculator ──
  // Typical usable tank sizes, in gallons. A reader's own tank will differ; the
  // widget says these are typical.
  const VEHICLES = [
    { key: 'compact', label: 'Compact car', gallons: 12 },
    { key: 'sedan', label: 'Midsize sedan', gallons: 15 },
    { key: 'suv', label: 'SUV', gallons: 19 },
    { key: 'pickup', label: 'Pickup', gallons: 26 },
  ];
  const dollars = v => Math.round(v * 100) / 100;
  // What filling up costs in one city today, and against a week ago (the city's
  // own history, same source), the cheapest listed station, and a year ago
  // (AAA's statewide change, since our per-city history is shorter than a year).
  function fillUp({ metros, history, aaa, city, fuel, gallons }) {
    const m = metros?.[city];
    const price = m?.current_avg?.[fuel];
    if (typeof price !== 'number' || !(gallons > 0)) return null;
    const cost = dollars(price * gallons);

    let week = null;
    const keys = sortedHistoryKeys(history);
    if (keys.length) {
      const series = keys.map(k => ({ t: historyKeyTime(k), price: history[k]?.[city]?.[fuel] }))
        .filter(p => typeof p.price === 'number');
      const lastT = historyKeyTime(keys[keys.length - 1]);
      const p = readingAtOrBefore(series, lastT - 7 * MS_DAY);
      if (p && lastT - p.t <= 10 * MS_DAY) week = { t: p.t, diff: dollars(cost - p.price * gallons) };
    }

    const st = (m.stations || []).filter(s => typeof s.prices?.[fuel] === 'number')
      .sort((a, b) => a.prices[fuel] - b.prices[fuel])[0];
    const cheapest = st ? { name: st.name, address: st.address || '', price: st.prices[fuel], save: dollars(cost - st.prices[fuel] * gallons) } : null;

    const now = aaa?.current?.[fuel], then = aaa?.year_ago?.[fuel];
    const year = typeof now === 'number' && typeof then === 'number' ? dollars((now - then) * gallons) : null;
    return { city, price, gallons, cost, week, cheapest, year };
  }

  // ── Map time-lapse ──
  // One frame per history day for the last `days` days: each metro's price and
  // bin against that day's statewide average. Stale cities are left out of the
  // history file, so a metro missing on a day shows its latest reading from the
  // previous week (marked `carried`); older than that it is absent from the frame.
  function mapFrames(history, fuel, days = 60) {
    const keys = sortedHistoryKeys(history);
    if (!keys.length) return [];
    const lastT = historyKeyTime(keys[keys.length - 1]);
    const latest = {};               // city -> { t, price } as of the frame being built
    const frames = [];
    for (const k of keys) {
      const t = historyKeyTime(k), day = history[k] || {};
      for (const [name, v] of Object.entries(day)) {
        if (name !== 'statewide' && typeof v?.[fuel] === 'number') latest[name] = { t, price: v[fuel] };
      }
      const avg = day.statewide?.[fuel];
      if (t < lastT - (days - 1) * MS_DAY || typeof avg !== 'number') continue;
      const metros = {};
      for (const [name, r] of Object.entries(latest)) {
        if (t - r.t > 7 * MS_DAY) continue;
        metros[name] = { price: r.price, bin: priceBin(r.price, avg), carried: r.t !== t };
      }
      frames.push({ key: k, t, avg, metros });
    }
    return frames;
  }

  // ── Statewide tab headline ──
  // `series` is WPR's daily statewide reading for one fuel, [{ t: ms, price }]
  // sorted ascending: the same source as the header price and the 30-day chart
  // under the headline, so the three can't disagree.
  function dateSince(t, lastT) {
    const d = new Date(t);
    return lastT - t > 300 * MS_DAY ? `${MONTHS[d.getUTCMonth()]} ${d.getUTCDate()}, ${d.getUTCFullYear()}` : shortDate(t);
  }
  function statewideStory(series, fuel) {
    if (!Array.isArray(series) || series.length < 2) return null;
    const last = series[series.length - 1];
    // A comparison only counts when the reading really is about that far back.
    const back = (days, minDays) => {
      const p = readingAtOrBefore(series, last.t - days * MS_DAY);
      return p && last.t - p.t <= (days + 3) * MS_DAY && last.t - p.t >= minDays * MS_DAY ? p : null;
    };
    const wk = back(7, 5), mo = back(30, 25);
    const cents = p => (p ? Math.round((last.price - p.price) * 100) : null);
    const c7 = cents(wk), c30 = cents(mo);

    // "Highest since …": the latest earlier reading at or above today's, if it
    // is at least two weeks back; none at all means the highest we've recorded.
    const first = series[0];
    let extreme = null;
    for (const kind of ['high', 'low']) {
      let since = null;
      for (let i = series.length - 2; i >= 0; i--) {
        const p = series[i];
        if (kind === 'high' ? p.price >= last.price - 0.0005 : p.price <= last.price + 0.0005) { since = p; break; }
      }
      if (since && last.t - since.t >= 14 * MS_DAY) { extreme = { kind, since: since.t }; break; }
      if (!since && last.t - first.t >= 30 * MS_DAY) { extreme = { kind, since: null }; break; }
    }

    const subject = `Wisconsin ${FUEL_PHRASE[fuel] || fuel}`;
    const move = (c, period) => `${subject} is ${c > 0 ? 'up' : 'down'} ${centsText(c / 100)} ${period}`;
    const headline = c7 != null && Math.abs(c7) >= 5 ? move(c7, 'in a week')
      : c30 != null && Math.abs(c30) >= 5 ? move(c30, 'over the past month')
      : `${subject} is holding steady`;

    let dek = `$${last.price.toFixed(2)} a gallon today`;
    if (extreme) {
      const word = extreme.kind === 'high' ? 'highest' : 'lowest';
      dek += extreme.since
        ? `, the ${word} since ${dateSince(extreme.since, last.t)}`
        : `, the ${word} since WPR began tracking on ${dateSince(first.t, last.t)}`;
    }
    dek += '.';
    return { last, wk, mo, c7, c30, extreme, headline, dek };
  }

  self.WprGasLogic = {
    FUELS, esc, historyKeyTime, sortedHistoryKeys, previousReading, comparisonLabel,
    delta, freshness, extremeStations, sortMetroNames, mapsUrl,
    isoTime, seasonOf, seasonLabel, heatingSummary, seasonPoints, fillCost,
    shortDate, centsText, valueAt, readingAtOrBefore, trendStory, statewideStory, mapFrames, VEHICLES, fillUp, NOTE_MAX, validNote, notesInWindow, HEATING_TANKS, sameWeekLastSeason, heatingStory,
    CITY_COORDS, CENTRAL_WI, projectWI, PRICE_BINS, priceBin, rankMetros, centralShare,
  };
})();
