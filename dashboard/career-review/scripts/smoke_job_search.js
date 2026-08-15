/* Focused acceptance test for Career Engine job/company search. */
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
  await page.goto(`${BASE}/index.html`, { waitUntil: 'networkidle' });
  const initial = await page.locator('.role-card').count();
  assert(initial > 1, 'Need multiple roles to exercise search', String(initial));

  const search = page.locator('#search-filter');
  assert(await search.getAttribute('placeholder') === 'Search jobs, roles, or companies…', 'Expected search placeholder');
  assert(!(await search.isVisible()), 'Mobile search is collapsed before the search icon is pressed');
  await page.locator('#mobile-search-toggle').click();
  assert(await search.isVisible(), 'Mobile search opens from the search icon');

  const firstCompany = (await page.locator('.role-card .role-company').first().innerText()).trim();
  assert(Boolean(firstCompany), 'First role has a company');
  await search.fill(firstCompany);
  await page.waitForTimeout(50);
  const companyMatches = page.locator('.role-card');
  const companyCount = await companyMatches.count();
  assert(companyCount > 0 && companyCount <= initial, 'Company search returns a bounded non-empty result', `${companyCount}/${initial}`);
  for (let i = 0; i < companyCount; i += 1) {
    const text = (await companyMatches.nth(i).innerText()).toLowerCase();
    assert(text.includes(firstCompany.toLowerCase()), 'Company search leaked a non-matching role', text.slice(0, 120));
  }

  await search.fill('');
  await page.waitForTimeout(50);
  const restored = await page.locator('.role-card').count();
  assert(restored >= initial, 'Clearing search restores the board without losing initially visible roles', `${restored}/${initial}`);

  const firstTitle = (await page.locator('.role-card .role-title').first().innerText()).trim();
  await search.fill(firstTitle.toUpperCase());
  await page.waitForTimeout(50);
  const titleMatches = page.locator('.role-card');
  const titleCount = await titleMatches.count();
  assert(titleCount > 0, 'Title search returns at least one role');
  for (let i = 0; i < titleCount; i += 1) {
    const text = (await titleMatches.nth(i).innerText()).toLowerCase();
    assert(text.includes(firstTitle.toLowerCase()), 'Title search is not case-insensitive or leaked a non-match', text.slice(0, 120));
  }

  console.log(JSON.stringify({ valid: true, initialRoles: initial, companyQuery: firstCompany, companyMatches: companyCount, titleQuery: firstTitle, titleMatches: titleCount }, null, 2));
  await context.close();
  await browser.close();
}
main().catch(error => { console.error(error.message); process.exit(1); });
