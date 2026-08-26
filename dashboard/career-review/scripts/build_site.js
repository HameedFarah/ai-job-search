const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { execFileSync } = require('child_process');
const { trackerPaths } = require('./tracker_base');

const ROOT = path.resolve(__dirname, '..');
const SITE = path.join(ROOT, 'site');
const REPO = path.resolve(ROOT, '../..');
const TRACKER = trackerPaths(REPO);
const MANIFEST = TRACKER.manifest;
const TRACKER_JOBS = TRACKER.jobs;
const TRACKER_ARTIFACTS = TRACKER.artifacts;
const REVIEWED = path.join(ROOT, 'data-reviewed.json');
const DATE_OVERRIDES = path.join(ROOT, 'job-date-overrides.json');

const ownerReview = {
  'mace-shopping-malls': {
    score: 62,
    decision: 'selective',
    brief: 'Credible senior design-management role, but a selective application rather than a primary target because the vacancy requires proven delivery of a major shopping mall or retail destination.',
    strengths: [
      'Strong design-management, consultant coordination, value-engineering, compliance and construction-stage leadership alignment.',
      'KSA experience, multidisciplinary team leadership and client-facing consultancy background fit the delivery environment.',
      'Commercial, mixed-use and one verified retail-branch assignment provide adjacent sector evidence.'
    ],
    gaps: [
      'No verified claim directly demonstrates delivery of a major shopping mall or retail destination.',
      'PMC, client-side or developer-side mall delivery is not directly evidenced.'
    ]
  },
  'zawaya-design-manager': {
    score: 85,
    decision: 'pursue',
    brief: 'Strong consultancy design-management opportunity with a verified current job description and recruitment email route. The role aligns closely with multidisciplinary delivery, Saudi codes, BIM-led coordination and team leadership.',
    strengths: [
      'Direct fit with multidisciplinary consultancy management, design review, technical coordination and project delivery.',
      'Riyadh market experience, team leadership, Saudi Building Code knowledge and client-management background align well.',
      'The current vacancy publishes a verified recruitment inbox, enabling a complete email application route.'
    ],
    gaps: [
      'The vacancy prefers immediate availability, which has not been disclosed because the owner has not approved that statement.',
      'Compensation and the exact authority, team size and reporting scope remain unstated.'
    ]
  },
  'buro-happold-senior-design-manager-jeddah': {
    score: 88,
    decision: 'pursue',
    brief: 'Excellent consultancy design-management fit spanning major buildings and infrastructure, client interface, RIBA-stage governance, BIM coordination, gateway reviews, value engineering, reporting and consultant selection.',
    strengths: [
      'Strong overlap with multidisciplinary design leadership, client management, technical governance and delivery oversight.',
      'RFP preparation, reporting, risk/change management and value-engineering requirements align with verified experience.',
      'The role values broad process leadership rather than production architecture.'
    ],
    gaps: [
      'Location is Jeddah, so relocation or travel expectations need consideration.',
      'The CV should show BIM-led governance without overstating hands-on software production.'
    ]
  },
  'qiddiya-director-design-commercial-office': {
    score: 75,
    decision: 'stretch',
    brief: 'High-upside design-director opportunity with strong leadership and governance alignment, but it has a specific investment-grade commercial-office product requirement that is not fully evidenced in the current career record.',
    strengths: [
      'Design governance, consultant management, multidisciplinary coordination, Saudi codes, value engineering and executive stakeholder engagement align strongly.',
      'Large programme, team-leadership and client-facing responsibilities suit the target seniority.',
      'Architecture and MBA background support the technical-commercial positioning.'
    ],
    gaps: [
      'Explicit Grade A office-tower or business-park delivery from concept through handover is a material requirement.',
      'Leasing, tenant strategy and investment-committee exposure should not be implied beyond verified evidence.',
      'This is a developer-side director role and may attract candidates with deeper commercial-office portfolios.'
    ]
  },
  'qiddiya-senior-manager-development-commercial-office': {
    score: 80,
    decision: 'selective',
    brief: 'A credible transition from design and project leadership into real-estate development management. Delivery, design coordination and governance fit well; feasibility modelling, office-product strategy and smart-building expertise are the main gaps.',
    strengths: [
      'End-to-end project delivery, design management, consultant coordination, procurement support and construction oversight are relevant.',
      'Budget, risk, stakeholder and quality management are supported by the career record.',
      'KSA market and multidisciplinary leadership experience fit the operating context.'
    ],
    gaps: [
      'Direct commercial-office development and financial feasibility modelling are preferred.',
      'ESG certification and smart-building technology knowledge are stated requirements and should be framed cautiously.',
      'The role is development-led rather than primarily architectural design management.'
    ]
  }
};

