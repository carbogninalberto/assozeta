// Inert callable consumer of the EXISTING UI. Parent owns Playwright, browser,
// services, fixtures and durable observations. No launch, CLI or runtime import.
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {classifyRequest, classifyBlockedExternal, forwardContained, readCompleteBody,
    createCompletionBarrier, withResponseAction} from './message-reads.mjs';

const ACTORS = ['owner', 'full_collaborator', 'foreign_owner', 'restricted_collaborator'];
const READERS = ACTORS.slice(0, -1);
const LIST = '/api/communications/messages/list', ADD = '/api/communications/messages/add';
const LOGIN = '/api/oauth2/login';
const LIST_DENIAL = Object.freeze({detail: "Access denied: No permission mapping for endpoint 'communications/messages/list'. Contact administrator to grant access."});
const LIST_ERROR = 'Impossibile caricare i dati.';
const SELECTED = Object.freeze({'/api/instance/status': 'GET', '/api/instance/config': 'GET',
    '/api/instance/access': 'GET', [LOGIN]: 'POST', '/api/profile/info': 'GET', [LIST]: 'GET', [ADD]: 'POST'});
const COEXISTENCE = Object.freeze({'/api/instance/manifest.json': 'GET', '/api/instance/logo.png': 'GET',
    '/api/association/export/active': 'GET', '/api/statistic/dashboard': 'GET',
    '/api/communications/staff-board/list': 'GET'});
const SOCKETS = ['/ws/health/', '/ws/notifications/'];
const uuid = value => typeof value === 'string' && /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/.test(value);
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const normalize = text => text.replace(/\s+/g, ' ').trim();
const deletePath = actor => `/api/communications/messages/${actor.delete_id}/delete`;

// Closed mutation vocabulary. These labels describe this consumer's actions;
// no error text, URL, payload or exception metadata is diagnostic input.
export const MUTATION_WORKFLOW_STEPS = Object.freeze([
    'mutation-initialization', 'mutation-input-validation', 'mutation-context-initialization',
    'mutation-context-routing', 'mutation-page-initialization', 'mutation-login-navigation',
    'mutation-login-form', 'mutation-login-submit', 'mutation-login-redirect',
    'mutation-login-dashboard', 'mutation-login-session', 'mutation-login-notice',
    'mutation-login-network-idle', 'mutation-login-contract',
    'mutation-restricted-create-absence', 'mutation-restricted-delete-disabled',
    'mutation-restricted-mutation-absence',
    'mutation-sidebar-navigation', 'mutation-list-navigation', 'mutation-list-body',
    'mutation-list-loading', 'mutation-list-page-size', 'mutation-list-row-count',
    'mutation-list-row-contents', 'mutation-list-forbidden-absence', 'mutation-list-controls',
    'mutation-create-open', 'mutation-create-form', 'mutation-create-submit',
    'mutation-created-row', 'mutation-create-reload', 'mutation-created-row-persistence',
    'mutation-delete-open', 'mutation-delete-confirmation', 'mutation-delete-submit',
    'mutation-deleted-row-absence', 'mutation-delete-reload',
    'mutation-barrier-drain', 'mutation-checkpoint', 'mutation-context-close',
    'mutation-final-counts', 'mutation-cleanup-drain',
]);
export const MUTATION_ACTIONS = Object.freeze(['initialization', 'login', 'read', 'create',
    'create_reload', 'delete', 'delete_reload', 'closed']);
export const MUTATION_FAILURE_KINDS = Object.freeze(['invalid-mutation-diagnostic',
    ...['fragment', 'redirect', 'origin', 'query', 'list', 'login', 'api', 'static', 'page']
        .map(category => `unexpected-request-${category}`),
    'unexpected-request', 'unexpected-websocket', 'request-failed', 'request-or-response-failed',
    'pageerror', 'download', 'popup', 'workflow-failed', 'context-close-failed', 'incomplete-cleanup',
    ...['fetch', 'observe', 'fulfill', 'dispose', 'abort', 'headers'].map(stage => `route-${stage}-failed`),
    'route-redirect-blocked', ...['pairing', 'classification', 'body-completion', 'backend',
        'static-status', 'static-body', 'api-status', 'content-type', 'json', 'mutation-body']
        .map(check => `response-${check}-failed`),
]);

