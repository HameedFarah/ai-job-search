'use strict';

const assert = require('assert');
const { mergeUniqueRoles } = require('../dashboard/career-review/scripts/build_site.js');

const tracker = [{ key: 'tracker-1', job_id: 'job-1', company: 'Current Co', role: 'Current role', score: 91 }];
const stalePrepared = [{ key: 'old-1', job_id: 'job-1', company: 'Stale Co', role: 'Old role', score: 20 }];
const manualOnly = [{ key: 'manual-1', company: 'Manual Co', role: 'Manual role', location: 'Riyadh' }];

const merged = mergeUniqueRoles(stalePrepared, tracker, manualOnly);
assert.strictEqual(merged[0].company, 'Current Co', 'canonical tracker row must win over stale prepared data');
assert.strictEqual(merged.length, 1, 'dashboard roles must come exclusively from CareerTracker');
assert.strictEqual(merged.some(row => row.company === 'Stale Co'), false, 'stale duplicate must be suppressed');
assert.strictEqual(merged.some(row => row.company === 'Manual Co'), false, 'legacy manual-only rows must not become dashboard authorities');

console.log('dashboard build authority regression tests passed');
