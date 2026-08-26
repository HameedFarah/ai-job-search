'use strict';

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { trackerPaths } = require('../dashboard/career-review/scripts/tracker_base');

const root = fs.mkdtempSync(path.join(os.tmpdir(), 'dashboard-authority-'));
const live = fs.mkdtempSync(path.join(os.tmpdir(), 'career-tracker-live-'));
fs.mkdirSync(path.join(root, 'projects/job-automation'), { recursive: true });
fs.mkdirSync(path.join(live, 'data/jobs'), { recursive: true });
fs.mkdirSync(path.join(live, 'artifacts'), { recursive: true });
fs.mkdirSync(path.join(live, 'runtime/review-diffs'), { recursive: true });
fs.writeFileSync(path.join(root, 'runtime-authority.json'), '{}');
fs.mkdirSync(path.join(root, 'runtime'), { recursive: true });
fs.writeFileSync(path.join(root, 'runtime/runtime-authority.json'), JSON.stringify({ schema_version: 1, tracker_base: live }));

const old = process.env.CAREER_ENGINE_TRACKER_BASE;
delete process.env.CAREER_ENGINE_TRACKER_BASE;
let paths = trackerPaths(root);
assert.strictEqual(paths.base, live, 'clean runtime must bind to live tracker pointer');
assert.strictEqual(paths.jobs, path.join(live, 'data/jobs'));
assert.strictEqual(paths.manifest, path.join(live, 'artifacts/five-applications-2026-08-04.json'));
assert.strictEqual(paths.canonicalSummary, path.join(live, 'runtime/canonical-tracker-summary.json'));

fs.writeFileSync(path.join(root, 'runtime/runtime-authority.json'), JSON.stringify({ schema_version: 1, tracker_base: '/does/not/exist' }));
assert.strictEqual(trackerPaths(root).base, path.join(root, 'projects/job-automation'), 'invalid pointer must use checkout-local fallback');

const explicit = fs.mkdtempSync(path.join(os.tmpdir(), 'career-tracker-explicit-'));
process.env.CAREER_ENGINE_TRACKER_BASE = explicit;
assert.strictEqual(trackerPaths(root).base, explicit, 'explicit tracker base must take precedence');
if (old === undefined) delete process.env.CAREER_ENGINE_TRACKER_BASE; else process.env.CAREER_ENGINE_TRACKER_BASE = old;
console.log('dashboard tracker authority regression tests passed');
