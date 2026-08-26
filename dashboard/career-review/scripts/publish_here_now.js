const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { execFileSync } = require('child_process');
const { trackerPaths } = require('./tracker_base');

// Resolve deployment inputs from this versioned dashboard checkout. The old
// /home/hameedo/websites/career-review copy was intentionally retired.
const ROOT = path.resolve(__dirname, '..');
const REPO = path.resolve(ROOT, '../..');
const TRACKER = trackerPaths(REPO);
const SITE = path.join(ROOT, 'site');
const STATE = path.join(ROOT, '.deploy.json');
const API = 'https://here.now/api/v1';
const UNIFIER = path.join(REPO, 'tools', 'career_tracker_unify_safe.py');
const BUILD = path.join(ROOT, 'scripts', 'build_site.js');
const CANONICAL_SUMMARY = TRACKER.canonicalSummary;

function loadApiKey() {
  let raw = process.env.HERENOW_API_KEY || '';
  if (!raw) {
    const credentialPath = path.join(process.env.HOME || '', '.herenow', 'credentials');
    if (fs.existsSync(credentialPath)) raw = fs.readFileSync(credentialPath, 'utf8').trim();
  }
  if (!raw) throw new Error('HERENOW_API_KEY is not configured');
  if (raw.startsWith('{')) {
    const parsed = JSON.parse(raw);
    raw = parsed.apiKey || parsed.api_key || parsed.key || parsed.token || parsed.secret || '';
  }
  if (!raw) throw new Error('Unable to parse here.now API key');
  return raw;
}

const key = loadApiKey();
const authHeaders = { Authorization: `Bearer ${key}`, 'X-HereNow-Client': 'chatgpt-career-engine' };

function contentType(file) {
  const ext = path.extname(file).toLowerCase();
  return ({
    '.html': 'text/html; charset=utf-8', '.css': 'text/css; charset=utf-8',
    '.js': 'text/javascript; charset=utf-8', '.json': 'application/json; charset=utf-8',
    '.pdf': 'application/pdf', '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.svg': 'image/svg+xml'
  })[ext] || 'application/octet-stream';
}

function walk(dir, base = dir) {
  const files = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const absolute = path.join(dir, entry.name);
    if (entry.isDirectory()) files.push(...walk(absolute, base));
    else files.push({ absolute, relative: path.relative(base, absolute).split(path.sep).join('/') });
  }
  return files;
}

async function request(url, options = {}) {
  const response = await fetch(url, options);
  const text = await response.text();
  let body = null;
  try { body = text ? JSON.parse(text) : {}; } catch { body = { raw: text }; }
  if (!response.ok) throw new Error(`${options.method || 'GET'} ${url} failed (${response.status}): ${JSON.stringify(body)}`);
  return body;
}

async function discoverExistingSlug() {
  if (fs.existsSync(STATE)) {
    const state = JSON.parse(fs.readFileSync(STATE, 'utf8'));
    if (state.slug) return state.slug;
  }
  const result = await request(`${API}/publishes?limit=100`, { headers: authHeaders });
  const rows = result.sites || result.publishes || result.items || result.results || [];
  const match = rows.find(row => row.displayName === 'Career Application Review' || row.viewer?.title === 'Career Application Review');
  return match?.slug || '';
}

function reconcileCanonicalTracker(slug) {
  if (!fs.existsSync(UNIFIER)) throw new Error(`Canonical tracker unifier missing: ${UNIFIER}`);
  const args = [UNIFIER, '--repo', REPO, '--apply'];
  if (slug) args.push('--site-slug', slug);
  else args.push('--skip-site-data');
  execFileSync('python3', args, {
    cwd: REPO,
    env: process.env,
    stdio: 'inherit',
    // Live Site Data reconciliation can legitimately exceed three minutes when
    // many canonical workflow rows require PATCH confirmation. Keep the publish
    // fail-closed, but allow the bounded reconciliation enough time to finish.
    timeout: 600000
  });
  execFileSync('node', [BUILD], {
    cwd: REPO,
    env: process.env,
    stdio: 'inherit',
    timeout: 180000
  });
}

