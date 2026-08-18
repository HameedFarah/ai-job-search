#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');

const REPO = path.resolve(__dirname, '../../..');
const DASHBOARD = path.resolve(__dirname, '..');
const SITE = path.join(DASHBOARD, 'site');
const CONFIG = path.join(DASHBOARD, 'wrangler.jsonc');
const RUNTIME = path.join(REPO, 'projects', 'job-automation', 'runtime');
const API = 'https://api.cloudflare.com/client/v4';

const HOSTNAME = 'career.farahdigital.com';
const ZONE_NAME = 'farahdigital.com';
const ROUTE_PATTERN = `${HOSTNAME}/*`;
const WORKER_NAME = 'career-engine-private';
const APP_NAME = 'Career Engine';
const POLICY_NAME = 'Career Engine owner only';
const OWNER_EMAIL = 'hameedo@gmail.com';

function fail(message) {
  throw new Error(message);
}

function requiredEnv(name) {
  const value = String(process.env[name] || '').trim();
  if (!value) fail(`${name} is not configured`);
  return value;
}

function credentials() {
  return {
    accountId: requiredEnv('CLOUDFLARE_ACCOUNT_ID'),
    zoneId: requiredEnv('CLOUDFLARE_ZONE_ID'),
    token: requiredEnv('CLOUDFLARE_API_TOKEN'),
  };
}