const routeFallback = {
  'mace-shopping-malls': {
    company: 'Mace', role: 'Senior Design Manager (Shopping Malls)', location: 'Riyadh, Saudi Arabia',
    application_url: 'https://sa.linkedin.com/jobs/view/senior-design-manager-shopping-malls-at-mace-4443933461',
    source_url: 'https://sa.linkedin.com/jobs/view/senior-design-manager-shopping-malls-at-mace-4443933461', route: 'portal', recipient: ''
  },
  'zawaya-design-manager': {
    company: 'Zawaya Albina Engineering Consultancy', role: 'Design Manager', location: 'Riyadh Region, Saudi Arabia',
    application_url: 'mailto:jobs@zaco.sa', source_url: 'https://zaco.sa/career/', route: 'email', recipient: 'jobs@zaco.sa'
  },
  'buro-happold-senior-design-manager-jeddah': {
    company: 'Buro Happold', role: 'Senior Design Manager', location: 'Jeddah, Saudi Arabia',
    application_url: 'https://careershub.burohappold.com/members/modules/job/detail.php?record=1907',
    source_url: 'https://careershub.burohappold.com/members/modules/job/detail.php?record=1907', route: 'portal', recipient: ''
  },
  'qiddiya-director-design-commercial-office': {
    company: 'Qiddiya Investment Company', role: 'Director - Design - Commercial Office (SPA187)', location: 'Riyadh, Saudi Arabia',
    application_url: 'https://www.linkedin.com/jobs/view/4447986545', source_url: 'https://www.linkedin.com/jobs/view/4447986545', route: 'portal', recipient: ''
  },
  'qiddiya-senior-manager-development-commercial-office': {
    company: 'Qiddiya Investment Company', role: 'Senior Manager - Real Estate Development - Commercial Office - SPA 356', location: 'Riyadh, Saudi Arabia',
    application_url: 'https://apply.workable.com/qiddiya-investment-company-1/j/65F4EA6E83/',
    source_url: 'https://apply.workable.com/qiddiya-investment-company-1/j/65F4EA6E83/', route: 'portal', recipient: ''
  }
};

function contentType(file) {
  const ext = path.extname(file).toLowerCase();
  return ({
    '.pdf': 'application/pdf',
    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
  })[ext] || 'application/octet-stream';
}

function readJson(file, fallback) {
  try { return JSON.parse(fs.readFileSync(file, 'utf8')); }
  catch { return fallback; }
}

const dateOverrides = readJson(DATE_OVERRIDES, { roles: {} }).roles || {};

function dateFieldsFor(key, fallback = {}) {
  const override = dateOverrides[key] || {};
  return {
    posting_date: override.posted_at ?? fallback.posting_date ?? null,
    posting_date_precision: override.posting_date_precision || fallback.posting_date_precision || 'unknown',
    posting_date_source: override.posting_date_source || fallback.posting_date_source || 'unknown'
  };
}

function ensureDir(dir) { fs.mkdirSync(dir, { recursive: true }); }
function cleanDir(dir) {
  fs.rmSync(dir, { recursive: true, force: true });
  ensureDir(dir);
}

function resolveArtifact(value) {
  if (!value || typeof value !== 'string') return '';
  if (path.isAbsolute(value)) return value;
  return path.join(REPO, value);
}

function loadNormalizedJob(item) {
  const seed = resolveArtifact(findFile(item, 'resume', 'pdf') || item.resume_pdf);
  const dir = seed ? path.dirname(seed) : '';
  const candidate = dir ? path.join(dir, 'normalized_job.json') : '';
  const payload = candidate && fs.existsSync(candidate) ? readJson(candidate, {}) : {};
  return payload.data || payload;
}

function findAtsVariant(item) {
  // Explicit fields win; otherwise scan the artifact directory for ATS-named files.
  const explicitPdf = resolveArtifact(findFile(item, 'resume_ats', 'pdf') || item.resume_ats_pdf);
  const explicitDocx = resolveArtifact(findFile(item, 'resume_ats', 'docx') || item.resume_ats_docx);
  if (explicitPdf || explicitDocx) return { docx: explicitDocx, pdf: explicitPdf };
  const seed = resolveArtifact(findFile(item, 'resume', 'pdf') || item.resume_pdf);
  if (!seed) return { docx: '', pdf: '' };
  const dir = path.dirname(seed);
  if (!fs.existsSync(dir)) return { docx: '', pdf: '' };
  const ats = fs.readdirSync(dir).filter(name => /ats/i.test(name));
  const pick = ext => {
    const match = ats.find(name => name.toLowerCase().endsWith(`.${ext}`));
    return match ? path.join(dir, match) : '';
  };
  return { docx: pick('docx'), pdf: pick('pdf') };
}

function verifyEmployerFacingPdf(source) {
  if (!/\.pdf$/i.test(source)) return;
  const filename = path.basename(source);
  if (!/(cv|resume|cover[_ -]?letter)/i.test(filename)) return;
  let text = '';
  try {
    text = execFileSync('pdftotext', [source, '-'], { encoding: 'utf8', maxBuffer: 8 * 1024 * 1024 });
  } catch (error) {
    throw new Error(`Unable to verify employer-facing PDF identity for ${source}: ${error.message}`);
  }
  if (/hameedo@gmail\.com/i.test(text)) {
    throw new Error(`Blocked employer-facing PDF with internal draft account exposed: ${source}`);
  }
  if (/(cv|resume)/i.test(filename) && !/hameedfarah@gmail\.com/i.test(text)) {
    throw new Error(`Blocked CV/resume without approved outward email hameedfarah@gmail.com: ${source}`);
  }
}

function copyArtifact(value, key) {
  const source = resolveArtifact(value);
  if (!source || !fs.existsSync(source) || !fs.statSync(source).isFile()) return '';
  verifyEmployerFacingPdf(source);
  const targetDir = path.join(SITE, 'files', key);
  ensureDir(targetDir);
  const filename = path.basename(source);
  fs.copyFileSync(source, path.join(targetDir, filename));
  return `files/${key}/${encodeURIComponent(filename)}`;
}

