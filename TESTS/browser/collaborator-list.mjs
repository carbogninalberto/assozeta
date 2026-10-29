// The unchanged compiled UI and ordinary password login exercise collaborator
// and invitation reads. Same-origin business responses are never intercepted.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {pathToFileURL} from 'node:url';

const input = JSON.parse(await fs.readFile(process.env.TESTS_FIXTURE, 'utf8'));
const output = process.env.TESTS_OUTPUT;
await fs.mkdir(output, {recursive: true});

const contexts = [], pending = [], responses = [], sockets = [], blockedExternal = [];
const failures = [], checks = [];
let browser = null, chromium, expect, activePage;
let phase = 'initialization', sequence = 0, success = false;
const selected = input.version === 'v1' ? 'django-v1' : 'go-v2';
const selectedRoutes = new Set(input.selected_routes);
const coexistenceRoutes = new Set(input.coexistence_routes);
const digest = value => createHash('sha256').update(String(value)).digest('hex');
const report = {
    success: false, version: input.version, browser_created: false,
    contexts_closed: false, browser_closed: false, checks, responses,
    sockets, blocked_external: blockedExternal, failures,
    authentication_state_seeded: false, api_responses_intercepted: false,
    private_proxy_selection_only: true,
};
const deadline = setTimeout(() => {
    failures.push({kind: 'workflow-deadline', phase});
    if (browser) void browser.close().catch(error =>
        failures.push({kind: 'deadline-close', error_class: error.name}));
}, 300000);

async function save() {
    await fs.writeFile(path.join(output, 'browser.json'), JSON.stringify(report, null, 2) + '\n');
}

async function checkpoint(actor, action, context = {}) {
    const id = ++sequence;
    const file = path.join(input.checkpoints, `${id}.json`);
    await fs.writeFile(file + '.pending',
        JSON.stringify({id, actor, action, context}), {mode: 0o600});
    await fs.rename(file + '.pending', file);
    const ack = path.join(input.checkpoints, `${id}.ack.json`);
    await expect.poll(async () => {
        try { return JSON.parse(await fs.readFile(ack, 'utf8')); }
        catch { return null; }
    }, {timeout: 30000}).not.toBeNull();
    const result = JSON.parse(await fs.readFile(ack, 'utf8'));
    assert.equal(result.success, true, 'Durable checkpoint failed');
    checks.push({actor, action, checkpoint: id, ...context});
}

async function record(response, actor) {
    const url = new URL(response.url());
    if (url.origin !== input.origin) return;
    const route = url.pathname.startsWith('/api/') ? url.pathname.slice(5) : null;
    const item = {
        actor, method: response.request().method(), path: url.pathname,
        status: response.status(),
        backend: response.headers()['x-fixture-backend'] || null,
    };
    responses.push(item);
    if (selectedRoutes.has(route)) {
        assert.equal(item.backend, selected, 'Selected route used the wrong backend');
        const allowed = route === 'collaborators/list' ? [200, 403] : [200, 201, 401, 403];
        assert(allowed.includes(item.status), 'Selected route returned an unexpected status');
    } else if (coexistenceRoutes.has(route)) {
        assert.equal(item.backend, 'django-coexistence', 'Coexistence route used the wrong backend');
        assert([200, 403, 404].includes(item.status), 'Coexistence route returned an unexpected status');
    } else {
        assert(!url.pathname.startsWith('/api/') && !url.pathname.startsWith('/instance-'),
            'Unaccounted API dependency');
        assert.equal(item.backend, 'ui-static', 'Static resource used the wrong backend');
        assert(item.status >= 200 && item.status < 400, 'Static resource failed');
    }
}