async function cf(method, pathname, body) {
  const { token } = credentials();
  const response = await fetch(`${API}${pathname}`, {
    method,
    headers: {
      Authorization: `Bearer ${token}`,
      ...(body === undefined ? {} : { 'content-type': 'application/json' }),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await response.text();
  let payload = {};
  try { payload = text ? JSON.parse(text) : {}; }
  catch { payload = { success: false, errors: [{ message: `non-JSON Cloudflare response (${response.status})` }] }; }
  if (!response.ok || payload.success === false) {
    const errors = Array.isArray(payload.errors) ? payload.errors.map(item => item.message || item.code).filter(Boolean) : [];
    fail(`Cloudflare ${method} ${pathname} failed (${response.status}): ${errors.join('; ') || 'request rejected'}`);
  }
  return payload.result;
}

function policyBody() {
  return {
    name: POLICY_NAME,
    decision: 'allow',
    precedence: 1,
    include: [{ email: { email: OWNER_EMAIL } }],
    exclude: [],
    require: [],
    session_duration: '24h',
  };
}

function isOwnerOnlyPolicy(policy) {
  if (!policy || policy.decision !== 'allow') return false;
  const include = Array.isArray(policy.include) ? policy.include : [];
  const exclude = Array.isArray(policy.exclude) ? policy.exclude : [];
  const requireRules = Array.isArray(policy.require) ? policy.require : [];
  return include.length === 1
    && include[0]?.email?.email === OWNER_EMAIL
    && exclude.length === 0
    && requireRules.length === 0;
}

function hasEveryoneAllow(policy) {
  if (!policy || policy.decision !== 'allow') return false;
  return (policy.include || []).some(rule => rule && Object.prototype.hasOwnProperty.call(rule, 'everyone'));
}

function safeAppSnapshot(app) {
  if (!app) return null;
  return {
    id: app.id || '',
    name: app.name || '',
    domain: app.domain || '',
    type: app.type || '',
    session_duration: app.session_duration || '',
    aud: app.aud || '',
  };
}

function safePolicySnapshot(policy) {
  return {
    id: policy.id || '',
    name: policy.name || '',
    decision: policy.decision || '',
    precedence: policy.precedence ?? null,
    include: policy.include || [],
    exclude: policy.exclude || [],
    require: policy.require || [],
    session_duration: policy.session_duration || '',
  };
}

async function inspectState() {
  const { accountId, zoneId } = credentials();

  const dnsResult = await cf('GET', `/zones/${zoneId}/dns_records?name=${encodeURIComponent(HOSTNAME)}`);
  const dnsRecords = Array.isArray(dnsResult) ? dnsResult : [];
  if (dnsRecords.length !== 1) fail(`Expected exactly one DNS record for ${HOSTNAME}; found ${dnsRecords.length}`);
  const dns = dnsRecords[0];
  if (!dns.proxied) fail(`${HOSTNAME} DNS is not Cloudflare-proxied; refusing Worker route deployment`);

  const appsResult = await cf('GET', `/accounts/${accountId}/access/apps`);
  const apps = Array.isArray(appsResult) ? appsResult : [];
  const matchingApps = apps.filter(app => String(app.domain || '').replace(/\/$/, '') === HOSTNAME);
  if (matchingApps.length > 1) fail(`Multiple Access applications target ${HOSTNAME}; refusing ambiguous mutation`);
  const app = matchingApps[0] || null;
  const policies = app
    ? (await cf('GET', `/accounts/${accountId}/access/apps/${app.id}/policies`)) || []
    : [];

  const routesResult = await cf('GET', `/zones/${zoneId}/workers/routes`);
  const routes = Array.isArray(routesResult) ? routesResult : [];
  const matchingRoutes = routes.filter(route => route.pattern === ROUTE_PATTERN);
  if (matchingRoutes.length > 1) fail(`Multiple Worker routes match ${ROUTE_PATTERN}; refusing ambiguous mutation`);
  const route = matchingRoutes[0] || null;

  let worker = null;
  let subdomain = null;
  const scriptsResult = await cf('GET', `/accounts/${accountId}/workers/scripts`);
  const scripts = Array.isArray(scriptsResult) ? scriptsResult : [];
  worker = scripts.find(script => script.id === WORKER_NAME) || null;
  if (worker) {
    try {
      subdomain = await cf('GET', `/accounts/${accountId}/workers/scripts/${WORKER_NAME}/subdomain`);
    } catch (error) {
      subdomain = { inspection_error: String(error.message || error) };
    }
  }

  return { dns, app, policies: Array.isArray(policies) ? policies : [], route, worker, subdomain };
}

function safeState(state) {
  return {
    dns: {
      id: state.dns?.id || '',
      type: state.dns?.type || '',
      name: state.dns?.name || '',
      content: state.dns?.content || '',
      proxied: Boolean(state.dns?.proxied),
    },
    access_app: safeAppSnapshot(state.app),
    policies: (state.policies || []).map(safePolicySnapshot),
    route: state.route ? { id: state.route.id || '', pattern: state.route.pattern || '', script: state.route.script || '' } : null,
    worker: state.worker ? { id: state.worker.id || '', modified_on: state.worker.modified_on || '' } : null,
    subdomain: state.subdomain ? {
      enabled: Boolean(state.subdomain.enabled),
      previews_enabled: Boolean(state.subdomain.previews_enabled),
      inspection_error: state.subdomain.inspection_error || '',
    } : null,
  };
}

function writeBackup(state) {
  fs.mkdirSync(RUNTIME, { recursive: true });
  const stamp = new Date().toISOString().replace(/[:.]/g, '-');
  const target = path.join(RUNTIME, `cloudflare-access-backup-${stamp}.json`);
  const payload = {
    schema_version: 1,
    created_at: new Date().toISOString(),
    hostname: HOSTNAME,
    worker_name: WORKER_NAME,
    previous: safeState(state),
  };
  fs.writeFileSync(target, `${JSON.stringify(payload, null, 2)}\n`, { mode: 0o600 });
  return target;
}

function writeHeaders() {
  const headers = `/*\n  Cache-Control: no-store, max-age=0, must-revalidate\n  X-Robots-Tag: noindex, nofollow, noarchive\n  X-Content-Type-Options: nosniff\n  Referrer-Policy: no-referrer\n  Permissions-Policy: camera=(), microphone=(), geolocation=()\n`;
  fs.writeFileSync(path.join(SITE, '_headers'), headers, 'utf8');
}

function runChecked(command, args, timeout = 180000) {
  execFileSync(command, args, {
    cwd: REPO,
    env: process.env,
    stdio: 'inherit',
    timeout,
  });
}

function buildCurrentDashboard() {
  runChecked('./career-engine', ['doctor']);
  runChecked('./career-engine', ['bundle', 'status']);
  runChecked('./career-engine', ['dashboard', '--sync']);
  runChecked('node', [path.join(DASHBOARD, 'scripts', 'build_site.js')], 300000);
  writeHeaders();
  if (!fs.existsSync(path.join(SITE, 'index.html'))) fail('dashboard build did not produce index.html');
}

async function ensureAccess(before) {
  const { accountId } = credentials();
  let app = before.app;
  if (!app) {
    app = await cf('POST', `/accounts/${accountId}/access/apps`, {
      name: APP_NAME,
      domain: HOSTNAME,
      type: 'self_hosted',
      session_duration: '24h',
    });
  } else {
    app = await cf('PUT', `/accounts/${accountId}/access/apps/${app.id}`, {
      name: APP_NAME,
      domain: HOSTNAME,
      type: 'self_hosted',
      session_duration: '24h',
    });
  }

  let policies = await cf('GET', `/accounts/${accountId}/access/apps/${app.id}/policies`);
  policies = Array.isArray(policies) ? policies : [];

  // This hostname is owner-only. Application-local policies are deliberately
  // reconciled to one exact-email allow policy so a stale broader allow cannot
  // silently bypass the intended security boundary.
  let ownerPolicy = policies.find(policy => policy.name === POLICY_NAME) || null;
  for (const policy of policies) {
    if (policy.id === ownerPolicy?.id) continue;
    await cf('DELETE', `/accounts/${accountId}/access/apps/${app.id}/policies/${policy.id}`);
  }
  if (ownerPolicy) {
    ownerPolicy = await cf('PUT', `/accounts/${accountId}/access/apps/${app.id}/policies/${ownerPolicy.id}`, policyBody());
  } else {
    ownerPolicy = await cf('POST', `/accounts/${accountId}/access/apps/${app.id}/policies`, policyBody());
  }

  const finalPolicies = await cf('GET', `/accounts/${accountId}/access/apps/${app.id}/policies`);
  if (!Array.isArray(finalPolicies) || finalPolicies.length !== 1) fail('Access policy reconciliation did not leave exactly one application-local policy');
  if (!isOwnerOnlyPolicy(finalPolicies[0])) fail('Access policy is not the exact owner-email allow policy');
  if (finalPolicies.some(hasEveryoneAllow)) fail('Unsafe Everyone allow rule detected after Access reconciliation');
  return { app, policies: finalPolicies };
}

function deployWorkerAssets() {
  // Pin to Wrangler v4 (current supported major) while allowing maintained v4
  // patch/minor fixes. The runtime Cloudflare token is inherited from Infisical.
  runChecked('npx', ['--yes', 'wrangler@4', 'deploy', '--config', CONFIG], 600000);
}

async function ensureRoute() {
  const { zoneId } = credentials();
  const routes = await cf('GET', `/zones/${zoneId}/workers/routes`);
  const matching = (Array.isArray(routes) ? routes : []).filter(route => route.pattern === ROUTE_PATTERN);
  if (matching.length > 1) fail(`Multiple Worker routes match ${ROUTE_PATTERN}`);
  if (matching.length === 1) {
    if (matching[0].script !== WORKER_NAME) {
      return cf('PUT', `/zones/${zoneId}/workers/routes/${matching[0].id}`, { pattern: ROUTE_PATTERN, script: WORKER_NAME });
    }
    return matching[0];
  }
  return cf('POST', `/zones/${zoneId}/workers/routes`, { pattern: ROUTE_PATTERN, script: WORKER_NAME });
}

async function unauthenticatedProbe() {
  const response = await fetch(`https://${HOSTNAME}/`, { redirect: 'manual', headers: { 'cache-control': 'no-cache' } });
  const text = await response.text();
  const leakedDashboard = /<title>Career Application Board<\/title>|<h1>Career Engine<\/h1>/i.test(text);
  if (response.status === 200 || leakedDashboard) fail(`Unauthenticated probe exposed dashboard content (HTTP ${response.status})`);
  return {
    status: response.status,
    location_host: (() => {
      const location = response.headers.get('location');
      if (!location) return '';
      try { return new URL(location, `https://${HOSTNAME}`).hostname; } catch { return ''; }
    })(),
    dashboard_content_exposed: leakedDashboard,
  };
}

async function verifyState() {
  const state = await inspectState();
  if (!state.app) fail('Cloudflare Access application is missing');
  if (state.policies.length !== 1 || !isOwnerOnlyPolicy(state.policies[0])) fail('Cloudflare Access is not owner-only');
  if (!state.route || state.route.script !== WORKER_NAME) fail('Career Engine Worker route is missing or points at another script');
  if (!state.worker) fail('Career Engine Worker script is missing');
  if (state.subdomain && (state.subdomain.enabled || state.subdomain.previews_enabled)) {
    fail('Worker alternate public hostname or preview URLs are enabled');
  }
  const probe = await unauthenticatedProbe();
  return { state: safeState(state), unauthenticated_probe: probe };
}

async function preflight() {
  const state = await inspectState();
  const unsafePolicies = (state.policies || []).filter(policy => policy.decision === 'allow' && !isOwnerOnlyPolicy(policy));
  return {
    mode: 'preflight',
    hostname: HOSTNAME,
    zone: ZONE_NAME,
    worker_name: WORKER_NAME,
    dns: safeState(state).dns,
    access_app_exists: Boolean(state.app),
    access_policy_count: state.policies.length,
    unsafe_existing_allow_policy_count: unsafePolicies.length,
    route: safeState(state).route,
    worker_exists: Boolean(state.worker),
    worker_subdomain: safeState(state).subdomain,
    credentials_present: true,
    mutation_performed: false,
  };
}

async function deploy() {
  const before = await inspectState();
  const backup = writeBackup(before);

  // Build and upload the Worker with no alternate public hostname. The route is
  // managed only after Access has been reconciled, so a failed upload cannot
  // accidentally make the dashboard public.
  buildCurrentDashboard();
  const access = await ensureAccess(before);
  deployWorkerAssets();
  await ensureRoute();
  const verification = await verifyState();

  return {
    mode: 'deploy',
    backup,
    access_app_id: access.app.id,
    access_policy_id: access.policies[0].id,
    ...verification,
  };
}

async function rollback(backupPath) {
  if (!backupPath) fail('--rollback requires a backup JSON path');
  const { accountId, zoneId } = credentials();
  const absolute = path.isAbsolute(backupPath) ? backupPath : path.resolve(REPO, backupPath);
  const snapshot = JSON.parse(fs.readFileSync(absolute, 'utf8'));
  if (snapshot.hostname !== HOSTNAME || snapshot.worker_name !== WORKER_NAME) fail('backup does not belong to this Career Engine migration');
  const previous = snapshot.previous || {};

  const routes = await cf('GET', `/zones/${zoneId}/workers/routes`);
  const current = (Array.isArray(routes) ? routes : []).find(route => route.pattern === ROUTE_PATTERN);
  if (previous.route) {
    if (current) await cf('PUT', `/zones/${zoneId}/workers/routes/${current.id}`, { pattern: previous.route.pattern, script: previous.route.script || null });
    else await cf('POST', `/zones/${zoneId}/workers/routes`, { pattern: previous.route.pattern, script: previous.route.script || null });
  } else if (current) {
    await cf('DELETE', `/zones/${zoneId}/workers/routes/${current.id}`);
  }

  const apps = await cf('GET', `/accounts/${accountId}/access/apps`);
  const app = (Array.isArray(apps) ? apps : []).find(item => String(item.domain || '').replace(/\/$/, '') === HOSTNAME);
  if (!previous.access_app && app) {
    await cf('DELETE', `/accounts/${accountId}/access/apps/${app.id}`);
  } else if (previous.access_app) {
    if (!app) fail('previous Access app existed but is now missing; refusing lossy automatic reconstruction');
    if (previous.access_app.id !== app.id) fail('Access app identity changed; refusing ambiguous rollback');
    const currentPolicies = await cf('GET', `/accounts/${accountId}/access/apps/${app.id}/policies`);
    for (const policy of Array.isArray(currentPolicies) ? currentPolicies : []) {
      await cf('DELETE', `/accounts/${accountId}/access/apps/${app.id}/policies/${policy.id}`);
    }
    for (const policy of previous.policies || []) {
      await cf('POST', `/accounts/${accountId}/access/apps/${app.id}/policies`, {
        name: policy.name,
        decision: policy.decision,
        precedence: policy.precedence,
        include: policy.include || [],
        exclude: policy.exclude || [],
        require: policy.require || [],
        ...(policy.session_duration ? { session_duration: policy.session_duration } : {}),
      });
    }
  }

  const after = await inspectState();
  return { mode: 'rollback', backup: absolute, restored: safeState(after) };
}

function selfTest() {
  const good = policyBody();
  const everyone = { ...policyBody(), include: [{ everyone: {} }] };
  const wrong = { ...policyBody(), include: [{ email: { email: 'other@example.com' } }] };
  if (!isOwnerOnlyPolicy(good)) fail('self-test: owner policy was rejected');
  if (isOwnerOnlyPolicy(everyone) || !hasEveryoneAllow(everyone)) fail('self-test: Everyone rule was not rejected');
  if (isOwnerOnlyPolicy(wrong)) fail('self-test: wrong email policy was accepted');
  return { mode: 'self-test', passed: true, owner_email: OWNER_EMAIL, worker_name: WORKER_NAME };
}

async function main() {
  const [mode, argument] = process.argv.slice(2);
  let result;
  if (mode === '--self-test') result = selfTest();
  else if (mode === '--preflight') result = await preflight();
  else if (mode === '--deploy') result = await deploy();
  else if (mode === '--verify') result = await verifyState();
  else if (mode === '--rollback') result = await rollback(argument);
  else fail('Usage: deploy_cloudflare_access.js --self-test|--preflight|--deploy|--verify|--rollback <backup.json>');
  console.log(JSON.stringify(result, null, 2));
}

if (require.main === module) {
  main().catch(error => {
    console.error(`ERROR: ${error.message}`);
    process.exit(1);
  });
}

module.exports = { isOwnerOnlyPolicy, hasEveryoneAllow, policyBody, safeState };
