// Renders a self-contained card page to a PNG: the email newsletter digest
// (docs/digest.html → docs/digest.png) and the square social card
// (docs/social.html → docs/social.png).
//
// Email clients strip <iframe> and can't run JS, and social posts need an image,
// so the live widget can't be used directly. This snapshots an already-built card
// in headless Chromium and writes the PNG. It produces IMAGES — it does NOT cache
// or feed the widget's data.
//
//   node scripts/render-digest.mjs <url> <outPath> [cardSelector] [viewportWidth] [scale] [altTextPath]
//
// Defaults are the digest's: .digest-card, 520px wide, 2× (a crisp ~960px PNG for
// retina email clients). The social card is `.social-card 1080 1` — 1080×1080 at 1×.
// With altTextPath, the card's plain-text version (body[data-alt]) is saved there:
// the newsletter's alt text, and the whole story for readers who see no images.
import { chromium } from 'playwright'
import { writeFileSync } from 'node:fs'

const url = process.argv[2] || 'http://127.0.0.1:4180/digest.html'
const out = process.argv[3] || 'docs/digest.png'
const selector = process.argv[4] || '.digest-card'
const width = Number(process.argv[5] || 520)
const scale = Number(process.argv[6] || 2)
const altOut = process.argv[7] || ''

const browser = await chromium.launch()
try {
  // Pin locale + Central time so dates on the card match the Wisconsin audience
  // (CI runners are UTC).
  const page = await browser.newPage({
    deviceScaleFactor: scale,
    viewport: { width, height: Math.max(1000, width + 40) },
    locale: 'en-US',
    timezoneId: 'America/Chicago',
  })
  await page.goto(url, { waitUntil: 'load', timeout: 60000 })

  // Each card page sets body[data-ready] once its data has been fetched and drawn
  // (or the honest error card is shown), so the screenshot never races the fetch.
  await page.waitForSelector('body[data-ready="1"]', { timeout: 60000 })

  // Let web fonts paint so numbers don't render in a fallback face. Best-effort:
  // fonts.ready resolves even when the network font fails, and we cap the wait.
  await page.evaluate(() => Promise.race([
    document.fonts.ready,
    new Promise((r) => setTimeout(r, 4000)),
  ]))
  await page.waitForTimeout(400)

  await page.locator(selector).screenshot({ path: out })
  console.log(`Wrote ${out}`)
  if (altOut) {
    const alt = await page.getAttribute('body', 'data-alt')
    if (!alt) throw new Error('card set no data-alt text')
    writeFileSync(altOut, alt + '\n')
    console.log(`Wrote ${altOut}`)
  }
} finally {
  await browser.close()
}
