'use strict';

/* Hermetic regression tests for metadata-first current-artifact selection in
   build_site.js. All fixtures live in an os.tmpdir directory; the live
   CareerTracker data, logs, runtime and site outputs are never read or
   written (copyArtifact targets are removed again during cleanup). */

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const crypto = require('crypto');

const {
  findGeneratedDocsIn,
  currentArtifactsFromMetadata,
  preferArtifactEntry,
  artifactSlotFor,
  findLatestSubmission
} = require('../dashboard/career-review/scripts/build_site.js');

const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'career-artifact-selection-'));
const artifactsRoot = path.join(tmpRoot, 'artifacts');
const jobsRoot = path.join(tmpRoot, 'data', 'jobs');
fs.mkdirSync(artifactsRoot, { recursive: true });
fs.mkdirSync(jobsRoot, { recursive: true });

const createdSiteKeys = [];
function writeArtifact(jobId, name, contents) {
  const dir = path.join(artifactsRoot, jobId);
  fs.mkdirSync(dir, { recursive: true });
  const file = path.join(dir, name);
  fs.writeFileSync(file, contents);
  return file;
}

function entry(type, variant, file, extra) {
  return Object.assign({
    type,
    variant,
    path: file,
    // Deliberately wrong: published SHA must be recomputed from disk bytes.
    sha256: 'stored-value-must-not-be-trusted',
    bundle_hash: 'bundle-1'
  }, extra || {});
}

// --- Job 1: full metadata set; sidebar v1.5 revision must beat base --------
const job1 = 'aaaaaaaaaaaaaaaaaaaa';
const basePdf = writeArtifact(job1, 'Base_Sidebar.pdf', 'base-pdf-bytes');
const baseDocx = writeArtifact(job1, 'Base_Sidebar.docx', 'base-docx-bytes');
const rev15Pdf = writeArtifact(job1, 'Sidebar_v1.5.pdf', 'revision-v15-pdf-bytes');
const rev15Docx = writeArtifact(job1, 'Sidebar_v1.5.docx', 'revision-v15-docx-bytes');
const atsPdf = writeArtifact(job1, 'AtsLinear.pdf', 'ats-pdf-bytes');
const atsDocx = writeArtifact(job1, 'AtsLinear.docx', 'ats-docx-bytes');
const coverOldPdf = writeArtifact(job1, 'Letter_Old.pdf', 'cover-old-bytes');
const coverNewPdf = writeArtifact(job1, 'Letter_New.pdf', 'cover-new-bytes');
const coverNewDocx = writeArtifact(job1, 'Letter_New.docx', 'cover-new-docx-bytes');

fs.writeFileSync(path.join(jobsRoot, `${job1}.json`), JSON.stringify({
  job: { job_id: job1 },
  generated_artifacts: [
    entry('final_pdf', 'modern-executive-sidebar', basePdf),
    entry('final_docx', 'modern-executive-sidebar', baseDocx),
    entry('ats_pdf', 'ats-linear', atsPdf),
    entry('ats_docx', 'ats-linear', atsDocx),
    entry('cover_letter_pdf', '', coverOldPdf),
    // Current v1.5 revision appended later by regenerate_sidebar_template.
    entry('final_pdf', 'modern-executive-sidebar', rev15Pdf, { template_version: '1.5' }),
    entry('final_docx', 'modern-executive-sidebar', rev15Docx, { template_version: '1.5' }),
    entry('cover_letter_pdf', '', coverNewPdf),
    entry('cover_letter_docx', '', coverNewDocx)
  ]
}));

createdSiteKeys.push(`tracker-${job1}`);
const docs1 = findGeneratedDocsIn(job1, { artifactsRoot, jobsRoot });
assert.strictEqual(path.basename(docs1.resume.pdf), 'Sidebar_v1.5.pdf',
  'metadata-first selection must pick the appended v1.5 revision over the unversioned base render');
assert.strictEqual(path.basename(docs1.resume.docx), 'Sidebar_v1.5.docx',
  'sidebar DOCX selection must follow the same logical-revision rule');
assert.strictEqual(docs1.resume.sha256,
  crypto.createHash('sha256').update('revision-v15-pdf-bytes').digest('hex'),
  'published SHA256 must be recomputed from disk bytes, never taken from stored metadata');
assert.notStrictEqual(docs1.resume.sha256, 'stored-value-must-not-be-trusted');
assert.strictEqual(path.basename(docs1.resume_ats.pdf), 'AtsLinear.pdf',
  'ATS slot semantics must stay unchanged under metadata-first selection');
assert.strictEqual(path.basename(docs1.resume_ats.docx), 'AtsLinear.docx');
assert.strictEqual(path.basename(docs1.cover_letter.pdf), 'Letter_New.pdf',
  'cover-letter slot must select the latest appended metadata entry');
assert.strictEqual(path.basename(docs1.cover_letter.docx), 'Letter_New.docx');

