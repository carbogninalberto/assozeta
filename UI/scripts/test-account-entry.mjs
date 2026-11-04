// Regression: "CREA o ACCEDI con l'account" on the registration wizards (normal and
// preregistration links) must open the login for anonymous visitors instead of
// skipping straight to the Anagrafica step. Compiles the actual Step0 components
// with the actual local stores, so the persisted anonymous defaults
// (sessionToken === 'null', userData === '{}') are exactly what a visitor has.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium, expect} from '../../selfhost/tests/browser/node_modules/@playwright/test/index.mjs';
import {compile} from '../node_modules/svelte/src/compiler/index.js';
import {build} from '../node_modules/esbuild/lib/main.js';

const ui = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const source = process.argv[2] ? path.resolve(process.argv[2]) : ui;
const origin = 'https://registration.example.test';
const wizards = ['subscribe', 'subscribe-multiple', 'subscribefamily'];

async function bundle(wizard) {
    const step0 = `${source}/src/routes/${wizard}/wizard/Step0.svelte`;
    const result = await build({
        stdin: {
            contents: `import Step0 from ${JSON.stringify(step0)};
                import {observeLoginIdentity} from ${JSON.stringify(source + '/src/utils/loginSession.js')};
                window.observeLoginIdentity = observeLoginIdentity;
                window.mountStep0 = wizardData => new Step0({target: document.body, props: {wizardData}});`,
            resolveDir: ui,
        },
        bundle: true, write: false, format: 'iife', nodePaths: [ui + '/node_modules'],
        mainFields: ['svelte', 'browser', 'module', 'main'], conditions: ['svelte', 'browser'],
        alias: {components: source + '/src/components', utils: source + '/src/utils', store: source + '/src/store',
            routes: source + '/src/routes', shim: source + '/src/shim'},
        plugins: [{name: 'fixture', setup(b) {
            // Only the network layer is replaced; stores and login helpers are real.
            b.onResolve({filter: /ApiMiddleware(?:\.js)?$/}, () => ({path: 'api', namespace: 'fixture'}));
            b.onLoad({filter: /.*/, namespace: 'fixture'}, () => ({
                contents: `export async function apiFetch(url){window.requests.push(url);return {status:200,error:false,response:{data:{}}};}
                    export const replaceUID=(url,uid,placeholder='<uid>')=>url.replace(placeholder,uid);`,
                resolveDir: ui,
            }));
        }}, {name: 'svelte', setup(b) {
            b.onLoad({filter: /\.svelte$/}, a => ({
                contents: compile(fs.readFileSync(a.path, 'utf8'), {filename: a.path, generate: 'dom', css: 'injected'}).js.code,
                resolveDir: path.dirname(a.path),
            }));
        }}],
    });
    return result.outputFiles[0].text;
}

async function mount(page, code, {preregistration, loggedIn, realPopup = false}) {
    await page.goto(origin + '/');
    await page.evaluate(({preregistration, loggedIn, realPopup}) => {
        localStorage.clear();
        if (loggedIn) {
            localStorage.setItem('sessionToken', JSON.stringify('access-token'));
            localStorage.setItem('userData', JSON.stringify({user_id: 'user-1', first_name: 'Ada', last_name: 'Rossi'}));
        }
        window.requests = [];
        window.steps = [];
        window.popups = [];
        window.documentMarker = 'original-document';
        if (!realPopup) window.open = (...args) => {
            const popup = {args, closed: 0, close() { popup.closed += 1; }};
            window.popups.push(popup);
            return popup;
        };
        window.__bakney = {env: {API: {SUBSCRIPTION: {LIST_ALL: location.origin + '/subscription/list-all'}}}};
        window.wizardData = {
            sportAssociationData: {
                sport_association_id: 'association-1',
                user: {disable_account_creation: false, force_account_creation: false,
                    sport_association: {enabled_for: ['associate'], enable_quotes_management: false}},
            },
            formData: {preregistration, associate_data: {type: null}, custom_data: {}},
            toStep: step => window.steps.push(['toStep', step]),
            nextStep: () => window.steps.push(['nextStep']),
            createSubscription: () => window.steps.push(['createSubscription']),
        };
    }, {preregistration, loggedIn, realPopup});
    await page.addScriptTag({content: code});
    // As main.js: every app document reloads when another tab changes identity.
    if (realPopup) await page.evaluate(() => window.observeLoginIdentity());
    await page.evaluate(() => window.mountStep0(window.wizardData));
}

