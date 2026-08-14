/* Mobile acceptance test for the live-source ATS CV design gallery. */
'use strict';
const fs = require('fs');
const path = require('path');
const http = require('http');
const { execSync } = require('child_process');

const ROOT = path.join(__dirname, '..', 'site');
const BASE = 'http://127.0.0.1:4174';

function loadPlaywright() {
  const root = execSync('npm root -g').toString().trim();
  try { return require(path.join(root, 'playwright')); }
  catch { return require('playwright'); }
}
function contentType(file) {
  const ext = path.extname(file).toLowerCase();
  return ({ '.html': 'text/html; charset=utf-8', '.css': 'text/css', '.js': 'text/javascript', '.json': 'application/json', '.png': 'image/png', '.pdf': 'application/pdf', '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' })[ext] || 'application/octet-stream';
}
function server() {
  return http.createServer((req, res) => {
    const requestPath = decodeURIComponent((req.url || '/').split('?')[0]);
    const relative = requestPath === '/' ? 'resume-designs.html' : requestPath.replace(/^\//, '');
    const file = path.resolve(ROOT, relative);
    if (!file.startsWith(path.resolve(ROOT)) || !fs.existsSync(file) || !fs.statSync(file).isFile()) {
      res.writeHead(404); res.end('not found'); return;
    }
    res.writeHead(200, { 'content-type': contentType(file) });
    fs.createReadStream(file).pipe(res);
  });
}
function assert(ok, message, detail = '') {
  if (!ok) throw new Error(`${message}${detail ? `: ${detail}` : ''}`);
}

async function main() {
  const local = server();
  await new Promise(resolve => local.listen(4174, '127.0.0.1', resolve));
  const { chromium } = loadPlaywright();
  const browser = await chromium.launch({ executablePath: '/usr/bin/google-chrome', args: ['--no-sandbox', '--disable-dev-shm-usage'] });
  const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', msg => { if (msg.type() === 'error' && !/404|unavailable|here\.now/i.test(msg.text())) errors.push(msg.text()); });
  try {
    await page.goto(`${BASE}/resume-designs.html`, { waitUntil: 'networkidle' });
    await page.waitForTimeout(250);
    assert(errors.length === 0, 'Console/page errors', errors.join(' | '));
    const header = await page.locator('.design-topbar').boundingBox();
    assert(header && header.height <= 52, 'Design header matches compact dashboard header', header ? `${header.height}px` : 'missing');
    assert((await page.locator('.design-topbar h1').innerText()).trim() === 'Career Engine', 'Compact header title is Career Engine');
    assert(await page.locator('#design-grid .resume-design-card').count() === 5, 'Five CV designs are shown');
    const columns = await page.locator('#design-grid').evaluate(el => getComputedStyle(el).gridTemplateColumns.split(' ').length);
    assert(columns === 1, 'Mobile gallery uses one readable CV column', String(columns));
    const images = page.locator('.design-preview');
    assert(await images.count() === 5, 'Five CV preview images are present');
    let previewHttp200 = 0;
    let pdfHttp200 = 0;
    for (let i = 0; i < 5; i += 1) {
      const metrics = await images.nth(i).evaluate(img => ({ complete: img.complete, naturalWidth: img.naturalWidth, naturalHeight: img.naturalHeight, src: img.getAttribute('src') }));
      assert(metrics.complete && metrics.naturalWidth > 0 && metrics.naturalHeight > 0, `CV preview ${i + 1} loads`, JSON.stringify(metrics));
      const link = images.nth(i).locator('xpath=..');
      const href = await link.getAttribute('href');
      assert(Boolean(href && /\.pdf$/i.test(href)), `CV preview ${i + 1} resolves to its PDF`, String(href));
      const previewResponse = await page.request.get(new URL(metrics.src, BASE).href);
      const pdfResponse = await page.request.get(new URL(href, BASE).href);
      assert(previewResponse.status() === 200, `CV preview ${i + 1} HTTP 200`, String(previewResponse.status()));
      assert(pdfResponse.status() === 200, `CV PDF ${i + 1} HTTP 200`, String(pdfResponse.status()));
      previewHttp200 += 1;
      pdfHttp200 += 1;
    }
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    assert(overflow <= 1, 'No page-level horizontal overflow', `${overflow}px`);
    console.log(JSON.stringify({ valid: true, viewport: '390x844', headerHeight: header.height, cards: 5, previewsLoaded: 5, previewHttp200, pdfHttp200, columns, horizontalOverflow: overflow }, null, 2));
  } finally {
    await context.close();
    await browser.close();
    await new Promise(resolve => local.close(resolve));
  }
}

main().catch(error => { console.error(error.message); process.exit(1); });