function textFrom(value) {
  if (!value) return '';
  if (typeof value === 'string' && fs.existsSync(resolveArtifact(value))) return fs.readFileSync(resolveArtifact(value), 'utf8').trim();
  return typeof value === 'string' ? value.trim() : '';
}

function itemsFromManifest(raw) {
  if (Array.isArray(raw)) return raw;
  if (Array.isArray(raw.applications)) return raw.applications;
  if (Array.isArray(raw.roles)) return raw.roles;
  if (Array.isArray(raw.packages)) return raw.packages;
  return [];
}

function findFile(item, kind, ext) {
  const candidates = [
    item[`${kind}_${ext}`], item[`${kind}${ext.toUpperCase()}`],
    item.artifacts?.[`${kind}_${ext}`], item.files?.[`${kind}_${ext}`],
    item[kind]?.[ext]
  ];
  return candidates.find(Boolean) || '';
}

function encodeHeader(value) {
  return /[^\x20-\x7E]/.test(value) ? `=?UTF-8?B?${Buffer.from(value).toString('base64')}?=` : value;
}

/* Drafts live in hameedo@gmail.com, but employer-facing material must expose
   only hameedfarah@gmail.com. Sanitize legacy body text before publishing. */
const DRAFT_ACCOUNT = 'hameedo@gmail.com';
const OUTWARD_EMAIL = 'hameedfarah@gmail.com';
function sanitizeAccount(value) {
  return String(value || '').replace(/hameedo@gmail\.com/gi, OUTWARD_EMAIL);
}

/* Canonical resume-template ids shared with the dashboard frontend
   (site/assets/shared.js). 'ats-linear' is a legacy alias of 'ats-classic'. */
const CANONICAL_TEMPLATES = ['ats-classic', 'modern-executive-sidebar'];
const TEMPLATE_ALIASES = { 'ats-linear': 'ats-classic' };
function normalizeTemplateId(value) {
  const id = TEMPLATE_ALIASES[value] || value || '';
  return CANONICAL_TEMPLATES.includes(id) ? id : '';
}

/* Exactly one submission CV per job: sidebar for email routes, ATS Classic for
   portal routes; falls back to whichever variant actually exists. */
function selectSubmissionResume(templateId, resume, resumeAts) {
  const template = normalizeTemplateId(templateId) || '';
  const preferAts = template !== 'modern-executive-sidebar';
  const primary = preferAts ? resumeAts : resume;
  const fallback = preferAts ? resume : resumeAts;
  return (primary.pdf || primary.docx) ? primary : fallback;
}

function wrapBase64(buffer) {
  return buffer.toString('base64').match(/.{1,76}/g).join('\r\n');
}

function createEml({ key, recipient, subject, body, attachments }) {
  const valid = attachments.filter(file => file && fs.existsSync(file));
  if (!recipient) throw new Error(`Email draft ${key} requires a verified recipient`);
  if (valid.length !== 1) throw new Error(`Email draft ${key} must contain exactly one selected CV attachment; found ${valid.length}`);
  const boundary = `career-engine-${key}-${Date.now()}`;
  const lines = [
    `From: Abdelhamid Farah <${OUTWARD_EMAIL}>`,
    `To: ${recipient || ''}`,
    `Subject: ${encodeHeader(subject)}`,
    'MIME-Version: 1.0',
    `Content-Type: multipart/mixed; boundary="${boundary}"`,
    '',
    `--${boundary}`,
    'Content-Type: text/plain; charset="UTF-8"',
    'Content-Transfer-Encoding: 8bit',
    '',
    sanitizeAccount(body) || '',
    ''
  ];
  for (const file of valid) {
    const filename = path.basename(file);
    lines.push(
      `--${boundary}`,
      `Content-Type: ${contentType(file)}; name="${filename}"`,
      'Content-Transfer-Encoding: base64',
      `Content-Disposition: attachment; filename="${filename}"`,
      '',
      wrapBase64(fs.readFileSync(file)),
      ''
    );
  }
  lines.push(`--${boundary}--`, '');
  const targetDir = path.join(SITE, 'files', key);
  ensureDir(targetDir);
  const target = path.join(targetDir, 'Application_Draft.eml');
  fs.writeFileSync(target, lines.join('\r\n'));
  return `files/${key}/Application_Draft.eml`;
}

