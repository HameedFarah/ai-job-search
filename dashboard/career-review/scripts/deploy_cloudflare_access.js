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
const CF_API = 'https://api.cloudflare.com/client/v4';

const HOSTNAME = 'career.farahdigital.com';
const ROUTE_PATTERN = `${HOSTNAME}/*`;
const WORKER_NAME = 'career-engine-private';
const ACCESS_APP_NAME = 'Career Engine';
const ACCESS_POLICY_NAME = 'Career Engine owner only';
const ACCESS_SERVICE_TOKEN_NAME = 'Career Engine VPS automation';
const ACCESS_SERVICE_POLICY_NAME = 'Career Engine automation service';
const ACCESS_SERVICE_TOKEN_DURATION = '8760h';
const ACCESS_SERVICE_CREDENTIALS = path.join(RUNTIME, 'cloudflare-access-service-token.json');
const OWNER_EMAIL = 'hameedo@gmail.com';

function die(message) {
  throw new Error(message);
}

function envValue(name) {
  const value = String(process.env[name] || '').trim();
  if (!value) die(`${name} is not configured`);
  return value;
}

function auth() {
  return {
    accountId: envValue('CLOUDFLARE_ACCOUNT_ID'),
    zoneId: envValue('CLOUDFLARE_ZONE_ID'),
    token: envValue('CLOUDFLARE_API_TOKEN'),
  };
}

