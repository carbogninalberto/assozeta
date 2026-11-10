// Web UI API version switch (v1 Django /api, v2 Go /api/v2) on the built UI
// behind the shipped Caddyfile. Real password login; no API response is mocked
// except the readiness probe failure of the fallback phase.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';

const input = JSON.parse(await fs.readFile(process.env.TESTS_FIXTURE, 'utf8'));
for (const key of ['origin', 'username', 'password', 'playwright_module']) assert(input[key], 'fixture.' + key);
const {chromium, expect} = await import(pathToFileURL(input.playwright_module).href);
const output = process.env.TESTS_OUTPUT;
assert(output, 'TESTS_OUTPUT');
await fs.mkdir(output, {recursive: true});
const browser = await chromium.launch();
const phases = [], failures = [], checks = [];
const pageIds = new WeakMap();
let contexts = 0;
let phase = 'initialization', phaseRequests = [], success = false, current = null;
const deadline = setTimeout(() => {failures.push({kind: 'workflow-deadline', phase}); void browser.close();}, 420000);
// The canonical logo/manifest URLs and WebSockets stay on Django by design.
const DJANGO_BY_DESIGN = [/^\/api\/instance\/manifest\.json$/, /^\/api\/instance\/logo\.png$/];
// Go refuses these inputs explicitly (not ported; docs/architecture/go-migration):
// recorded as known gaps instead of failing the run.
const KNOWN_V2_GAPS = ['/api/v2/statistic/dashboard?widget=todaylessons'];
const knownGaps = [];

function begin(name) {
    phase = name; phaseRequests = [];
    phases.push({name, start: Date.now(), end: null, requests: phaseRequests});
}
function end() { phases.at(-1).end = Date.now(); }

async function context({query = '', readyz = null} = {}) {
    const ctx = await browser.newContext({viewport: {width: 1440, height: 1000}});
    // Only external requests and the simulated probe failure are intercepted;
    // same-origin application traffic reaches Caddy untouched.
    await ctx.route(url => url.origin !== input.origin, route => route.abort('blockedbyclient'));
    if (readyz) await ctx.route(url => url.origin === input.origin && url.pathname === '/api/v2/readyz',
        route => route.fulfill({status: readyz, body: 'unavailable'}));
    const page = await ctx.newPage(); page.setDefaultTimeout(30000); current = page;
    const id = ++contexts; pageIds.set(page, id);
    page.on('request', request => {
        const url = new URL(request.url());
        if (url.origin === input.origin && (url.pathname.startsWith('/api') || url.pathname.startsWith('/ws/')))
            phaseRequests.push({context: id, method: request.method(), path: url.pathname, type: request.resourceType()});
    });
    page.on('response', response => {
        const url = new URL(response.url());
        if (url.origin === input.origin && url.pathname.startsWith('/api'))
            phaseRequests.push({response: response.status(), path: url.pathname});
        if (url.origin === input.origin && response.status() >= 500 && KNOWN_V2_GAPS.includes(url.pathname + url.search)) {
            knownGaps.push({phase, path: url.pathname + url.search, status: response.status()});
        } else if (url.origin === input.origin && url.pathname.startsWith('/api') && response.status() >= 500
            && !(readyz && url.pathname === '/api/v2/readyz'))
            void response.text().then(text => failures.push({kind: 'server-error', phase, path: url.pathname,
                status: response.status(), body: text.slice(0, 300)}), () => failures.push({kind: 'server-error', phase,
                path: url.pathname, status: response.status()}));
    });
    page.on('requestfailed', request => {
        const url = new URL(request.url());
        if (url.origin === input.origin) phaseRequests.push({failed: request.failure()?.errorText, path: url.pathname});
    });
    page.on('pageerror', error => failures.push({kind: 'pageerror', phase, message: String(error.message).slice(0, 200)}));
    await page.goto(input.origin + '/' + query + '#/login');
    return {ctx, page};
}

async function login(page) {
    await expect(page.locator('input[name="username_login"]')).toBeVisible();
    await page.locator('input[name="username_login"]').fill(input.username);
    await page.locator('input[name="password_login"]').fill(input.password);
    await page.locator('#bkn_login_signin_submit').click();
    // The login redirect settles before the pages are opened directly.
    await expect(page.locator('#bkn_aside')).toBeVisible();
    await page.waitForLoadState('networkidle');
    await page.goto(input.origin + '/#/');
    await expect(page.locator('#bkn_aside')).toBeVisible();
    const notice = page.getByRole('button', {name: /Ok, grazie/});
    if (await notice.isVisible()) await notice.click();
}

// Core pages: dashboard, members, payment categories, Self Instance.
async function corePages(page) {
    await page.goto(input.origin + '/#/');
    await expect(page.locator('#bkn_aside')).toBeVisible();
    await page.goto(input.origin + '/#/members/list');
    await expect(page.getByText('Tesserati e soci tesserati.', {exact: true})).toBeVisible();
    await expect(page.getByText('Nessun dato disponibile', {exact: true})).toBeVisible();
    await page.goto(input.origin + '/#/payment/category/list');
    await expect(page.getByText('Causali dei pagamenti', {exact: true})).toBeVisible();
    await expect(page.locator('[data-row]').filter({hasText: 'BROWSER BASELINE CATEGORY'})).toBeVisible();
    await openApiSettings(page);
    await page.waitForLoadState('networkidle');
}