function normalize(item) {
  const key = item.key || item.slug || item.application_key;
  const fallback = routeFallback[key] || {};
  const review = ownerReview[key] || {};
  const normalizedJob = loadNormalizedJob(item);
  const company = item.company || fallback.company || '';
  const role = item.role || fallback.role || '';
  const recipient = item.recipient || fallback.recipient || '';
  const route = item.route || fallback.route || 'portal';
  const emailSubject = item.email_subject || `Abdelhamid Farah - ${role || 'Position'}`;
  const emailBody = sanitizeAccount(textFrom(item.email_body_path || item.email_body || item.cover_email_body_path) || item.email_body || '');
  const resumeDocxSource = resolveArtifact(findFile(item, 'resume', 'docx') || item.resume_docx);
  const resumePdfSource = resolveArtifact(findFile(item, 'resume', 'pdf') || item.resume_pdf);
  const coverDocxSource = resolveArtifact(findFile(item, 'cover_letter', 'docx') || item.cover_letter_docx);
  const coverPdfSource = resolveArtifact(findFile(item, 'cover_letter', 'pdf') || item.cover_letter_pdf);
  const atsSource = findAtsVariant(item);
  const resume = {
    docx: copyArtifact(resumeDocxSource, key),
    pdf: copyArtifact(resumePdfSource, key),
    sha256: sha256(resumePdfSource),
    text: extractPdfText(resumePdfSource)
  };
  const coverLetter = {
    docx: copyArtifact(coverDocxSource, key),
    pdf: copyArtifact(coverPdfSource, key),
    sha256: sha256(coverPdfSource),
    text: extractPdfText(coverPdfSource)
  };
  const resumeAts = {
    docx: copyArtifact(atsSource.docx, key),
    pdf: copyArtifact(atsSource.pdf, key),
    sha256: sha256(atsSource.pdf),
    text: extractPdfText(atsSource.pdf)
  };
  const defaultResumeTemplate = route === 'portal' ? 'ats-classic' : 'modern-executive-sidebar';
  const selectedResumeTemplate = normalizeTemplateId(item.selected_resume_template || item.resume_template_override) || defaultResumeTemplate;
  const selectedResumeSource = selectedResumeTemplate === 'modern-executive-sidebar'
    ? (resumePdfSource || atsSource.pdf)
    : (atsSource.pdf || resumePdfSource);
  const staleEmailFile = path.join(SITE, 'files', key, 'Application_Draft.eml');
  const canCreateEmailDraft = route === 'email' && Boolean(recipient) && Boolean(selectedResumeSource);
  if (!canCreateEmailDraft && fs.existsSync(staleEmailFile)) fs.unlinkSync(staleEmailFile);
  const emailFile = canCreateEmailDraft
    ? createEml({
        key,
        recipient,
        subject: emailSubject,
        body: emailBody,
        attachments: [selectedResumeSource]
      })
    : '';
  const dates = dateFieldsFor(key, {
    posting_date: item.posting_date || normalizedJob.posting_date || normalizedJob.posted_date || normalizedJob.date_posted || null,
    posting_date_precision: item.posting_date_precision,
    posting_date_source: item.posting_date_source || normalizedJob.posting_date_source
  });
  const foundAt = item.found_at || item.first_seen || normalizedJob.live_verified_at || '';
  const rawApplicationUrl = item.application_url || fallback.application_url || '';
  const applicationUrl = rawApplicationUrl.startsWith('mailto:')
    ? (item.source_url || fallback.source_url || '')
    : rawApplicationUrl;
  return {
    key,
    job_id: item.job_id || '',
    company,
    role,
    location: item.location || fallback.location || '',
    score: review.score ?? item.owner_score ?? item.score ?? item.fit_score ?? null,
    decision: review.decision || item.decision || item.recommendation || 'selective',
    brief: review.brief || item.brief || item.summary || '',
    strengths: review.strengths || item.strengths || [],
    gaps: review.gaps || item.gaps || [],
    source_url: item.source_url || fallback.source_url || '',
    application_url: applicationUrl,
    route,
    recipient,
    email_subject: emailSubject,
    email_body: emailBody,
    email_file: emailFile,
    resume,
    resume_ats: resumeAts,
    recommended_resume_template: selectedResumeTemplate,
    selected_resume_template: selectedResumeTemplate,
    recommended_resume: selectSubmissionResume(selectedResumeTemplate, resume, resumeAts),
    cover_letter: coverLetter,
    submitted_package: findLatestSubmission(item.job_id || ''),
    posting_date: dates.posting_date ? String(dates.posting_date) : null,
    posting_date_precision: dates.posting_date_precision,
    posting_date_source: dates.posting_date_source,
    found_at: foundAt || null,
    scanned_at: normalizedJob.live_verified_at || item.scanned_at || null,
    full_job_description: item.full_job_description || normalizedJob.full_job_description || '',
    qa_status: item.qa_status || item.validation_status || 'Generated; owner review required',
    notes: item.notes || ''
  };
}

function sha256(file) {
  if (!file || !fs.existsSync(file)) return '';
  return crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
}

