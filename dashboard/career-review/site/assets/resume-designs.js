/* resume-designs.js — five ATS-safe resume design comparison */
'use strict';

function pickArray(payload) {
  if (Array.isArray(payload)) return payload;
  return payload.options || payload.designs || payload.variants || payload.styles || [];
}

function pathOf(item, key) {
  return item[key] || item.files?.[key] || item.artifacts?.[key] || '';
}

function renderDesign(item) {
  const fragment = $('#design-template').content.cloneNode(true);
  $('.design-id', fragment).textContent = item.id || item.key || '';
  $('.design-label', fragment).textContent = item.label || item.name || 'ATS design';
  $('.design-description', fragment).textContent = item.description || '';
  const recommended = Boolean(item.recommended || item.default || item.is_recommended);
  $('.design-recommended', fragment).hidden = !recommended;

  const preview = pathOf(item, 'preview') || pathOf(item, 'preview_png') || pathOf(item, 'first_page_png');
  const pdf = pathOf(item, 'pdf') || pathOf(item, 'pdf_path');
  const docx = pathOf(item, 'docx') || pathOf(item, 'docx_path');
  const image = $('.design-preview', fragment);
  const previewLink = $('.design-preview-link', fragment);
  if (preview) {
    image.src = preview;
    image.alt = `${item.label || item.id} first-page preview`;
    previewLink.href = pdf || preview;
  } else {
    image.remove();
    previewLink.replaceWith(Object.assign(document.createElement('p'), { className: 'missing-note', textContent: 'Preview unavailable.' }));
  }

  const metrics = $('.design-metrics', fragment);
  const pageCount = item.page_count ?? item.pages ?? item.qa?.page_count;
  const wordCount = item.word_count ?? item.words ?? item.qa?.word_count;
  for (const [value, label] of [[pageCount, 'pages'], [wordCount, 'words']]) {
    if (value == null) continue;
    const chip = document.createElement('span');
    chip.className = 'tag tag-type';
    chip.textContent = `${value} ${label}`;
    metrics.append(chip);
  }

  const checks = $('.design-checks', fragment);
  const rawChecks = item.ats_safe_checks || item.checks || item.qa?.checks || [];
  const checkList = Array.isArray(rawChecks)
    ? rawChecks
    : Object.entries(rawChecks).filter(([, passed]) => passed).map(([name]) => name);
  const visibleChecks = checkList.length ? checkList : ['single column', 'searchable text', 'no images', 'no tables'];
  for (const check of visibleChecks) {
    const chip = document.createElement('span');
    chip.className = 'design-check';
    chip.textContent = `✓ ${String(check).replaceAll('_', ' ')}`;
    checks.append(chip);
  }

  const actions = $('.design-actions', fragment);
  if (pdf) actions.append(makeLink('Open PDF', pdf, 'primary'));
  if (docx) actions.append(makeLink('Download DOCX', docx));
  return fragment;
}

async function init() {
  $('#back-button').addEventListener('click', () => { window.location.href = 'index.html'; });
  await initTheme();
  $('#theme-select').addEventListener('change', async event => {
    const theme = applyTheme(event.target.value);
    try { await savePreference(THEME_PREF_KEY, theme.id); } catch (error) { showToast(error.message, true); }
  });
  try {
    const response = await fetch('data/ats-design-options.json', { cache: 'no-store' });
    if (!response.ok) throw new Error(`Unable to load designs (${response.status})`);
    const payload = await response.json();
    const options = pickArray(payload);
    if (!options.length) throw new Error('No ATS design options were generated.');
    const grid = $('#design-grid');
    for (const option of options) grid.append(renderDesign(option));
  } catch (error) {
    $('#design-error').hidden = false;
    $('#design-error').textContent = error.message;
  }
}

init();
