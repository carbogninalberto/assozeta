// Phase 5 installed lane (TESTS/handoff/test_p5_installed.py): a signed-in owner switches the real
// installation from the Runtime tab, unmodified UI, no request interception.
//   P5_BASE_URL, P5_SESSION (JSON localStorage entries of a signed-in owner), P5_PASSWORD, P5_TARGET,
//   P5_REPORT (output JSON), PLAYWRIGHT_MODULE (path to playwright's index.mjs).
// Report: {operation, checks[], failure|null}. A contract failure is reported as 'P5 contract red: ...'.
import fs from 'node:fs/promises';
import {pathToFileURL} from 'node:url';

const {chromium} = await import(pathToFileURL(process.env.PLAYWRIGHT_MODULE).href);
const base = process.env.P5_BASE_URL, target = process.env.P5_TARGET, password = process.env.P5_PASSWORD;
const session = JSON.parse(process.env.P5_SESSION);
const label = {django: 'Django', go: 'Go'}[target];
const settings = '/#/profile?page=self-instance&tab=api';
const report = {operation: null, checks: [], failure: null};
const red = message => { throw new Error('P5 contract red: ' + message); };
const check = (ok, message) => { report.checks.push({ok: !!ok, message}); if (!ok) red(message); };
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
let browser;

async function context() {
    const items = Object.entries(session).map(([name, value]) => ({name, value: JSON.stringify(value)}));
    return browser.newContext({baseURL: base, serviceWorkers: 'block',
        storageState: {cookies: [], origins: [{origin: base, localStorage: items}]}});
}
async function open(ctx, path) {
    const page = await ctx.newPage();
    page.setDefaultTimeout(30000);
    await page.goto(path);
    return page;
}
async function ready() {
    try { return (await fetch(base + '/api/readyz', {cache: 'no-store'})).status; } catch { return null; }
}
const text = async (page, id) => (await page.getByTestId('runtime-' + id).filter({visible: true}).first().textContent() || '').trim();

try {
    browser = await chromium.launch({headless: true});
    const settingsContext = await context(), otherContext = await context();
    const page = await open(settingsContext, settings);
    await page.getByRole('switch', {name: 'Usa Go per l’istanza', exact: true}).waitFor();
    check(await text(page, 'effective') !== label, `settings open before submission show the source runtime`);
    const other = await open(otherContext, '/#/');
    const sockets = [];
    other.on('websocket', ws => sockets.push({url: ws.url(), at: Date.now()}));
    let navigations = 0;
    other.on('framenavigated', frame => { if (frame === other.mainFrame()) navigations += 1; });
    navigations = 0;

    const toggle = page.getByRole('switch', {name: 'Usa Go per l’istanza', exact: true});
    await toggle.click();
    const dialog = page.getByRole('dialog');
    await dialog.getByLabel('Conferma la tua password').fill(password);
    await dialog.getByRole('button', {name: 'Conferma cambio'}).click();
    await page.getByTestId('runtime-operation').filter({visible: true}).first().waitFor({timeout: 60000});
    report.operation = await text(page, 'operation');
    check(/^[0-9a-f-]{36}$/.test(report.operation), 'the submitted operation id is shown');

    // While neither API serves, a cold signed-in browser resumes the same operation from the controller.
    let cold = null, deadline = Date.now() + 15 * 60000;
    while (Date.now() < deadline && !cold) {
        if ((await ready()) !== 200) {
            const coldContext = await context();
            const coldPage = await open(coldContext, settings);
            await coldPage.getByTestId('runtime-operation').filter({visible: true}).first().waitFor({timeout: 60000});
            cold = await text(coldPage, 'operation');
            await coldContext.close();
        } else if ((await text(page, 'effective')) === label) break;
        else await sleep(500);
    }
    check(cold === report.operation, `a cold signed-in browser during maintenance resumes operation ${report.operation} (saw ${cold})`);

    const completedAt = await (async () => {
        const until = Date.now() + 15 * 60000;
        while (Date.now() < until) {
            if ((await text(page, 'effective')) === label && (await text(page, 'intended')) === label) return Date.now();
            await sleep(1000);
        }
        return null;
    })();
    check(completedAt, `the open settings page reaches effective and intended ${label}`);
    const reconnect = Date.now() + 120000;
    while (Date.now() < reconnect && !sockets.some(s => s.at >= completedAt - 30000)) await sleep(1000);
    check(sockets.some(s => s.at >= completedAt - 30000), 'the other open route reconnects its socket after the switch');
    check(navigations === 0, 'the other open route reconnected without a page reload');
} catch (error) {
    report.failure = String(error && error.message || error);
} finally {
    if (browser) await browser.close().catch(() => {});
    await fs.writeFile(process.env.P5_REPORT, JSON.stringify(report, null, 1) + '\n');
}
process.exit(report.failure ? 1 : 0);
