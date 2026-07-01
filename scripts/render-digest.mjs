// Renders docs/digest.html to a PNG for the email newsletter.
//
// Email clients strip <iframe> and can't run JS, so the live widget can't be embedded.
// This snapshots the already-built digest card in headless Chromium and writes the PNG.
// It produces an IMAGE for email — it does NOT cache or feed the widget's data.
//
//   node scripts/render-digest.mjs <url> <outPath>
import { chromium } from 'playwright'

const url = process.argv[2] || 'http://127.0.0.1:4180/digest.html'
const out = process.argv[3] || 'docs/digest.png'

const browser = await chromium.launch()
try {
  // 2x scale → a crisp ~960px-wide PNG that stays sharp on retina/HiDPI email clients.
  // Pin locale + Central time so the "Updated <date>" line matches the Wisconsin audience
  // (CI runners are UTC).
  const page = await browser.newPage({
    deviceScaleFactor: 2,
    viewport: { width: 520, height: 1000 },
    locale: 'en-US',
    timezoneId: 'America/Chicago',
  })
  await page.goto(url, { waitUntil: 'load', timeout: 60000 })

  // digest.html sets body[data-ready] once the data has been fetched and rendered
  // (or the honest error card is shown), so the screenshot never races the fetch.
  await page.waitForSelector('body[data-ready="1"]', { timeout: 60000 })

  // Let web fonts paint so numbers don't render in a fallback face. Best-effort:
  // fonts.ready resolves even when the network font fails, and we cap the wait.
  await page.evaluate(() => Promise.race([
    document.fonts.ready,
    new Promise((r) => setTimeout(r, 4000)),
  ]))
  await page.waitForTimeout(400)

  await page.locator('.digest-card').screenshot({ path: out })
  console.log(`Wrote ${out}`)
} finally {
  await browser.close()
}
