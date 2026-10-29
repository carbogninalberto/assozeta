// Actual unchanged checkout. Only fixture gate synchronization is synthetic;
// every business response comes from selected real APIs or explicit Django coexistence.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';

const input = JSON.parse(await fs.readFile(process.env.TESTS_FIXTURE, 'utf8'));
const {chromium, expect} = await import(pathToFileURL(input.playwright_module).href);
const output = process.env.TESTS_OUTPUT;
await fs.mkdir(output, {recursive: true});
const report = {success: false, scope: 'Unchanged anonymous checkout summary during loading, then real disabled-payment rejection',
    enabled_checkout_verified: false, security_semantics_approved: false, cases: [], responses: [],
    blocked_external: [], stripe_initialization_skipped: [], failures: [], contexts_closed: false, browser_closed: false};
const selected = input.version === 'v1' ? 'django-v1' : 'go-v2';
const pending = [], contexts = [];
let browser, phase = 'startup';
const match = (value, pattern) => new RegExp('^' + pattern.split('*').map(x => x.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('[^/]+') + '$').test(value);
async function save() { await fs.writeFile(path.join(output, 'browser.json'), JSON.stringify(report, null, 2) + '\n'); }
async function control(context, item, action) {
    const url = `${input.origin}/__fixture/payment-info/${item.id}/${action}`;
    const res = action === 'status' ? await context.request.get(url) : await context.request.post(url);
    assert.equal(res.status(), 200); return res.json();
}
async function observe(response) {
    const url = new URL(response.url());
    if (url.origin !== input.origin) return;
    const row = {phase, method: response.request().method(), path: url.pathname,
        status: response.status(), backend: await response.headerValue('x-fixture-backend')};
    report.responses.push(row);
    if (input.selected_routes.some(route => match(row.path, '/api/' + route))) {
        assert.equal(row.backend, selected, 'Wrong selected backend');
        assert.equal(row.method, 'GET'); assert.equal(row.status, 200, `Selected read failed: ${row.path}`);
        assert(!response.request().headers().authorization, 'Public checkout unexpectedly authenticated');
        if (row.path === '/api/instance/config') assert.equal((await response.json()).stripe.publicKey, null);
    } else if (input.coexistence_routes.some(route => match(row.path, '/api/' + route))) {
        assert.equal(row.backend, 'django-coexistence');
        assert.equal(row.status, row.path.startsWith('/api/stripe/pay/') ? 409 : 200);
    } else {
        assert(!row.path.startsWith('/api/'), 'Unaccounted API request');
        assert.equal(row.backend, 'ui-static');
        assert(row.status >= 200 && row.status < 400, 'Static resource failed');
    }
}
try {
    browser = await chromium.launch();
    for (const item of input.cases) {
        const context = await browser.newContext({viewport: {width: 1440, height: 1000}});
        contexts.push(context);
        await context.route('**/*', route => {
            const url = new URL(route.request().url());
            if (url.origin === input.origin) return route.continue();
            report.blocked_external.push({origin: url.origin, path: url.pathname});
            return route.abort('blockedbyclient');
        });
        const page = await context.newPage();
        page.setDefaultTimeout(15000);
        page.on('response', response => pending.push(observe(response).catch(error => {
            report.failures.push({phase, kind: 'response', message: error.message});
        })));
        page.on('console', message => {
            if (message.type() === 'warning' && message.text() === '[STRIPE] Cannot load Stripe Component because of missing public key')
                report.stripe_initialization_skipped.push({phase});
        });
        page.on('pageerror', error => report.failures.push({phase, kind: 'pageerror', name: error.name, message: error.message}));
        page.on('requestfailed', request => {
            const url = new URL(request.url());
            if (url.origin === input.origin && request.failure()?.errorText !== 'net::ERR_ABORTED')
                report.failures.push({phase, kind: 'requestfailed', path: url.pathname});
        });
        for (const navigation of ['open', 'revisit']) {
            phase = item.name + '-' + navigation;
            const warningsBefore = report.stripe_initialization_skipped.length;
            const receipt = {name: item.name, navigation, success: false};
            report.cases.push(receipt);
            await control(context, item, 'arm');
            const infoURL = '/api/payment/' + item.id + '/info';
            const intentURL = '/api/stripe/pay/' + item.id;
            const infoPending = page.waitForResponse(res => new URL(res.url()).pathname === infoURL);
            // Revisit the real checkout URL; after the
            // rejection the current page has navigated away. This is not page.reload().
            await page.goto(input.origin + '/#/stripe/pay/' + item.id);
            const infoResponse = await infoPending;
            assert.equal(infoResponse.status(), 200, 'Actual payment-info route must exist');
            const data = (await infoResponse.json()).data;
            assert.equal(data.amount, item.amount); assert.equal(data.subject, item.subject);
            assert.deepEqual(data.associate, {first_name: 'Ada', last_name: 'Browser', tax_code: 'BROWSERFAMILYCODE'});
            try {
                await expect.poll(async () => (await control(context, item, 'status')).entered).toBe(true);
                await expect(page.locator('#accordion-payment-details > div').first().locator('h3')).toHaveText(item.heading);
                await expect(page.locator('#info-payment-invoice')).toContainText('Ada Browser');
                await expect(page.locator('#info-payment-invoice')).toContainText('BROWSERFAMILYCODE');
                const amount = '€' + item.amount.replace('.', ',');
                await expect(page.locator('.card-header')).toContainText('Cassa');
                await expect(page.locator('.card-header')).toContainText(amount);
                const summary = page.locator('#accordion-payment-details');
                await expect(summary).toContainText(amount + ' x 1');
                const metadata = summary.locator('div.px-2 h3');
                await expect(metadata).toHaveText(item.metadata.map(row => row[0]));
                if (item.metadata.length) {
                    await expect(summary.getByText('Altre Causali', {exact: true})).toBeVisible();
                    const amounts = summary.locator('div.px-2 span.text-dark-80');
                    await expect(amounts).toHaveText(item.metadata.map(row => row[1]));
                } else await expect(summary.getByText('Altre Causali', {exact: true})).toHaveCount(0);
                await expect(page.getByText('Informazioni aggiuntive', {exact: true})).toHaveCount(item.additional ? 1 : 0);
                if (item.additional) await expect(page.getByText('Conserva la ricevuta & il riepilogo.', {exact: true})).toBeVisible();
                await expect(page.locator('.loading-overlay')).toBeVisible();
                await expect(page.locator('.loading-overlay')).toContainText('Caricamento in corso...');
                await expect(page.locator('.card-body > .row')).toHaveCSS('opacity', '0.3');
                await expect(page.locator('.card-body > .row')).toHaveCSS('pointer-events', 'none');
                await expect(page.locator('#submit')).toHaveCount(0);
                await expect(page.locator('#payment-element iframe')).toHaveCount(0);
                assert.equal(await page.evaluate(() => localStorage.getItem('sessionToken')), null);
                const gate = await control(context, item, 'status');
                assert(gate.entered && !gate.released && !gate.completed && !gate.timed_out);
                receipt.summary_rendered_under_real_loading_state = true;
                await page.screenshot({path: path.join(output, phase + '.png'), fullPage: true});
                const rejected = page.waitForResponse(res => new URL(res.url()).pathname === intentURL && res.request().method() === 'POST');
                await control(context, item, 'release');
                const rejection = await rejected;
                assert.equal(rejection.status(), 409);
                assert.deepEqual(await rejection.json(), {error: 'Online payments are not configured.'});
                await expect.poll(async () => (await control(context, item, 'status')).completed).toBe(true);
                await expect.poll(() => new URL(page.url()).hash.startsWith('#/stripe/pay/')).toBe(false);
                await expect.poll(() => report.stripe_initialization_skipped.length).toBe(warningsBefore + 1);
                receipt.missing_public_key_warning_observed = true;
                receipt.final_hash = new URL(page.url()).hash;
                receipt.actual_disabled_payment_status = 409;
                assert.equal((await control(context, item, 'checkpoint')).managed_state_unchanged, true);
                receipt.managed_state_unchanged = true;
                receipt.success = true;
            } finally {
                const gate = await control(context, item, 'status');
                if (gate.entered && !gate.released) await control(context, item, 'release');
                await expect.poll(async () => (await control(context, item, 'status')).completed).toBe(true);
                await save();
            }
        }
        await context.close();
    }
    await Promise.all(pending);
    assert.deepEqual(report.failures, []);
    assert.equal(report.cases.length, 8);
    assert(report.cases.every(row => row.success));
    report.success = true;
} catch (error) {
    report.failures.push({phase, kind: 'workflow', name: error.name, message: error.message});
    process.exitCode = 1;
} finally {
    await finalize();
}

async function finalize() {
    let closedContexts = 0;
    try {
        for (const context of contexts) {
            try {
                await context.close();
                closedContexts += 1;
            } catch (error) {
                report.failures.push({phase, kind: 'context-close', message: String(error)});
            }
        }
        report.contexts_closed = closedContexts === contexts.length;
    } finally {
        try {
            if (browser) {
                try {
                    await browser.close();
                    report.browser_closed = true;
                    // A successful browser close also closes any context whose
                    // individual close failed; the error still fails the proof.
                    report.contexts_closed = true;
                } catch (error) {
                    report.failures.push({phase, kind: 'browser-close', message: String(error)});
                }
            }
        } finally {
            try {
                await Promise.all(pending);
            } catch (error) {
                report.failures.push({phase, kind: 'final-observation', message: String(error)});
            } finally {
                report.success = Boolean(report.success && report.failures.length === 0 &&
                    report.contexts_closed && report.browser_closed);
                if (!report.success) process.exitCode = 1;
                try {
                    await save();
                } catch (error) {
                    process.exitCode = 1;
                    throw error;
                }
            }
        }
    }
}
