/* Focused browser smoke test for application evidence semantics.
 * It mocks here.now Site Data locally, never writes production data, and proves:
 * 1) opening a portal appends portal_opened only;
 * 2) explicit owner confirmation appends application_submitted separately;
 * 3) external destinations use _blank;
 * 4) the full tracker snapshot is visible in the generated dashboard data. */
'use strict';

const path = require('path');
const { execSync } = require('child_process');

const BASE = 'http://127.0.0.1:4173';

function loadPlaywright() {
  const globalRoot = execSync('npm root -g').toString().trim();
  try { return require(path.join(globalRoot, 'playwright')); }
  catch { return require('playwright'); }
}

async function main() {
  const { chromium } = loadPlaywright();
  const browser = await chromium.launch({ executablePath: '/usr/bin/google-chrome', args: ['--no-sandbox', '--disable-dev-shm-usage'] });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  const writes = [];
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  page.on('console', message => {
    if (message.type() === 'error') pageErrors.push(message.text());
  });

  await page.route('**/.herenow/data/**', async route => {
    const request = route.request();
    if (request.method() === 'GET') {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ records: [] }) });
      return;
    }
    const body = request.postDataJSON ? request.postDataJSON() : JSON.parse(request.postData() || '{}');
    writes.push({ url: request.url(), method: request.method(), body });
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ record: { id: `test-${writes.length}`, data: body, createdAt: new Date().toISOString(), updatedAt: new Date().toISOString() } })
    });
  });

  await page.addInitScript(() => {
    window.__opened = [];
    window.open = (url, target, features) => {
      window.__opened.push({ url, target, features });
      return {};
    };
    window.confirm = () => true;
    window.prompt = () => 'TEST-CONFIRMATION-REF';
  });

  await page.goto(`${BASE}/detail.html?job=buro-happold-senior-design-manager-jeddah`, { waitUntil: 'networkidle' });
  await page.locator('#d-actions .card-button.primary').first().click();
  await page.waitForTimeout(150);

  const opened = await page.evaluate(() => window.__opened);
  if (!opened.length || opened.some(item => item.target !== '_blank')) throw new Error(`Portal did not open in _blank: ${JSON.stringify(opened)}`);
  const portalOpen = writes.find(item => item.body?.event === 'portal_opened');
  if (!portalOpen) throw new Error(`portal_opened evidence missing: ${JSON.stringify(writes)}`);
  if (portalOpen.body.evidence_level !== 'portal_open_only_not_submission') throw new Error('Portal-open evidence level is ambiguous');
  if (writes.some(item => item.body?.event === 'application_submitted')) throw new Error('Opening portal incorrectly marked submission');

  const confirmed = await page.evaluate(async () => confirmApplicationSubmitted(state.role, 'smoke_test'));
  if (!confirmed) throw new Error('Explicit owner confirmation returned false');
  const submitted = writes.find(item => item.body?.event === 'application_submitted');
  if (!submitted) throw new Error(`application_submitted evidence missing: ${JSON.stringify(writes)}`);
  if (submitted.body.evidence_type !== 'explicit_owner_confirmation') throw new Error('Submission evidence type is not explicit owner confirmation');
  if (submitted.body.confirmation_reference !== 'TEST-CONFIRMATION-REF') throw new Error('Confirmation reference was not preserved');
  if (!submitted.body.document_pdf || !submitted.body.document_sha256) throw new Error('Selected document evidence was not recorded');
  const submittedSnapshot = JSON.parse(submitted.body.note || '{}');
  if (!submittedSnapshot.job_id || !submittedSnapshot.company || !submittedSnapshot.role) throw new Error('Submission snapshot is missing job identity');
  if (!submittedSnapshot.document_sha256 || !submittedSnapshot.document_pdf) throw new Error('Submission snapshot is missing exact resume archive keys');
  if ('document_text' in submittedSnapshot || 'cover_letter_text' in submittedSnapshot) throw new Error('Browser history payload must stay compact; exact text is archived by the Career Engine worker');
  if (!submittedSnapshot.cover_letter_pdf || !submittedSnapshot.cover_letter_sha256) throw new Error('Submission snapshot is missing the cover-letter artifact evidence');

  const snapshot = await (await page.request.get(`${BASE}/data/jobs.json`)).json();
  if (!Number.isInteger(snapshot.tracker_records) || snapshot.tracker_records < 59 || snapshot.total_roles < snapshot.tracker_records) {
    throw new Error(`Full tracker snapshot missing: tracker=${snapshot.tracker_records} total=${snapshot.total_roles}`);
  }
  await page.goto(`${BASE}/`, { waitUntil: 'networkidle' });
  const renderedCards = await page.locator('#board .role-card').count();
  if (renderedCards !== snapshot.total_roles) {
    throw new Error(`Dashboard rendered ${renderedCards} cards for ${snapshot.total_roles} roles; page errors: ${JSON.stringify(pageErrors)}`);
  }

  // Permanent submission provenance regression: the historical Mace submission
  // is archived by exact SHA and remains readable even after later CV regeneration.
  await page.goto(`${BASE}/?job=tracker-92a74dc737156c7e41ea`, { waitUntil: 'networkidle' });
  if (!(await page.locator('#ov-submission-record-section').isVisible())) throw new Error('Archived submitted-package section is not visible');
  await page.locator('#ov-submission-record-section').evaluate(node => { node.open = true; });
  const submittedText = await page.locator('.submission-text-snapshot').inputValue();
  if (submittedText.length < 1000 || !submittedText.includes('Infrastructure Design Management')) throw new Error('Exact submitted resume text snapshot is missing');
  const submittedPanelText = await page.locator('#ov-submission-record').innerText();
  if (!submittedPanelText.includes('a67d88a33bdeb9c4fee33f566d8fb5eb7cb830814340b5525f187d0bfec48580')) throw new Error('Submitted resume SHA is not exposed');
  if (!(await page.locator('#ov-submission-record a').filter({ hasText: 'Submitted CV PDF' }).isVisible())) throw new Error('Archived submitted resume PDF is not downloadable');
  await page.evaluate(() => closeOverlay());

  // Mobile-detail regression: the selected ATS resume must have a usable PDF
  // preview plus explicit PDF/DOCX actions, and icon menus must close outside.
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`${BASE}/?job=tracker-fa42cc413b1abb52bf55`, { waitUntil: 'networkidle' });
  const resumeSrc = await page.locator('#ov-resume-frame').getAttribute('src');
  if (!resumeSrc || !/Senior_Design_Manager_ATS\.pdf/i.test(resumeSrc)) {
    throw new Error(`Selected ATS PDF preview missing: ${resumeSrc}`);
  }
  for (const selector of ['#ov-resume-open', '#ov-resume-download', '#ov-resume-docx']) {
    if (!(await page.locator(selector).isVisible())) throw new Error(`Resume action is not visible: ${selector}`);
  }
  await page.locator('.detail-tool-menu > summary').click();
  if (!(await page.locator('.detail-tool-menu').evaluate(node => node.open))) throw new Error('Detail menu did not open');
  await page.locator('.overlay-title-block').click();
  if (await page.locator('.detail-tool-menu').evaluate(node => node.open)) throw new Error('Detail menu did not close on outside click');
  await page.evaluate(() => closeOverlay());

  const filterMenu = page.locator('[data-direct-control="decision-filter"]').locator('xpath=ancestor::details[1]');
  await filterMenu.locator('summary').click();
  if (!(await filterMenu.evaluate(node => node.open))) throw new Error('Main filter menu did not open');
  if (!(await page.locator('[data-direct-control="decision-filter"] .direct-menu-item').first().isVisible())) {
    throw new Error('Direct filter options are not visible');
  }
  await page.locator('[data-direct-control="decision-filter"] .direct-menu-item[data-value="pursue"]').click();
  if (await filterMenu.evaluate(node => node.open)) throw new Error('Main filter menu did not close after direct selection');

  const firstStageSelect = page.locator('.card-stage-select').first();
  const expectedStageCount = await page.evaluate(() => STAGES.length);
  if (!(await firstStageSelect.isVisible())) throw new Error('Always-visible card status dropdown is missing');
  if ((await firstStageSelect.locator('option').count()) !== expectedStageCount) throw new Error(`Card status dropdown does not expose all ${expectedStageCount} stages`);
  if ((await page.locator('.card-stage-menu').count()) !== 0) throw new Error('Legacy hamburger stage menu is still present');

  await browser.close();
  console.log(JSON.stringify({
    result: 'PASS',
    portal_open_event: true,
    submission_confirmation_event: true,
    external_target_blank: true,
    tracker_records: snapshot.tracker_records,
    total_roles: snapshot.total_roles,
    selected_resume_pdf_preview: true,
    resume_view_download_actions: true,
    direct_icon_menus: true,
    outside_click_close: true,
    production_writes: 0
  }, null, 2));
}

main().catch(error => { console.error(error.stack || error.message); process.exit(1); });
