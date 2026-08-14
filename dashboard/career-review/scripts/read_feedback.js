/* read_feedback.js — securely list Career Review comments and AI requests via the owner API.
 * The here.now credential is loaded locally and never printed.
 * Usage: node scripts/read_feedback.js [role-key] [--pending-only]
 */
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const API = 'https://here.now/api/v1';
const slug = JSON.parse(fs.readFileSync(path.join(ROOT, '.deploy.json'), 'utf8')).slug || 'gilded-timber-xfj7';
const args = process.argv.slice(2);
const roleKey = args.find(arg => !arg.startsWith('--')) || '';
const pendingOnly = args.includes('--pending-only');

function loadApiKey() {
  let raw = process.env.HERENOW_API_KEY || '';
  if (!raw) {
    const credentialPath = path.join(process.env.HOME || '', '.herenow', 'credentials');
    if (fs.existsSync(credentialPath)) raw = fs.readFileSync(credentialPath, 'utf8').trim();
  }
  if (!raw) throw new Error('HERENOW_API_KEY is not configured');
  return raw;
}

const headers = { Authorization: `Bearer ${loadApiKey()}`, 'X-HereNow-Client': 'career-review-feedback-reader' };

async function list(collection) {
  const response = await fetch(`${API}/publishes/${slug}/data/${collection}?limit=300`, { headers });
  if (!response.ok) throw new Error(`${collection} read failed (${response.status})`);
  const body = await response.json();
  return Array.isArray(body.records) ? body.records : [];
}

function normalize(record) {
  return {
    id: record.id,
    created_at: record.createdAt || record.created_at || '',
    updated_at: record.updatedAt || record.updated_at || '',
    ...(record.data || record)
  };
}

async function main() {
  const [commentsRaw, requestsRaw] = await Promise.all([list('comments'), list('ai_requests')]);
  let comments = commentsRaw.map(normalize);
  let ai_requests = requestsRaw.map(normalize);
  if (roleKey) {
    comments = comments.filter(item => item.role_key === roleKey);
    ai_requests = ai_requests.filter(item => item.role_key === roleKey);
  }
  if (pendingOnly) {
    comments = comments.filter(item => item.resolved !== true);
    ai_requests = ai_requests.filter(item => (item.state || 'pending') === 'pending');
  }
  comments.sort((a, b) => String(a.created_at).localeCompare(String(b.created_at)));
  ai_requests.sort((a, b) => String(a.created_at).localeCompare(String(b.created_at)));
  console.log(JSON.stringify({ slug, role_key: roleKey || null, pending_only: pendingOnly, comments, ai_requests }, null, 2));
}

main().catch(error => { console.error(error.message); process.exitCode = 1; });