async function fresh(pageName) {
    const context = await browser.newContext({viewport: {width: 1440, height: 1000}});
    const contextEntry = {context, closed: false};
    contexts.push(contextEntry);
    await context.route('**/*', route => {
        const url = new URL(route.request().url());
        if (url.origin === input.origin) return route.continue();
        blockedExternal.push({origin: url.origin, path: url.pathname});
        return route.abort('blockedbyclient');
    });
    const page = await context.newPage();
    activePage = page;
    page.setDefaultTimeout(30000);
    page.on('response', response => pending.push(record(response, pageName).catch(() =>
        failures.push({kind: 'response', phase, actor: pageName,
            path: new URL(response.url()).pathname, status: response.status()}))));
    page.on('pageerror', error => failures.push({
        kind: 'pageerror', phase, actor: pageName, error_class: error.name,
        asset_frames: [...String(error.stack || '').matchAll(
            /\/build-assets\/([A-Za-z0-9_.-]+\.js):(\d+):(\d+)/g)]
            .map(match => ({asset: match[1], line: Number(match[2]), column: Number(match[3])})),
    }));
    page.on('requestfailed', request => {
        const url = new URL(request.url());
        if (url.origin === input.origin && request.failure()?.errorText !== 'net::ERR_ABORTED')
            failures.push({kind: 'requestfailed', phase, actor: pageName, path: url.pathname});
    });
    page.on('websocket', socket => {
        const item = {
            actor: pageName, path: new URL(socket.url()).pathname,
            backend: 'django-coexistence',
            received_types: [],
        };
        sockets.push(item);
        if (!input.websocket_routes.includes(item.path))
            failures.push({kind: 'unaccounted-websocket', actor: pageName, path: item.path});
        socket.on('framereceived', frame => {
            try {
                const body = JSON.parse(String(frame.payload));
                if (typeof body.type === 'string') item.received_types.push(body.type);
            } catch {
                failures.push({kind: 'invalid-websocket-frame', phase, actor: pageName,
                    path: item.path});
            }
        });
    });
    return {context: contextEntry, page};
}

async function closeContext(entry) {
    if (!entry || entry.closed) return;
    await entry.context.close();
    entry.closed = true;
}

async function login(page, actorName, actor) {
    await page.goto(input.origin + '/#/login');
    await expect(page.locator('input[name="username_login"]')).toBeVisible();
    assert.equal(await page.evaluate(() => localStorage.getItem('sessionToken')), null,
        'Fresh browser context was already authenticated');
    await page.locator('input[name="username_login"]').fill(actor.username);
    await page.locator('input[name="password_login"]').fill(input.password);
    const waiting = page.waitForResponse(response =>
        new URL(response.url()).pathname === '/api/oauth2/login'
        && response.request().method() === 'POST');
    await page.locator('#bkn_login_signin_submit').click();
    const response = await waiting;
    assert.equal(response.status(), 200, 'Password login failed');
    assert.equal(response.headers()['x-fixture-backend'], selected,
        'Password login used the wrong backend');
    const payload = await response.json();
    assert.equal(payload.user_data?.user_id, actor.user_id, 'Login identity differs');
    assert.equal(payload.user_data?.requires_welcome, false, 'Fixture actor still requires onboarding');
    await expect(page.locator('#bkn_aside')).toBeVisible();
    await expect.poll(() => page.evaluate(() =>
        JSON.parse(localStorage.getItem('userData') || '{}').user_id))
        .toBe(actor.user_id);
    const notice = page.getByRole('button', {name: /Ok, grazie/});
    if (await notice.isVisible()) await notice.click();
    const credentials = await page.evaluate(() => ({
        access: localStorage.getItem('sessionToken'),
        refresh: localStorage.getItem('refreshToken'),
    }));
    assert(credentials.access && credentials.refresh, 'Password login did not create an ordinary UI session');
    await page.waitForTimeout(750);
    await expect.poll(() => responses.some(row =>
        row.actor === actorName && row.path === '/api/association/export/active'
        && row.status === (['owner', 'foreign-owner'].includes(actorName) ? 200 : 403))).toBe(true);
    await expect.poll(() => sockets.some(row =>
        row.actor === actorName && row.path === '/ws/health/' && row.received_types.includes('health'))).toBe(true);
    await expect.poll(() => sockets.some(row =>
        row.actor === actorName && row.path === '/ws/notifications/' && row.received_types.includes('notifications'))).toBe(true);
    await checkpoint(actorName, 'login', {
        user_id: actor.user_id,
        access_token_sha256: digest(credentials.access),
        refresh_token_sha256: digest(credentials.refresh),
    });
}

const sidebarLink = page => page.locator('a[href="/#/connected-collaborators"]').first();

