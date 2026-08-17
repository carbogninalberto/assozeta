#!/usr/bin/env node
/*
Generate concise architecture coverage markdown artifacts from docs/matrix/architecture-inventory.json.
The output is deterministic and helps keep docs in sync after route changes.
*/

import fs from 'node:fs';
import path from 'node:path';

const ROOT = process.cwd();
const MATRIX_PATH = path.join(ROOT, 'docs/matrix/architecture-inventory.json');
const OUT_SUMMARY = path.join(ROOT, 'docs/architecture/coverage-metrics.md');

const data = JSON.parse(fs.readFileSync(MATRIX_PATH, 'utf8'));

const topLevelCounts = (items) => {
  const map = {};
  for (const item of items) {
    const prefix = item.split('/').filter(Boolean)[0] || '(root)';
    map[prefix] = (map[prefix] || 0) + 1;
  }
  return Object.fromEntries(
    Object.entries(map).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])),
  );
};

const byApp = data.backendByApp || {};
const byTopBackend = topLevelCounts(data.apiPatterns || []);
const byTopFrontend = topLevelCounts((data.frontendRoutes || []).map((r) => r.route));
const fpMissing = (data.frontendRoutes || []).filter((r) => !(r.hasExplicitPermissionCheck));

const lines = [];
lines.push('# Coverage metrics (auto-generated)');
lines.push('');
lines.push('Generated from `docs/matrix/architecture-inventory.json` at build time.');
lines.push('');
lines.push('## Totals');
lines.push(`- backend endpoint patterns: ${data.apiPatterns?.length || 0}`);
lines.push(`- permission registry patterns: ${(data.permissionRegistry || []).length}`);
lines.push(`- front-end routes: ${(data.frontendRoutes || []).length}`);
lines.push(`- routes with explicit permission checks: ${(data.frontendRoutes || []).length - fpMissing.length}`);
lines.push(`- routes without explicit permission checks: ${fpMissing.length}`);
lines.push(`- unmapped backend endpoints: ${(data.unmappedBackend || []).length}`);
lines.push(`- permissions without backend match: ${(data.permissionsWithoutBackend || []).length}`);
lines.push('');
lines.push('## Backend route volume by top-level prefix');
lines.push('');
lines.push('| prefix | patterns |');
lines.push('| --- | ---: |');
for (const [k, v] of Object.entries(byTopBackend)) {
  lines.push(`| ${k} | ${v} |`);
}
lines.push('');
lines.push('## Frontend route volume by top-level prefix');
lines.push('');
lines.push('| prefix | routes |');
lines.push('| --- | ---: |');
for (const [k, v] of Object.entries(byTopFrontend)) {
  lines.push(`| ${k} | ${v} |`);
}
lines.push('');
lines.push('## Backend app route volume');
lines.push('');
lines.push('| app | routes |');
lines.push('| --- | ---: |');
for (const [app, routes] of Object.entries(byApp).sort((a, b) => a[0].localeCompare(b[0]))) {
  lines.push(`| ${app} | ${routes.length} |`);
}
lines.push('');
lines.push('## Frontend routes without explicit permission checks');
lines.push('');
for (const r of fpMissing) {
  lines.push(`- ${r.route}`);
}

fs.writeFileSync(OUT_SUMMARY, `${lines.join('\n')}\n`, 'utf8');
console.log(`Generated ${OUT_SUMMARY}`);
