// Regression: "CREA o ACCEDI con l'account" logs in in the same tab and comes
// back to the registration link, already logged in, on the next step. Mounts the
// actual Subscribe page with the actual stores and login-session code; only the
// network layer is replaced.
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
const link = '#/subscribe/club/preregistration';

const result = await build({
    stdin: {
        // index.html provides moment globally in the application.
        contents: `import moment from 'moment'; window.moment = moment;
            import Subscribe from ${JSON.stringify(source + '/src/routes/subscribe/Subscribe.svelte')};
            import {initializeLoginSession} from ${JSON.stringify(source + '/src/utils/loginSession.js')};
            import {accountReturnPath} from ${JSON.stringify(source + '/src/utils/accountSession.js')};
            window.mountSubscribe = () => new Subscribe({target: document.body,
                props: {params: {sportAssociationUsername: 'club', preregistration: 'preregistration'}}});
            // What the login page does after a successful login.
            window.completeLogin = response => {
                initializeLoginSession(response);
                const back = accountReturnPath();
                location.href = back ? '/' + back : '/';
            };`,
        resolveDir: ui,
    },
    bundle: true, write: false, format: 'iife', nodePaths: [ui + '/node_modules'], logLevel: 'error',
    mainFields: ['svelte', 'browser', 'module', 'main'], conditions: ['svelte', 'browser'], loader: {'.json': 'json'},
    alias: {components: source + '/src/components', utils: source + '/src/utils', store: source + '/src/store',
        routes: source + '/src/routes', shim: source + '/src/shim'},
    plugins: [{name: 'network-fixture', setup(b) {
        b.onResolve({filter: /ApiMiddleware(?:\.js)?$/}, () => ({path: 'api', namespace: 'fixture'}));
        b.onLoad({filter: /.*/, namespace: 'fixture'}, () => ({
            contents: `export async function apiFetch(url) {
                    window.requests.push(url);
                    if (url.includes('/search/profile/')) return {status: 200, error: false, response: {data: window.profile}};
                    return {status: 200, error: false, response: {data: {}}};
                }
                export const replaceUID = (url, uid, placeholder = '<uid>') => url.replace(placeholder, uid);
                export const originalFetch = (...args) => fetch(...args);
                export const ApiMiddlewareUrls = {};`,
            resolveDir: ui,
        }));
    }}, {name: 'svelte', setup(b) {
        b.onLoad({filter: /\.svelte$/}, a => ({
            contents: compile(fs.readFileSync(a.path, 'utf8'), {filename: a.path, generate: 'dom', css: 'injected'}).js.code,
            resolveDir: path.dirname(a.path),
        }));
    }}],
});
const code = result.outputFiles[0].text;

async function open(page) {
    await page.evaluate(() => {
        window.requests = [];
        // Vendor form validation, loaded globally by index.html in the application.
        const chain = new Proxy(function () {}, {get: (target, key) => key === 'then' ? undefined : chain,
            apply: () => chain});
        window.FormValidation = {formValidation: () => chain, plugins: chain};
        window.__bakney = {env: {API: {SEARCH: {PROFILE: location.origin + '/search/profile'},
            SUBSCRIPTION: {LIST_ALL: location.origin + '/subscription/list/all', ADD: location.origin + '/subscription/add'}}}};
        window.profile = {
            sport_association_id: 'association-1',
            user: {username: 'club', avatar_image: null, disable_account_creation: false, force_account_creation: false,
                online_payments: false, preview_and_custom_features: [],
                sport_association: {denomination: 'Club', enabled_for: ['associate'], enable_quotes_management: false,
                    preregistration_module_closed: false, remaining_preregistration_days: 30, additional_fields: [],
                    configuration: {mandatory_phone: false, mandatory_email: false}}},
        };
    });
    await page.addScriptTag({content: code});
    await page.evaluate(() => window.mountSubscribe());
}

let browser;
try {
    browser = await chromium.launch({headless: true});
    const context = await browser.newContext();
    await context.route(origin + '/**', route => route.fulfill({contentType: 'text/html', body: '<html><body></body></html>'}));
    const page = await context.newPage();
    const errors = [], popups = [];
    page.on('pageerror', e => errors.push(e.message));
    context.on('page', p => { if (p !== page) popups.push(p.url()); });

    // An anonymous visitor opens the preregistration link.
    await page.goto(origin + '/' + link);
    await open(page);
    const account = page.getByRole('button', {name: "CREA o ACCEDI con l'account"});
    await expect(account).toBeEnabled({timeout: 15000});
    await account.click();

    // Same tab: off to the login page, with the way back remembered.
    await expect.poll(() => new URL(page.url()).hash).toBe('#/login');
    assert.deepEqual(popups, [], 'the login must not open another tab');
    const remembered = await page.evaluate(() => JSON.parse(sessionStorage.getItem('accountLoginReturn')));
    assert.equal(remembered.path, link);
    assert.equal(remembered.state.associate_type, 1, 'the first-step choice is kept');

    // The login page completes the login and returns to the link.
    await page.evaluate(() => window.completeLogin({access_token: 'member-access', refresh_token: 'member-refresh',
        expires_in: 3600, role: 'athlete', user_data: {user_id: 'member-1', first_name: 'Ada', last_name: 'Rossi'}}));
    await expect.poll(() => new URL(page.url()).hash).toBe(link);

    // The registration page opens again (the router mounts it afresh) and resumes
    // on the next step, logged in, instead of starting over.
    await page.reload();
    await open(page);
    await expect(page.getByRole('heading', {name: 'Associato', exact: true})).toBeVisible({timeout: 15000});
    await expect(page.getByRole('button', {name: "CREA o ACCEDI con l'account"})).toHaveCount(0);
    assert.equal(await page.evaluate(() => sessionStorage.getItem('accountLoginReturn')), null, 'resumed once');

    // A later visit to the link starts normally, as the logged-in member.
    await page.reload();
    await open(page);
    await expect(page.getByRole('button', {name: /Continua come/})).toBeVisible({timeout: 15000});
    await expect(page.getByRole('heading', {name: 'Associato', exact: true})).toHaveCount(0);

    assert.deepEqual(popups, []);
    assert.deepEqual(errors, [], 'Unexpected browser runtime errors');
    console.log('PASS: "CREA o ACCEDI" logs in in the same tab and resumes the registration on the next step.');
} finally {
    await browser?.close();
}