async function openList(page, byNavigation) {
    if (byNavigation) {
        await expect(sidebarLink(page)).toBeVisible();
    }
    const waiting = page.waitForResponse(response =>
        new URL(response.url()).pathname === '/api/collaborators/list'
        && response.request().method() === 'GET');
    if (byNavigation) await sidebarLink(page).click();
    else await page.goto(input.origin + '/#/connected-collaborators');
    const response = await waiting;
    assert.equal(response.headers()['x-fixture-backend'], selected,
        'Collaborator list used the wrong backend');
    return response;
}

async function collectPage(page, expectedControl) {
    await expect(page.locator('.datatable-loading')).toHaveCount(0);
    const detail = page.locator('.datatable-pager-detail');
    await expect(detail).toContainText(/Mostrati \d+ - \d+ di \d+/);
    assert.equal(await page.getByText('Nessun dato disponibile', {exact: true}).count(), 0,
        'Non-empty collaborator result rendered as an empty table');
    const rows = page.locator('.datatable-body div.datatable-row[data-row]');
    const count = await rows.count();
    const displayed = [], controls = [];
    for (let index = 0; index < count; index += 1) {
        const row = rows.nth(index);
        for (const field of ['name', 'email', 'accepted', 'role'])
            await expect(row.locator(`[data-field="${field}"]`)).toBeVisible();
        const name = (await row.locator('[data-field="name"]').innerText()).trim();
        const email = (await row.locator('[data-field="email"]').innerText()).trim();
        const status = (await row.locator('[data-field="accepted"]').innerText()).trim();
        const role = (await row.locator('[data-field="role"]').innerText()).trim();
        const edit = row.locator(
            '.action-column button:is([title="Modifica"],[data-original-title="Modifica"])');
        const remove = row.locator(
            '.action-column button:is([title="Elimina"],[data-original-title="Elimina"])');
        const editCount = await edit.count(), removeCount = await remove.count();
        if (editCount) await expect(edit).toBeVisible();
        await expect(remove).toBeVisible();
        if (expectedControl === 'manage') {
            if (editCount) assert.equal(await edit.isDisabled(), false, 'Owner edit control is disabled');
            assert.equal(removeCount, 1);
            assert.equal(await remove.isDisabled(), false, 'Owner delete control is disabled');
        } else {
            if (editCount) assert.equal(await edit.isDisabled(), true, 'Restricted edit control is enabled');
            assert.equal(removeCount, 1);
            assert.equal(await remove.isDisabled(), true, 'Restricted delete control is enabled');
        }
        displayed.push({name, email, status, role, edit_control: editCount,
            delete_disabled: await remove.isDisabled()});
        controls.push({email, edit_count: editCount, delete_count: removeCount});
    }
    const userRowCount = controls.filter(row => row.edit_count > 0).length;
    await expect.poll(() =>
        page.locator('#portal-elements [id^="editModal-"]').count()).toBe(userRowCount);
    const ids = await page.locator('#portal-elements [id^="editModal-"]').evaluateAll(nodes =>
        nodes.map(node => node.id.slice('editModal-'.length)));
    return {
        rows: displayed,
        user_ids: [...new Set(ids)].sort(),
        controls,
        page_text: (await detail.innerText()).trim(),
        page_size: await page.locator('.datatable-pager-size').inputValue(),
    };
}

const rowKey = row => [row.email, row.name, row.status, row.role].join('\u0000');
const normalizedRows = rows => rows.map(({name, email, status, role}) =>
    ({name, email, status, role})).sort((left, right) =>
    rowKey(left).localeCompare(rowKey(right)));

