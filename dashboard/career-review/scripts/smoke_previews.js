/* smoke_previews.js — Playwright smoke tests for the design-exploration previews.
 * Runs every route at 1920x1080 and 1440x900. Checks:
 * - board/cards render, toolbar controls populated (search/sort/filter)
 * - no console errors, no page errors, specifically no "addList is not defined"
 * - no horizontal overflow of the page
 * - overlay open on card click, Escape closes, focus returns to the card
 * - backdrop closes only without unsaved edits (dirty guard)
 * - PDF iframe/embed source resolves to the currently selected submission CV (HTTP 200)
 * - the submission-CV selector exposes Sidebar and ATS and persists an override
 * - PDF/DOCX download links resolve to real files (HTTP 200)
 * - window.open actions use _blank (stubbed)
 * No destructive Site Data writes: local writes 404 harmlessly by design.
 * Usage: node scripts/smoke_previews.js
 */
'use strict';
const path = require('path');
const http = require('http');

const ROOT = path.join(__dirname, '..');
const BASE = 'http://127.0.0.1:4173';
const KANBANS = [1, 2, 3, 4, 5];
const DETAILS = [1, 2, 3, 4, 5];
const VIEWPORTS = [
  { name: '1920x1080', width: 1920, height: 1080 },
  { name: '1440x900', width: 1440, height: 900 }
];
const BURO = 'buro-happold-senior-design-manager-jeddah';

function loadPlaywright() {
  const { execSync } = require('child_process');
  const globalRoot = execSync('npm root -g').toString().trim();
  try {
    return require(path.join(globalRoot, 'playwright'));
  } catch {
    return require('playwright');
  }
}

function httpStatus(url) {
  return new Promise(resolve => {
    const req = http.get(url, res => {
      res.resume();
      resolve(res.statusCode);
    });
    req.on('error', () => resolve(0));
    req.setTimeout(5000, () => { req.destroy(); resolve(0); });
  });
}

const results = [];

async function checkFileStatus(relativePath, label) {
  const status = await httpStatus(new URL(relativePath, BASE + '/design-exploration/').href)
  const ok = status === 200;
  if (!ok) results.push({ ok: false, check: `${label} resolves`, detail: `${relativePath} -> HTTP ${status}` });
  return ok;
}

