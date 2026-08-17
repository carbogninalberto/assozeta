#!/usr/bin/env node
/**
Generate `docs/architecture/coverage-gaps.md` from docs/matrix/architecture-inventory.json.
The output is deterministic and can be regenerated anytime after updating the inventory.
*/

import fs from 'node:fs';
import path from 'node:path';

const ROOT = process.cwd();
const MATRIX_PATH = path.join(ROOT, 'docs/matrix/architecture-inventory.json');
const OUT_PATH = path.join(ROOT, 'docs/architecture/coverage-gaps.md');

const topLevelPrefix = (route = '') => route.split('/').filter(Boolean)[0] || '(root)';

const groupByTopLevel = (items) => {
  const grouped = {};
  for (const item of items) {
    const prefix = topLevelPrefix(item);
    if (!grouped[prefix]) grouped[prefix] = [];
    grouped[prefix].push(item);
  }
  return Object.entries(grouped).sort((a, b) => b[1].length - a[1].length || a[0].localeCompare(b[0]));
};

const payload = JSON.parse(fs.readFileSync(MATRIX_PATH, 'utf8'));

const backendWithoutPerm = payload.unmappedBackend || [];
const permsWithoutBackend = payload.permissionsWithoutBackend || [];
const frontendRoutes = payload.frontendRoutes || [];
const missingFrontendChecks = frontendRoutes.filter((r) => !r.hasExplicitPermissionCheck);

const backendGroups = groupByTopLevel(backendWithoutPerm);
const lines = [];

lines.push('# Coverage gaps and documentation risk register');
lines.push('');
lines.push('This file is generated from `docs/matrix/architecture-inventory.json`.');
lines.push('');
lines.push('## Baseline numbers');
lines.push('');
lines.push(`- Backend patterns: ${payload.apiPatterns?.length || 0}`);
lines.push(`- Permission entries: ${(payload.permissionRegistry || []).length}`);
lines.push(`- Unmapped backend patterns (missing permission registry entry): ${backendWithoutPerm.length}`);
lines.push(`- Permission entries without backend pattern match: ${permsWithoutBackend.length}`);
lines.push(`- Frontend route entries: ${frontendRoutes.length}`);
lines.push(`- Frontend entries with explicit permission route checks: ${frontendRoutes.length - missingFrontendChecks.length}`);
lines.push(`- Frontend entries without explicit permission route checks: ${missingFrontendChecks.length}`);
lines.push('');

lines.push('## Backend routes without permission mapping');
lines.push('');
if (backendGroups.length === 0) {
  lines.push('- None');
} else {
  for (const [prefix, items] of backendGroups) {
    lines.push(`- **${prefix}** (${items.length})`);
    for (const endpoint of items.slice().sort()) {
      lines.push(`  - \`${endpoint}\``);
    }
  }
}
lines.push('');

lines.push('## Permissions defined without backend endpoint match');
lines.push('');
if (permsWithoutBackend.length === 0) {
  lines.push('- None');
} else {
  for (const permission of permsWithoutBackend.slice().sort()) {
    lines.push(`- \`${permission}\``);
  }
}
lines.push('');

lines.push('## Frontend routes without explicit route-level permission checks');
lines.push('');
if (missingFrontendChecks.length === 0) {
  lines.push('- None');
} else {
  for (const route of missingFrontendChecks) {
    lines.push(`- \`${route.route}\``);
  }
}

await fs.promises.writeFile(OUT_PATH, `${lines.join('\n')}\n`, 'utf8');
console.log(`Generated ${OUT_PATH}`);