export function validMutationLocation(value) {
    return value && typeof value === 'object' && !Array.isArray(value)
        && Object.keys(value).length === 3 && Object.keys(value).every(key => ['actor', 'action', 'step'].includes(key))
        && MUTATION_WORKFLOW_STEPS.includes(value.step) && MUTATION_ACTIONS.includes(value.action)
        && (value.actor === null && value.action === 'initialization'
            || ACTORS.includes(value.actor) && value.action !== 'initialization'
                && (value.actor === 'restricted_collaborator'
                    ? ['login', 'read', 'closed'].includes(value.action) : true));
}

export function authoredRoutes(input) {
    return {...SELECTED, ...Object.fromEntries(READERS.map(actor => [deletePath(input.actors[actor]), 'DELETE']))};
}

export function validateInput(input) {
    assert.deepEqual(Object.keys(input).sort(), ['actors', 'artifact_sha256', 'origin', 'version']);
    const origin = new URL(input.origin);
    assert.equal(origin.protocol, 'http:');
    assert(['localhost', '127.0.0.1'].includes(origin.hostname) && origin.port);
    assert.equal(input.origin, origin.origin);
    assert(['v1', 'v2'].includes(input.version));
    assert.deepEqual(Object.keys(input.actors).sort(), [...ACTORS].sort());
    for (const name of ACTORS) {
        const actor = input.actors[name];
        assert(typeof actor.username === 'string' && actor.username && typeof actor.password === 'string' && actor.password);
        assert(uuid(actor.user_id));
        const keys = ['username', 'password', 'user_id', 'forbidden_ids'];
        assert(Array.isArray(actor.forbidden_ids) && actor.forbidden_ids.every(uuid));
        if (READERS.includes(name)) {
            keys.push('create_body', 'delete_id');
            assert(uuid(actor.delete_id));
            assert.deepEqual(Object.keys(actor.create_body).sort(), ['message', 'subject', 'type']);
            assert.equal(actor.create_body.type, 'EMAIL');
            assert(actor.create_body.subject.length >= 5 && actor.create_body.subject.length <= 100);
            assert(actor.create_body.message.length >= 20 && actor.create_body.message.length <= 1000);
        }
        assert.deepEqual(Object.keys(actor).sort(), keys.sort());
    }
    assert(Object.hasOwn(input.artifact_sha256, 'index.html'));
    for (const [file, digest] of Object.entries(input.artifact_sha256)) {
        assert(!file.startsWith('/') && !file.includes('\\') && !file.split('/').some(p => ['..', '.', ''].includes(p)));
        assert(/^[a-f0-9]{64}$/.test(digest));
    }
    return input;
}

export function classifyMutationRequest(input, address, method) {
    let url;
    try { url = new URL(address); } catch { return null; }
    if (url.hash) return null;
    const classification = classifyRequest({origin: input.origin, selected_routes: authoredRoutes(input),
        coexistence_routes: COEXISTENCE, artifact_sha256: input.artifact_sha256}, address, method);
    const widgets = ['subscriptions', 'payments', 'associates', 'bestcourses', 'todaylessons',
        'expiringcarnets', 'expiringmedicalcertificates', 'expiredmedicalcertificates',
        'subscriptionstoapprove', 'incomeAndExpenses', 'expiredPayments'];
    if (url.search && !(classification?.kind === 'static' && url.search === '?v=0') &&
        !(classification?.kind === 'coexistence' && (
            url.pathname === '/api/instance/logo.png' && /^\?v=[0-9]{20}$/.test(url.search) ||
            url.pathname === '/api/statistic/dashboard' && widgets.some(name => url.search === '?widget=' + name)))) return null;
    return classification;
}

const businessPath = path => [LOGIN, LIST, ADD].includes(path) || path.endsWith('/delete');

