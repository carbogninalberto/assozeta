// Real frontend + real disposable backend. No billing/profile API mocks.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium, expect} from '../../selfhost/tests/browser/node_modules/@playwright/test/index.mjs';
import {preview} from '../../UI/node_modules/vite/dist/node/index.js';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const fixture = JSON.parse(await fs.readFile(process.env.TESTS_FIXTURE, 'utf8'));
const output = process.env.TESTS_OUTPUT || path.join(root, 'quality-reports/contracts/browser');
await fs.mkdir(output, {recursive: true});
process.env.DEPLOY_ENV = 'development';
process.env.VITE_DEV_HTTPS = 'false';
process.env.VITE_API_PROXY_TARGET = process.env.TESTS_BASE_URL;
process.env.VITE_WS_PROXY_TARGET = process.env.TESTS_BASE_URL.replace(/^http/, 'ws');
process.chdir(path.join(root, 'UI'));
await fs.access(path.join(root, 'UI/dist/public/index.html'));
const server = await preview({root: process.cwd(), configFile: path.join(process.cwd(), 'vite.config.js'),
    preview: {host: '127.0.0.1', port: 0, https: false}, logLevel: 'error'});
let browser, activePage;
const results = [];
try {
    const origin = `http://127.0.0.1:${server.httpServer.address().port}`;
    browser = await chromium.launch({headless: true});
    for (const account of ['missing', 'expired']) {
        console.log(`Checking ${account} owner login and navigation`);
        const context = await browser.newContext({viewport: {width: 1440, height: 1000}});
        const page = await context.newPage();
        activePage = page;
        page.setDefaultTimeout(90000);
        const billingRequests = [], errors = [];
        page.on('pageerror', error => errors.push(error.message));
        page.on('request', request => {
            if (new URL(request.url()).pathname.includes('/billing/')) billingRequests.push(request.url());
        });
        await page.addInitScript(() => {
            localStorage.setItem('isExpired', 'true');
            localStorage.setItem('billingData', JSON.stringify({active_plan: {billing_type: 1}, ends_on: '2000-01-01'}));
        });
        await page.goto(origin + '/#/login', {waitUntil: 'domcontentloaded', timeout: 120000});
        await page.getByPlaceholder('Inserisci email / username').fill(fixture.accounts[account].username);
        await page.getByPlaceholder('Inserisci la password...').fill(fixture.accounts[account].password);
        await page.getByRole('button', {name: 'Accedi', exact: true}).click();
        await expect(page.locator('#bkn_aside')).toBeVisible({timeout: 60000});
        assert(!page.url().includes('/subscription/upgrade'));
        for (const route of ['/calendar', '/payment/list']) {
            await page.goto(origin + '/#' + route, {waitUntil: 'domcontentloaded', timeout: 120000});
            await expect(page.locator('#bkn_aside')).toBeVisible({timeout: 30000});
            await expect.poll(() => page.evaluate(() => JSON.parse(localStorage.getItem('permissions') || '[]')))
                .toContain(route === '/calendar' ? 'association.calendar.read' : 'bookeeping.payments.read');
            assert.equal(new URL(page.url()).hash, '#' + route);
            if (route === '/calendar') {
                await expect(page.locator('#general_calendar').getByRole('button', {name: 'Oggi', exact: true})).toBeVisible({timeout: 60000});
                await expect(page.locator('#general_calendar').getByText('Nessun evento', {exact: true})).toBeVisible();
            } else {
                await expect(page.locator('#payment-card .datatable:not(.datatable-loading):not(.datatable-error) .datatable-body')).toBeVisible({timeout: 60000});
            }
        }
        await expect(page.getByText('Attendere prego...', {exact: true})).toBeHidden({timeout: 60000});
        await page.screenshot({path: path.join(output, account + '-navigation.png'), fullPage: true, timeout: 15000, animations: 'disabled'});
        assert.deepEqual(billingRequests, [], 'Frontend must not request software billing');
        assert.deepEqual(errors, [], 'Unexpected browser runtime errors');
        results.push({account, login: true, calendar: true, payments: true, billingRequests, errors});
        await context.close();
    }
    await fs.writeFile(path.join(output, 'browser.json'), JSON.stringify(results, null, 2));
} catch (error) {
    await fs.writeFile(path.join(output, 'failure.json'), JSON.stringify({message: error.message, stack: error.stack}, null, 2));
    await activePage?.screenshot({path: path.join(output, 'failure.png'), fullPage: true, timeout: 15000, animations: 'disabled'}).catch(() => {});
    throw error;
} finally {
    await browser?.close();
    server.httpServer.closeAllConnections();
    await new Promise((resolve, reject) => server.httpServer.close(error => error ? reject(error) : resolve()));
}