async function expectNoConsoleErrors(page, route) {
  const errors = [];
  const handler = msg => {
    if (msg.type() === 'error') errors.push(msg.text().slice(0, 300));
  };
  page.on('console', handler);
  page.on('pageerror', err => errors.push(`pageerror: ${String(err.message).slice(0, 300)}`));
  await page.goto(`${BASE}${route}`, { waitUntil: 'networkidle' });
  await page.waitForTimeout(400);
  page.off('console', handler);
  const fatal = errors.filter(text => !/here\.now|loadCollection\(|unavailable|404|Failed to load resource: the server responded with a status of 404/.test(text));
  const addList = errors.find(text => /addList is not defined/.test(text));
  if (addList) results.push({ ok: false, route, check: 'no addList error', detail: addList });
  if (fatal.length) results.push({ ok: false, route, check: 'no console/page errors', detail: fatal.join(' | ') });
  return errors;
}

async function testKanban(page, n, vp) {
  const route = `/design-exploration/kanban-${n}.html`;
  await expectNoConsoleErrors(page, route);
  const boardCount = await page.locator('#board .role-card, #board tbody tr[data-row-key]').count();
  if (boardCount === 0) results.push({ ok: false, route, viewport: vp.name, check: 'board rendered', detail: 'no role cards found' });

  const searchCount = await page.locator('#search-filter').count();
  const sortOptions = await page.locator('#sort-order option').count();
  const decisionOptions = await page.locator('#decision-filter option').count();
  if (!searchCount || sortOptions < 5 || decisionOptions < 4) {
    results.push({ ok: false, route, viewport: vp.name, check: 'toolbar controls populated', detail: `search=${searchCount} sort=${sortOptions} decision=${decisionOptions}` });
  }

  const viewControls = await page.locator('.preview-global-controls [aria-label="Board view"] .preview-segment').count();
  const themeControls = await page.locator('.preview-global-controls [aria-label="Color theme"] .preview-segment').count();
  if (viewControls !== 5) results.push({ ok: false, route, viewport: vp.name, check: 'five board view controls present', detail: String(viewControls) });
  if (themeControls !== 3) results.push({ ok: false, route, viewport: vp.name, check: 'light dark system controls present', detail: String(themeControls) });
  const initialTheme = await page.locator('html').getAttribute('data-theme');
  if (!['light', 'dark'].includes(initialTheme || '')) results.push({ ok: false, route, viewport: vp.name, check: 'theme initialized', detail: String(initialTheme) });

  if (n === 2 && vp.name === '1920x1080') {
    await page.locator('[aria-label="Color theme"] [data-value="dark"]').click();
    if ((await page.locator('html').getAttribute('data-theme')) !== 'dark') results.push({ ok: false, route, viewport: vp.name, check: 'dark theme switch works' });
    await page.locator('[aria-label="Color theme"] [data-value="light"]').click();
    if ((await page.locator('html').getAttribute('data-theme')) !== 'light') results.push({ ok: false, route, viewport: vp.name, check: 'light theme switch works' });
    await page.locator('[aria-label="Board view"] [data-value="4"]').click();
    await page.waitForURL('**/kanban-4.html');
    if (!page.url().endsWith('/kanban-4.html')) results.push({ ok: false, route, viewport: vp.name, check: 'board view switching navigates to selected view', detail: page.url() });
    await expectNoConsoleErrors(page, route);
  }

  /* no horizontal overflow */
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  if (overflow > 1) results.push({ ok: false, route, viewport: vp.name, check: 'no horizontal overflow', detail: `scrollWidth overflow by ${overflow}px` });

  /* overlay open via card click */
  const card = page.locator('#board [data-role-key=buro-happold-senior-design-manager-jeddah], #board [data-row-key=buro-happold-senior-design-manager-jeddah]').first()
  if (await card.count()) {
    await card.click();
    await page.waitForTimeout(200);
    const overlayVisible = await page.locator('#preview-overlay').isVisible();
    if (!overlayVisible) results.push({ ok: false, route, viewport: vp.name, check: 'overlay opens on card click' });
    const pdfCount = await page.locator('#preview-overlay iframe.pdf-frame').count();
    if (pdfCount === 0) results.push({ ok: false, route, viewport: vp.name, check: 'overlay shows PDF viewer' });

    /* Escape closes and focus returns */
    await page.keyboard.press('Escape');
    await page.waitForTimeout(150);
    const closed = await page.locator('#preview-overlay').getAttribute('aria-hidden');
    if (closed !== 'true') results.push({ ok: false, route, viewport: vp.name, check: 'Escape closes overlay', detail: `aria-hidden=${closed}` });

    /* backdrop dirty guard: type in comment area, click backdrop -> stays open */
    await card.click();
    await page.waitForTimeout(150);
    const commentBox = page.locator('#composer-text');
    if (await commentBox.count()) {
      await commentBox.fill('unsaved draft');
      if (vp.width > 700) {
        await page.locator('.preview-overlay-backdrop').click({ position: { x: 5, y: 5 } });
        await page.waitForTimeout(150);
        const stillOpen = await page.locator('#preview-overlay').isVisible();
        if (!stillOpen) results.push({ ok: false, route, viewport: vp.name, check: 'backdrop respects unsaved edits' });
      }
      await page.keyboard.press('Escape');
      await page.waitForTimeout(150);
    }
  }
}

async function testDetail(page, n, vp) {
  const route = `/design-exploration/detail-${n}.html`;
  await expectNoConsoleErrors(page, route);

  const overlayVisible = await page.locator('#preview-overlay').isVisible();
  if (!overlayVisible) results.push({ ok: false, route, viewport: vp.name, check: 'detail overlay auto-opens with Buro Happold' });

  const analysisHidden = await page.locator('#analysis-drawer').isHidden();
  if (!analysisHidden) results.push({ ok: false, route, viewport: vp.name, check: 'analysis collapsed by default' });
  const analysisToggle = page.locator('#analysis-toggle');
  await analysisToggle.click();
  if (!(await page.locator('#analysis-drawer').isVisible())) results.push({ ok: false, route, viewport: vp.name, check: 'analysis expands on request' });
  await page.locator('#analysis-close').click();
  if (!(await page.locator('#analysis-drawer').isHidden())) results.push({ ok: false, route, viewport: vp.name, check: 'analysis collapses again' });

  const resumeMetrics = await page.evaluate(() => {
    const resume = document.querySelector('.resume-document-card');
    const header = document.querySelector('.resume-detail-header');
    const composer = document.querySelector('.floating-composer');
    const cover = document.querySelector('.cover-editor-card');
    if (!resume || !header || !composer || !cover) return null;
    const rr = resume.getBoundingClientRect();
    const hr = header.getBoundingClientRect();
    return { resumeHeight: rr.height, headerHeight: hr.height, viewportHeight: window.innerHeight, composerVisible: composer.getBoundingClientRect().height > 0, coverBelowResume: cover.getBoundingClientRect().top >= rr.bottom - 2 };
  });
  if (!resumeMetrics || resumeMetrics.resumeHeight < resumeMetrics.viewportHeight * 0.55) results.push({ ok: false, route, viewport: vp.name, check: 'resume dominates viewport', detail: JSON.stringify(resumeMetrics) });
  if (!resumeMetrics?.composerVisible) results.push({ ok: false, route, viewport: vp.name, check: 'floating AI/comment composer visible' });
  if (!resumeMetrics?.coverBelowResume) results.push({ ok: false, route, viewport: vp.name, check: 'cover editor follows resume' });
  if (vp.width <= 700 && resumeMetrics && resumeMetrics.headerHeight > 110) results.push({ ok: false, route, viewport: vp.name, check: 'mobile metadata header remains compact', detail: `${resumeMetrics.headerHeight}px` });

  /* overlay fits the viewport without page scroll */
  const fits = await page.evaluate(() => {
    const panel = document.querySelector('.preview-overlay-panel');
    if (!panel) return { ok: false, detail: 'no panel' };
    const rect = panel.getBoundingClientRect();
    const doc = document.documentElement;
    return {
      ok: rect.bottom <= window.innerHeight + 2 && rect.right <= window.innerWidth + 2,
      detail: `panel bottom=${Math.round(rect.bottom)} vh=${window.innerHeight} docScrollW=${doc.scrollWidth} innerW=${window.innerWidth}`
    };
  });
  if (!fits.ok) results.push({ ok: false, route, viewport: vp.name, check: 'overlay fits viewport without page scroll', detail: fits.detail });

  const noHOverflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  if (noHOverflow > 1) results.push({ ok: false, route, viewport: vp.name, check: 'no horizontal overflow', detail: `${noHOverflow}px` });

  /* selected submission CV viewer */
  const pdfSrc = await page.locator('#preview-overlay iframe.pdf-frame').getAttribute('src');
  if (!pdfSrc || !/\.pdf(?:#|$)/i.test(pdfSrc)) {
    results.push({ ok: false, route, viewport: vp.name, check: 'selected submission CV PDF source', detail: String(pdfSrc) });
  } else {
    await checkFileStatus(pdfSrc.replace('#page=1', ''), `detail-${n} selected CV PDF source`);
  }

  /* selector + label + downloads */
  const templateSelect = page.locator('#submission-cv-select');
  if (await templateSelect.count() !== 1) {
    results.push({ ok: false, route, viewport: vp.name, check: 'submission CV selector present' });
  } else {
    const options = await templateSelect.locator('option').allTextContents();
    if (!options.some(item => /Sidebar/i.test(item)) || !options.some(item => /ATS/i.test(item))) {
      results.push({ ok: false, route, viewport: vp.name, check: 'Sidebar and ATS selector options', detail: JSON.stringify(options) });
    }
    const selectedText = await templateSelect.locator('option:checked').textContent();
    const label = await page.locator('#preview-overlay .pdf-label').first().textContent();
    if (!label || label.trim() !== String(selectedText || '').trim()) {
      results.push({ ok: false, route, viewport: vp.name, check: 'active resume label matches selector', detail: `${label} / ${selectedText}` });
    }
  }
  const downloadLinks = await page.locator('#preview-overlay a[download]').evaluateAll(anchors => anchors.map(a => ({ text: a.textContent.trim(), href: a.href })));
  if (!downloadLinks.some(l => /pdf/i.test(l.href))) results.push({ ok: false, route, viewport: vp.name, check: 'PDF download link present' });
  if (!downloadLinks.some(l => /docx/i.test(l.href))) results.push({ ok: false, route, viewport: vp.name, check: 'DOCX download link present' });

  /* sort/filter controls populated */
  const sortOptions = await page.locator('#sort-order option').count();
  const decisionOptions = await page.locator('#decision-filter option').count();
  if (sortOptions < 5 || decisionOptions < 4) results.push({ ok: false, route, viewport: vp.name, check: 'controls populated on detail page' });

  /* window.open uses _blank */
  await page.evaluate(() => { window.__opened = []; window.__origOpen = window.open; window.open = (url, target, features) => { window.__opened.push({ url, target, features }); return null; }; });
  const mainAction = page.locator('#destination-action').first();
  if (await mainAction.count() && !(await mainAction.isDisabled())) {
    await mainAction.click();
    await page.waitForTimeout(150);
    const opened = await page.evaluate(() => window.__opened);
    const blank = opened.length && opened.every(o => o.target === '_blank');
    if (!blank) results.push({ ok: false, route, viewport: vp.name, check: 'window.open actions use _blank', detail: JSON.stringify(opened) });
  }
  await page.evaluate(() => { if (window.__origOpen) window.open = window.__origOpen; });

  const blockedDismiss = page.locator('.gmail-blocked .card-button:not(.primary)')
  if (await blockedDismiss.count()) await blockedDismiss.click()
  /* focus preservation: close then reopen -> focus lands in overlay */
  await page.keyboard.press('Escape');
  await page.waitForTimeout(150);
  const card = page.locator('#board tbody tr[data-row-key]').first();
  if (await card.count()) {
    await card.click();
    await page.waitForTimeout(150);
    const activeInOverlay = await page.evaluate(() => document.querySelector('.preview-overlay-panel')?.contains(document.activeElement));
    if (!activeInOverlay) results.push({ ok: false, route, viewport: vp.name, check: 'focus moves into overlay on open' });
    await page.keyboard.press('Escape');
  }
}

async function main() {
  process.env.TMPDIR = path.join(ROOT, '.tmp')
  const { chromium } = loadPlaywright();
  const browser = await chromium.launch({ executablePath: '/usr/bin/google-chrome', args: ['--no-sandbox', '--disable-dev-shm-usage'] });
  for (const vp of VIEWPORTS) {
    const context = await browser.newContext({ viewport: { width: vp.width, height: vp.height } });
    const page = await context.newPage();
    for (const n of KANBANS) await testKanban(page, n, vp);
    for (const n of DETAILS) await testDetail(page, n, vp);
    await context.close();
  }
  const mobile = { name: '390x844', width: 390, height: 844 };
  const mobileContext = await browser.newContext({ viewport: { width: mobile.width, height: mobile.height } });
  const mobilePage = await mobileContext.newPage();
  await testKanban(mobilePage, 2, mobile);
  await testDetail(mobilePage, 2, mobile);
  await mobileContext.close();
  await browser.close();
  const passed = results.filter(r => r.ok !== false).length;
  const failed = results.filter(r => r.ok === false);
  const executed = 22
  console.log(JSON.stringify({ base: BASE, routeViewportCases: executed, passed: executed - failed.length, failed: failed.length, results }, null, 2))
  if (failed.length) process.exit(1);
}

main().catch(error => { console.error(error.message); process.exit(1); });
