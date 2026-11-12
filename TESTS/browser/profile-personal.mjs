// Actual unchanged UI, password login and profile save. No API responses or
// authentication storage are replaced by this workflow. The private HTTP proxy
// makes the selected backend explicit without implementing runtime selection.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';

const input = JSON.parse(await fs.readFile(process.env.TESTS_FIXTURE, 'utf8'));
const {chromium, expect} = await import(pathToFileURL(input.playwright_module).href);
const output = process.env.TESTS_OUTPUT;
await fs.mkdir(output, {recursive: true});
const browser = await chromium.launch();
const responses = [], sockets = [], blockedExternal = [], failures = [], contexts = [], pending = [], profileShapes = [];
const deadline = setTimeout(() => {
    failures.push({kind: 'workflow-deadline'});
    void browser.close();
}, 150000);
const selected = input.version === 'v1' ? 'django-v1' : 'go-v2';
const core = new Map([
    ['/api/instance/status', ['GET']], ['/api/instance/config', ['GET']],
    ['/api/instance/access', ['GET']], ['/api/oauth2/login', ['POST']],
    ['/api/oauth2/refresh-token', ['POST']], ['/api/oauth2/check/username', ['GET']],
    ['/api/profile/info', ['GET']], ['/api/profile/update', ['PATCH']],
]);
const coexistence = new Map([
    ['/api/instance/manifest.json', 200], ['/api/instance/logo.png', 200],
    ['/api/association/export/active', 403],
]);
let phase = 'initialization', success = false, successfulLogins = 0;
const record = async response => {
    const url = new URL(response.url());
    if (url.origin !== input.origin) return;
    const item = {method: response.request().method(), path: url.pathname,
        status: response.status(), backend: await response.headerValue('x-fixture-backend')};
    responses.push(item);
    if (core.has(item.path)) {
        assert(core.get(item.path).includes(item.method), 'Unexpected core API method');
        assert.equal(item.backend, selected, 'Workflow API used the wrong backend');
        const denied = (item.path === '/api/oauth2/login' && item.status === 401)
            || (item.path === '/api/profile/info' && [401, 403].includes(item.status));
        assert(item.status === 200 || denied, 'Unexpected workflow API response');
    } else if (coexistence.has(item.path)) {
        assert.equal(item.backend, 'django-coexistence');
        assert.equal(item.status, coexistence.get(item.path), 'Unexpected coexistence response');
    } else {
        assert(!item.path.startsWith('/api/') && !item.path.startsWith('/instance-'), 'Unaccounted API request');
        assert.equal(item.backend, 'ui-static', 'Unexpected resource backend');
        assert(item.status >= 200 && item.status < 400, 'Failed static frontend resource');
    }
};
async function fresh() {
    const context = await browser.newContext({viewport: {width: 1440, height: 1000}});
    contexts.push(context);
    // Only external provider/font traffic is blocked. Every same-origin request
    // reaches the real HTTP proxy and real application without interception.
    await context.route('**/*', route => {
        const url = new URL(route.request().url());
        if (url.origin === input.origin) return route.continue();
        blockedExternal.push({method: route.request().method(), origin: url.origin, path: url.pathname});
        return route.abort('blockedbyclient');
    });
    const page = await context.newPage();
    page.setDefaultTimeout(30000);
    page.on('pageerror', error => failures.push({kind: 'pageerror', phase, error_class: error.name,
        // Record only static asset filename/line coordinates, never error text,
        // arbitrary URL query parameters or application payloads.
        asset_frames: [...String(error.stack || '').matchAll(/\/build-assets\/([A-Za-z0-9_.-]+\.js):(\d+):(\d+)/g)]
            .map(match => ({asset: match[1], line: Number(match[2]), column: Number(match[3])}))}));
    page.on('response', response => pending.push(record(response).catch(() => failures.push({kind: 'response', phase}))));
    page.on('requestfailed', request => {
        const url = new URL(request.url());
        if (url.origin === input.origin && request.failure()?.errorText !== 'net::ERR_ABORTED')
            failures.push({kind: 'requestfailed', method: request.method(), path: url.pathname});
    });
    page.on('websocket', socket => {
        const item = {path: new URL(socket.url()).pathname, backend: 'django-coexistence', received_types: []};
        sockets.push(item);
        socket.on('framereceived', frame => {
            try {
                const body = JSON.parse(String(frame.payload));
                if (typeof body.type === 'string') item.received_types.push(body.type);
            } catch { failures.push({kind: 'invalid-websocket-frame', path: item.path}); }
        });
    });
    return {context, page};
}
async function credentials(page, wrong = false) {
    await expect(page.locator('input[name="username_login"]')).toBeVisible();
    assert(await page.evaluate(() => localStorage.getItem('sessionToken') === null), 'Fresh login was already authenticated');
    await page.locator('input[name="username_login"]').fill(input.username);
    await page.locator('input[name="password_login"]').fill(wrong ? 'incorrect-private-fixture-password' : input.password);
    const login = page.waitForResponse(r => new URL(r.url()).pathname === '/api/oauth2/login' && r.request().method() === 'POST');
    await page.locator('#bkn_login_signin_submit').click();
    const response = await login;
    assert.equal(response.status(), wrong ? 401 : 200, 'Password login outcome');
    if (wrong) {
        assert(await page.evaluate(() => localStorage.getItem('sessionToken') === null), 'Wrong password created an authenticated session');
        const dismiss = page.locator('.swal2-confirm');
        if (await dismiss.isVisible()) await dismiss.click();
        return;
    }
    const payload = await response.json();
    assert.equal(payload.user_data?.user_id, input.user_id, 'Login identity mismatch');
    assert.equal(payload.role, 'association');
    profileShapes.push({source: 'login', has_association: Boolean(payload.user_data?.sport_association),
        has_username: typeof payload.user_data?.username === 'string'});
    successfulLogins += 1;
    await expect(page.locator('#bkn_aside')).toBeVisible();
    await expect(page.getByText('Non ci sono dati da visualizzare in questa pagina.', {exact: false})).toBeVisible();
    await expect(page.locator('.dashboard-widget')).toHaveCount(0);
    const notice = page.getByRole('button', {name: /Ok, grazie/});
    if (await notice.isVisible()) await notice.click();
}
const field = (page, label) => page.locator('#bkn_form_account_update .form-group')
    .filter({has: page.locator('label').filter({hasText: new RegExp('^' + label + '$')})}).locator('input').first();
