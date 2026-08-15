/* Focused acceptance test for the production Career Engine mobile surface. */
'use strict';
const path = require('path');
const { execSync } = require('child_process');

const BASE = 'http://127.0.0.1:4173';
function loadPlaywright() {
  const root = execSync('npm root -g').toString().trim();
  try { return require(path.join(root, 'playwright')); }
  catch { return require('playwright'); }
}

function assert(ok, message, detail = '') {
  if (!ok) throw new Error(`${message}${detail ? `: ${detail}` : ''}`);
}

async function main() {
  const { chromium } = loadPlaywright();
  const browser = await chromium.launch({ executablePath: '/usr/bin/google-chrome', args: ['--no-sandbox', '--disable-dev-shm-usage'] });
  const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', msg => { if (msg.type() === 'error' && !/404|here\.now|unavailable/.test(msg.text())) errors.push(msg.text()); });

  await page.goto(`${BASE}/index.html`, { waitUntil: 'networkidle' });
  await page.waitForTimeout(300);
  assert(errors.length === 0, 'Console/page errors', errors.join(' | '));

  const header = await page.locator('.board-topbar').boundingBox();
  assert(header && header.height <= 52, 'Mobile header is compact', header ? `${header.height}px` : 'missing');
  const headerText = (await page.locator('.board-topbar').innerText()).toLowerCase();
  assert(!/private|owner-controlled|nothing auto-sent|discover|process, review/.test(headerText), 'Header removes operational boilerplate', headerText);
  assert(await page.locator('.scan-status').isVisible(), 'Last scan status is visible');
  const scanFits = await page.locator('#scan-relative').evaluate(el => el.scrollWidth <= el.clientWidth + 1);
  assert(scanFits, 'Last scan timing is not visually cropped');

  const summary = page.locator('#summary .summary-filter');
  assert(await summary.count() === 4, 'Four compact status chips are present after removing Processing', String(await summary.count()));
  assert(await page.locator('.kanban-column[data-stage="inactive"]').count() === 1, 'Closed / inactive Kanban column is present');
  assert(await page.locator('#summary .priority-filter').isVisible(), 'Awaiting Action chip is visible');
  const chipHeight = await page.locator('#summary .summary-filter').first().evaluate(el => el.getBoundingClientRect().height);
  assert(chipHeight <= 32, 'Status chips remain compact', `${chipHeight}px`);
  const firstColumn = await page.locator('.kanban-column').first().boundingBox();
  assert(firstColumn && firstColumn.width <= 330 && firstColumn.width >= 300, 'Mobile column leaves a partial next column visible', firstColumn ? `${firstColumn.width}px` : 'missing');

  assert(await page.locator('#mobile-search-toggle').isVisible(), 'Mobile search is represented by a dedicated icon');
  assert(!(await page.locator('#search-filter').isVisible()), 'Mobile search field stays collapsed by default');
  await page.locator('#mobile-search-toggle').click();
  assert(await page.locator('#search-filter').isVisible(), 'Mobile search field drops down after tapping the icon');
  await page.locator('#search-filter').fill('Parsons');
  await page.locator('#search-filter').fill('');
  await page.keyboard.press('Escape');
  assert(!(await page.locator('#search-filter').isVisible()), 'Mobile search field collapses again');
  assert(await page.locator('.compact-toolbar .tool-menu').count() === 3, 'Filter/sort/display use compact menus');
  assert(await page.locator('.compact-toolbar .tool-menu[open]').count() === 0, 'Compact menus are collapsed by default');
  await page.locator('.compact-toolbar .tool-menu').first().locator('summary').click();
  assert(await page.locator('.compact-toolbar .tool-menu').first().locator('.tool-popover').isVisible(), 'Filter menu expands on click');
  await page.keyboard.press('Escape').catch(() => {});

  const awaiting = page.locator('#summary .priority-filter');
  await awaiting.click();
  await page.waitForTimeout(350);
  const readyLeft = await page.locator('.kanban-column[data-stage="ready_review"]').evaluate(el => el.getBoundingClientRect().left);
  assert(Math.abs(readyLeft) < 35, 'Awaiting Action chip jumps to review column', `${readyLeft}px`);

  // The initial board payload is intentionally slim; resume and cover-letter
  // artifacts are loaded only when a job opens. Exercise the known legacy
  // Qiddiya package that previously showed a blank resume pane.
  const qiddiyaKey = 'tracker-5a531dd6cfca13213694';
  const resumeCard = page.locator(`.role-card[data-role-key="${qiddiyaKey}"]`);
  assert(await resumeCard.count() === 1, 'Qiddiya Design Governance role is available for detail testing');
  await resumeCard.locator('.role-title').click();
  await page.waitForTimeout(450);
  assert(await page.locator('#job-overlay').isVisible(), 'Detail overlay opens');
  const deepLinkedJob = await page.evaluate(() => new URLSearchParams(location.search).get('job'));
  assert(deepLinkedJob === qiddiyaKey, 'Opening detail creates a stable job deep link', String(deepLinkedJob));
  assert(await page.locator('.overlay-close').isVisible(), 'Close button is visible');
  assert(await page.locator('#ov-main-action').isVisible(), 'Primary next action is visible');
  assert(await page.locator('#ov-job-applied').isVisible(), 'Job applied confirmation button is visible in the top CTA bar');

  const closeTop = await page.locator('.overlay-close').evaluate(el => el.getBoundingClientRect().top);
  const actionTop = await page.locator('#ov-main-action').evaluate(el => el.getBoundingClientRect().top);
  assert(closeTop >= 0 && closeTop < 60, 'Close button stays at top', `${closeTop}px`);
  assert(actionTop >= 35 && actionTop < 130, 'Next action stays at top', `${actionTop}px`);

  const viewer = page.locator('#ov-resume-viewer');
  const viewerMetrics = await viewer.evaluate(el => {
    const style = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return { height: rect.height, overflowY: style.overflowY, touchAction: style.touchAction };
  });
  assert(viewerMetrics.height >= 500, 'Resume viewer dominates mobile detail view', JSON.stringify(viewerMetrics));
  assert(['auto', 'scroll'].includes(viewerMetrics.overflowY), 'Resume bounding box is scrollable', JSON.stringify(viewerMetrics));
  const frame = page.locator('#ov-resume-frame');
  assert(await frame.isVisible(), 'Inline resume frame is visible');
  const src = await frame.getAttribute('src');
  assert(Boolean(src && /\.pdf/i.test(src)), 'Inline resume points to selected PDF', String(src));
  assert(/Design_Governance_Manager\.pdf/i.test(src), 'Qiddiya detail falls back to its generated role-specific resume', String(src));
  const coverText = await page.locator('#ov-email-body').inputValue();
  assert(/Dear Hiring Team/i.test(coverText) && /Manager - Design Governance/i.test(coverText), 'Generated Qiddiya cover letter text is visible', coverText.slice(0, 160));
  assert(await page.locator('#ov-cover-pdf-download').isVisible(), 'Qiddiya cover-letter PDF download is visible');

  await page.locator('.overlay-workspace').evaluate(el => { el.scrollTop = el.scrollHeight; });
  await page.waitForTimeout(100);
  assert(await page.locator('.overlay-close').isVisible(), 'Close remains visible after detail scrolling');
  assert(await page.locator('#ov-main-action').isVisible(), 'Next action remains visible after detail scrolling');

  await page.locator('.overlay-close').click();
  await page.waitForTimeout(100);
  assert(!(await page.locator('#job-overlay').isVisible()), 'Detail overlay closes');
  const closedJobParam = await page.evaluate(() => new URLSearchParams(location.search).get('job'));
  assert(closedJobParam === null, 'Closing detail restores the board URL', String(closedJobParam));

  const bodyOverflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  assert(bodyOverflow <= 1, 'No page-level horizontal overflow', `${bodyOverflow}px`);

  console.log(JSON.stringify({ valid: true, viewport: '390x844', headerHeight: header.height, chipHeight, readyColumnLeft: readyLeft, viewer: viewerMetrics, resumeSrc: src }, null, 2));
  await context.close();
  await browser.close();
}

main().catch(error => { console.error(error.message); process.exit(1); });