async function assertList(page, actorName, actor, expectedControl, action) {
    assert(actor.expected_rows.length > 0, 'Acceptance fixture must not rely on an empty list');
    const expected = actor.expected_rows;
    const collected = [], pageChecks = [];
    let pageNumber = 1;
    const observedIDs = new Set();
    while (true) {
        const pageData = await collectPage(page, expectedControl);
        assert(pageData.rows.length > 0, `Pagination page ${pageNumber} rendered empty`);
        const expectedPageSize = actor.expected_pages[pageNumber - 1];
        assert.equal(pageData.page_size, '10', 'Collaborator page-size selection differs');
        assert.equal(pageData.page_text,
            `Mostrati ${(pageNumber - 1) * 10 + 1} - ${Math.min(pageNumber * 10, expected.length)} di ${expected.length}`,
            'Collaborator pagination summary differs');
        assert.equal(pageData.rows.length, expectedPageSize, 'Collaborator page row count differs');
        collected.push(...pageData.rows);
        pageData.user_ids.forEach(id => observedIDs.add(id));
        pageChecks.push({
            page: pageNumber, row_count: pageData.rows.length,
            page_text: pageData.page_text, page_size: pageData.page_size,
            rows: pageData.rows,
        });
        const next = page.locator('.datatable-pager-link-next');
        if (await next.isDisabled()) break;
        await next.click();
        pageNumber += 1;
        await expect(page.locator('.datatable-pager-link-active')).toHaveText(String(pageNumber));
    }
    assert.equal(pageNumber, actor.expected_pages.length, 'Collaborator pagination page count differs');
    assert.deepEqual(pageChecks.map(item => item.row_count), actor.expected_pages,
        'Collaborator page sizes differ');
    assert.deepEqual(normalizedRows(collected), normalizedRows(expected),
        'Rendered collaborator/invitation data differs');
    const expectedUserIDs = expected.filter(row => row.user_id).map(row => row.user_id).sort();
    assert.deepEqual([...observedIDs].sort(), expectedUserIDs,
        'Rendered owned collaborator user IDs differ');
    for (const row of expected.filter(row => row.collaboration_invite_id)) {
        assert(![...observedIDs].includes(row.collaboration_invite_id),
            'Invitation ID was incorrectly rendered as a user identity');
    }
    assert(actor.forbidden_emails.length > 0, 'Foreign contrast fixture is empty');
    for (const email of actor.forbidden_emails) {
        // Exact text avoids treating pending@example.test as a match inside
        // the distinct, legitimately owned foreign-pending@example.test row.
        await expect(page.getByText(email, {exact: true})).toHaveCount(0);
    }
    checks.push({
        actor: actorName, action, count: collected.length,
        pages: pageChecks, user_ids: [...observedIDs].sort(),
        create_control_visible: await page.locator('.card-toolbar')
            .getByText('Collaboratore', {exact: true}).isVisible(),
        no_empty_state: true, foreign_emails_absent: actor.forbidden_emails.length,
    });
}

async function runReader(actorName) {
    const actor = input.actors[actorName];
    const {context, page} = await fresh(actorName);
    try {
        phase = actorName + '-login';
        await login(page, actorName, actor);
        phase = actorName + '-list-read';
        const first = await openList(page, true);
        assert.equal(first.status(), 200, 'Authorized collaborator list was denied');
        await expect(page.locator('.card-title h3')).toContainText('Collaboratori');
        const expectedControl = actor.action_mode;
        assert.equal(await page.locator('.card-toolbar')
            .getByText('Collaboratore', {exact: true}).count(),
        expectedControl === 'manage' ? 1 : 0,
        'Collaborator create-control visibility differs');
        await assertList(page, actorName, actor, expectedControl, 'displayed');
        await checkpoint(actorName, 'read', {count: actor.expected_rows.length});
        phase = actorName + '-reload';
        const reloading = page.waitForResponse(response =>
            new URL(response.url()).pathname === '/api/collaborators/list'
            && response.request().method() === 'GET');
        await page.reload();
        const reloaded = await reloading;
        assert.equal(reloaded.status(), 200);
        assert.equal(reloaded.headers()['x-fixture-backend'], selected);
        await assertList(page, actorName, actor, expectedControl, 'reloaded');
        await checkpoint(actorName, 'reload', {count: actor.expected_rows.length});
    } finally {
        await Promise.allSettled(pending);
        await closeContext(context);
    }
}

