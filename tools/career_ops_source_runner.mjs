#!/usr/bin/env node
/**
 * Thin runtime bridge into the maintained Fighter90/career-ops-ui portal
 * adapter contract. This file deliberately does not reimplement ATS fetching.
 *
 * Usage:
 *   node tools/career_ops_source_runner.mjs <provider> '<company-json>' [checkout]
 *
 * The external checkout must be pinned by the Python wrapper before this runner
 * is called. It imports server/lib/portals/adapters/<provider>.mjs, discovers
 * the exported adapter object, builds its endpoint and calls its maintained
 * fetch implementation. JSON only on stdout; diagnostics/errors on stderr.
 */

import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const [provider, companyRaw, checkoutArg] = process.argv.slice(2);
if (!provider || !/^[a-z0-9-]+$/.test(provider)) {
  console.error('career_ops_source_runner: invalid provider');
  process.exit(2);
}

let company;
try {
  company = JSON.parse(companyRaw || '{}');
} catch (err) {
  console.error(`career_ops_source_runner: invalid company JSON: ${err.message}`);
  process.exit(2);
}
if (!company || typeof company !== 'object' || Array.isArray(company)) {
  console.error('career_ops_source_runner: company must be a JSON object');
  process.exit(2);
}

const checkout = checkoutArg || process.env.CAREER_OPS_UI_DIR || '/home/hameedo/projects/career-ops-ui';
const adapterPath = path.resolve(checkout, 'server', 'lib', 'portals', 'adapters', `${provider}.mjs`);
if (!fs.existsSync(adapterPath)) {
  console.error(`career_ops_source_runner: adapter not found: ${adapterPath}`);
  process.exit(3);
}

try {
  const mod = await import(pathToFileURL(adapterPath).href);
  const adapter = Object.values(mod).find(
    (value) => value
      && typeof value === 'object'
      && value.id === provider
      && typeof value.buildEndpoint === 'function'
      && typeof value.fetch === 'function',
  );
  if (!adapter) {
    throw new Error(`no adapter contract export found for ${provider}`);
  }
  const endpoint = adapter.buildEndpoint(company);
  if (!endpoint) {
    throw new Error(`provider ${provider} could not build an endpoint from the company entry`);
  }
  const jobs = await adapter.fetch(endpoint, { company });
  if (!Array.isArray(jobs)) {
    throw new Error(`provider ${provider} returned a non-array result`);
  }
  process.stdout.write(JSON.stringify({ provider, endpoint, jobs }));
} catch (err) {
  console.error(`career_ops_source_runner: ${err?.stack || err?.message || String(err)}`);
  process.exit(4);
}
