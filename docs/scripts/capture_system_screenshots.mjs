#!/usr/bin/env node
/**
Capture architecture screenshots for the SPA routes defined in UI/src/routes.js.

Usage examples:
  node docs/scripts/capture_system_screenshots.mjs \
    --base-url=http://localhost:5001 \
    --out-dir=docs/screenshots \
    --headless=true

  node docs/scripts/capture_system_screenshots.mjs \
    --base-url=http://localhost:5001 \
    --auth \
    --session-token=$SESSION_TOKEN \
    --role=association \
    --route-filter=subscription \
    --limit=20
*/

import fs from 'node:fs';
import path from 'node:path';

const ROOT = process.cwd();
const ROUTES_PATH = path.join(ROOT, 'UI/src/routes.js');

const args = process.argv.slice(2);
const toCamelCase = (input = '') => input
  .split('-')
  .filter(Boolean)
  .map((part, index) => (index === 0 ? part : `${part[0].toUpperCase()}${part.slice(1)}`))
  .join('');

const options = {
  baseUrl: 'http://localhost:5001',
  outDir: path.join(ROOT, 'docs/screenshots'),
  headless: true,
  auth: false,
  role: 'association',
  sessionToken: '',
  refreshToken: '',
  permissions: [],
  permissionsFile: '',
  limit: 0,
  routeFilter: '',
  manifest: true,
  waitMs: 900,
};

for (let i = 0; i < args.length; i += 1) {
  const current = args[i];
  if (!current.startsWith('--')) continue;

  const [rawKey, rawValue] = current.split('=', 2);
  const key = toCamelCase(rawKey.replace(/^--/, ''));

  if (typeof rawValue === 'string') {
    if ((key === 'route' || key === 'routeFilter') && !options.routeFilter) {
      options.routeFilter = rawValue;
      continue;
    }
    options[key] = rawValue;
  } else if (key === 'auth') {
    options.auth = true;
  } else if (key === 'headless') {
    options.headless = String(args[i + 1] || rawValue).toLowerCase() !== 'false';
    if (!current.includes('=')) i += 1;
  } else if (i + 1 < args.length && !args[i + 1].startsWith('--')) {
    options[key] = args[i + 1];
    i += 1;
  } else if (key === 'headless') {
    options.headless = true;
  } else if (key) {
    options[key] = 'true';
  }
}

if (options.permissionsFile) {
  const abs = path.resolve(options.permissionsFile);
  const fileValue = fs.readFileSync(abs, 'utf8').trim();
  options.permissions = fileValue
    ? JSON.parse(fileValue)
    : [];
}

if (typeof options.permissions === 'string' && options.permissions) {
  try {
    options.permissions = JSON.parse(options.permissions);
  } catch {
    options.permissions = options.permissions
      .split(',')
      .map((x) => x.trim())
      .filter(Boolean);
  }
}

if (!Array.isArray(options.permissions)) options.permissions = [];
if (typeof options.limit === 'string') options.limit = parseInt(options.limit, 10) || 0;
if (typeof options.waitMs === 'string') options.waitMs = parseInt(options.waitMs, 10) || 900;
if (typeof options.headless === 'string') {
  options.headless = ['false', '0', 'off', 'no'].indexOf(options.headless.toLowerCase()) === -1;
}
if (typeof options.auth === 'string') {
  options.auth = ['true', '1', 'on', 'yes'].indexOf(options.auth.toLowerCase()) !== -1;
}
if (typeof options.manifest === 'string') {
  options.manifest = ['true', '1', 'on', 'yes'].indexOf(options.manifest.toLowerCase()) !== -1;
}
if (options.routeFilter === 'true') options.routeFilter = '';

if (!fs.existsSync(ROUTES_PATH)) {
  throw new Error(`Missing route file: ${ROUTES_PATH}`);
}

