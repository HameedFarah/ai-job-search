/* test_site_data.js — owner-API integration test for the career-review Site Data collections.
 * Creates, patches, reads and deletes a marked test record in every collection.
 * Never prints the credential; leaves no records behind. Exits non-zero on any failure.
 */
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const API = 'https://here.now/api/v1';
const slug = JSON.parse(fs.readFileSync(path.join(ROOT, '.deploy.json'), 'utf8')).slug || 'gilded-timber-xfj7';

function loadApiKey() {
  let raw = process.env.HERENOW_API_KEY || '';
  if (!raw) {
    const credentialPath = path.join(process.env.HOME || '', '.herenow', 'credentials');
    if (fs.existsSync(credentialPath)) raw = fs.readFileSync(credentialPath, 'utf8').trim();
  }
  if (!raw) throw new Error('HERENOW_API_KEY is not configured');
  return raw;
}

const headers = { Authorization: `Bearer ${loadApiKey()}`, 'content-type': 'application/json', 'X-HereNow-Client': 'career-review-test' };

async function request(method, url, body) {
  const options = { method, headers };
  if (body !== undefined) {
    options.body = typeof body === 'string' ? body : JSON.stringify(body);
    if (method === 'POST') options.headers['Idempotency-Key'] = `career-review-test-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  }
  const response = await fetch(url, options);
  const text = await response.text();
  let parsed = {};
  try { parsed = text ? JSON.parse(text) : {}; } catch { parsed = { raw: text }; }
  return { status: response.status, body: parsed };
}

const COLLECTIONS = [
  {
    name: 'workflow',
    payload: marker => ({ role_key: marker, stage: 'processing', route: 'portal', company: '__TEST__', role: '__TEST__', template_id: 'ats-classic' }),
    patch: { stage: 'ready_review', template_id: 'ats-executive-line' },
    check: (record, marker) => record && record.role_key === marker && record.stage === 'ready_review' && record.template_id === 'ats-executive-line'
  },
  {
    name: 'comments',
    payload: marker => ({ role_key: marker, comment: `__TEST__ comment ${marker}`, comment_type: 'note', resolved: false }),
    patch: { resolved: true },
    check: (record, marker) => record && record.role_key === marker && record.comment === `__TEST__ comment ${marker}` && record.resolved === true
  },
  {
    name: 'history',
    payload: marker => ({
      role_key: marker,
      event: 'portal_opened',
      from_stage: 'approved',
      to_stage: 'approved',
      note: JSON.stringify({
        actor: 'owner_ui',
        ui_source: 'site_data_test',
        evidence_level: 'portal_open_only_not_submission',
        url: 'https://example.com/test-job',
        opened_at: '2026-08-05T19:30:00Z',
        template_id: 'ats-classic',
        document_pdf: 'files/__TEST__/resume.pdf',
        document_docx: 'files/__TEST__/resume.docx',
        document_sha256: 'a'.repeat(64),
        package_version: 'test-v1'
      })
    }),
    patch: { note: '__TEST__ patched' },
    check: (record, marker) => record && record.role_key === marker && record.event === 'portal_opened' && record.note === '__TEST__ patched' && record.from_stage === 'approved' && record.to_stage === 'approved'
  },
  {
    name: 'ai_requests',
    payload: marker => ({ role_key: marker, request_type: 'ats_check', prompt: `__TEST__ prompt ${marker}`, state: 'pending' }),
    patch: { state: 'done' },
    check: (record, marker) => record && record.role_key === marker && record.state === 'done' && /__TEST__ prompt/.test(record.prompt || '')
  },
  {
    name: 'preferences',
    payload: marker => ({ key: marker, value: '__TEST__' }),
    patch: { value: '__TEST__ patched' },
    check: (record, marker) => record && record.key === marker && record.value === '__TEST__ patched'
  }
];

async function runCollection(definition) {
  const marker = `__test__${Date.now()}`;
  const created = await request('POST', `${API}/publishes/${slug}/data/${definition.name}`, definition.payload(marker));
  if (created.status !== 200 && created.status !== 201) {
    return { collection: definition.name, passed: false, step: 'create', error: `${created.status} ${JSON.stringify(created.body).slice(0, 200)}`, marker };
  }
  const record = created.body.record;
  const id = record && (record.id || record.recordId);

  let patched = null;
  const patchRes = await request('PATCH', `${API}/publishes/${slug}/data/${definition.name}/${id}`, definition.patch);
  if (patchRes.status !== 200) {
    return { collection: definition.name, passed: false, step: 'patch', error: `${patchRes.status} ${JSON.stringify(patchRes.body).slice(0, 200)}`, marker, id };
  }
  patched = patchRes.body.record;

  const readRes = await request('GET', `${API}/publishes/${slug}/data/${definition.name}/${id}`);
  if (readRes.status !== 200) {
    return { collection: definition.name, passed: false, step: 'read', error: `${readRes.status} ${JSON.stringify(readRes.body).slice(0, 200)}`, marker, id };
  }
  const readBack = readRes.body.record || readRes.body;
  const data = (readBack && readBack.data) || readBack || {};
  const ok = definition.check(data, marker);

  await request('DELETE', `${API}/publishes/${slug}/data/${definition.name}/${id}`);
  const after = await request('GET', `${API}/publishes/${slug}/data/${definition.name}/${id}`);
  const deleted = after.status === 404 || (after.body && after.body.deleted === true);

  if (!ok || !deleted) {
    return { collection: definition.name, passed: false, step: ok ? 'delete-verify' : 'check', error: `validation ${ok ? 'ok' : 'failed'}; delete verify ${deleted ? 'ok' : 'failed'}`, marker, id };
  }
  return { collection: definition.name, passed: true, marker, id };
}

async function cleanup(created) {
  for (const entry of created.filter(item => item && item.marker && !item.passed)) {
    const recordId = entry.id;
    if (recordId) {
      const res = await request('DELETE', `${API}/publishes/${slug}/data/${entry.collection}/${recordId}`);
      if (res.status === 200) console.log(`cleanup: removed ${entry.collection}/${recordId}`);
    }
  }
}

async function main() {
  const results = [];
  const created = [];
  for (const definition of COLLECTIONS) {
    const result = await runCollection(definition);
    created.push(result);
    results.push(result);
  }
  await cleanup(created);
  const passed = results.filter(r => r.passed).length;
  const failed = results.filter(r => !r.passed);
  const summary = { slug, tested: results.length, passed, failed: failed.length, collections: results, overall: failed.length === 0 ? 'PASS' : 'FAIL' };
  console.log(JSON.stringify(summary, null, 2));
  if (failed.length) process.exitCode = 1;
}

main().catch(error => { console.error(error.message); process.exitCode = 1; });