async function profile(page) {
    const access = page.waitForResponse(r => new URL(r.url()).pathname === '/api/instance/access');
    await page.locator('a[href="/#/profile"]').first().click();
    const response = await access;
    assert.equal(response.status(), 200);
    const body = await response.json();
    assert(body.is_owner === false && body.is_administrator === false, 'Collaborator acquired instance administration');
    await expect(page.locator('#bkn_form_account_update')).toBeVisible();
}
try {
    phase = 'fresh-login-denials';
    const first = await fresh();
    const denied = await first.context.request.get(input.origin + '/api/profile/info');
    assert([401, 403].includes(denied.status()), 'Anonymous protected profile allowed');
    responses.push({method: 'GET', path: '/api/profile/info', status: denied.status(), backend: denied.headers()['x-fixture-backend']});
    assert.equal(denied.headers()['x-fixture-backend'], selected);
    await first.page.goto(input.origin + '/#/login');
    await credentials(first.page, true);
    await credentials(first.page);
    phase = 'profile-edit';
    await profile(first.page);
    await expect(field(first.page, 'Nome')).toHaveValue(input.initial_first_name);
    await expect(field(first.page, 'Cognome')).toHaveValue(input.initial_last_name);
    await field(first.page, 'Nome').fill(input.changed_first_name);
    await field(first.page, 'Cognome').fill(input.changed_last_name);
    const saved = first.page.waitForResponse(r => new URL(r.url()).pathname === '/api/profile/update' && r.request().method() === 'PATCH');
    await first.page.locator('#bkn_form_account_update_submit').click();
    const savedResponse = await saved;
    assert.equal(savedResponse.status(), 200);
    const savedBody = await savedResponse.json();
    profileShapes.push({source: 'profile-update', has_association: Boolean(savedBody.user_data?.sport_association),
        has_username: typeof savedBody.user_data?.username === 'string'});
    assert.equal(savedBody.user_data?.user_id, input.user_id);
    assert.equal(savedBody.user_data?.first_name, input.changed_first_name);
    assert.equal(savedBody.user_data?.last_name, input.changed_last_name);
    await expect(first.page.locator('#bkn_form_account_update_submit')).toBeDisabled();
    phase = 'reload-and-server-readback';
    await first.page.reload();
    phase = 'reload-visible-profile';
    await expect(field(first.page, 'Nome')).toHaveValue(input.changed_first_name);
    await expect(field(first.page, 'Cognome')).toHaveValue(input.changed_last_name);
    // Normal UI reload can reuse login-populated profile storage. An actual
    // authenticated server read and fresh second login establish persistence.
    phase = 'authenticated-server-readback';
    const readback = await first.page.evaluate(async () => {
        const response = await fetch('/api/profile/info', {headers: {
            Authorization: 'Bearer ' + JSON.parse(localStorage.getItem('sessionToken'))}});
        const body = await response.json();
        return {status: response.status, user_id: body.user_data?.user_id,
            first_name: body.user_data?.first_name, last_name: body.user_data?.last_name};
    });
    assert.deepEqual(readback, {status: 200, user_id: input.user_id,
        first_name: input.changed_first_name, last_name: input.changed_last_name});
    phase = 'second-fresh-login';
    const second = await fresh();
    await second.page.goto(input.origin + '/#/login');
    await credentials(second.page);
    await profile(second.page);
    await expect(field(second.page, 'Nome')).toHaveValue(input.changed_first_name);
    await expect(field(second.page, 'Cognome')).toHaveValue(input.changed_last_name);
    phase = 'background-contracts';
    await expect.poll(() => responses.some(r => r.path === '/api/association/export/active' && r.status === 403)).toBe(true);
    await expect.poll(() => sockets.some(s => s.path === '/ws/health/' && s.received_types.includes('health'))).toBe(true);
    await expect.poll(() => sockets.some(s => s.path === '/ws/notifications/' && s.received_types.includes('notifications'))).toBe(true);
    await expect.poll(() => responses.some(r => r.path === '/api/instance/logo.png' && r.status === 200)).toBe(true);
    // Chromium may defer manifest retrieval; read the unchanged rendered URL
    // through a real browser fetch, never substitute a synthetic manifest.
    const manifest = await second.page.evaluate(async () => {
        const url = new URL(document.querySelector('link[rel="manifest"]').href);
        const response = await fetch(url); return {path: url.pathname, status: response.status};
    });
    assert.deepEqual(manifest, {path: '/api/instance/manifest.json', status: 200});
    await Promise.all(pending);
    assert.equal(failures.length, 0, 'Unaccounted browser failure');
    assert.equal(responses.filter(r => r.path === '/api/profile/update').length, 1, 'Duplicate profile mutation');
    success = true;
} catch (error) {
    failures.push({kind: 'workflow-assertion', phase, error_class: error.name,
        assertion_code: error.code || null,
        category: ['Execution context was destroyed', 'Target page, context or browser has been closed',
            'Failed to fetch', 'Illegal invocation', 'Unexpected token', 'Unexpected end of JSON input',
            'Cannot read properties', 'not defined', 'Timeout', 'JSON.parse'].find(value => String(error.message).includes(value)) || 'unclassified'});
    throw new Error('Personal-profile browser contract failed at sanitized phase: ' + phase);
} finally {
    clearTimeout(deadline);
    await browser.close();
    await Promise.all(pending);
    if (failures.length) success = false;
    await fs.writeFile(path.join(output, 'browser.json'), JSON.stringify({success, version: input.version, phase,
        responses, sockets, profile_response_shapes: profileShapes, blocked_external: blockedExternal, failures,
        fresh_password_login: successfulLogins > 0, successful_password_logins: successfulLogins, authentication_state_seeded: false,
        api_responses_intercepted: false, private_proxy_selection_only: true}, null, 2) + '\n');
    if (!success) throw new Error('Personal-profile browser contract failed at sanitized phase: ' + phase);
}