const source = fs.readFileSync(ROUTES_PATH, 'utf8');
const routeMatches = [...source.matchAll(/^\s*['"]([^'"]+)['"]\s*:\s*wrap\(\{/gm)];
const rawRoutes = [...new Set(routeMatches.map((m) => m[1]))];

const sampleMap = {
  id: 'sample-id',
  uid: 'sample-uid',
  token: 'sample-token',
  tokenId: 'sample-token',
  workflow_id: 'wf-01',
  response_id: 'resp-01',
  username: 'sample-user',
  subscriptionId: 'sub-01',
  page: 'details',
  idp: 'idp',
  tab: 'main',
};

const concreteRoute = (route) => {
  if (route === '*' || route === '/') return '/';
  return route
    .split('/')
    .filter(Boolean)
    .map((seg) => {
      if (seg === '*') return 'sample';
      if (!seg.startsWith(':')) return seg;
      const clean = seg.replace(':', '').replace('?', '');
      return sampleMap[clean] || `sample-${clean}`;
    })
    .join('/');
};

const safeName = (route) => {
  const name = concreteRoute(route === '*' ? '/' : route).replace(/^\//, '') || 'root';
  return name
    .replace(/\//g, '__')
    .replace(/[^a-zA-Z0-9._-]/g, '-')
    .replace(/-+/g, '-')
    .toLowerCase();
};

const selectedRoutes = rawRoutes.filter((route) => {
  if (!options.routeFilter) return true;
  return route.includes(options.routeFilter);
});

const routesToCapture = options.limit > 0 ? selectedRoutes.slice(0, options.limit) : selectedRoutes;
const routeManifest = [];

let chromium;
try {
  ({ chromium } = await import('playwright'));
} catch {
  console.error(
    '[error] Playwright is not installed. '
    + 'Install it in the repo (for example: npm install -D playwright) '
    + 'or run scripts from an environment that has playwright available.',
  );
  process.exit(1);
}

console.log(`Captured route definitions: ${rawRoutes.length}`);
console.log(`Filtered route set: ${routesToCapture.length}`);

await fs.promises.mkdir(path.resolve(options.outDir), { recursive: true });
const browser = await chromium.launch({
  headless: options.headless !== false,
  args: ['--no-sandbox'],
});

const context = await browser.newContext();
if (options.auth && options.sessionToken) {
  const base = new URL(options.baseUrl);
  await context.addCookies([{
    name: 'BKN_AUTH',
    value: options.sessionToken,
    domain: base.hostname,
    path: '/',
    secure: base.protocol === 'https:',
    httpOnly: true,
    sameSite: 'Lax',
  }]);
}

await context.addInitScript(({ role, sessionToken, refreshToken, permissions, auth, expiresMs }) => {
  if (!localStorage) return;
  if (auth) {
    localStorage.setItem('sessionToken', JSON.stringify(sessionToken || null));
    localStorage.setItem('refreshToken', JSON.stringify(refreshToken || null));
    localStorage.setItem('role', JSON.stringify(role || 'association'));
    localStorage.setItem('permissions', JSON.stringify(permissions || []));
    localStorage.setItem('expires', JSON.stringify(Date.now() + (expiresMs || 36000000)));
  }
}, {
  role: options.role,
  sessionToken: options.sessionToken,
  refreshToken: options.refreshToken,
  permissions: options.permissions,
  auth: options.auth,
  expiresMs: parseInt(process.env.SCREENSHOT_SESSION_TTL || '7200000', 10),
});

let passed = 0;
let failed = 0;

for (const route of routesToCapture) {
  const clean = concreteRoute(route);
  const url = `${options.baseUrl}/#/${clean}`.replace('/#//', '/#/');
  const fileName = `${safeName(route)}.png`;
  const target = path.join(path.resolve(options.outDir), fileName);
  const page = await context.newPage();
  try {
    await page.setViewportSize({ width: 1600, height: 1200 });
    await page.goto(url, { waitUntil: 'networkidle', timeout: 45000 });
    await page.waitForTimeout(options.waitMs);
    await page.screenshot({
      path: target,
      fullPage: true,
    });
    passed += 1;
    routeManifest.push({
      route,
      concreteRoute: clean,
      url,
      file: path.relative(ROOT, target),
    });
    console.log(`[ok] ${route} -> ${target}`);
  } catch (error) {
    failed += 1;
    console.error(`[skip] ${route} :: ${error.message}`);
  } finally {
    await page.close();
  }
}

await browser.close();

if (options.manifest && passed > 0) {
  const manifestPath = path.join(path.resolve(options.outDir), 'index.md');
  const lines = [
    '# UI route screenshots',
    '',
    `Generated by \`docs/scripts/capture_system_screenshots.mjs\` from ${ROUTES_PATH}`,
    '',
    `Base URL: ${options.baseUrl}`,
    `Headless: ${options.headless}`,
    `Output directory: ${options.outDir}`,
    '',
    '| Route (template) | Concrete route | URL | Screenshot |',
    '| --- | --- | --- | --- |',
    ...routeManifest.map((entry) => `| \`${entry.route}\` | \`/${entry.concreteRoute}\` | ${entry.url} | ![${entry.route}](${entry.file}) |`),
    '',
  ];
  await fs.promises.writeFile(manifestPath, `${lines.join('\n')}\n`, 'utf8');
  console.log(`Manifest written: ${manifestPath}`);
}
console.log(`Done. success=${passed} failed=${failed} output=${options.outDir}`);
