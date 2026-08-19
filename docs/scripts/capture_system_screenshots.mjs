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
  loginUsername: process.env.SCREENSHOT_USERNAME || '',
  loginPassword: process.env.SCREENSHOT_PASSWORD || '',
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
  sportAssociationUsername: process.env.SCREENSHOT_ASSOCIATION || 'BAKNEY',
  subscriptionId: process.env.SCREENSHOT_SUBSCRIPTION_ID || '0f133c8b-084d-43cf-b402-9fa9265289dc',
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
      if (!seg.startsWith(':')) return seg.replace(/\?$/, '');
      const clean = seg.replace(/^:/, '').replace(/\?$/, '');
      return sampleMap[clean] || `sample-${clean}`;
    })
    .join('/');
};

const safeName = (route) => {
  const name = route === '*'
    ? 'wildcard-root'
    : (concreteRoute(route).replace(/^\//, '') || 'root');
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
  try {
    // The application keeps JavaScript dependencies under UI/; this fallback
    // lets the docs script run from the repository root without a second lockfile.
    ({ chromium } = await import(path.join(ROOT, 'UI', 'node_modules', 'playwright', 'index.mjs')));
  } catch {
    console.error(
      '[error] Playwright is not installed. '
      + 'Run `npm install --prefix UI -D playwright` and install a browser if needed.',
    );
    process.exit(1);
  }
}

console.log(`Captured route definitions: ${rawRoutes.length}`);
console.log(`Filtered route set: ${routesToCapture.length}`);

await fs.promises.mkdir(path.resolve(options.outDir), { recursive: true });
const browser = await chromium.launch({
  headless: options.headless !== false,
  args: ['--no-sandbox'],
  ...(process.env.PLAYWRIGHT_EXECUTABLE_PATH
    ? { executablePath: process.env.PLAYWRIGHT_EXECUTABLE_PATH }
    : {}),
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

let authenticationMode = options.auth ? 'synthetic-session' : 'anonymous';
let authenticatedStorageState = null;
if (options.loginUsername && options.loginPassword) {
  const loginPage = await context.newPage();
  try {
    await loginPage.goto(`${options.baseUrl}/#/login`, { waitUntil: 'domcontentloaded', timeout: 45000 });
    await loginPage.waitForSelector('input[name="username_login"]', { state: 'attached', timeout: 45000 });
    await loginPage.locator('input[name="username_login"]').fill(options.loginUsername);
    await loginPage.locator('input[name="password_login"]').fill(options.loginPassword);
    await loginPage.locator('#bkn_login_signin_submit').click();
    await loginPage.waitForFunction(
      () => Boolean(JSON.parse(localStorage.getItem('sessionToken') || 'null')),
      { timeout: 30000 },
    );
    authenticatedStorageState = await context.storageState();
    authenticationMode = 'browser-login';
    console.log('[ok] authenticated Playwright context');
  } catch (error) {
    throw new Error(`Playwright login failed: ${error.message}`);
  } finally {
    await loginPage.close();
  }
}

let passed = 0;
let failed = 0;

for (const route of routesToCapture) {
  const clean = concreteRoute(route);
  const url = `${options.baseUrl}/#/${clean}`.replace('/#//', '/#/');
  const fileName = `${safeName(route)}.png`;
  const target = path.join(path.resolve(options.outDir), fileName);
  // Route components can mutate currentPage or redirect to /login. Isolate
  // every capture so one route cannot contaminate the next route's session.
  // The login page itself is intentionally captured anonymously.
  const isolatedContext = authenticatedStorageState && route !== '/login'
    ? await browser.newContext({ storageState: authenticatedStorageState })
    : route === '/login'
      ? await browser.newContext()
      : context;
  const page = await isolatedContext.newPage();
  const pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));
  try {
    await page.setViewportSize({ width: 1600, height: 1200 });
    // Do not wait for networkidle: authenticated SPA pages intentionally keep
    // WebSocket connections open, so networkidle may never be reached.
    let navigationError = null;
    for (let attempt = 1; attempt <= 3; attempt += 1) {
      try {
        // `commit` avoids waiting on route-specific API/WebSocket work; the
        // explicit SPA readiness check below still gates the screenshot.
        await page.goto(url, { waitUntil: 'commit', timeout: 30000 });
        navigationError = null;
        break;
      } catch (error) {
        navigationError = error;
        if (attempt < 3) await page.waitForTimeout(500 * attempt);
      }
    }
    if (navigationError) throw navigationError;
    // A successful HTTP response is not enough for this Svelte SPA. Vite can
    // return index.html while the application bundle is still compiling or
    // while a runtime error has left #view empty; saving at that point creates
    // misleading all-white evidence. Wait until App.svelte has mounted.
    // replaceContainer() replaces #view itself, so the mounted App is not
    // reliably addressable through #view after startup. Visible body text is
    // the stable readiness signal for both the login page and routed views.
    let readinessError = null;
    try {
      await page.waitForFunction(
        () => Boolean(document.body?.innerText?.trim()),
        { timeout: 15000 },
      );
    } catch (error) {
      readinessError = error;
    }
    if (!readinessError) await page.waitForTimeout(options.waitMs);
    const visibleText = await page.evaluate(() => document.body?.innerText?.trim() || '');
    let captureStatus = 'rendered';
    if (readinessError || !visibleText) {
      // Some protected/data-dependent routes intentionally render no markup
      // when captured without a real session. Keep the screenshot useful and
      // explicit instead of publishing an indistinguishable white PNG.
      captureStatus = readinessError ? 'route-did-not-mount' : 'no-visible-content';
      await page.evaluate(({ route: routeName, targetUrl }) => {
        const notice = document.createElement('div');
        notice.setAttribute('data-screenshot-capture-notice', 'true');
        notice.style.cssText = [
          'box-sizing:border-box', 'position:fixed', 'inset:0', 'z-index:2147483647',
          'display:flex', 'align-items:center', 'justify-content:center',
          'padding:48px', 'background:#f8f9fc', 'font:600 22px/1.5 system-ui,sans-serif',
          'color:#1f2340', 'text-align:center',
        ].join(';');
        notice.textContent = `Route ${routeName} mounted without visible content in this capture.\n${targetUrl}`;
        document.body.appendChild(notice);
      }, { route, targetUrl: url });
    }
    await page.screenshot({
      path: target,
      fullPage: true,
    });
    passed += 1;
    routeManifest.push({
      route,
      concreteRoute: clean,
      url,
      // The manifest is written inside outDir, so links must be relative to it.
      file: path.relative(path.resolve(options.outDir), target),
      pageErrors,
      captureStatus,
    });
    console.log(`[ok] ${route} -> ${target}`);
  } catch (error) {
    failed += 1;
    console.error(`[skip] ${route} :: ${error.message}`);
  } finally {
    await page.close();
    if (isolatedContext !== context) await isolatedContext.close();
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
    `Authentication mode: ${authenticationMode}`,
    '',
    '| Route (template) | Concrete route | URL | Capture status | Screenshot | Browser errors |',
    '| --- | --- | --- | --- | --- | --- |',
    ...routeManifest.map((entry) => `| \`${entry.route}\` | \`/${entry.concreteRoute}\` | ${entry.url} | ${entry.captureStatus} | ![${entry.route}](${entry.file}) | ${entry.pageErrors.length} |`),
    '',
  ];
  await fs.promises.writeFile(manifestPath, `${lines.join('\n')}\n`, 'utf8');
  console.log(`Manifest written: ${manifestPath}`);
}
console.log(`Done. success=${passed} failed=${failed} output=${options.outDir}`);
