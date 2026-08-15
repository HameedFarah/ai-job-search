/* Acceptance smoke for lifecycle controls and the job assistant form. */
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
  const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const page = await context.newPage();
  await page.goto(`${BASE}/index.html`, { waitUntil: 'networkidle' });

  const columns = await page.locator('.column-header h2').allTextContents();
  assert(!columns.some(label => /approved/i.test(label)), 'Approved lifecycle column must be removed', columns.join(', '));
  assert(columns.includes('Needs review'), 'Needs review column is present');
  assert(columns.indexOf('Needs review') === columns.indexOf('Found jobs') + 1, 'Needs review follows Found jobs');
  assert(columns.includes('Ready for review'), 'Ready for review column is present');
  assert(await page.locator('#refresh-board').count() === 1, 'Hermes refresh button is present');
  assert(await page.locator('#process-score').inputValue() === '70', 'Process score defaults to 70');
  assert(await page.locator('#process-jobs').count() === 1, 'Process jobs button is present');
  assert(await page.locator('.inactive-column.is-collapsed').count() === 1, 'Closed / inactive lane starts collapsed');
  await page.locator('.inactive-column.is-collapsed').click({ position: { x: 18, y: 80 } });
  assert(await page.locator('.inactive-column.is-collapsed').count() === 0, 'Clicking the collapsed inactive rail expands it');
  await page.locator('.inactive-column .column-collapse').click();
  assert(await page.locator('.inactive-column.is-collapsed').count() === 1, 'Closed / inactive lane can collapse again');

  const batchRoleKey = await page.locator('.role-card').first().getAttribute('data-role-key');
  await page.evaluate(roleKey => {
    state.aiRequests.push({
      id: 'fake-batch-progress',
      role_key: GLOBAL_ROLE_KEY,
      request_type: 'process_jobs',
      min_score: 70,
      state: 'processing',
      answer: JSON.stringify({
        kind: 'batch_progress', phase: 'processing', threshold: 70,
        total: 5, done: 2, remaining: 3, succeeded: 2, failed: 0,
        current_role_key: roleKey, current_role: 'Senior Design Manager', current_company: 'Example Co',
        completed_role_keys: [], eta_seconds: 180
      }),
      createdAt: new Date().toISOString()
    });
    renderGlobalOperationStatus();
  }, batchRoleKey);
  const progressText = await page.locator('#operation-status').innerText();
  assert(progressText.includes('2 done') && progressText.includes('3 remaining') && progressText.includes('(5 total)') && progressText.includes('ETA ~3 min'), 'Batch progress shows done, remaining, total and ETA', progressText);
  assert(await page.locator('.kanban-column[data-stage="processing"]').count() === 0, 'Processing workflow lane is removed');
  assert(await page.locator(`.role-card[data-role-key="${batchRoleKey}"]`).count() === 1, 'Current batch job remains visible in its canonical workflow lane');
  await page.evaluate(() => {
    state.aiRequests = state.aiRequests.filter(item => item.id !== 'fake-batch-progress');
    state.batchProgress = null;
    state.batchStageOverrides.clear();
    renderBoard();
    renderGlobalOperationStatus();
  });

  await page.locator('.role-card').first().click();
  await page.locator('#job-overlay:not([hidden])').waitFor();
  assert(await page.locator('.detail-utility > .overlay-assistant-top').count() === 1, 'Assistant is the first utility in the job detail workspace');
  const assistantIsFirstUtility = await page.evaluate(() => {
    const utility = document.querySelector('.detail-utility');
    const assistant = document.querySelector('.detail-utility > .overlay-assistant-top');
    return Boolean(utility && assistant && utility.firstElementChild === assistant);
  });
  assert(assistantIsFirstUtility, 'Assistant stays at the top of the right-side application utilities');

  await page.evaluate(() => {
    window.__careerSmokeCalls = [];
    createRecord = async (collection, payload) => {
      window.__careerSmokeCalls.push({ collection, payload });
      return { id: `fake-${collection}-${window.__careerSmokeCalls.length}`, data: payload, createdAt: new Date().toISOString(), updatedAt: new Date().toISOString() };
    };
  });
  await page.locator('[data-overlay-assistant-action="cover_letter"]').click();
  await page.locator('#ov-ai-prompt').fill('give me the cover letter');
  await page.locator('#ov-ai-form button[type="submit"]').click();
  await page.waitForTimeout(150);
  const status = await page.locator('#ov-ai-status').innerText();
  assert(status.includes('Queued'), 'Assistant form queues without ReferenceError', status);
  const calls = await page.evaluate(() => window.__careerSmokeCalls);
  assert(calls.some(call => call.collection === 'ai_requests' && call.payload.request_type === 'cover_letter'), 'Assistant request uses selected quick action', JSON.stringify(calls));
  assert(calls.some(call => call.collection === 'history' && /Cover Letter/i.test(call.payload.note)), 'Assistant history write no longer references removed aiSelect', JSON.stringify(calls));

  console.log(JSON.stringify({ valid: true, columns, processDefault: 70, assistantStatus: status }, null, 2));
  await context.close();
  await browser.close();
}
main().catch(error => { console.error(error.message); process.exit(1); });