function verifyCanonicalBuild() {
  const jobsPath = path.join(SITE, 'data', 'jobs.json');
  if (!fs.existsSync(jobsPath)) throw new Error('dashboard jobs.json is missing after canonical rebuild');
  if (!fs.existsSync(CANONICAL_SUMMARY)) throw new Error('canonical tracker summary is missing after reconciliation');
  const jobs = JSON.parse(fs.readFileSync(jobsPath, 'utf8'));
  const summary = JSON.parse(fs.readFileSync(CANONICAL_SUMMARY, 'utf8'));
  const roles = [...(jobs.applications || []), ...(jobs.reviewed || [])];
  if (Number(jobs.total_roles) !== Number(jobs.tracker_records)) {
    throw new Error(`Canonical dashboard invariant failed: total_roles=${jobs.total_roles} tracker_records=${jobs.tracker_records}. Legacy/non-tracker roles must be migrated before publish.`);
  }
  if (roles.length !== Number(jobs.tracker_records)) {
    throw new Error(`Canonical dashboard invariant failed: rendered roles=${roles.length} tracker_records=${jobs.tracker_records}`);
  }
  const appliedValues = new Set(['applied', 'submitted', 'sent', 'application_submitted', 'email_sent', 'submitted_pending_response', 'email_sent_owner_confirmed']);
  const normalized = value => String(value || '').trim().toLowerCase().replace(/[\s-]+/g, '_');
  const applied = roles.filter(role => normalized(role.processing_status) === 'applied' || appliedValues.has(normalized(role.application_status))).length;
  const canonicalApplied = Number(summary.counts?.applied_total || 0);
  if (applied !== canonicalApplied) {
    throw new Error(`Canonical dashboard invariant failed: dashboard applied=${applied} tracker applied=${canonicalApplied}`);
  }
  if (Number(summary.counts?.submitted_portal || 0) + Number(summary.counts?.sent_email || 0) !== canonicalApplied) {
    throw new Error('Canonical application split invariant failed: submitted + sent must equal applied total');
  }
}

async function main() {
  const slug = await discoverExistingSlug();

  // Site Data is a write queue/evidence surface, not a second status authority.
  // Reconcile it into CareerTracker, migrate legacy-only jobs, rebuild the site,
  // then fail closed if the published role/application counts diverge.
  reconcileCanonicalTracker(slug);
  verifyCanonicalBuild();

  const siteFiles = walk(SITE).map(file => {
    const buffer = fs.readFileSync(file.absolute);
    return {
      ...file,
      buffer,
      descriptor: {
        path: file.relative,
        size: buffer.length,
        contentType: contentType(file.relative),
        hash: crypto.createHash('sha256').update(buffer).digest('hex')
      }
    };
  });
  if (!siteFiles.some(f => f.relative === 'index.html')) throw new Error('index.html is missing');

  const payload = {
    files: siteFiles.map(file => file.descriptor),
    ttlSeconds: null,
    displayName: 'Career Application Review',
    displayDescription: 'Private Career Engine dashboard for reviewing tailored applications, documents, routes and owner comments.',
    viewer: {
      title: 'Career Application Review',
      description: 'Private tailored application review dashboard.'
    }
  };
  const publishUrl = slug ? `${API}/publish/${slug}` : `${API}/publish`;
  const created = await request(publishUrl, {
    method: slug ? 'PUT' : 'POST',
    headers: { ...authHeaders, 'content-type': 'application/json' },
    body: JSON.stringify(payload)
  });
  const upload = created.upload || created;
  const uploads = upload.uploads || [];
  for (const target of uploads) {
    const source = siteFiles.find(file => file.relative === target.path);
    if (!source) throw new Error(`Upload source not found: ${target.path}`);
    const headers = { 'content-type': source.descriptor.contentType, ...(target.headers || {}) };
    const response = await fetch(target.url, { method: 'PUT', headers, body: source.buffer });
    if (!response.ok) throw new Error(`Upload failed for ${target.path}: ${response.status}`);
  }
  const finalizeUrl = upload.finalizeUrl || created.finalizeUrl;
  const versionId = upload.versionId || created.versionId;
  const finalized = await request(finalizeUrl, {
    method: 'POST',
    headers: { ...authHeaders, 'content-type': 'application/json' },
    body: JSON.stringify({ versionId })
  });
  const finalSlug = finalized.slug || created.slug || slug;
  const siteUrl = finalized.siteUrl || created.siteUrl || `https://${finalSlug}.here.now/`;

  const access = await request(`${API}/publish/${finalSlug}/access`, {
    method: 'PATCH',
    headers: { ...authHeaders, 'content-type': 'application/json' },
    body: JSON.stringify({
      mode: 'restricted',
      allowedEmails: ['hameedo@gmail.com'],
      allowedDomains: [],
      notify: false
    })
  });

  let previousState = {};
  if (fs.existsSync(STATE)) {
    try { previousState = JSON.parse(fs.readFileSync(STATE, 'utf8')); } catch { previousState = {}; }
  }
  fs.writeFileSync(STATE, `${JSON.stringify({ ...previousState, slug: finalSlug, siteUrl, updatedAt: new Date().toISOString() }, null, 2)}\n`);
  console.log(JSON.stringify({ slug: finalSlug, siteUrl, files: siteFiles.length, canonicalTracker: true, access: access.access || access }, null, 2));
}

main().catch(error => { console.error(error.message); process.exit(1); });