// --- Pure selection layer --------------------------------------------------
const mixed = currentArtifactsFromMetadata({
  generated_artifacts: [
    entry('final_pdf', 'modern-executive-sidebar', '/nonexistent/stale.pdf'),
    entry('final_pdf', 'modern-executive-sidebar', rev15Pdf, { template_version: '1.5' })
  ]
});
assert.strictEqual(mixed.resume.pdf, rev15Pdf,
  'entries whose paths do not exist must be skipped (existence validation)');
assert.deepStrictEqual(currentArtifactsFromMetadata({}), {
  resume: { pdf: '', docx: '' },
  resume_ats: { pdf: '', docx: '' },
  cover_letter: { pdf: '', docx: '' }
}, 'records without generated_artifacts must yield empty selections');
assert.strictEqual(currentArtifactsFromMetadata(null).resume.pdf, '');

assert.strictEqual(preferArtifactEntry(
  { template_version: '1.5' }, {}
), true, 'versioned current revision must beat unversioned base');
assert.strictEqual(preferArtifactEntry(
  { template_version: '1.5' }, { template_version: '1.6' }
), false, 'lower versions must never displace higher ones');
assert.strictEqual(preferArtifactEntry({ template_version: 'junk' }, {}), true,
  'unparsable versions rank lowest, so append order decides');

// Slot mapping sanity (legacy alias included).
assert.deepStrictEqual(artifactSlotFor({ type: 'final_pdf', variant: 'modern-executive-sidebar' }), ['resume', 'pdf']);
assert.deepStrictEqual(artifactSlotFor({ type: 'ats_docx', variant: 'ats-linear' }), ['resume_ats', 'docx']);
assert.deepStrictEqual(artifactSlotFor({ type: 'cover_letter_pdf', variant: '' }), ['cover_letter', 'pdf']);
assert.strictEqual(artifactSlotFor({ type: 'final_pdf', variant: 'unknown-template' }), null,
  'unknown variants must fall through to the legacy scan instead of a wrong slot');

// --- Job 2: stale-only metadata falls back to exact legacy directory scan --
// DOCX-only fixtures keep the test independent of pdftotext availability;
// legacy first-match naming semantics are what the fallback must preserve.
const job2 = 'bbbbbbbbbbbbbbbbbbbb';
writeArtifact(job2, 'Abdelhamid_Farah_CV.docx', 'legacy-executive-docx');
writeArtifact(job2, 'Abdelhamid_Farah_CV_ATS.docx', 'legacy-ats-docx');
writeArtifact(job2, 'Abdelhamid_Farah_Cover_Letter.docx', 'legacy-cover-docx');
fs.writeFileSync(path.join(jobsRoot, `${job2}.json`), JSON.stringify({
  job: { job_id: job2 },
  generated_artifacts: [entry('final_pdf', 'modern-executive-sidebar', '/gone/v15.pdf')]
}));
createdSiteKeys.push(`tracker-${job2}`);
const docs2 = findGeneratedDocsIn(job2, { artifactsRoot, jobsRoot });
assert.strictEqual(path.basename(docs2.resume.docx), 'Abdelhamid_Farah_CV.docx',
  'with unusable metadata the exact legacy directory-scan fallback must apply');
assert.strictEqual(path.basename(docs2.resume_ats.docx), 'Abdelhamid_Farah_CV_ATS.docx',
  'legacy ATS scan semantics must be preserved exactly');
assert.strictEqual(docs2.resume.pdf, '',
  'no legacy PDF exists, so the PDF field stays empty');

// --- Job 3: no metadata section at all -> purely legacy behaviour ----------
const job3 = 'cccccccccccccccccccc';
writeArtifact(job3, 'Abdelhamid_Farah_CV.docx', 'only-docx');
fs.writeFileSync(path.join(jobsRoot, `${job3}.json`), JSON.stringify({ job: { job_id: job3 } }));
createdSiteKeys.push(`tracker-${job3}`);
const docs3 = findGeneratedDocsIn(job3, { artifactsRoot, jobsRoot });
assert.strictEqual(path.basename(docs3.resume.docx), 'Abdelhamid_Farah_CV.docx');

// --- Untouched surfaces ----------------------------------------------------
assert.strictEqual(findLatestSubmission(''), null);
assert.strictEqual(findLatestSubmission('no-such-job-id-in-live-data'), null,
  'findLatestSubmission keeps its contract and must stay untouched');

// --- Cleanup of worktree-local copy targets --------------------------------
const siteFiles = path.join(path.resolve(__dirname, '..'), 'dashboard/career-review/site/files');
for (const key of createdSiteKeys) {
  fs.rmSync(path.join(siteFiles, key), { recursive: true, force: true });
}
fs.rmSync(tmpRoot, { recursive: true, force: true });

console.log('dashboard artifact selection tests passed');
