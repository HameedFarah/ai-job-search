/* Initial-load acceptance: board paint must not wait for detail-only Site Data. */
'use strict';
const path = require('path');
const { execSync } = require('child_process');
const BASE = String(process.argv[2] || 'http://127.0.0.1:4173').replace(/\/$/, '');
function loadPlaywright() {
  const root = execSync('npm root -g').toString().trim();
  try { return require(path.join(root, 'playwright')); } catch { return require('playwright'); }
}
function assert(ok, message, detail = '') { if (!ok) throw new Error(`${message}${detail ? `: ${detail}` : ''}`); }
async function main() {
  const { chromium } = loadPlaywright();
  const browser = await chromium.launch({ executablePath: '/usr/bin/google-chrome', args: ['--no-sandbox', '--disable-dev-shm-usage'] });
  const context = await browser.newContext({ viewport: { width: 1600, height: 900 } });
  const page = await context.newPage();
  const started = Date.now();
  await page.goto(`${BASE}/index.html`, { waitUntil: 'load' });
  await page.locator('.role-card').first().waitFor();
  const firstCardMs = Date.now() - started;
  await page.waitForTimeout(1200);
  const perf = await page.evaluate(() => {
    const resources = performance.getEntriesByType('resource').map(entry => ({
      name: entry.name,
      transfer: entry.transferSize || 0,
      encoded: entry.encodedBodySize || 0
    }));
    const nav = performance.getEntriesByType('navigation')[0];
    const paints = Object.fromEntries(performance.getEntriesByType('paint').map(item => [item.name, Math.round(item.startTime)]));
    return {
      resources,
      domContentLoaded: nav ? Math.round(nav.domContentLoadedEventEnd) : 0,
      load: nav ? Math.round(nav.loadEventEnd) : 0,
      navigationTransfer: nav ? (nav.transferSize || 0) : 0,
      paints
    };
  });
  const urls = perf.resources.map(item => new URL(item.name));
  const paths = urls.map(url => `${url.pathname}${url.search}`);
  for (const forbidden of ['ai_requests', '/history?', '/comments?', 'ats-design-options.json']) {
    assert(!paths.some(value => value.includes(forbidden)), 'Detail-only request leaked into initial board load', forbidden);
  }
  const transferBytes = perf.navigationTransfer + perf.resources.reduce((sum, item) => sum + (item.transfer || item.encoded || 0), 0);
  assert(firstCardMs < 1500, 'First visible job card should render in under 1.5s on local acceptance', String(firstCardMs));
  assert(perf.domContentLoaded < 1500, 'DOMContentLoaded should stay below 1.5s on local acceptance', String(perf.domContentLoaded));
  console.log(JSON.stringify({ valid: true, firstCardMs, requestCount: perf.resources.length + 1, transferBytes, domContentLoaded: perf.domContentLoaded, load: perf.load, paints: perf.paints, requests: paths }, null, 2));
  await browser.close();
}
main().catch(error => { console.error(error.message); process.exit(1); });