async function openApiSettings(page) {
    await page.goto(input.origin + '/#/profile?page=self-instance');
    const tabs = page.getByRole('tablist', {name: 'Sezioni istanza'});
    await tabs.getByRole('tab', {name: 'Impostazioni'}).click();
    await page.getByRole('tablist', {name: 'Impostazioni'}).getByRole('tab', {name: 'API (beta)'}).click();
    await expect(page.locator('#instance-api-version')).toBeVisible();
    return page.locator('#instance-api-v2');
}

function apiRequests() {
    return phaseRequests.filter(r => r.method && r.path.startsWith('/api') && r.path !== '/api/v2/readyz'
        && !DJANGO_BY_DESIGN.some(pattern => pattern.test(r.path)));
}
// A browser without a cached instance setting (first visit, or after login
// clears localStorage) reads the public status/config from /api to learn it.
const BOOTSTRAP = new Set(['/api/instance/status', '/api/instance/config']);
// Only the given page's requests: an earlier browser left open keeps polling.
function requireOnly(page, version, {bootstrapOnV1 = false} = {}) {
    const requests = apiRequests().filter(r => r.context === pageIds.get(page) && !(bootstrapOnV1 && BOOTSTRAP.has(r.path)));
    assert(requests.length >= 5, phase + ': too few API requests observed (' + requests.length + ')');
    const wrong = requests.filter(r => version === 'v2' ? !r.path.startsWith('/api/v2/') : r.path.startsWith('/api/v'));
    assert.deepEqual(wrong, [], phase + ': API requests on the wrong version');
    checks.push(phase + ': ' + requests.length + ' API requests all on ' + (version === 'v2' ? '/api/v2' : '/api'));
}
async function noScaryUi(page) {
    await expect(page.locator('.swal2-popup:visible')).toHaveCount(0);
    await expect(page.locator('[data-sonner-toast][data-type="error"]:visible')).toHaveCount(0);
}

try {
    begin('v1-default');
    let a = await context();
    await login(a.page);
    await corePages(a.page);
    const toggle = await openApiSettings(a.page);
    await expect(toggle).not.toBeChecked();
    await expect(a.page.getByText('Questo browser usa ora l’API attuale (v1).')).toBeVisible();
    requireOnly(a.page, 'v1');
    end();

    begin('enable');
    await a.page.locator('label[for="instance-api-v2"]').click();
    await expect(a.page.getByText('Nuova API attivata.')).toBeVisible();
    await expect(toggle).toBeChecked();
    await expect(a.page.getByText('Questo browser usa ora l’API v2.')).toBeVisible();
    assert(phaseRequests.some(r => r.method === 'PUT' && r.path === '/api/instance/admin'), 'enable: PUT on the current API');
    checks.push('enable: owner toggle saved through PUT /api/instance/admin');
    end();

    begin('v2-same-session');
    await corePages(a.page);
    requireOnly(a.page, 'v2');
    await a.ctx.close();
    end();

    begin('v2-new-browser');
    let b = await context();
    await login(b.page);
    await corePages(b.page);
    await noScaryUi(b.page);
    requireOnly(b.page, 'v2', {bootstrapOnV1: true});
    assert(phaseRequests.some(r => r.path === '/api/v2/readyz'), 'v2: readiness probe');
    assert(phaseRequests.some(r => r.path === '/api/v2/oauth2/login'), 'v2: password login on v2');
    end();

    begin('override-v1');
    let c = await context({query: '?api=v1'});
    await login(c.page);
    await corePages(c.page);
    requireOnly(c.page, 'v1');
    assert.equal(await c.page.evaluate(() => localStorage.getItem('assozeta_api_override')), 'v1');
    await expect(c.page.getByText('Questo browser usa la v1 (override ?api=v1).')).toBeVisible();
    await c.ctx.close();
    end();

    begin('probe-failure-fallback');
    let d = await context({readyz: 503});
    await login(d.page);
    await corePages(d.page);
    await noScaryUi(d.page);
    requireOnly(d.page, 'v1');
    assert(phaseRequests.some(r => r.path === '/api/v2/readyz'), 'fallback: probe attempted');
    await d.ctx.close();
    end();

    begin('disable');
    const off = await openApiSettings(b.page);
    await expect(off).toBeChecked();
    await b.page.locator('label[for="instance-api-v2"]').click();
    await expect(b.page.getByText('Nuova API disattivata.')).toBeVisible();
    await expect(off).not.toBeChecked();
    assert(phaseRequests.some(r => r.method === 'PUT' && r.path === '/api/v2/instance/admin'), 'disable: PUT on v2');
    await b.ctx.close();
    end();

    begin('v1-after-disable');
    let e = await context();
    await login(e.page);
    await corePages(e.page);
    requireOnly(e.page, 'v1');
    await e.ctx.close();
    end();

    assert.deepEqual(failures, [], 'browser failures');
    success = true;
} catch (error) {
    failures.push({kind: 'assertion', phase, message: String(error.message).slice(0, 2000)});
    if (current) await current.screenshot({path: path.join(output, 'failure.png'), fullPage: true}).catch(() => {});
} finally {
    clearTimeout(deadline);
    if (phases.length && phases.at(-1).end === null) end();
    await fs.writeFile(path.join(output, 'browser.json'), JSON.stringify({success, failures, checks, known_gaps: knownGaps, phases}, null, 1) + '\n');
    await browser.close();
}
process.exit(success ? 0 : 1);