function refreshAssetFingerprints() {
  const pages = ['index.html', 'detail.html', 'resume-designs.html'];
  for (const pageName of pages) {
    const pagePath = path.join(SITE, pageName);
    if (!fs.existsSync(pagePath)) continue;
    const before = fs.readFileSync(pagePath, 'utf8');
    const after = before.replace(/assets\/([A-Za-z0-9._-]+\.(?:js|css))(?:\?v=[^"']*)?/g, (match, fileName) => {
      const assetPath = path.join(SITE, 'assets', fileName);
      if (!fs.existsSync(assetPath)) return match;
      return `assets/${fileName}?v=${sha256(assetPath).slice(0, 12)}`;
    });
    if (after !== before) fs.writeFileSync(pagePath, after);
  }
}

function extractPdfText(file) {
  if (!file || !fs.existsSync(file) || path.extname(file).toLowerCase() !== '.pdf') return '';
  try {
    return execFileSync('pdftotext', [file, '-'], { encoding: 'utf8', maxBuffer: 2 * 1024 * 1024 })
      .replace(/\f/g, '\n')
      .replace(/[ \t]+\n/g, '\n')
      .replace(/\n{3,}/g, '\n\n')
      .trim();
  } catch {
    return '';
  }
}

function trackerDecision(recommendation, score) {
  const value = String(recommendation || '').toLowerCase();
  if (value === 'high_priority' || Number(score) >= 80) return 'pursue';
  if (value === 'credible' || Number(score) >= 65) return 'selective';
  if (value === 'selective' || Number(score) >= 50) return 'stretch';
  return 'do_not_pursue';
}

function findLatestSubmission(jobId) {
  if (!jobId) return null;
  const root = path.join(TRACKER_ARTIFACTS, jobId, 'submissions');
  if (!fs.existsSync(root)) return null;
  const manifests = [];
  for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
    if (!entry.isDirectory()) continue;
    const manifestPath = path.join(root, entry.name, 'submission_manifest.json');
    const manifest = readJson(manifestPath, null);
    if (!manifest || manifest.status !== 'archived' || !manifest.resume?.sha256) continue;
    manifests.push({ manifest, manifestPath, archiveDir: path.dirname(manifestPath), archiveId: entry.name });
  }
  if (!manifests.length) return null;
  manifests.sort((a, b) => String(a.manifest.submitted_at || a.manifest.archived_at || '').localeCompare(String(b.manifest.submitted_at || b.manifest.archived_at || '')));
  const latest = manifests.at(-1);
  const copyKey = `tracker-${jobId}/submitted-${latest.archiveId}`;
  const resumePdfSource = latest.manifest.resume.pdf ? path.join(latest.archiveDir, latest.manifest.resume.pdf) : '';
  const resumeDocxSource = latest.manifest.resume.docx ? path.join(latest.archiveDir, latest.manifest.resume.docx) : '';
  const coverPdfSource = latest.manifest.cover_letter?.pdf ? path.join(latest.archiveDir, latest.manifest.cover_letter.pdf) : '';
  const coverDocxSource = latest.manifest.cover_letter?.docx ? path.join(latest.archiveDir, latest.manifest.cover_letter.docx) : '';
  return {
    history_event_id: latest.manifest.source_history_event_id || '',
    submitted_at: latest.manifest.submitted_at || '',
    company: latest.manifest.company || '',
    role: latest.manifest.role || '',
    route: latest.manifest.route || '',
    application_url: latest.manifest.application_url || '',
    confirmation_reference: latest.manifest.confirmation_reference || '',
    package_version: latest.manifest.package_version || '',
    resume: {
      template_id: normalizeTemplateId(latest.manifest.resume.template_id) || latest.manifest.resume.template_id || '',
      pdf: copyArtifact(resumePdfSource, copyKey),
      docx: copyArtifact(resumeDocxSource, copyKey),
      sha256: latest.manifest.resume.sha256 || '',
      text: latest.manifest.resume.text || extractPdfText(resumePdfSource)
    },
    cover_letter: {
      pdf: copyArtifact(coverPdfSource, copyKey),
      docx: copyArtifact(coverDocxSource, copyKey),
      sha256: latest.manifest.cover_letter?.sha256 || '',
      text: latest.manifest.cover_letter?.text || extractPdfText(coverPdfSource)
    }
  };
}

/* Canonical current-revision selection from the tracker's generated_artifacts
   metadata. The tracker section is append-only, so later entries are newer
   revisions; sidebar revision entries additionally carry a numeric
   template_version (e.g. the current v1.5 beats an unversioned base render).
   Every candidate must still exist on disk; stale metadata falls back to the
   exact legacy directory scan in findGeneratedDocsIn. */
function artifactVersionRank(entry) {
  const raw = String(entry?.template_version || '').trim();
  if (!/^\d+(?:\.\d+)*$/.test(raw)) return null;
  return raw.split('.').map(part => parseInt(part, 10));
}

function preferArtifactEntry(candidate, incumbent) {
  // Higher parsed template_version wins (a missing/unparsable version ranks
  // lowest); any tie falls through to append order — later entries in the
  // append-only generated_artifacts section are newer revisions.
  const rank = entry => artifactVersionRank(entry) || [-1];
  const ra = rank(candidate);
  const rb = rank(incumbent);
  for (let i = 0; i < Math.max(ra.length, rb.length); i++) {
    const av = ra[i] ?? -1;
    const bv = rb[i] ?? -1;
    if (av !== bv) return av > bv;
  }
  return true;
}

function artifactSlotFor(entry) {
  const variant = normalizeTemplateId(entry.variant); // '' for unknown variants
  switch (String(entry.type || '')) {
    case 'final_pdf': return variant === 'modern-executive-sidebar' ? ['resume', 'pdf'] : null;
    case 'final_docx': return variant === 'modern-executive-sidebar' ? ['resume', 'docx'] : null;
    case 'ats_pdf': return ['resume_ats', 'pdf'];
    case 'ats_docx': return ['resume_ats', 'docx'];
    case 'cover_letter_pdf': return ['cover_letter', 'pdf'];
    case 'cover_letter_docx': return ['cover_letter', 'docx'];
    default: return null;
  }
}

function validFile(source) {
  try {
    return Boolean(source) && fs.existsSync(source) && fs.statSync(source).isFile();
  } catch {
    return false;
  }
}