let browser;
try {
    browser = await chromium.launch({headless: true});
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    // Context-wide, so the real login popup is served too.
    await page.context().route(origin + '/**', route => route.fulfill({contentType: 'text/html', body: '<html><body></body></html>'}));

    for (const wizard of wizards) {
        const code = await bundle(wizard);
        for (const preregistration of [false, true]) {
            const label = `${wizard}${preregistration ? ' (preregistration)' : ''}`;

            // Anonymous visitor: must be sent to the login, not to Anagrafica.
            await mount(page, code, {preregistration, loggedIn: false});
            assert.equal(await page.evaluate(() => localStorage.getItem('sessionToken')), 'null',
                `${label}: anonymous visitors persist the 'null' token default`);
            const account = page.getByRole('button', {name: "CREA o ACCEDI con l'account"});
            await expect(account).toBeEnabled();
            await account.click();
            assert.deepEqual(await page.evaluate(() => window.steps), [], `${label}: skipped the login and changed step`);
            assert.deepEqual(await page.evaluate(() => window.popups.map(p => p.args)),
                [[origin + '/#/login?redirect=/', 'assozeta-account-login', 'width=600,height=600']], `${label}: login popup not opened`);
            await page.waitForTimeout(1300);
            assert.deepEqual(await page.evaluate(() => window.steps), [], `${label}: continued without logging in`);

            // The popup logs in: the wizard continues with the account and closes it.
            await page.evaluate(() => {
                localStorage.setItem('sessionToken', JSON.stringify('access-token'));
                localStorage.setItem('userData', JSON.stringify({user_id: 'user-1'}));
            });
            await expect.poll(() => page.evaluate(() => window.steps), {timeout: 5000}).toEqual([['toStep', 1]]);
            assert.equal(await page.evaluate(() => window.popups[0].closed), 1, `${label}: login popup not closed`);
            await page.waitForTimeout(1300);
            assert.deepEqual(await page.evaluate(() => window.steps), [['toStep', 1]], `${label}: polling did not stop`);

            // Real popup and identity observer: logging in inside the popup must
            // close it and continue this wizard, not reload this tab and orphan it.
            await mount(page, code, {preregistration, loggedIn: false, realPopup: true});
            const opened = page.waitForEvent('popup');
            await page.getByRole('button', {name: "CREA o ACCEDI con l'account"}).click();
            const popup = await opened;
            assert.equal(popup.url(), origin + '/#/login?redirect=/', `${label}: unexpected login popup URL`);
            await popup.evaluate(() => {
                // What initializeLoginSession() stores after a successful login.
                localStorage.setItem('sessionToken', JSON.stringify('popup-access'));
                localStorage.setItem('userData', JSON.stringify({user_id: 'member-1', first_name: 'Ada', last_name: 'Rossi'}));
                localStorage.setItem('loginIdentity', 'member-1');
            });
            await expect.poll(() => popup.isClosed(), {timeout: 5000, message: `${label}: login tab left open`}).toBe(true);
            await expect.poll(() => page.evaluate(() => window.steps), {timeout: 5000}).toEqual([['toStep', 1]]);
            assert.equal(await page.evaluate(() => window.documentMarker), 'original-document',
                `${label}: the wizard tab was reloaded by the login`);

            // "Continua senza account" still goes to the next step without an account.
            await mount(page, code, {preregistration, loggedIn: false});
            await page.getByRole('button', {name: 'Continua senza account'}).click();
            assert.deepEqual(await page.evaluate(() => window.steps), [['nextStep']], `${label}: continue without account`);
            assert.deepEqual(await page.evaluate(() => window.popups), []);

            // Logged-in user: offered "Continua come <name>", no login popup.
            await mount(page, code, {preregistration, loggedIn: true});
            await expect(page.getByRole('button', {name: "CREA o ACCEDI con l'account"})).toHaveCount(0);
            await page.getByRole('button', {name: /Continua come/}).click();
            assert.deepEqual(await page.evaluate(() => window.steps), [['nextStep']], `${label}: continue as user`);
            assert.deepEqual(await page.evaluate(() => window.popups), []);

            // subscribe-multiple does not load preregistrations (fetch commented out upstream).
            if (preregistration && wizard !== 'subscribe-multiple') {
                assert(await page.evaluate(() => window.requests.some(url => url.includes('preregistration=1'))),
                    `${label}: preregistration subscriptions not requested`);
            }
            console.log(`ok - ${label}`);
        }
    }
    assert.deepEqual(errors, [], 'Unexpected browser runtime errors');
    console.log('PASS: registration wizards send anonymous visitors to the login and continue after it.');
} finally {
    await browser?.close();
}
