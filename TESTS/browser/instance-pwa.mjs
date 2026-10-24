// Unmodified UI consumer proof; no PWA installation or API-response interception.
import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';

const input = JSON.parse(await fs.readFile(process.env.TESTS_FIXTURE, 'utf8'));
const output = process.env.TESTS_OUTPUT;
const {chromium, expect} = await import(pathToFileURL(input.playwright_module).href);
const report = {success: false, checks: [], requests: [], external_blocked: [], failures: [],
    installed: false, operator_selection_verified: false, browser_closed: false, context_closed: false};
let browser, context;
const pending = [];
const save = () => fs.writeFile(path.join(output, 'browser.json'), JSON.stringify(report, null, 2) + '\n');
try {
    browser = await chromium.launch({headless: true});
    context = await browser.newContext({viewport: {width: 1280, height: 900}, colorScheme: 'light'});
    await context.route('**/*', async route => {
        const url = new URL(route.request().url());
        if (url.origin !== input.origin) {
            report.external_blocked.push({origin: url.origin, path: url.pathname});
            return route.abort();
        }
        return route.continue();
    });
    const page = await context.newPage();
    page.setDefaultTimeout(15000);
    page.on('pageerror', error => report.failures.push({kind: 'pageerror', message: error.message}));
    page.on('response', response => pending.push((async () => {
        const url = new URL(response.url());
        if (url.origin !== input.origin) return;
        const row = {path: url.pathname, query: url.search, status: response.status(), method: response.request().method()};
        report.requests.push(row);
        if (url.pathname.startsWith('/api/')) {
            assert(input.selected.includes(url.pathname), 'Unexpected API request: ' + url.pathname);
            assert.equal(response.request().method(), 'GET');
            assert.equal(response.status(), 200, 'Selected resource failed: ' + url.pathname);
            assert.equal(response.request().headers().authorization, undefined);
        }
        if (url.pathname === '/api/instance/logo.png') {
            const data = await response.body();
            row.sha256 = crypto.createHash('sha256').update(data).digest('hex');
            assert.equal(row.sha256, input.logo_sha256);
            assert.equal(response.headers()['content-type'], 'image/png');
            assert.equal(response.headers()['cache-control'], 'no-cache');
        }
        if (url.pathname === '/api/instance/manifest.json') {
            assert.deepEqual(await response.json(), input.manifest);
            assert.equal(response.headers()['content-type'], 'application/json');
        }
    })().catch(error => report.failures.push({kind: 'response', message: error.message}))));
    const cdp = await context.newCDPSession(page);
    await cdp.send('Page.enable');
    for (const navigation of ['open', 'reload']) {
        if (navigation === 'open') await page.goto(input.origin + '/#/login');
        else await page.reload();
        await expect(page).toHaveTitle(input.title);
        await expect(page.locator('meta[name="theme-color"]')).toHaveAttribute('content', input.color);
        await expect(page.locator('link[rel="manifest"]')).toHaveAttribute('href', '/api/instance/manifest.json');
        const logo = page.locator('img#logo:visible');
        await expect(logo).toHaveCount(1);
        await expect(logo).toHaveAttribute('src', input.logo_url);
        await expect.poll(() => logo.evaluate(img => img.complete && img.naturalWidth === 512 && img.naturalHeight === 512)).toBe(true);
        assert.equal(await page.evaluate(() => localStorage.getItem('sessionToken')), null);
        const manifest = await cdp.send('Page.getAppManifest');
        assert.equal(manifest.url, input.origin + '/api/instance/manifest.json');
        assert.deepEqual(JSON.parse(manifest.data), input.manifest);
        assert.deepEqual(manifest.errors, []);
        await page.screenshot({path: path.join(output, navigation + '.png'), fullPage: true});
        report.checks.push({navigation, success: true, title: await page.title(),
            manifest_url: manifest.url, manifest_parsed_by_chromium: true, logo_decoded: true,
            icon_installation_verified: false});
    }
    await Promise.all(pending);
    for (const selected of input.selected) assert(report.requests.some(row => row.path === selected && row.status === 200), 'No actual selected response: ' + selected);
    assert.deepEqual(report.failures, []);
    report.success = true;
} catch (error) {
    report.failures.push({kind: 'workflow', message: error.message});
    process.exitCode = 1;
} finally {
    try {
        if (context) {
            try { await context.close(); report.context_closed = true; }
            catch (error) { report.failures.push({kind: 'context-close', message: String(error)}); }
        }
    } finally {
        try {
            if (browser) {
                try { await browser.close(); report.browser_closed = report.context_closed = true; }
                catch (error) { report.failures.push({kind: 'browser-close', message: String(error)}); }
            }
        } finally {
            try { await Promise.all(pending); }
            catch (error) { report.failures.push({kind: 'observation-close', message: String(error)}); }
            report.success = Boolean(report.success && !report.failures.length && report.context_closed && report.browser_closed);
            if (!report.success) process.exitCode = 1;
            try { await save(); } catch (error) { process.exitCode = 1; throw error; }
        }
    }
}