function currentArtifactsFromMetadata(payload) {
  const slots = {
    resume: {}, resume_ats: {}, cover_letter: {}
  };
  const entries = Array.isArray(payload?.generated_artifacts) ? payload.generated_artifacts : [];
  for (const entry of entries) {
    if (!entry || typeof entry !== 'object') continue;
    const slot = artifactSlotFor(entry);
    if (!slot) continue;
    const resolved = resolveArtifact(String(entry.path || ''));
    if (!validFile(resolved)) continue; // existence validation before selection
    const [name, ext] = slot;
    const incumbent = slots[name][ext];
    if (!incumbent || preferArtifactEntry(entry, incumbent.entry)) {
      slots[name][ext] = { path: resolved, entry };
    }
  }
  const pickPaths = group => ({
    pdf: slots[group].pdf?.path || '',
    docx: slots[group].docx?.path || ''
  });
  return { resume: pickPaths('resume'), resume_ats: pickPaths('resume_ats'), cover_letter: pickPaths('cover_letter') };
}

function findGeneratedDocs(jobId) {
  return findGeneratedDocsIn(jobId, { artifactsRoot: TRACKER_ARTIFACTS, jobsRoot: TRACKER_JOBS });
}

function findGeneratedDocsIn(jobId, { artifactsRoot = TRACKER_ARTIFACTS, jobsRoot = TRACKER_JOBS } = {}) {
  const dir = path.join(artifactsRoot, jobId);
  if (!fs.existsSync(dir)) return { resume: {}, resume_ats: {}, cover_letter: {}, email_body: '' };
  // Metadata-first: the tracker's generated_artifacts section is the canonical
  // current-revision authority, so the latest matching validated entry wins.
  const trackerPayload = jobsRoot ? readJson(path.join(jobsRoot, `${jobId}.json`), {}) : {};
  const current = currentArtifactsFromMetadata(trackerPayload);
  // Exact legacy directory-scan fallback per file kind when metadata offers no
  // valid current entry for that kind.
  const names = fs.readdirSync(dir).filter(name => fs.statSync(path.join(dir, name)).isFile());
  const pick = predicate => {
    const name = names.find(predicate);
    return name ? path.join(dir, name) : '';
  };
  const executivePdf = current.resume.pdf || pick(name => /CV/i.test(name) && /\.pdf$/i.test(name) && !/ATS/i.test(name));
  const executiveDocx = current.resume.docx || pick(name => /CV/i.test(name) && /\.docx$/i.test(name) && !/ATS/i.test(name));
  const atsPdf = current.resume_ats.pdf || pick(name => /CV/i.test(name) && /ATS/i.test(name) && /\.pdf$/i.test(name));
  const atsDocx = current.resume_ats.docx || pick(name => /CV/i.test(name) && /ATS/i.test(name) && /\.docx$/i.test(name));
  const coverPdf = current.cover_letter.pdf || pick(name => /Cover_Letter/i.test(name) && /\.pdf$/i.test(name));
  const coverDocx = current.cover_letter.docx || pick(name => /Cover_Letter/i.test(name) && /\.docx$/i.test(name));
  const emailText = pick(name => /application_email/i.test(name) && /\.txt$/i.test(name));
  const generatedApplication = readJson(path.join(dir, 'generated_application.json'), {});
  const generatedCoverBody = String(generatedApplication?.cover_email?.body || '').trim();
  return {
    resume: {
      pdf: copyArtifact(executivePdf, `tracker-${jobId}`),
      docx: copyArtifact(executiveDocx, `tracker-${jobId}`),
      sha256: sha256(executivePdf), // always recomputed from disk bytes
      text: extractPdfText(executivePdf)
    },
    resume_ats: {
      pdf: copyArtifact(atsPdf, `tracker-${jobId}`),
      docx: copyArtifact(atsDocx, `tracker-${jobId}`),
      sha256: sha256(atsPdf),
      text: extractPdfText(atsPdf)
    },
    cover_letter: {
      pdf: copyArtifact(coverPdf, `tracker-${jobId}`),
      docx: copyArtifact(coverDocx, `tracker-${jobId}`),
      sha256: sha256(coverPdf),
      text: extractPdfText(coverPdf)
    },
    email_body: generatedCoverBody || (emailText ? fs.readFileSync(emailText, 'utf8').trim() : '')
  };
}