export async function forwardMutationRequest(route, {input, actor, action, cid, used, blocked,
    dependencies, fail, observe, register}) {
    const request = route.request(), url = new URL(request.url());
    const classification = classifyMutationRequest(input, request.url(), request.method());
    let allowed = classification && !request.redirectedFrom();
    const mutation = url.pathname === ADD || url.pathname.endsWith('/delete');
    if (mutation) {
        allowed &&= READERS.includes(actor) && ['create', 'delete'].includes(action)
            && !used.has(`${actor}:${action}`);
        if (allowed && action === 'create') {
            allowed &&= url.pathname === ADD;
            try { assert.deepEqual(JSON.parse(request.postData()), input.actors[actor].create_body); }
            catch { allowed = false; }
        } else if (allowed) {
            allowed &&= url.pathname === deletePath(input.actors[actor])
                && [null, ''].includes(request.postData());
        }
    } else if (url.pathname === LIST) {
        allowed &&= ACTORS.includes(actor) && (actor === 'restricted_collaborator' ? action === 'read'
            : ['read', 'create', 'create_reload', 'delete', 'delete_reload'].includes(action))
            && !used.has(`${actor}:${action}:list`);
    } else if (url.pathname === LOGIN) {
        allowed &&= action === 'login' && !used.has(`${actor}:login`);
    }
    if (!allowed) {
        blocked.add(request);
        if (!request.redirectedFrom() && classifyBlockedExternal(request.url(), request.method(), request.resourceType())) {
            dependencies.push({actor, action, dependency: 'blocked-known-external'});
        } else {
            // Fixed labels only: never publish rejected URLs, query values or bodies.
            const category = url.hash ? 'fragment' : request.redirectedFrom() ? 'redirect'
                : url.origin !== input.origin ? 'origin' : url.search ? 'query'
                : url.pathname === LIST ? 'list' : url.pathname === LOGIN ? 'login'
                : url.pathname.startsWith('/api/') ? 'api'
                : classification?.kind === 'static' ? 'static' : 'page';
            fail('unexpected-request-' + category);
        }
        try { await route.abort('blockedbyclient'); }
        catch (error) { fail('route-abort-failed'); throw error; }
        return;
    }
    if (mutation) used.add(`${actor}:${action}`);
    if (url.pathname === LIST) used.add(`${actor}:${action}:list`);
    if (url.pathname === LOGIN) used.add(`${actor}:login`);
    let headers;
    if (businessPath(url.pathname)) {
        try { headers = {...await request.allHeaders(), 'x-correlation-id': cid}; }
        catch (error) { fail('route-headers-failed'); throw error; }
        register?.(request, cid);
    }
    // Always enable the shared exact request/response URL pairing check, even
    // when the parent has no additional observation callback.
    await forwardContained(route, headers, {
        observe: async (request, response) => observe?.(request, response),
        diagnostic: (stage, error, category) => {
            if (category === 'redirect-blocked') fail('route-redirect-blocked');
            else if (error) fail(`route-${stage}-failed`);
        },
    });
}

export async function validateBrowserResponse(input, response, onFailure, location) {
    let check = 'pairing';
    try {
        const request = response.request(), path = new URL(response.url()).pathname;
        assert.equal(response.url(), request.url());
        check = 'classification';
        const classification = classifyMutationRequest(input, response.url(), request.method());
        assert(classification && !request.redirectedFrom() && !response.fromServiceWorker());
        const deniedList = path === LIST && request.method() === 'GET'
            && location?.actor === 'restricted_collaborator' && location.action === 'read';
        check = 'body-completion';
        const bytes = await readCompleteBody(response);
        check = 'backend';
        const backend = await response.headerValue('x-fixture-backend');
        if (classification.kind === 'static') {
            assert.equal(backend, 'ui-static');
            check = 'static-status'; assert.equal(response.status(), 200);
            check = 'static-body';
            assert.equal(hash(bytes), input.artifact_sha256[classification.file]);
        } else {
            assert.equal(backend, classification.kind === 'coexistence' ? 'django-coexistence'
                : input.version === 'v1' ? 'django-v1' : 'go-v2');
            check = 'api-status';
            assert((classification.kind === 'coexistence' ? [200, 403, 404]
                : deniedList ? [403] : [200]).includes(response.status()));
        }
        let body;
        if (businessPath(path)) {
            check = 'content-type';
            assert.equal((await response.headerValue('content-type'))?.split(';')[0], 'application/json');
            check = 'json'; body = JSON.parse(bytes.toString('utf8'));
            check = 'mutation-body';
            if (deniedList) assert.deepEqual(body, LIST_DENIAL);
            if (path === ADD || path.endsWith('/delete'))
                assert.deepEqual(body, {msg: path === ADD ? 'message added.' : 'message deleted.'});
        }
        return {classification, bytes, body, path}; // private observation, never public receipt
    } catch (error) { onFailure?.(`response-${check}-failed`); throw error; }
}

export async function boundedClose(context, timeoutMs = 5000) {
    let timer;
    const closed = Promise.resolve().then(() => context.close()).then(() => true, () => false);
    try { return await Promise.race([closed, new Promise(resolve => {timer = setTimeout(() => resolve(false), timeoutMs);})]); }
    finally { clearTimeout(timer); }
}

