#!/usr/bin/env node

import { readFileSync, existsSync } from 'node:fs';

const DEFAULT_TRACKER = 'docs/implementation/career-cloudflare-access-20260818-tracker.csv';
const HEADERS = [
  'id','milestone','workstream','title','priority','status','effort','dependencies',
  'owner_role','deliverable','acceptance_criteria','validation','evidence','blocker',
  'notes','started_at','completed_at','updated_at'
];
const STATUSES = new Set(['Not Started','Ready','In Progress','Blocked','Review','Done','Deferred']);
const PRIORITIES = new Set(['P0','P1','P2','P3']);
const PRIORITY_RANK = { P0: 0, P1: 1, P2: 2, P3: 3 };

function parseCsv(text) {
  const rawRows = [];
  let row = [], field = '', quoted = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (quoted) {
      if (ch === '"' && text[i + 1] === '"') { field += '"'; i++; }
      else if (ch === '"') quoted = false;
      else field += ch;
    } else if (ch === '"') quoted = true;
    else if (ch === ',') { row.push(field); field = ''; }
    else if (ch === '\n') {
      row.push(field.replace(/\r$/, ''));
      if (row.some(v => v.length)) rawRows.push(row);
      row = []; field = '';
    } else field += ch;
  }
  if (field.length || row.length) {
    row.push(field.replace(/\r$/, ''));
    if (row.some(v => v.length)) rawRows.push(row);
  }
  if (!rawRows.length) return { headers: [], rows: [] };
  const headers = rawRows[0].map(v => v.trim());
  const rows = rawRows.slice(1).map(values => Object.fromEntries(headers.map((h, i) => [h, (values[i] ?? '').trim()])));
  return { headers, rows };
}

function deps(row) {
  return String(row.dependencies || '').split(',').map(v => v.trim()).filter(Boolean);
}

function load(path) {
  if (!existsSync(path)) throw new Error(`tracker not found: ${path}`);
  const parsed = parseCsv(readFileSync(path, 'utf8'));
  if (JSON.stringify(parsed.headers) !== JSON.stringify(HEADERS)) {
    throw new Error(`tracker header must exactly be: ${HEADERS.join(',')}`);
  }
  if (!parsed.rows.length) throw new Error('tracker has no task rows');
  return parsed.rows;
}

function validate(rows) {
  const errors = [], warnings = [];
  const byId = new Map();
  const required = ['id','milestone','workstream','title','priority','status','effort','owner_role','deliverable','acceptance_criteria','validation','updated_at'];
  for (const row of rows) {
    for (const key of required) if (!row[key]) errors.push(`${row.id || '<unknown>'}: missing ${key}`);
    if (byId.has(row.id)) errors.push(`duplicate id: ${row.id}`);
    byId.set(row.id, row);
    if (!STATUSES.has(row.status)) errors.push(`${row.id}: invalid status ${row.status}`);
    if (!PRIORITIES.has(row.priority)) errors.push(`${row.id}: invalid priority ${row.priority}`);
    if (row.status === 'Done' && !row.evidence) errors.push(`${row.id}: Done task requires evidence`);
    if (row.status === 'Done' && !row.completed_at) errors.push(`${row.id}: Done task requires completed_at`);
    if (row.status === 'Blocked' && !row.blocker) errors.push(`${row.id}: Blocked task requires blocker`);
    if (row.status === 'In Progress' && !row.started_at) warnings.push(`${row.id}: In Progress task has no started_at`);
    if (row.status === 'Review' && !row.evidence) warnings.push(`${row.id}: Review task has no evidence yet`);
  }
  for (const row of rows) {
    for (const dep of deps(row)) {
      if (!byId.has(dep)) errors.push(`${row.id}: unknown dependency ${dep}`);
      if (dep === row.id) errors.push(`${row.id}: self dependency`);
    }
    if (row.status === 'Ready') {
      const incomplete = deps(row).filter(id => byId.get(id)?.status !== 'Done');
      if (incomplete.length) errors.push(`${row.id}: Ready with incomplete dependencies: ${incomplete.join(', ')}`);
    }
  }
  const visiting = new Set(), visited = new Set();
  function visit(id, chain = []) {
    if (visiting.has(id)) { errors.push(`dependency cycle: ${[...chain, id].join(' -> ')}`); return; }
    if (visited.has(id)) return;
    visiting.add(id);
    for (const dep of deps(byId.get(id) || {})) if (byId.has(dep)) visit(dep, [...chain, id]);
    visiting.delete(id); visited.add(id);
  }
  for (const id of byId.keys()) visit(id);
  return { errors: [...new Set(errors)], warnings: [...new Set(warnings)] };
}

function summary(rows) {
  const counts = new Map();
  for (const row of rows) counts.set(row.status, (counts.get(row.status) || 0) + 1);
  const done = counts.get('Done') || 0;
  console.log(`Total: ${rows.length} | Done: ${done} | Open: ${rows.length - done} | Completion: ${((done / rows.length) * 100).toFixed(1)}%`);
  for (const status of ['Not Started','Ready','In Progress','Blocked','Review','Done','Deferred']) {
    if (counts.get(status)) console.log(`${status}: ${counts.get(status)}`);
  }
}

function next(rows) {
  const byId = new Map(rows.map(row => [row.id, row]));
  const ready = rows.filter(row => ['Not Started','Ready'].includes(row.status) && deps(row).every(id => byId.get(id)?.status === 'Done'))
    .sort((a, b) => (PRIORITY_RANK[a.priority] - PRIORITY_RANK[b.priority]) || a.id.localeCompare(b.id));
  console.log(`Dependency-ready tasks: ${ready.length}`);
  for (const row of ready) console.log(`${row.priority} ${row.id} [${row.milestone}] ${row.title} | owner: ${row.owner_role} | effort: ${row.effort}`);
}

const command = process.argv[2];
const tracker = process.argv[3] || DEFAULT_TRACKER;
if (!['validate','summary','next'].includes(command)) {
  console.error(`Usage: node scripts/career-cloudflare-access-20260818-plan.mjs <validate|summary|next> [tracker.csv]`);
  process.exit(2);
}
try {
  const rows = load(tracker);
  if (command === 'validate') {
    const result = validate(rows);
    result.warnings.forEach(w => console.warn(`WARN: ${w}`));
    if (result.errors.length) {
      result.errors.forEach(e => console.error(`ERROR: ${e}`));
      process.exit(1);
    }
    console.log(`Tracker valid: ${rows.length} tasks, ${result.warnings.length} warnings.`);
  } else if (command === 'summary') summary(rows);
  else next(rows);
} catch (error) {
  console.error(`ERROR: ${error.message}`);
  process.exit(1);
}