async function api(method, pathname, body) {
  const { token } = auth();
  const response = await fetch(`${CF_API}${pathname}`, {
    method,
    headers: {
      Authorization: `Bearer ${token}`,
      ...(body === undefined ? {} : { 'content-type': 'application/json' }),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const raw = await response.text();
  let payload = {};
  try { payload = raw ? JSON.parse(raw) : {}; }
  catch { die(`Cloudflare ${method} ${pathname} returned non-JSON HTTP ${response.status}`); }
  if (!response.ok || payload.success === false) {
    const messages = Array.isArray(payload.errors)
      ? payload.errors.map(item => item.message || item.code).filter(Boolean)
      : [];
    die(`Cloudflare ${method} ${pathname} failed (HTTP ${response.status}): ${messages.join('; ') || 'request rejected'}`);
  }
  return payload.result;
}

function ownerPolicyBody() {
  return {
    name: ACCESS_POLICY_NAME,
    decision: 'allow',
    precedence: 1,
    include: [{ email: { email: OWNER_EMAIL } }],
    exclude: [],
    require: [],
    session_duration: '24h',
  };
}

function servicePolicyBody(tokenId) {
  if (!String(tokenId || '').trim()) die('service token ID is required');
  return {
    name: ACCESS_SERVICE_POLICY_NAME,
    decision: 'non_identity',
    precedence: 2,
    include: [{ service_token: { token_id: tokenId } }],
    exclude: [],
    require: [],
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

function isServiceAuthPolicy(policy, tokenId = '') {
  if (!policy || policy.decision !== 'non_identity') return false;
  const include = Array.isArray(policy.include) ? policy.include : [];
  const exclude = Array.isArray(policy.exclude) ? policy.exclude : [];
  const requireRules = Array.isArray(policy.require) ? policy.require : [];
  const actualTokenId = String(include[0]?.service_token?.token_id || '').trim();
  return include.length === 1
    && Boolean(actualTokenId)
    && (!tokenId || actualTokenId === tokenId)
    && exclude.length === 0
    && requireRules.length === 0;
}

function isManagedPolicySet(policies, { requireService = false } = {}) {
  const list = Array.isArray(policies) ? policies : [];
  const owners = list.filter(policy => policy.name === ACCESS_POLICY_NAME);
  const services = list.filter(policy => policy.name === ACCESS_SERVICE_POLICY_NAME);
  const unmanaged = list.filter(policy => ![ACCESS_POLICY_NAME, ACCESS_SERVICE_POLICY_NAME].includes(policy.name));
  if (owners.length !== 1 || !isOwnerOnlyPolicy(owners[0])) return false;
  if (unmanaged.length !== 0 || services.length > 1) return false;
  if (services.length === 1 && !isServiceAuthPolicy(services[0])) return false;
  return requireService ? services.length === 1 : true;
}

function safePolicy(policy) {
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

function safeState(state) {
  return {
    dns: state.dns ? {
      id: state.dns.id || '',
      type: state.dns.type || '',
      name: state.dns.name || '',
      content: state.dns.content || '',
      proxied: Boolean(state.dns.proxied),
    } : null,
    access_app: state.app ? {
      id: state.app.id || '',
      name: state.app.name || '',
      domain: state.app.domain || '',
      type: state.app.type || '',
      session_duration: state.app.session_duration || '',
      aud: state.app.aud || '',
    } : null,
    access_policies: (state.policies || []).map(safePolicy),
    worker_route: state.route ? {
      id: state.route.id || '',
      pattern: state.route.pattern || '',
      script: state.route.script || '',
    } : null,
    worker: state.worker ? {
      id: state.worker.id || '',
      modified_on: state.worker.modified_on || '',
    } : null,
    worker_subdomain: state.subdomain ? {
      enabled: Boolean(state.subdomain.enabled),
      previews_enabled: Boolean(state.subdomain.previews_enabled),
    } : null,
  };
}

async function inspect({ requireProxied = true } = {}) {
  const { accountId, zoneId } = auth();

  const dnsRows = await api('GET', `/zones/${zoneId}/dns_records?name=${encodeURIComponent(HOSTNAME)}`);
  const dnsList = Array.isArray(dnsRows) ? dnsRows : [];
  if (dnsList.length !== 1) die(`Expected exactly one DNS record for ${HOSTNAME}; found ${dnsList.length}`);
  const dns = dnsList[0];
  if (requireProxied && !dns.proxied) die(`${HOSTNAME} is not Cloudflare-proxied; a Worker route would not be safe or effective`);

  const appsRows = await api('GET', `/accounts/${accountId}/access/apps`);
  const apps = Array.isArray(appsRows) ? appsRows : [];
  const matches = apps.filter(app => String(app.domain || '').replace(/\/$/, '') === HOSTNAME);
  if (matches.length > 1) die(`Multiple Access applications target ${HOSTNAME}`);
  const app = matches[0] || null;
  const policiesRows = app
    ? await api('GET', `/accounts/${accountId}/access/apps/${app.id}/policies`)
    : [];
  const policies = Array.isArray(policiesRows) ? policiesRows : [];

  const routeRows = await api('GET', `/zones/${zoneId}/workers/routes`);
  const routeMatches = (Array.isArray(routeRows) ? routeRows : []).filter(route => route.pattern === ROUTE_PATTERN);
  if (routeMatches.length > 1) die(`Multiple Worker routes match ${ROUTE_PATTERN}`);
  const route = routeMatches[0] || null;

  const scriptRows = await api('GET', `/accounts/${accountId}/workers/scripts`);
  const worker = (Array.isArray(scriptRows) ? scriptRows : []).find(row => row.id === WORKER_NAME) || null;
  const subdomain = worker
    ? await api('GET', `/accounts/${accountId}/workers/scripts/${WORKER_NAME}/subdomain`)
    : null;

  return { dns, app, policies, route, worker, subdomain };
}

function writeBackup(state) {
  fs.mkdirSync(RUNTIME, { recursive: true });
  const stamp = new Date().toISOString().replace(/[:.]/g, '-');
  const target = path.join(RUNTIME, `cloudflare-access-backup-${stamp}.json`);
  fs.writeFileSync(target, `${JSON.stringify({
    schema_version: 1,
    created_at: new Date().toISOString(),
    hostname: HOSTNAME,
    worker_name: WORKER_NAME,
    previous: safeState(state),
  }, null, 2)}\n`, { mode: 0o600 });
  return target;
}

function loadServiceCredentials() {
  if (!fs.existsSync(ACCESS_SERVICE_CREDENTIALS)) return null;
  const stat = fs.statSync(ACCESS_SERVICE_CREDENTIALS);
  if ((stat.mode & 0o077) !== 0) die('Cloudflare Access service credential file permissions are too broad');
  const parsed = JSON.parse(fs.readFileSync(ACCESS_SERVICE_CREDENTIALS, 'utf8'));
  const serviceTokenId = String(parsed.service_token_id || '').trim();
  const clientId = String(parsed.client_id || '').trim();
  const clientSecret = String(parsed.client_secret || '').trim();
  if (!serviceTokenId || !clientId || !clientSecret) die('Cloudflare Access service credential file is incomplete');
  return { serviceTokenId, clientId, clientSecret };
}

function writeServiceCredentials(token) {
  const serviceTokenId = String(token?.id || '').trim();
  const clientId = String(token?.client_id || '').trim();
  const clientSecret = String(token?.client_secret || '').trim();
  if (!serviceTokenId || !clientId || !clientSecret) die('Cloudflare did not return complete service-token credentials');
  fs.mkdirSync(RUNTIME, { recursive: true });
  fs.writeFileSync(ACCESS_SERVICE_CREDENTIALS, `${JSON.stringify({
    schema_version: 1,
    created_at: new Date().toISOString(),
    hostname: HOSTNAME,
    service_token_id: serviceTokenId,
    client_id: clientId,
    client_secret: clientSecret,
  }, null, 2)}\n`, { mode: 0o600 });
  fs.chmodSync(ACCESS_SERVICE_CREDENTIALS, 0o600);
  return { serviceTokenId, clientId, clientSecret };
}

function removeServiceCredentials() {
  try { fs.rmSync(ACCESS_SERVICE_CREDENTIALS, { force: true }); }
  catch { /* best-effort cleanup only */ }
}

async function ensureServiceToken() {
  const { accountId } = auth();
  const tokenRows = await api('GET', `/accounts/${accountId}/access/service_tokens`);
  const matches = (Array.isArray(tokenRows) ? tokenRows : []).filter(token => token.name === ACCESS_SERVICE_TOKEN_NAME);
  if (matches.length > 1) die(`Multiple service tokens named ${ACCESS_SERVICE_TOKEN_NAME}`);

  if (matches.length === 0) {
    const created = await api('POST', `/accounts/${accountId}/access/service_tokens`, {
      name: ACCESS_SERVICE_TOKEN_NAME,
      duration: ACCESS_SERVICE_TOKEN_DURATION,
    });
    return { token: created, credentials: writeServiceCredentials(created), created: true, rotated: false };
  }

  const token = matches[0];
  const local = loadServiceCredentials();
  if (local && local.serviceTokenId === token.id && local.clientId === token.client_id) {
    return { token, credentials: local, created: false, rotated: false };
  }

  const rotated = await api('POST', `/accounts/${accountId}/access/service_tokens/${token.id}/rotate`, {});
  return { token: rotated, credentials: writeServiceCredentials(rotated), created: false, rotated: true };
}

async function ensureServicePolicy(app, policies, tokenId) {
  const { accountId } = auth();
  if (!app?.id) die('Access application is missing');
  if (!isManagedPolicySet(policies)) {
    die(`Existing Access application for ${HOSTNAME} has unmanaged policies; refusing service-auth mutation`);
  }
  const existing = (policies || []).find(policy => policy.name === ACCESS_SERVICE_POLICY_NAME);
  if (existing) {
    if (!isServiceAuthPolicy(existing, tokenId)) {
      die('Existing Career Engine automation service policy targets an unexpected service token');
    }
    return { policy: existing, created: false };
  }
  const policy = await api('POST', `/accounts/${accountId}/access/apps/${app.id}/policies`, servicePolicyBody(tokenId));
  if (!isServiceAuthPolicy(policy, tokenId)) die('Created service-auth policy did not round-trip safely');
  return { policy, created: true };
}

function run(command, args, timeout = 180000) {
  execFileSync(command, args, {
    cwd: REPO,
    env: process.env,
    stdio: 'inherit',
    timeout,
  });
}

function prepareSite() {
  run('./career-engine', ['doctor']);
  run('./career-engine', ['bundle', 'status']);
  run('./career-engine', ['dashboard', '--sync']);
  run('node', [path.join(DASHBOARD, 'scripts', 'build_site.js')], 300000);
  if (!fs.existsSync(path.join(SITE, 'index.html'))) die('dashboard build did not produce index.html');
  fs.writeFileSync(path.join(SITE, '_headers'), [
    '/*',
    '  Cache-Control: no-store, max-age=0, must-revalidate',
    '  X-Robots-Tag: noindex, nofollow, noarchive',
    '  X-Content-Type-Options: nosniff',
    '  Referrer-Policy: no-referrer',
    '  Permissions-Policy: camera=(), microphone=(), geolocation=()',
    '',
  ].join('\n'), 'utf8');
}

async function ensureAccess(before) {
  const { accountId } = auth();
  let app = before.app;
  let created = false;

  if (!app) {
    app = await api('POST', `/accounts/${accountId}/access/apps`, {
      name: ACCESS_APP_NAME,
      domain: HOSTNAME,
      type: 'self_hosted',
      session_duration: '24h',
    });
    created = true;
  } else {
    if (app.type !== 'self_hosted') die(`Existing Access application for ${HOSTNAME} is not self_hosted`);
    const current = before.policies || [];
    if (!isManagedPolicySet(current)) {
      die(`Existing Access application for ${HOSTNAME} has unmanaged policies; refusing destructive replacement without a separate review`);
    }
    return { app, policy: current.find(item => item.name === ACCESS_POLICY_NAME), created: false };
  }

  try {
    const policy = await api('POST', `/accounts/${accountId}/access/apps/${app.id}/policies`, ownerPolicyBody());
    if (!isOwnerOnlyPolicy(policy)) die('Created Access policy did not round-trip as exact owner-only policy');
    return { app, policy, created };
  } catch (error) {
    if (created && app?.id) {
      try { await api('DELETE', `/accounts/${accountId}/access/apps/${app.id}`); }
      catch { /* original error remains primary */ }
    }
    throw error;
  }
}

function deployWorker() {
  run('npx', ['--yes', 'wrangler@4', 'deploy', '--config', CONFIG], 600000);
}

async function probeUnauthenticated() {
  const response = await fetch(`https://${HOSTNAME}/`, {
    redirect: 'manual',
    headers: { 'cache-control': 'no-cache' },
  });
  const body = await response.text();
  const leaked = /Career Application Review|Career Application Board|data\/jobs\.json/i.test(body);
  if (response.status === 200 || leaked) {
    die(`Unauthenticated request exposed dashboard content (HTTP ${response.status})`);
  }
  let locationHost = '';
  const location = response.headers.get('location');
  if (location) {
    try { locationHost = new URL(location, `https://${HOSTNAME}/`).hostname; }
    catch { locationHost = ''; }
  }
  return { status: response.status, location_host: locationHost, dashboard_content_exposed: leaked };
}

async function probeServiceAuth(credentials) {
  const response = await fetch(`https://${HOSTNAME}/`, {
    redirect: 'manual',
    headers: {
      'cache-control': 'no-cache',
      'CF-Access-Client-Id': credentials.clientId,
      'CF-Access-Client-Secret': credentials.clientSecret,
    },
  });
  const body = await response.text();
  if (response.status !== 200) {
    let locationHost = '';
    const location = response.headers.get('location');
    if (location) {
      try { locationHost = new URL(location, `https://${HOSTNAME}/`).hostname; }
      catch { locationHost = ''; }
    }
    die(`Service-authenticated request failed (HTTP ${response.status}${locationHost ? ` -> ${locationHost}` : ''})`);
  }
  return {
    status: response.status,
    dashboard_content_visible: /Career Application Review|Career Application Board/i.test(body),
  };
}

async function verify({ requireServiceAuth = false } = {}) {
  const state = await inspect();
  if (!state.app) die('Access application is missing');
  if (!isManagedPolicySet(state.policies, { requireService: requireServiceAuth })) {
    die(requireServiceAuth
      ? 'Access policies are not exactly the managed owner + automation service policies'
      : 'Access policies contain an unmanaged or unsafe policy');
  }
  if (!state.worker) die('Career Engine Worker is missing');
  if (!state.route || state.route.script !== WORKER_NAME) die(`Worker route ${ROUTE_PATTERN} is missing or targets another script`);
  if (!state.subdomain) die('Unable to prove Worker subdomain state');
  if (state.subdomain.enabled || state.subdomain.previews_enabled) {
    die('workers.dev or Worker preview URLs are enabled');
  }
  return { state: safeState(state), unauthenticated_probe: await probeUnauthenticated() };
}

async function verifyServiceAuth() {
  const credentials = loadServiceCredentials();
  if (!credentials) die('Cloudflare Access service credentials are not configured on this host');
  const acceptance = await verify({ requireServiceAuth: true });
  return {
    mode: 'service-auth-verify',
    ...acceptance,
    service_authenticated_probe: await probeServiceAuth(credentials),
    credentials_file: ACCESS_SERVICE_CREDENTIALS,
    credentials_permissions: '0600',
  };
}

async function rollbackFromSnapshot(backupPath) {
  const { accountId, zoneId } = auth();
  const absolute = path.isAbsolute(backupPath) ? backupPath : path.resolve(REPO, backupPath);
  const snapshot = JSON.parse(fs.readFileSync(absolute, 'utf8'));
  if (snapshot.hostname !== HOSTNAME || snapshot.worker_name !== WORKER_NAME) die('Rollback snapshot does not belong to this migration');
  const previous = snapshot.previous || {};

  const routeRows = await api('GET', `/zones/${zoneId}/workers/routes`);
  const currentRoute = (Array.isArray(routeRows) ? routeRows : []).find(route => route.pattern === ROUTE_PATTERN);
  if (previous.worker_route) {
    const body = { pattern: previous.worker_route.pattern, script: previous.worker_route.script || null };
    if (currentRoute) await api('PUT', `/zones/${zoneId}/workers/routes/${currentRoute.id}`, body);
    else await api('POST', `/zones/${zoneId}/workers/routes`, body);
  } else if (currentRoute) {
    await api('DELETE', `/zones/${zoneId}/workers/routes/${currentRoute.id}`);
  }

  const appRows = await api('GET', `/accounts/${accountId}/access/apps`);
  const currentApp = (Array.isArray(appRows) ? appRows : []).find(app => String(app.domain || '').replace(/\/$/, '') === HOSTNAME) || null;
  if (!previous.access_app && currentApp) {
    await api('DELETE', `/accounts/${accountId}/access/apps/${currentApp.id}`);
  } else if (previous.access_app) {
    if (!currentApp || currentApp.id !== previous.access_app.id) die('Access application identity changed; refusing ambiguous rollback');
    const nowPolicies = await api('GET', `/accounts/${accountId}/access/apps/${currentApp.id}/policies`);
    for (const policy of Array.isArray(nowPolicies) ? nowPolicies : []) {
      await api('DELETE', `/accounts/${accountId}/access/apps/${currentApp.id}/policies/${policy.id}`);
    }
    for (const saved of previous.access_policies || []) {
      const body = {
        name: saved.name,
        decision: saved.decision,
        include: saved.include || [],
        exclude: saved.exclude || [],
        require: saved.require || [],
        ...(saved.precedence === null || saved.precedence === undefined ? {} : { precedence: saved.precedence }),
        ...(saved.session_duration ? { session_duration: saved.session_duration } : {}),
      };
      await api('POST', `/accounts/${accountId}/access/apps/${currentApp.id}/policies`, body);
    }
  }

  if (previous.dns) {
    const dnsRows = await api('GET', `/zones/${zoneId}/dns_records?name=${encodeURIComponent(HOSTNAME)}`);
    const dnsList = Array.isArray(dnsRows) ? dnsRows : [];
    if (dnsList.length !== 1 || dnsList[0].id !== previous.dns.id) {
      die('DNS record identity changed; refusing ambiguous rollback');
    }
    if (Boolean(dnsList[0].proxied) !== Boolean(previous.dns.proxied)) {
      await api('PATCH', `/zones/${zoneId}/dns_records/${previous.dns.id}`, { proxied: Boolean(previous.dns.proxied) });
    }
  }

  return { mode: 'rollback', backup: absolute, restored: safeState(await inspect({ requireProxied: false })) };
}

async function preflight() {
  const state = await inspect({ requireProxied: false });
  return {
    mode: 'preflight',
    hostname: HOSTNAME,
    worker_name: WORKER_NAME,
    credentials_present: true,
    mutation_performed: false,
    dns_proxy_ready: Boolean(state.dns?.proxied),
    state: safeState(state),
    existing_access_safe: !state.app || isManagedPolicySet(state.policies),
  };
}

async function setupServiceAuth() {
  const { accountId } = auth();
  const before = await inspect();
  if (!before.app) die('Career Engine Access application must exist before service auth is configured');
  if (!isManagedPolicySet(before.policies)) {
    die('Career Engine Access application contains unmanaged policies; refusing service-auth setup');
  }
  const backup = writeBackup(before);
  let serviceToken;

  try {
    serviceToken = await ensureServiceToken();
    const policy = await ensureServicePolicy(before.app, before.policies, serviceToken.token.id);
    const acceptance = await verifyServiceAuth();
    return {
      mode: 'service-auth-setup',
      backup,
      service_token_name: ACCESS_SERVICE_TOKEN_NAME,
      service_token_id: serviceToken.token.id,
      service_token_created: serviceToken.created,
      service_token_rotated: serviceToken.rotated,
      service_policy_id: policy.policy.id,
      service_policy_created: policy.created,
      credentials_file: ACCESS_SERVICE_CREDENTIALS,
      credentials_permissions: '0600',
      ...acceptance,
    };
  } catch (error) {
    try { await rollbackFromSnapshot(backup); }
    catch (rollbackError) {
      throw new Error(`${error.message}; automatic Access-policy rollback also failed: ${rollbackError.message}`);
    }
    if (serviceToken?.created && serviceToken?.token?.id) {
      try { await api('DELETE', `/accounts/${accountId}/access/service_tokens/${serviceToken.token.id}`); }
      catch { /* original error remains primary */ }
      removeServiceCredentials();
    }
    throw error;
  }
}

async function deploy() {
  const before = await inspect({ requireProxied: false });
  const backup = writeBackup(before);
  prepareSite();

  try {
    if (!before.dns.proxied) {
      const { zoneId } = auth();
      await api('PATCH', `/zones/${zoneId}/dns_records/${before.dns.id}`, { proxied: true });
    }
    const ready = await inspect();
    const access = await ensureAccess(ready);
    deployWorker();
    const acceptance = await verify();
    return {
      mode: 'deploy',
      backup,
      dns_proxy_changed: !before.dns.proxied,
      access_app_id: access.app.id,
      access_policy_id: access.policy.id,
      ...acceptance,
    };
  } catch (error) {
    try { await rollbackFromSnapshot(backup); }
    catch (rollbackError) {
      throw new Error(`${error.message}; automatic rollback also failed: ${rollbackError.message}`);
    }
    throw error;
  }
}

function selfTest() {
  const owner = ownerPolicyBody();
  const service = servicePolicyBody('svc-test');
  const everyone = { ...ownerPolicyBody(), include: [{ everyone: {} }] };
  const other = { ...ownerPolicyBody(), include: [{ email: { email: 'other@example.com' } }] };
  const wrongService = { ...servicePolicyBody('svc-test'), include: [{ service_token: { token_id: 'other-service' } }] };
  if (!isOwnerOnlyPolicy(owner)) die('self-test rejected the canonical owner policy');
  if (isOwnerOnlyPolicy(everyone)) die('self-test accepted Everyone');
  if (isOwnerOnlyPolicy(other)) die('self-test accepted the wrong email');
  if (!isServiceAuthPolicy(service, 'svc-test')) die('self-test rejected the canonical service policy');
  if (isServiceAuthPolicy(wrongService, 'svc-test')) die('self-test accepted the wrong service token');
  if (!isManagedPolicySet([owner])) die('self-test rejected owner-only managed policy set');
  if (!isManagedPolicySet([owner, service], { requireService: true })) die('self-test rejected owner + service managed policy set');
  if (isManagedPolicySet([owner, { ...service, name: 'unmanaged' }])) die('self-test accepted unmanaged policy');
  return {
    mode: 'self-test',
    passed: true,
    worker_name: WORKER_NAME,
    owner_email: OWNER_EMAIL,
    service_token_name: ACCESS_SERVICE_TOKEN_NAME,
  };
}

async function main() {
  const [mode, argument] = process.argv.slice(2);
  let result;
  if (mode === '--self-test') result = selfTest();
  else if (mode === '--preflight') result = await preflight();
  else if (mode === '--deploy') result = await deploy();
  else if (mode === '--verify') result = await verify();
  else if (mode === '--service-auth-setup') result = await setupServiceAuth();
  else if (mode === '--service-auth-verify') result = await verifyServiceAuth();
  else if (mode === '--rollback') result = await rollbackFromSnapshot(argument);
  else die('Usage: deploy_cloudflare_access.js --self-test|--preflight|--deploy|--verify|--service-auth-setup|--service-auth-verify|--rollback <backup.json>');
  console.log(JSON.stringify(result, null, 2));
}

if (require.main === module) {
  main().catch(error => {
    console.error(`ERROR: ${error.message}`);
    process.exit(1);
  });
}

module.exports = {
  ownerPolicyBody,
  servicePolicyBody,
  isOwnerOnlyPolicy,
  isServiceAuthPolicy,
  isManagedPolicySet,
  safeState,
};
