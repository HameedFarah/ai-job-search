const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');

// Deployment state lives with the versioned dashboard source. The obsolete
// /home/hameedo/websites/career-review copy has been removed.
const ROOT = path.resolve(__dirname, '..');
const STATE_PATH = path.join(ROOT, '.deploy.json');
const HERE_API = 'https://here.now/api/v1';
const CF_API = 'https://api.cloudflare.com/client/v4';
const DOMAIN = 'career.farahdigital.com';
const ZONE_NAME = 'farahdigital.com';

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function loadHereNowApiKey() {
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

function loadCloudflareApiToken() {
  const token = (process.env.CLOUDFLARE_API_TOKEN || '').trim();
  if (!token) throw new Error('CLOUDFLARE_API_TOKEN was not injected');
  return token;
}

function reexecWithInfisical() {
  const runtimeExec = '/home/hameedo/vps-infra-dev/scripts/infisical-vps/runtime_exec.py';
  const manifest = '/home/hameedo/vps-infra-dev/scripts/infisical-vps/state/career-review-domain-runtime-manifest.json';
  const result = spawnSync('python3', [runtimeExec, '--manifest', manifest, '--', 'node', __filename, ...process.argv.slice(2)], {
    encoding: 'utf8',
    env: process.env,
    maxBuffer: 4 * 1024 * 1024
  });
  if (result.stdout) process.stdout.write(result.stdout);
  if (result.stderr) process.stderr.write(result.stderr);
  process.exit(result.status === null ? 1 : result.status);
}

async function requestJson(url, options = {}, acceptedStatuses = []) {
  const response = await fetch(url, options);
  const text = await response.text();
  let body = {};
  try { body = text ? JSON.parse(text) : {}; } catch { body = { raw: text }; }
  if (!response.ok && !acceptedStatuses.includes(response.status)) {
    const safeMessage = body?.errors?.map(item => item.message).filter(Boolean).join('; ') || body?.error || body?.message || `HTTP ${response.status}`;
    throw new Error(`${options.method || 'GET'} ${new URL(url).pathname} failed: ${safeMessage}`);
  }
  return { status: response.status, body };
}

async function ensureHereNowDomain(hereHeaders) {
  let current = await requestJson(`${HERE_API}/domains/${encodeURIComponent(DOMAIN)}`, { headers: hereHeaders }, [404]);
  if (current.status === 404) {
    current = await requestJson(`${HERE_API}/domains`, {
      method: 'POST',
      headers: { ...hereHeaders, 'content-type': 'application/json' },
      body: JSON.stringify({ domain: DOMAIN })
    });
  }
  return current.body;
}

function expectedCname(domainBody) {
  const instructions = domainBody.dns_instructions || domainBody.dnsInstructions || [];
  const cname = instructions.find(item => String(item.type || '').toUpperCase() === 'CNAME');
  return cname?.value || cname?.content || 'fallback.here.now';
}

async function ensureCloudflareCname(cfHeaders, target) {
  const zones = await requestJson(`${CF_API}/zones?name=${encodeURIComponent(ZONE_NAME)}&status=active&per_page=50`, { headers: cfHeaders });
  const zone = zones.body?.result?.find(item => item.name === ZONE_NAME);
  if (!zone) throw new Error(`Cloudflare zone not found: ${ZONE_NAME}`);

  const records = await requestJson(`${CF_API}/zones/${zone.id}/dns_records?type=CNAME&name=${encodeURIComponent(DOMAIN)}&per_page=100`, { headers: cfHeaders });
  const existing = records.body?.result?.[0];
  const payload = { type: 'CNAME', name: DOMAIN, content: target, ttl: 1, proxied: false };
  if (existing) {
    const unchanged = existing.content === target && existing.proxied === false;
    if (unchanged) return { action: 'unchanged', target };
    await requestJson(`${CF_API}/zones/${zone.id}/dns_records/${existing.id}`, {
      method: 'PUT',
      headers: { ...cfHeaders, 'content-type': 'application/json' },
      body: JSON.stringify(payload)
    });
    return { action: 'updated', target };
  }

  await requestJson(`${CF_API}/zones/${zone.id}/dns_records`, {
    method: 'POST',
    headers: { ...cfHeaders, 'content-type': 'application/json' },
    body: JSON.stringify(payload)
  });
  return { action: 'created', target };
}

async function ensureHereNowLink(hereHeaders, slug) {
  const rootUrl = `${HERE_API}/links/__root__?domain=${encodeURIComponent(DOMAIN)}`;
  const searched = await requestJson(`${HERE_API}/publishes/search?q=${encodeURIComponent(DOMAIN)}&limit=10`, { headers: hereHeaders });
  const linked = (searched.body?.results || []).find(item => item.slug === slug && item.primaryUrl === `https://${DOMAIN}/`);
  if (linked) return 'unchanged';

  const listed = await requestJson(`${HERE_API}/links?domain=${encodeURIComponent(DOMAIN)}`, { headers: hereHeaders });
  const rows = listed.body?.links || listed.body?.items || listed.body?.results || (Array.isArray(listed.body) ? listed.body : []);
  const root = rows.find(item => {
    const location = item.location ?? item.path ?? '';
    const domain = item.domain || item.customDomain || DOMAIN;
    return (location === '' || location === '__root__' || location === '/') && domain === DOMAIN;
  });

  const currentSlug = root?.slug || root?.siteSlug || root?.publishSlug;
  if (currentSlug === slug) return 'unchanged';
  if (root) {
    await requestJson(rootUrl, {
      method: 'PATCH',
      headers: { ...hereHeaders, 'content-type': 'application/json' },
      body: JSON.stringify({ slug })
    });
    return 'updated';
  }

  await requestJson(`${HERE_API}/links`, {
    method: 'POST',
    headers: { ...hereHeaders, 'content-type': 'application/json' },
    body: JSON.stringify({ location: '', slug, domain: DOMAIN })
  });
  return 'created';
}

async function verifyDomain(hereHeaders) {
  let body = {};
  for (let attempt = 0; attempt < 12; attempt += 1) {
    const response = await requestJson(`${HERE_API}/domains/${encodeURIComponent(DOMAIN)}`, { headers: hereHeaders });
    body = response.body;
    if (body.status === 'active') break;
    await sleep(5000);
  }
  return body;
}

async function getCloudflareCname(cfHeaders) {
  const zones = await requestJson(`${CF_API}/zones?name=${encodeURIComponent(ZONE_NAME)}&status=active&per_page=50`, { headers: cfHeaders });
  const zone = zones.body?.result?.find(item => item.name === ZONE_NAME);
  if (!zone) throw new Error(`Cloudflare zone not found: ${ZONE_NAME}`);
  const records = await requestJson(`${CF_API}/zones/${zone.id}/dns_records?name=${encodeURIComponent(DOMAIN)}&per_page=100`, { headers: cfHeaders });
  return (records.body?.result || []).map(record => ({
    type: record.type,
    name: record.name,
    content: record.content,
    proxied: record.proxied,
    ttl: record.ttl
  }));
}

async function main() {
  if (!process.env.CLOUDFLARE_API_TOKEN) reexecWithInfisical();
  if (!fs.existsSync(STATE_PATH)) throw new Error('Career dashboard deployment state is missing');
  const state = JSON.parse(fs.readFileSync(STATE_PATH, 'utf8'));
  if (!state.slug) throw new Error('Career dashboard slug is missing');

  const hereKey = loadHereNowApiKey();
  const cfToken = loadCloudflareApiToken();
  const hereHeaders = { Authorization: `Bearer ${hereKey}`, 'X-HereNow-Client': 'chatgpt-career-engine' };
  const cfHeaders = { Authorization: `Bearer ${cfToken}` };

  if (process.argv.includes('--check')) {
    const domainStatus = await requestJson(`${HERE_API}/domains/${encodeURIComponent(DOMAIN)}`, { headers: hereHeaders }, [404]);
    const records = await getCloudflareCname(cfHeaders);
    const linksResponse = await requestJson(`${HERE_API}/links?domain=${encodeURIComponent(DOMAIN)}`, { headers: hereHeaders });
    const searchResponse = await requestJson(`${HERE_API}/publishes/search?q=${encodeURIComponent(DOMAIN)}&limit=10`, { headers: hereHeaders });
    const publicResponse = await fetch(`https://${DOMAIN}/`, { redirect: 'manual' });
    console.log(JSON.stringify({
      hereNowStatus: domainStatus.status === 404 ? 'not_registered' : domainStatus.body.status,
      dnsInstructions: domainStatus.body.dns_instructions || domainStatus.body.dnsInstructions || [],
      cloudflareRecords: records,
      hereNowLinks: linksResponse.body,
      hereNowSearch: searchResponse.body,
      publicResponse: {
        status: publicResponse.status,
        location: publicResponse.headers.get('location') || null,
        contentType: publicResponse.headers.get('content-type') || null
      }
    }, null, 2));
    return;
  }

  const domain = await ensureHereNowDomain(hereHeaders);
  const target = expectedCname(domain);
  const dns = await ensureCloudflareCname(cfHeaders, target);
  const verified = await verifyDomain(hereHeaders);
  if (verified.status !== 'active') {
    throw new Error(`here.now custom-domain verification remains ${verified.status || 'pending'}`);
  }
  const link = await ensureHereNowLink(hereHeaders, state.slug);

  const nextState = {
    ...state,
    customDomain: DOMAIN,
    customUrl: `https://${DOMAIN}/`,
    customDomainStatus: verified.status || 'pending',
    updatedAt: new Date().toISOString()
  };
  fs.writeFileSync(STATE_PATH, `${JSON.stringify(nextState, null, 2)}\n`);

  console.log(JSON.stringify({
    domain: DOMAIN,
    customUrl: nextState.customUrl,
    slug: state.slug,
    dns,
    link,
    domainStatus: nextState.customDomainStatus
  }, null, 2));
}

main().catch(error => {
  console.error(error.message);
  process.exit(1);
});
