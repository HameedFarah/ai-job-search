const fs = require('fs');
const path = require('path');

const POINTER = ['runtime', 'runtime-authority.json'];

function resolveTrackerBase(repo) {
  const explicit = String(process.env.CAREER_ENGINE_TRACKER_BASE || '').trim();
  if (explicit) return path.resolve(explicit);

  const pointerPath = path.join(repo, ...POINTER);
  if (fs.existsSync(pointerPath)) {
    try {
      const pointer = JSON.parse(fs.readFileSync(pointerPath, 'utf8'));
      const target = String(pointer.tracker_base || '').trim();
      if (pointer.schema_version === 1 && target) {
        const resolved = path.resolve(target);
        if (fs.existsSync(resolved) && fs.statSync(resolved).isDirectory()) return resolved;
      }
    } catch (_) {
      // A bad or incomplete pointer must not strand a clean checkout.
    }
  }
  return path.resolve(repo, 'projects/job-automation');
}

function trackerPaths(repo) {
  const base = resolveTrackerBase(repo);
  const artifactOverride = String(process.env.CAREER_REVIEW_TRACKER_ARTIFACTS || '').trim();
  return {
    base,
    jobs: path.join(base, 'data', 'jobs'),
    artifacts: artifactOverride ? path.resolve(artifactOverride) : path.join(base, 'artifacts'),
    manifest: path.join(base, 'artifacts', 'five-applications-2026-08-04.json'),
    canonicalSummary: path.join(base, 'runtime', 'canonical-tracker-summary.json'),
    reviewInputs: path.join(base, 'runtime', 'review-diffs')
  };
}

module.exports = { resolveTrackerBase, trackerPaths };
