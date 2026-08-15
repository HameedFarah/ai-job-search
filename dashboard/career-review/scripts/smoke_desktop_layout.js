/* Desktop visual-structure smoke for the screenshot-guided Career Engine UI. */
'use strict';
const path = require('path');
const { execSync } = require('child_process');
const BASE = 'http://127.0.0.1:4173';
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
  await page.goto(`${BASE}/index.html`, { waitUntil: 'networkidle' });

  assert(await page.locator('.summary-filter').count() === 4, 'Desktop summary must show four stat cards after removing Processing');
  assert(await page.locator('.compact-brand .subtitle').isVisible(), 'Desktop header includes the workflow subtitle');
  const searchBox = await page.locator('.desktop-search-control').boundingBox();
  assert(searchBox && searchBox.width >= 280, 'Desktop search is a wide direct input', JSON.stringify(searchBox));
  const toolbarLabels = await page.locator('.compact-toolbar .toolbar-label').allTextContents();
  assert(toolbarLabels.includes('Filters') && toolbarLabels.includes('Sort') && toolbarLabels.includes('Display'), 'Desktop toolbar exposes named controls', toolbarLabels.join(', '));
  const activeBox = await page.locator('.kanban-column[data-stage="found"]').boundingBox();
  const inactiveBox = await page.locator('.inactive-column.is-collapsed').boundingBox();
  const firstCardBox = await page.locator('.role-card').first().boundingBox();
  assert(activeBox && activeBox.width > 250, 'Active lane should remain full width', JSON.stringify(activeBox));
  assert(inactiveBox && inactiveBox.width <= 60, 'Inactive lane should collapse to a narrow rail', JSON.stringify(inactiveBox));
  assert(firstCardBox && firstCardBox.height < 260, 'Job cards stay dense enough to fit more roles in view', JSON.stringify(firstCardBox));
  assert(await page.locator('.card-stage-select').first().isVisible(), 'Card status dropdown is always visible on desktop');
  assert(await page.locator('.card-stage-menu').count() === 0, 'Legacy card hamburger stage menu is removed');
  assert(await page.locator('.confirm-submission').count() > 0, 'Submission confirmation is available as a compact secondary icon action');

  const qiddiyaKey = 'tracker-5a531dd6cfca13213694';
  const qiddiyaCard = page.locator(`.role-card[data-role-key="${qiddiyaKey}"]`);
  assert(await qiddiyaCard.count() === 1, 'Qiddiya Design Governance role is present');
  await qiddiyaCard.click();
  await page.locator('#job-overlay:not([hidden])').waitFor();
  await page.waitForTimeout(350);
  const overlayRadius = await page.locator('.overlay-panel').evaluate(node => getComputedStyle(node).borderRadius);
  const actionBackground = await page.locator('.overlay-action-bar').evaluate(node => getComputedStyle(node).backgroundImage);
  const resumeBox = await page.locator('.resume-workspace').boundingBox();
  const assistantBox = await page.locator('.detail-utility > .overlay-assistant-top').boundingBox();
  assert(parseFloat(overlayRadius) >= 14, 'Detail modal should use the rounded screenshot-guided shell', overlayRadius);
  assert(/linear-gradient/i.test(actionBackground), 'Next-step banner should use the blue gradient treatment', actionBackground);
  assert(await page.locator('#ov-job-applied').isVisible(), 'Top CTA bar includes the smaller Job applied button');
  const ctaStyles = await page.locator('#ov-main-action').evaluate(node => {
    const style = getComputedStyle(node);
    return { color: style.color, background: style.backgroundColor, border: style.borderColor };
  });
  assert(ctaStyles.background !== 'rgb(255, 255, 255)' || ctaStyles.color !== 'rgb(255, 255, 255)', 'Primary job-detail CTA is not white-on-white', JSON.stringify(ctaStyles));
  const appliedStyles = await page.locator('#ov-job-applied').evaluate(node => {
    const style = getComputedStyle(node);
    return { color: style.color, background: style.backgroundColor, border: style.borderColor };
  });
  assert(appliedStyles.background !== appliedStyles.color, 'Job applied CTA has visible contrast', JSON.stringify(appliedStyles));
  assert(await page.locator('#ov-cover-copy').count() === 1, 'Cover text has a one-click copy control');
  assert(await page.locator('#ov-cover-pdf-download').isVisible(), 'Cover section exposes direct PDF download when available');
  const resumeSrc = await page.locator('#ov-resume-frame').getAttribute('src');
  assert(/Design_Governance_Manager\.pdf/i.test(resumeSrc || ''), 'Qiddiya generated resume is displayed', String(resumeSrc));
  const coverText = await page.locator('#ov-email-body').inputValue();
  assert(/Dear Hiring Team/i.test(coverText) && /Manager - Design Governance/i.test(coverText), 'Qiddiya generated cover letter text is displayed', coverText.slice(0, 160));
  assert(resumeBox && assistantBox && assistantBox.x > resumeBox.x + resumeBox.width, 'Assistant should sit to the right of the resume on desktop');
  assert(Math.abs(assistantBox.y - resumeBox.y) < 16, 'Assistant should start at the top of the right utility column', `${assistantBox.y} vs ${resumeBox.y}`);

  console.log(JSON.stringify({
    valid: true,
    summaryCards: await page.locator('.summary-filter').count(),
    activeLaneWidth: Math.round(activeBox.width),
    inactiveRailWidth: Math.round(inactiveBox.width),
    overlayRadius,
    resumeWidth: Math.round(resumeBox.width),
    assistantWidth: Math.round(assistantBox.width)
  }, null, 2));
  await context.close();
  await browser.close();
}
main().catch(error => { console.error(error.message); process.exit(1); });