async function runDenied() {
    const actorName = 'denied', actor = input.actors.denied;
    const {context, page} = await fresh(actorName);
    try {
        phase = actorName + '-login';
        await login(page, actorName, actor);
        phase = actorName + '-denied';
        assert.equal(await sidebarLink(page).count(), 0,
            'Denied collaborator received a collaborators sidebar link');
        const response = await openList(page, false);
        assert.equal(response.status(), 403, 'Protected collaborator list was not denied');
        assert.equal(response.headers()['x-fixture-backend'], selected);
        await expect(page.locator('.datatable .alert[role="alert"]'))
            .toContainText('Impossibile caricare i dati.');
        assert.equal(await page.getByText('Nessun dato disponibile', {exact: true}).count(), 0,
            'Denied collaborator was falsely accepted as an empty list');
        assert.equal(await page.locator('[data-row]').count(), 0);
        checks.push({actor: actorName, action: 'denied', http_status: response.status(),
            sidebar_link_visible: false, ui_error: 'Impossibile caricare i dati.',
            no_false_empty_state: true});
        await checkpoint(actorName, 'denied', {http_status: response.status()});
    } finally {
        await Promise.allSettled(pending);
        await closeContext(context);
    }
}

try {
    await save();
    phase = 'browser-launch';
    const playwright = await import(pathToFileURL(input.playwright_module).href);
    chromium = playwright.chromium;
    expect = playwright.expect.configure({timeout: 30000});
    browser = await chromium.launch();
    report.browser_created = true;
    assert.equal(process.env.PLAYWRIGHT_BROWSERS_PATH || null,
        input.browser_cache_path, 'Browser cache environment differs from invocation');
    if (input.browser_cache_path && input.browser_cache_path !== '0') {
        const relative = path.relative(path.resolve(input.browser_cache_path), chromium.executablePath());
        assert(relative && !relative.startsWith('..') && !path.isAbsolute(relative),
            'Browser ignored the invocation cache');
    }
    report.browser_cache_matches_invocation = true;

    for (const actorName of ['owner', 'restricted', 'foreign-owner']) {
        await runReader(actorName);
    }
    await runDenied();
    await Promise.all(pending);
    const loginResponses = responses.filter(row => row.path === '/api/oauth2/login');
    assert.deepEqual(loginResponses.map(row => row.actor),
        ['owner', 'restricted', 'foreign-owner', 'denied'], 'Ordinary login count/order differs');
    assert(loginResponses.every(row => row.method === 'POST' && row.status === 200
        && row.backend === selected), 'Ordinary login response differs');
    const listResponses = responses.filter(row => row.path === '/api/collaborators/list');
    assert.equal(listResponses.length, 7, 'Collaborator list request count differs');
    assert.equal(listResponses.filter(row => row.status === 200).length, 6);
    assert.equal(listResponses.filter(row => row.status === 403).length, 1);
    assert(listResponses.every(row => row.backend === selected));
    assert.deepEqual(failures, [], 'Unaccounted browser failure');
    success = true;
} catch (error) {
    failures.push({
        kind: 'workflow-assertion', phase, error_class: error.name,
        assertion_code: error.code || null,
        assertion_frames: [...String(error.stack).matchAll(/collaborator-list\.mjs:(\d+):(\d+)/g)]
            .slice(0, 3).map(match => ({line: Number(match[1]), column: Number(match[2])})),
        category: String(error.message).includes('Timeout') ? 'timeout'
            : String(error.message).includes('Execution context was destroyed')
                ? 'navigation-race' : 'assertion',
    });
    try {
        report.debug = await activePage.evaluate(() => ({
            hash: location.hash,
            user_id: JSON.parse(localStorage.getItem('userData') || '{}').user_id,
            body: document.body.innerText.slice(0, 4000),
        }));
        await activePage.screenshot({path: path.join(output, 'failure.png'), fullPage: true});
    } catch {}
} finally {
    clearTimeout(deadline);
    try {
        for (const entry of contexts) {
            try { await closeContext(entry); }
            catch (error) { failures.push({kind: 'context-close', error_class: error.name}); }
        }
        report.contexts_closed = !failures.some(row => row.kind === 'context-close');
        try {
            if (browser) await browser.close();
            report.browser_closed = true;
        } catch (error) {
            failures.push({kind: 'browser-close', error_class: error.name});
        }
        await Promise.allSettled(pending);
    } finally {
        success = success && failures.length === 0
            && report.contexts_closed && report.browser_closed;
        report.success = success;
        report.phase = phase;
        if (!success) process.exitCode = 1;
        await save();
    }
}