/**
 * exercise({browser, expect, input, checkpoint, onForwardedResponse?, onWorkflowStep?})
 *
 * checkpoint(privatePayload) must obtain independent raw observations, invoke
 * Python CheckpointVerifier.observe and return its numbered acknowledgment.
 * onForwardedResponse(request, APIResponse) observes the exact forwarded response
 * before fulfill; it must not dispose/mutate/replay it. No callback supplies UI
 * actions or responses. onWorkflowStep(step, {actor, action}) observes only fixed
 * mutation steps. Failure locations survive subsequent cleanup; at most 16 fixed
 * failure rows and a saturated count are retained. No exception text is inspected.
 * Parent enforces an overall deadline and browser cleanup.
 */
export async function exercise({browser, expect, input, checkpoint, onForwardedResponse, onWorkflowStep}) {
    const report = {success: false, version: null, contexts_created: 0, contexts_closed: false,
        browser_owned_by_parent: true, service_workers_blocked: true, business_responses_mocked: false,
        authentication_state_seeded: false, all_response_bodies_complete: false,
        external_integrations_excluded: true, font_parity_excluded: true, complete_message_family_parity: false,
        transport: 'route.fetch(maxRedirects=0,maxRetries=0)->route.fulfill(response)',
        requests: [], checkpoints: [], failures: [], failure_count: 0, failures_truncated: false, static_verified: [], blocked_dependencies: [],
        artifact_sha256: {}};
    const contexts = [], staticFiles = new Set(), used = new Set(), blocked = new WeakSet(), cids = new WeakMap();
    const observations = new WeakMap(), requestLocations = new WeakMap(), created = [], deleted = new Set();
    let actor = null, action = 'initialization', sequence = 0, payloadRequests = [], closing = false;
    let workflowStep = 'mutation-initialization';
    const location = () => ({actor, action, step: workflowStep});
    const mark = step => {
        assert(MUTATION_WORKFLOW_STEPS.includes(step));
        workflowStep = step;
        onWorkflowStep?.(step, {actor, action});
    };
    const fail = (kind, at = location()) => {
        assert(MUTATION_FAILURE_KINDS.includes(kind));
        report.workflow_failure ||= {...at};
        report.failure_count = Math.min(report.failure_count + 1, 1000000);
        report.failures_truncated = report.failure_count > 16;
        if (report.failures.length < 16) report.failures.push({kind, ...at});
    };
    const barrier = createCompletionBarrier(kind => fail(kind === 'requestfailed'
        ? 'request-failed' : 'request-or-response-failed'));
    const track = (task, at = location()) => barrier.track(Promise.resolve(task)
        .catch(() => fail('request-or-response-failed', at)));
    const drain = async () => {
        mark('mutation-barrier-drain');
        await barrier.drain(); assert.equal(report.failure_count, 0);
    };
    const now = async extra => {
        await drain();
        payloadRequests.sort((a, b) => Number(a.cid.split('-').at(-1)) - Number(b.cid.split('-').at(-1)));
        mark('mutation-checkpoint');
        const ack = await checkpoint({actor, action, requests: payloadRequests, ...extra});
        assert.deepEqual(ack, {success: true, checkpoint: report.checkpoints.length + 1});
        report.requests.push(...payloadRequests);
        report.checkpoints.push({actor, action});
        payloadRequests = [];
    };
    const close = async entry => {
        if (entry.attempted) return;
        entry.attempted = true;
        const at = {...entry.location, step: 'mutation-context-close'};
        // An observer error must not prevent the owned context close.
        try { mark('mutation-context-close'); } catch { fail('workflow-failed', at); }
        entry.closed = await boundedClose(entry.context);
        if (!entry.closed) fail('context-close-failed', at);
    };
    try {
        mark('mutation-input-validation');
        validateInput(input);
        report.version = input.version;
        report.artifact_sha256 = {...input.artifact_sha256};
        for (const name of ACTORS) {
            actor = name; action = 'login';
            mark('mutation-context-initialization');
            const identity = input.actors[name];
            const context = await browser.newContext({viewport: {width: 1600, height: 1000},
                locale: 'it-IT', timezoneId: 'UTC', extraHTTPHeaders: {'Accept-Language': 'en'},
                serviceWorkers: 'block', acceptDownloads: false});
            const entry = {context, attempted: false, closed: false, location: location()};
            contexts.push(entry); report.contexts_created++;
            const eventLocation = () => actor === name ? location()
                : {...entry.location, step: 'mutation-context-close'};
            mark('mutation-context-routing');
            await context.routeWebSocket('**/*', socket => {
                const url = new URL(socket.url());
                if (url.origin.replace(/^ws/, 'http') === input.origin && !url.username && !url.password
                    && SOCKETS.includes(url.pathname)) socket.connectToServer();
                else {fail('unexpected-websocket', eventLocation()); socket.close();}
            });
            context.on('request', request => {
                requestLocations.set(request, eventLocation());
                if (!closing) barrier.start(request);
            });
            context.on('requestfailed', request => {
                if (!blocked.has(request)) fail('request-failed', requestLocations.get(request) || eventLocation());
                barrier.failed(request, true);
            });
            await context.route('**/*', route => {
                const at = requestLocations.get(route.request()) || eventLocation();
                return track(forwardMutationRequest(route, {
                    input, actor: name, action, used, blocked, dependencies: report.blocked_dependencies,
                    fail: kind => fail(kind, at),
                    cid: `message-mutations-${input.version}-${name}-${action}-${++sequence}`,
                    register: (request, cid) => cids.set(request, cid), observe: onForwardedResponse,
                }), at);
            });
            context.on('response', response => {
                const responseLocation = requestLocations.get(response.request()) || eventLocation();
                const at = responseLocation.action;
                track((async () => {
                    const request = response.request();
                    const {classification, bytes, body, path} = await validateBrowserResponse(
                        input, response, kind => fail(kind, responseLocation), responseLocation);
                    if (classification.kind === 'static') {
                        staticFiles.add(classification.file);
                    }
                    if (businessPath(path)) {
                        const row = {cid: cids.get(request), method: request.method(), path: path.slice(4),
                            status: response.status(), body_sha256: hash(bytes)};
                        assert(row.cid);
                        payloadRequests.push(row);
                    }
                    observations.set(request, {body, at});
                })().finally(() => barrier.complete(response.request())), responseLocation);
            });
            mark('mutation-page-initialization');
            const page = await context.newPage();
            page.setDefaultTimeout(30000); page.setDefaultNavigationTimeout(30000);
            page.on('pageerror', () => fail('pageerror', eventLocation()));
            page.on('download', () => fail('download', eventLocation()));
            page.on('popup', popup => {
                const at = eventLocation(); fail('popup', at); track(popup.close(), at);
            });
            mark('mutation-login-navigation');
            await page.goto(input.origin + '/#/login');
            mark('mutation-login-form');
            await expect(page.locator('input[name="username_login"]')).toBeVisible();
            assert.equal(await page.evaluate(() => localStorage.getItem('sessionToken')), null);
            await page.locator('input[name="username_login"]').fill(identity.username);
            await page.locator('input[name="password_login"]').fill(identity.password);
            mark('mutation-login-submit');
            const login = await withResponseAction(page, response => new URL(response.url()).pathname === LOGIN
                && response.request().method() === 'POST', () => page.locator('#bkn_login_signin_submit').click());
            mark('mutation-login-redirect');
            await page.waitForURL(url => url.origin === input.origin && url.pathname === '/' && ['', '#/'].includes(url.hash));
            mark('mutation-login-dashboard');
            await expect(page.locator('#bkn_aside')).toBeVisible();
            mark('mutation-login-session');
            await expect.poll(() => page.evaluate(() => JSON.parse(localStorage.getItem('userData') || '{}').user_id)).toBe(identity.user_id);
            mark('mutation-login-notice');
            const notice = page.getByRole('button', {name: /Ok, grazie/});
            if (await notice.isVisible()) await notice.click();
            mark('mutation-login-network-idle');
            await page.waitForLoadState('networkidle');
            await drain();
            mark('mutation-login-contract');
            const loginBody = observations.get(login.request()).body;
            assert.equal(loginBody.user_data.user_id, identity.user_id);
            assert.equal(loginBody.user_data.requires_welcome, false);
            await now({identity: {user_id: identity.user_id, requires_welcome: false}});
            action = 'read';
            const readonly = name === 'restricted_collaborator';
            const sidebar = page.locator('a[href="/#/communication/messages"]').first();
            const collect = async response => {
                await drain();
                mark('mutation-list-body');
                const api = observations.get(response.request()).body;
                assert(Array.isArray(api) && api.length < 50, 'Bounded fixture exceeded');
                const rows = page.locator('.datatable-body div.datatable-row[data-row]');
                mark('mutation-list-loading');
                await expect(page.locator('.datatable-loading')).toHaveCount(0);
                mark('mutation-list-page-size');
                await page.locator('.datatable-pager-size').selectOption('50');
                mark('mutation-list-row-count');
                await expect.poll(() => rows.count()).toBe(api.length);
                mark('mutation-list-row-contents');
                const displayed = [];
                for (const row of await rows.all()) {
                    const item = {id: (await row.locator('[id^="action-col-"]').getAttribute('id')).slice(11)};
                    for (const field of ['message', 'type', 'subject', 'created_at']) {
                        const cell = row.locator(`[data-field="${field}"]`);
                        await expect(cell).toBeVisible();
                        item[field === 'created_at' ? 'date' : field] = normalize(await cell.innerText());
                        if (['message', 'subject'].includes(field)) item[field + '_title'] =
                            await cell.locator('p').getAttribute('data-original-title') ?? await cell.locator('p').getAttribute('title');
                    }
                    displayed.push(item);
                }
                const absent = [...identity.forbidden_ids.filter(id => !deleted.has(id)), ...created.filter(row => row.actor !== name &&
                    (row.actor === 'foreign_owner' || name === 'foreign_owner')).map(row => row.id)].sort();
                mark('mutation-list-forbidden-absence');
                for (const id of absent) await expect(page.locator(`#action-col-${id}`)).toHaveCount(0);
                mark('mutation-list-controls');
                const creates = page.locator('[data-target="#addModal"], [data-target="#addModalPost"]');
                await expect(creates).toHaveCount(2);
                await expect(page.locator('[data-target="#addModal"]')).toBeVisible();
                const deletes = page.locator('[id^="action-col-"] button[title="Elimina"]');
                await expect(deletes).toHaveCount(api.length);
                for (const button of await deletes.all()) {
                    await expect(button).toBeVisible();
                    await expect(button).toBeEnabled();
                }
                await expect(sidebar).toBeVisible();
                assert.equal(new URL(page.url()).hash, '#/communication/messages');
                return {response: api, rows: displayed, absent_ids: absent,
                    create_control: true, delete_enabled: true,
                    create_controls: await creates.count(), delete_controls: await deletes.count(),
                    sidebar_visible: await sidebar.isVisible(), hash: new URL(page.url()).hash};
            };
            const collectDenied = async response => {
                await drain();
                mark('mutation-list-body');
                const api = observations.get(response.request()).body;
                assert.equal(response.status(), 403);
                assert.deepEqual(api, LIST_DENIAL);
                mark('mutation-list-loading');
                await expect(page.locator('.datatable-loading')).toHaveCount(0);
                const rows = page.locator('.datatable-body div.datatable-row[data-row]');
                mark('mutation-list-row-count');
                await expect(rows).toHaveCount(0);
                mark('mutation-list-row-contents');
                await expect(page.locator('[id^="action-col-"]')).toHaveCount(0);
                mark('mutation-list-controls');
                // BKNDatatable.loadRows uses this fallback because the backend
                // denial has `detail`, not `message`. It renders an inline alert.
                const error = page.locator('.datatable-error [role="alert"]');
                await expect(error).toHaveCount(1);
                await expect(error).toBeVisible();
                await expect(error).toHaveText(LIST_ERROR);
                mark('mutation-restricted-create-absence');
                const creates = page.locator('[data-target="#addModal"], [data-target="#addModalPost"]');
                await expect(creates).toHaveCount(0);
                mark('mutation-restricted-delete-disabled');
                const deletes = page.locator('[id^="action-col-"] button[title="Elimina"]');
                const enabled = page.locator('[id^="action-col-"] button[title="Elimina"]:enabled');
                await expect(deletes).toHaveCount(0);
                await expect(enabled).toHaveCount(0);
                await expect(sidebar).toBeVisible();
                assert.equal(new URL(page.url()).hash, '#/communication/messages');
                return {response: api, rows: [], error_text: normalize(await error.innerText()),
                    error_visible: await error.isVisible(), create_controls: await creates.count(),
                    delete_controls: await deletes.count(), enabled_delete_controls: await enabled.count(),
                    sidebar_visible: await sidebar.isVisible(), hash: new URL(page.url()).hash};
            };
            const listed = async act => withResponseAction(page, response => new URL(response.url()).pathname === LIST
                && response.request().method() === 'GET', act);
            action = 'read';
            mark('mutation-sidebar-navigation');
            await page.locator('#bkn_aside').getByText('Comunicazioni', {exact: true}).click();
            await expect(sidebar).toBeVisible();
            mark('mutation-list-navigation');
            let response = await listed(() => sidebar.click());
            let view = await (readonly ? collectDenied(response) : collect(response));
            if (readonly) {
                await drain();
                mark('mutation-restricted-mutation-absence');
                assert.deepEqual([...used].filter(key => key.startsWith(name + ':')).sort(),
                    [`${name}:login`, `${name}:read:list`].sort());
            }
            await now({view});
            if (!readonly) {
                const initialIds = view.response.map(row => row.message_id);
                action = 'create';
                mark('mutation-create-open');
                await page.locator('[data-target="#addModal"]').click();
                await expect(page.locator('#addModal')).toBeVisible();
                mark('mutation-create-form');
                await expect(page.locator('#communications_form input[name="select"]')).not.toBeChecked();
                await page.locator('#communications_form select[name="type"]').selectOption('EMAIL');
                await page.locator('#communications_form input[name="subject"]').fill(identity.create_body.subject);
                await page.locator('#communications_form textarea[name="message"]').fill(identity.create_body.message);
                mark('mutation-create-submit');
                response = await listed(() => page.locator('#communications_form button[type="submit"]').click());
                view = await collect(response);
                mark('mutation-created-row');
                const added = view.response.filter(row => !initialIds.includes(row.message_id));
                assert.equal(added.length, 1); assert(uuid(added[0].message_id));
                created.push({actor: name, id: added[0].message_id});
                await now({view});
                action = 'create_reload';
                mark('mutation-create-reload');
                response = await listed(() => page.reload()); view = await collect(response);
                mark('mutation-created-row-persistence');
                assert(view.response.some(row => row.message_id === added[0].message_id));
                await now({view});
                action = 'delete';
                mark('mutation-delete-open');
                const button = page.locator(`#action-col-${identity.delete_id} button[title="Elimina"]`);
                await expect(button).toBeVisible(); await expect(button).toBeEnabled(); await button.click();
                mark('mutation-delete-confirmation');
                await expect(page.getByText('Vuoi eliminare il messaggio?', {exact: true})).toBeVisible();
                mark('mutation-delete-submit');
                response = await listed(() => page.locator('.swal2-confirm').click());
                view = await collect(response);
                mark('mutation-deleted-row-absence');
                await expect(page.locator(`#action-col-${identity.delete_id}`)).toHaveCount(0);
                deleted.add(identity.delete_id);
                await now({view});
                action = 'delete_reload';
                mark('mutation-delete-reload');
                response = await listed(() => page.reload()); view = await collect(response);
                mark('mutation-deleted-row-absence');
                await expect(page.locator(`#action-col-${identity.delete_id}`)).toHaveCount(0);
                await now({view});
            }
            await drain();
            closing = true; entry.location = location(); await close(entry);
            assert(entry.closed);
            action = 'closed'; await now({});
            closing = false;
        }
        await drain();
        mark('mutation-final-counts');
        assert.equal(report.checkpoints.length, 24);
        assert.equal(report.requests.length, 26);
        assert.equal(report.requests.filter(row => row.path === LIST.slice(4)).length, 16);
        assert.equal(report.requests.filter(row => row.path === LOGIN.slice(4)).length, 4);
        assert.equal(report.requests.filter(row => row.path === ADD.slice(4)).length, 3);
        assert.equal(report.requests.filter(row => row.method === 'DELETE').length, 3);
        report.all_response_bodies_complete = true;
        report.success = true;
    } catch {
        fail('workflow-failed');
    } finally {
        closing = true;
        for (const entry of contexts) {
            if (!entry.attempted) entry.location = location();
            await close(entry);
        }
        try { mark('mutation-cleanup-drain'); await barrier.drain(); }
        catch { fail('incomplete-cleanup'); }
        report.contexts_closed = contexts.every(entry => entry.closed);
        report.static_verified = [...staticFiles].sort();
        report.success &&= report.contexts_created === 4 && report.contexts_closed && report.failures.length === 0;
    }
    return report;
}
