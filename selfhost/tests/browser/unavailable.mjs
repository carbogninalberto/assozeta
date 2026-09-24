// Production frontend + shipped Caddy routes, with disposable upstream responses.
// Run after building UI: node selfhost/tests/browser/unavailable.mjs
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {createServer} from 'node:http';
import {mkdtemp, mkdir, readFile, writeFile, unlink, rm, realpath} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium} from '@playwright/test';

const root = fileURLToPath(new URL('../../../', import.meta.url));
const dist = join(root, 'UI/dist/public');
for (const asset of ['sw.js', 'offline.html', 'maintenance.html']) {
    assert.equal(await readFile(join(dist, asset), 'utf8'),
        await readFile(join(root, 'UI/public', asset), 'utf8'),
        'Rebuild the production frontend before running this test: ' + asset);
}
const temporary = await realpath(await mkdtemp(join(tmpdir(), 'assozeta-unavailable-')));
const output = process.env.ASSOZETA_UNAVAILABLE_OUTPUT || join(tmpdir(), 'assozeta-unavailable-browser');
await mkdir(output, {recursive: true});
const marker = join(temporary, 'maintenance.flag');
const container = 'assozeta-unavailable-' + process.pid;
let ready = true;
const api = createServer((req, res) => {
    res.setHeader('Content-Type', 'application/json');
    res.setHeader('Cache-Control', 'no-store');
    const send = (status, body) => {res.writeHead(status); res.end(JSON.stringify(body));};
    if (req.url === '/readyz') return send(ready ? 200 : 503, {ready});
    if (req.url === '/instance/status') return send(200, {configured: false});
    if (req.url === '/instance-update-status') {
        if (req.headers.authorization !== 'Bearer fixture-owner') return send(403, {error: 'Forbidden'});
        return send(200, {active: {stage: 'migrating'}});
    }
    if (req.url === '/failure') return send(500, {error: 'Upstream failure'});
    return send(404, {error: 'Unknown fixture endpoint'});
});
let browser;
let running = false;
try {
    await new Promise(resolve => api.listen(0, '0.0.0.0', resolve));
    const upstream = 'host.docker.internal:' + api.address().port;
    const config = (await readFile(join(root, 'selfhost/caddy/Caddyfile'), 'utf8'))
        .replaceAll('api:8000', upstream)
        .replace('unix//run/assozeta-update-status/status.sock', upstream);
    await writeFile(join(temporary, 'Caddyfile'), config);
    execFileSync('docker', ['run', '--rm', '-d', '--name', container,
        '--add-host', 'host.docker.internal:host-gateway', '-p', '127.0.0.1::80',
        '-e', 'SITE_ADDRESS=:80', '-v', dist + ':/srv:ro',
        '-v', temporary + ':/run/assozeta-operations:ro',
        '-v', join(temporary, 'Caddyfile') + ':/etc/caddy/Caddyfile:ro', 'caddy:2.9.1-alpine'],
    {stdio: 'pipe'});
    running = true;
    const port = execFileSync('docker', ['port', container, '80/tcp'], {encoding: 'utf8'}).trim().split(':').at(-1);
    const origin = 'http://127.0.0.1:' + port;
    let listening = false;
    for (let attempt = 0; attempt < 50; attempt++) {
        try {listening = (await fetch(origin + '/healthz')).ok;} catch {}
        if (listening) break;
        await new Promise(resolve => setTimeout(resolve, 100));
    }
    assert.ok(listening, 'Caddy started');
    browser = await chromium.launch({headless: true});
    const context = await browser.newContext({serviceWorkers: 'allow'});
    // The production app installs the worker itself; no test-side registration.
    const page = await context.newPage();
    await page.route('**/*', route => new URL(route.request().url()).origin === origin
        ? route.continue() : route.abort());
    await page.goto(origin + '/#/login', {waitUntil: 'domcontentloaded'});
    await page.waitForFunction(() => navigator.serviceWorker.controller !== null, null, {timeout: 30000});
    assert.equal(await page.evaluate(() => new URL(navigator.serviceWorker.controller.scriptURL).pathname), '/sw.js');
    await page.evaluate(() => localStorage.setItem('unavailable-test-sentinel', 'preserved'));
    await context.setOffline(true);
    assert.equal(await page.evaluate(() => fetch('/api/probe').then(() => 'response', () => 'failed')), 'failed');
    const offline = await page.goto(origin + '/members?view=active#/detail', {waitUntil: 'domcontentloaded'});
    assert.equal(offline.status(), 200);
    assert.match(await page.locator('h1').innerText(), /Non riusciamo/);
    // Set a saved palette after leaving the app, whose startup can still update it.
    await page.evaluate(() => {
        localStorage.setItem('userData', JSON.stringify({dark_mode: false}));
        localStorage.setItem('assozeta_brand_palette', JSON.stringify({
            light: {'--primary': '#f9e300', '--primary-text': '#554d00', '--on-primary': '#000000'},
            dark: {'--primary': '#f9e300', '--primary-text': '#f9e300', '--on-primary': '#000000'},
        }));
    });
    await page.reload({waitUntil: 'domcontentloaded'});
    assert.equal(await page.locator('main').evaluate(el => getComputedStyle(el).borderRadius), '24px');
    assert.equal(await page.locator('.btn-primary').evaluate(el => getComputedStyle(el).backgroundColor), 'rgb(249, 227, 0)');
    assert.equal(await page.locator('.eyebrow').evaluate(el => getComputedStyle(el).color), 'rgb(85, 77, 0)');
    assert.equal(await page.locator('link[rel="stylesheet"], script[src]').count(), 0);
    for (const [width, dark] of [[1280, false], [320, false], [390, true]]) {
        await page.setViewportSize({width, height: 720});
        await page.evaluate(dark => localStorage.setItem('userData', JSON.stringify({dark_mode: dark})), dark);
        await page.reload({waitUntil: 'domcontentloaded'});
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
        assert.equal(await page.evaluate(() => document.documentElement.dataset.theme === 'dark'), dark);
        await page.screenshot({path: join(output, 'offline-' + width + '.png'), fullPage: true});
    }
    await context.setOffline(false);
    await writeFile(marker, '');
    ready = false;
    for (const method of ['GET', 'HEAD', 'POST', 'PUT', 'DELETE']) {
        const response = await fetch(origin + '/api/example', {method});
        assert.equal(response.status, 503);
        assert.equal(response.headers.get('cache-control'), 'no-store');
        assert.equal(response.headers.get('retry-after'), '60');
    }
    assert.equal((await fetch(origin + '/healthz')).status, 200);
    const apiReadiness = await fetch(origin + '/api/readyz');
    assert.equal(apiReadiness.status, 503);
    assert.deepEqual(await apiReadiness.json(), {ready: false});
    assert.equal((await fetch(origin + '/instance-update-status', {method: 'POST'})).status, 403);
    assert.deepEqual(await (await fetch(origin + '/instance-update-status', {
        method: 'POST', headers: {Authorization: 'Bearer fixture-owner'},
    })).json(), {active: {stage: 'migrating'}});
    for (const asset of ['/sw.js', '/offline.html']) assert.equal((await fetch(origin + asset)).status, 200);
    const maintenance = await page.reload({waitUntil: 'domcontentloaded'});
    assert.equal(maintenance.status(), 503, 'Worker must preserve the live maintenance response');
    assert.match(await page.locator('h1').innerText(), /Torniamo tra poco/);
    assert.equal(await page.locator('link[rel="stylesheet"], script[src]').count(), 0);
    await page.screenshot({path: join(output, 'maintenance-mobile-dark.png'), fullPage: true});
    const fresh = await browser.newContext();
    const freshPage = await fresh.newPage();
    assert.equal((await freshPage.goto(origin + '/deep/link')).status(), 503);
    assert.equal(await freshPage.locator('main').evaluate(el => getComputedStyle(el).borderRadius), '24px');
    await freshPage.screenshot({path: join(output, 'maintenance-desktop.png')});
    await fresh.close();
    ready = true;
    assert.equal((await fetch(origin + '/api/readyz')).status, 200);
    assert.equal((await fetch(origin + '/')).status, 503, 'Readiness does not bypass the maintenance marker');
    await unlink(marker);
    const navigated = page.waitForNavigation({waitUntil: 'domcontentloaded'});
    await page.getByRole('button', {name: 'Riprova'}).click();
    assert.equal((await navigated).status(), 200);
    assert.equal(page.url(), origin + '/members?view=active#/detail');
    assert.equal(await page.evaluate(() => localStorage.getItem('unavailable-test-sentinel')), 'preserved');
    assert.equal((await page.goto(origin + '/api/failure')).status(), 500, 'HTTP errors are not offline fallbacks');
    console.log('PASS: automatic production registration, styled offline fallback, mobile/dark themes, maintenance 503, readiness/status access, and retry recovery.');
    console.log('Screenshots: ' + output);
} finally {
    if (browser) await browser.close();
    if (running) execFileSync('docker', ['stop', container], {stdio: 'pipe'});
    api.closeAllConnections();
    await new Promise(resolve => api.close(resolve));
    await rm(temporary, {recursive: true, force: true});
}
