'use strict';

const HERE_API = 'https://here.now/api/v1';
const SITE_SLUG = 'gilded-timber-xfj7';
const OWNER_EMAIL = 'hameedo@gmail.com';
const DATA_PREFIX = '/.herenow/data/';
const ALLOWED_COLLECTIONS = new Set(['workflow', 'comments', 'history', 'ai_requests', 'preferences']);
const ALLOWED_METHODS = new Set(['GET', 'POST', 'PATCH', 'DELETE']);

function jsonError(status, message) {
  return new Response(JSON.stringify({ error: message }), {
    status,
    headers: {
      'content-type': 'application/json; charset=utf-8',
      'cache-control': 'no-store'
    }
  });
}

function siteDataTarget(url) {
  if (!url.pathname.startsWith(DATA_PREFIX)) return null;
  const suffix = url.pathname.slice(DATA_PREFIX.length);
  const segments = suffix.split('/').filter(Boolean);
  if (segments.length < 1 || segments.length > 2) return null;
  const [collection, recordId = ''] = segments;
  if (!ALLOWED_COLLECTIONS.has(collection)) return null;
  if (recordId && !/^rec_[A-Za-z0-9]+$/.test(recordId)) return null;
  const target = new URL(`${HERE_API}/publishes/${SITE_SLUG}/data/${encodeURIComponent(collection)}${recordId ? `/${encodeURIComponent(recordId)}` : ''}`);
  target.search = url.search;
  return target;
}

async function isAuthorizedAccess(request, ctx) {
  const owner = String(request.headers.get('cf-access-authenticated-user-email') || '').trim().toLowerCase();
  if (owner === OWNER_EMAIL) return true;
  if (!ctx?.access) return false;
  try {
    const identity = await ctx.access.getIdentity();
    const email = String(identity?.email || '').trim().toLowerCase();
    if (email === OWNER_EMAIL) return true;
    return identity?.service_token_status === true
      && Boolean(String(identity?.service_token_id || '').trim());
  } catch {
    return false;
  }
}

async function proxySiteData(request, env, ctx) {
  if (!await isAuthorizedAccess(request, ctx)) {
    return jsonError(403, 'Owner or approved Cloudflare Access service token required');
  }
  if (!env.HERENOW_API_KEY) return jsonError(503, 'Site Data proxy is not configured');
  if (!ALLOWED_METHODS.has(request.method)) return jsonError(405, 'Method not allowed');

  const target = siteDataTarget(new URL(request.url));
  if (!target) return jsonError(404, 'Site Data collection not found');

  const headers = new Headers({
    Authorization: `Bearer ${env.HERENOW_API_KEY}`,
    'X-HereNow-Client': 'career-private-worker'
  });
  const contentType = request.headers.get('content-type');
  const idempotencyKey = request.headers.get('idempotency-key');
  if (contentType) headers.set('content-type', contentType);
  if (idempotencyKey) headers.set('Idempotency-Key', idempotencyKey);

  const body = request.method === 'GET' || request.method === 'DELETE'
    ? undefined
    : await request.arrayBuffer();
  const upstream = await fetch(target, {
    method: request.method,
    headers,
    body,
    redirect: 'manual'
  });

  const responseHeaders = new Headers();
  const upstreamContentType = upstream.headers.get('content-type');
  if (upstreamContentType) responseHeaders.set('content-type', upstreamContentType);
  responseHeaders.set('cache-control', 'no-store');
  responseHeaders.set('x-content-type-options', 'nosniff');
  return new Response(upstream.body, {
    status: upstream.status,
    headers: responseHeaders
  });
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (url.pathname.startsWith(DATA_PREFIX)) return proxySiteData(request, env, ctx);
    return env.ASSETS.fetch(request);
  }
};