function isFixtureJob(job) {
  const sourceUrl = String(job.source_url || '').toLowerCase();
  const company = String(job.company || '').toLowerCase();
  const role = String(job.role || '').toLowerCase();
  const externalId = String(job.external_job_id || '').toLowerCase();
  const sourceHost = sourceUrl.replace(/^https?:\/\//, '').split('/')[0];
  if (sourceHost === 'example.com' || sourceHost === 'example.org' || sourceHost === 'example.net') return true;
  if (sourceUrl.includes('boards.example.greenhouse.io') || sourceUrl.includes('example.com/test')) return true;
  if (/(^|[/._-])(test|fixture|dummy|placeholder)([/._-]|$)|sample[-_]?job|boards\.example/.test(sourceUrl)) return true;
  if (company === 'oasis development co') return true;
  if (/\b(test|fixture|dummy|placeholder|sample)\s+(job|role|position|vacancy)\b/.test(`${company} ${role}`)) return true;
  if (/^(test|fixture|dummy|sample)[-_]/.test(externalId)) return true;
  return false;
}

function trackerRoles() {
  if (!fs.existsSync(TRACKER_JOBS)) return [];
  const files = fs.readdirSync(TRACKER_JOBS).filter(name => /^[a-f0-9]+\.json$/i.test(name));
  return files.map(name => {
    const payload = readJson(path.join(TRACKER_JOBS, name), {});
    const job = payload.job || {};
    if (!job.job_id || !job.company || !job.role || isFixtureJob(job)) return null;
    if (String(job.processing_status || '').toLowerCase() === 'superseded') return null;
    const route = payload.processing_state?.route || {};
    const scoring = payload.scoring || {};
    const score = Number(job.owner_score ?? payload.owner_score ?? scoring.human_adjusted_score ?? scoring.total ?? job.fit_score);
    const validScore = Number.isFinite(score) ? score : null;
    const docs = findGeneratedDocs(job.job_id);
    const routeType = route.route && route.route !== 'unresolved' ? route.route : (job.source_url ? 'portal' : 'unresolved');
    const applicationUrl = route.application_url || payload.application_url || job.application_url || job.source_url || '';
    const recommendation = scoring.recommendation || job.priority || '';
    const liveStatus = payload.processing_state?.live_status || payload.live_status || 'unverified';
    const defaultTemplate = routeType === 'portal' ? 'ats-classic' : 'modern-executive-sidebar';
    const selectedTemplate = normalizeTemplateId(payload.resume_template_override
      || payload.processing_state?.selected_resume_variant
      || payload.submission_package?.selected_resume_variant) || defaultTemplate;
    const selectedResume = selectSubmissionResume(selectedTemplate, docs.resume, docs.resume_ats);
    const briefParts = [
      `Tracker status: ${job.processing_status || 'unknown'}.`,
      `Live status: ${liveStatus}.`,
      job.next_action ? `Next action: ${job.next_action}.` : ''
    ].filter(Boolean);
    return {
      key: `tracker-${job.job_id}`,
      job_id: job.job_id,
      external_job_id: job.external_job_id || '',
      jd_hash: job.jd_hash || '',
      company: job.company,
      role: job.role,
      location: job.location || '',
      score: validScore,
      raw_engine_score: Number.isFinite(Number(scoring.total)) ? Number(scoring.total) : validScore,
      decision: trackerDecision(recommendation, validScore),
      brief: briefParts.join(' '),
      strengths: Array.isArray(scoring.strengths) ? scoring.strengths.slice(0, 8) : [],
      gaps: Array.isArray(scoring.gaps) ? scoring.gaps.slice(0, 8) : [],
      source_url: job.source_url || payload.provenance?.source_url || '',
      application_url: applicationUrl,
      route: routeType,
      recipient: route.recipient || '',
      email_subject: route.subject || payload.email_subject || `Abdelhamid Farah - ${job.role}`,
      email_body: sanitizeAccount(docs.email_body),
      resume: docs.resume,
      resume_ats: docs.resume_ats,
      recommended_resume_template: selectedTemplate,
      selected_resume_variant: selectedTemplate,
      recommended_resume: selectedResume,
      submission_package: payload.submission_package || payload.processing_state?.submission_package || {},
      cover_letter: docs.cover_letter,
      submitted_package: findLatestSubmission(job.job_id),
      posting_date: job.posting_date || null,
      posting_date_precision: job.posting_date ? 'source_exact_or_tracker' : 'unknown',
      posting_date_source: job.posting_date ? job.source || 'tracker' : 'unknown',
      found_at: job.first_seen || null,
      scanned_at: job.last_seen || job.last_updated || null,
      full_job_description: payload.full_job_description || '',
      qa_status: job.pdf_status === 'validated' ? 'Validated' : (job.processing_status || 'Tracked'),
      processing_status: job.processing_status || '',
      manual_review_reason: payload.processing_state?.reason_code || job.manual_review_reason || '',
      manual_review_detail: payload.processing_state?.reason || job.manual_review_detail || '',
      application_status: job.application_status || 'not_submitted',
      live_status: liveStatus,
      notes: job.notes || '',
      kind: docs.resume.pdf || docs.resume_ats.pdf ? 'application' : 'reviewed'
    };
  }).filter(Boolean);
}

function canonicalIdentity(role) {
  if (role.job_id) return `job:${role.job_id}`;
  if (role.external_job_id) return `external:${role.external_job_id}`;
  if (role.application_url) return `url:${String(role.application_url).replace(/[?#].*$/, '').replace(/\/$/, '').toLowerCase()}`;
  if (role.source_url) return `url:${String(role.source_url).replace(/[?#].*$/, '').replace(/\/$/, '').toLowerCase()}`;
  if (role.jd_hash) return `jd:${role.jd_hash}`;
  return `text:${String(role.company).toLowerCase()}|${String(role.role).toLowerCase()}|${String(role.location).toLowerCase()}`;
}

function mergeUniqueRoles(prepared, tracker, reviewed) {
  // CareerTracker is the sole dashboard role authority. Legacy prepared/manual
  // datasets may still supply artifacts during migration, but they must never
  // create an additional rendered role or revive a superseded tracker record.
  void prepared;
  void reviewed;
  return [...tracker];
}

function main() {
  const raw = readJson(MANIFEST, {});
  const items = itemsFromManifest(raw);
  /* Preserve preview-safe and prior approved files that are intentionally not
     owned by the current package manifest. Managed artifacts are overwritten
     by filename; stale-file cleanup is a separate explicit maintenance task. */
  ensureDir(path.join(SITE, 'files'));
  const prepared = items.map(normalize).filter(item => item.key);
  const tracker = trackerRoles();
  const manualReviewed = readJson(REVIEWED, []).map(role => {
    const dates = dateFieldsFor(role.key, role);
    return {
      ...role,
      posting_date: dates.posting_date ? String(dates.posting_date) : null,
      posting_date_precision: dates.posting_date_precision,
      posting_date_source: dates.posting_date_source,
      found_at: role.found_at || raw.generated_at || new Date().toISOString(),
      scanned_at: role.scanned_at || raw.generated_at || null,
      kind: 'reviewed'
    };
  });
  const merged = mergeUniqueRoles(prepared, tracker, manualReviewed);
  const applications = merged.filter(role => role.kind === 'application' || role.resume?.pdf || role.resume_ats?.pdf);
  const reviewed = merged.filter(role => !applications.includes(role));
  ensureDir(path.join(SITE, 'data'));
  const detailsDir = path.join(SITE, 'data', 'job-details');
  // Keep lazy detail loading without creating one publish file per job. here.now
  // caps a publish at 1,000 files, so use a small deterministic shard set instead.
  fs.rmSync(detailsDir, { recursive: true, force: true });
  ensureDir(detailsDir);
  const detailKeys = [
    'full_job_description', 'brief', 'strengths', 'gaps', 'notes', 'submission_package', 'submitted_package',
    'email_body', 'email_subject', 'recipient', 'application_url', 'source_url',
    'resume', 'resume_ats', 'resume_variants', 'recommended_resume', 'cover_letter'
  ];
  const detailShardCount = 16;
  const detailShards = Array.from({ length: detailShardCount }, () => ({}));
  const detailShardFor = key => {
    let hash = 2166136261;
    for (const char of String(key || '')) {
      hash ^= char.charCodeAt(0);
      hash = Math.imul(hash, 16777619);
    }
    return (hash >>> 0) % detailShardCount;
  };
  const slim = role => {
    const detail = {};
    for (const key of detailKeys) if (role[key] !== undefined) detail[key] = role[key];
    for (const key of ['resume', 'resume_ats', 'recommended_resume', 'cover_letter']) {
      if (detail[key]) detail[key] = { ...detail[key] };
      // Resume text is large and is not needed for the detail viewer because the
      // generated PDF is rendered directly. Keep cover-letter text so legacy
      // portal packages with no email_body still show their generated letter.
      if (key !== 'cover_letter' && detail[key]?.text) delete detail[key].text;
    }
    if (detail.resume_variants) detail.resume_variants = Object.fromEntries(Object.entries(detail.resume_variants).map(([id, files]) => {
      const clean = { ...(files || {}) }; delete clean.text; return [id, clean];
    }));
    const shard = detailShardFor(role.key);
    detailShards[shard][role.key] = detail;
    const cardKeys = ['key', 'role', 'company', 'location', 'score', 'decision', 'stage', 'route', 'found_at', 'scanned_at', 'posting_date', 'posting_date_precision', 'posting_date_source', 'kind', 'status', 'application_status', 'processing_status', 'tags', 'external_job_id', 'job_id'];
    const copy = Object.fromEntries(cardKeys.filter(key => role[key] !== undefined).map(key => [key, role[key]]));
    for (const key of detailKeys) delete copy[key];
    copy.brief = String(role.brief || '').slice(0, 180);
    copy.manual_review_detail = String(role.manual_review_detail || '').slice(0, 180);
    copy.card_resume_pdf = role.recommended_resume?.pdf || role.resume_ats?.pdf || role.resume?.pdf || '';
    copy.card_cover_pdf = role.cover_letter?.pdf || '';
    copy.card_application_url = role.application_url || role.source_url || '';
    copy.detail_shard = shard;
    return copy;
  };
  const slimApplications = applications.map(slim);
  const slimReviewed = reviewed.map(slim);
  detailShards.forEach((shard, index) => {
    fs.writeFileSync(path.join(detailsDir, `${index}.json`), JSON.stringify(shard));
  });
  fs.writeFileSync(path.join(SITE, 'data', 'jobs.json'), JSON.stringify({
    generated_at: new Date().toISOString(),
    bundle_hash: raw.bundle_hash || '',
    tracker_records: tracker.length,
    total_roles: merged.length,
    applications: slimApplications,
    reviewed: slimReviewed
  }));
  refreshAssetFingerprints();
  const missingPrepared = prepared.filter(r => !r.resume.pdf || !r.cover_letter.pdf).map(r => r.key);
  console.log(JSON.stringify({
    prepared_packages: prepared.length,
    tracker_records: tracker.length,
    total_roles: merged.length,
    applications: applications.length,
    reviewed: reviewed.length,
    missing_prepared_documents: missingPrepared
  }, null, 2));
  // Legacy packages are optional fallback data; stale or incomplete packages
  // must never make a canonical tracker build fail.
}

if (require.main === module) main();

module.exports = {
  canonicalIdentity,
  mergeUniqueRoles,
  itemsFromManifest,
  isFixtureJob,
  findGeneratedDocsIn,
  currentArtifactsFromMetadata,
  preferArtifactEntry,
  artifactSlotFor,
  findLatestSubmission
};
