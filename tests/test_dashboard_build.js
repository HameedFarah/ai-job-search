'use strict';

const assert = require('assert');
const { mergeUniqueRoles, isFixtureJob } = require('../dashboard/career-review/scripts/build_site.js');

const tracker = [{ key: 'tracker-1', job_id: 'job-1', company: 'Current Co', role: 'Current role', score: 91 }];
const stalePrepared = [{ key: 'old-1', job_id: 'job-1', company: 'Stale Co', role: 'Old role', score: 20 }];
const manualOnly = [{ key: 'manual-1', company: 'Manual Co', role: 'Manual role', location: 'Riyadh' }];

const merged = mergeUniqueRoles(stalePrepared, tracker, manualOnly);
assert.strictEqual(merged[0].company, 'Current Co', 'canonical tracker row must win over stale prepared data');
assert.strictEqual(merged.length, 1, 'dashboard roles must come exclusively from CareerTracker');
assert.strictEqual(merged.some(row => row.company === 'Stale Co'), false, 'stale duplicate must be suppressed');
assert.strictEqual(merged.some(row => row.company === 'Manual Co'), false, 'legacy manual-only rows must not become dashboard authorities');

assert.strictEqual(isFixtureJob({
  source_url: 'https://kbr.wd5.myworkdayjobs.com/job/Riyadh-Riyadh-Saudi-Arabia/Furniture-Fixtures-and-Equipment-Coordinator---Saudi-National_R2128415-1',
  company: 'kbr',
  role: 'Furniture Fixtures and Equipment Coordinator - Saudi National',
  external_job_id: 'R2128415-1'
}), false, 'legitimate URLs containing the word Fixtures must not be treated as test fixtures');
assert.strictEqual(isFixtureJob({ source_url: 'https://careers.acme.com/fixture/design-manager', company: 'Acme', role: 'Design Manager' }), true, 'explicit fixture URL tokens must still be filtered');

console.log('dashboard build authority regression tests passed');
