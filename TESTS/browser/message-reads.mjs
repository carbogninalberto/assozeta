// Callable real-browser consumer. Parent owns browser, services, fixture and
// checkpoints; this module owns only fresh contexts. No top-level execution,
// Playwright import, browser launch, or business response replay. When called,
// requests are transparently forwarded only to approved real local endpoints.
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {isDeepStrictEqual} from 'node:util';

const LIST = '/api/communications/messages/list';
const ACTORS = ['owner', 'full_collaborator', 'foreign_owner', 'restricted_collaborator'];
const sha256 = bytes => createHash('sha256').update(bytes).digest('hex');
const normalize = value => value.replace(/\s+/g, ' ').trim();

// Authored operation names only: no selectors, values, DOM or error messages.
export const WORKFLOW_STEPS = Object.freeze(['context-initialization', 'login-page-navigation',
    'login-form', 'login-session-observation', 'login-response-status', 'login-response-header',
    'login-response-body', 'login-identity', 'login-onboarding', 'login-contract-complete',
    'login-navigation', 'dashboard-visible', 'login-notice', 'login-network-idle',
    'response-observation', 'checkpoint', 'context-close', 'page-navigation',
    'sidebar-navigation', 'list-navigation', 'list-status', 'list-header', 'list-body',
    'rendered-header', 'rendered-row-count', 'rendered-row-contents', 'rendered-row-comparison',
    'forbidden-row-count', 'controls-headings', 'search', 'search-empty', 'search-clear',
    'sort-ascending', 'sort-descending', 'sort-reset', 'page-size', 'page-controls',
    'controls-comparison', 'reread', 'reload', 'final-response-counts', 'denied-navigation']);
export const CONTROL_FIELDS = Object.freeze(['headings', 'search_query', 'search_ids', 'search_empty',
    'sort_field', 'sort_asc_ids', 'sort_desc_ids', 'page_sizes', 'pages', 'next_disabled',
    'previous_disabled', 'summary', 'list_requests_during_controls']);

export const ROUTE_STAGES = Object.freeze(['fetch', 'observe', 'fulfill', 'dispose', 'abort', 'requestfailed']);
export const NETWORK_CATEGORIES = Object.freeze(['none', 'other', 'redirect-blocked', 'response-disposed',
    'target-closed', 'context-disposed', 'route-already-handled', 'browser-aborted', 'blocked-by-client',
    'network-failed', 'connection-reset', 'connection-refused', 'socket-hang-up', 'timeout', 'upstream-aborted']);
export const DIAGNOSTIC_PATHS = Object.freeze(['/api/instance/status', '/api/instance/config',
    '/api/instance/access', '/api/oauth2/login', '/api/profile/info', LIST,
    '/api/instance/manifest.json', '/api/instance/logo.png', '/api/association/export/active',
    '/api/statistic/dashboard', '/api/communications/staff-board/list']);
export const RESOURCE_TYPES = Object.freeze(['document', 'stylesheet', 'image', 'media', 'font',
    'script', 'texttrack', 'xhr', 'fetch', 'eventsource', 'websocket', 'manifest', 'other']);
export const NAVIGATION_STATES = Object.freeze(['unobserved', 'login', 'root', 'messages', 'denied', 'other']);

// Exact first-line signatures from pinned Playwright 1.63 / Chromium, never a
// substring search through potentially private logs. Only these enums leave JS.
export function networkErrorCategory(error) {
    let line = typeof error?.message === 'string' ? error.message.split('\n', 1)[0] : '';
    line = line.replace(/^(?:route\.(?:fetch|fulfill|abort)|apiResponse\.(?:body|dispose)): /, '');
    const known = new Map([
        ['Response has been disposed', 'response-disposed'],
        ['Fetch response has been disposed', 'response-disposed'],
        ['Target page, context or browser has been closed', 'target-closed'],
        ['Request context disposed.', 'context-disposed'], ['Route is already handled!', 'route-already-handled'],
        ['net::ERR_ABORTED', 'browser-aborted'], ['net::ERR_BLOCKED_BY_CLIENT', 'blocked-by-client'],
        ['net::ERR_FAILED', 'network-failed'], ['read ECONNRESET', 'connection-reset'],
        ['socket hang up', 'socket-hang-up'], ['aborted', 'upstream-aborted'],
    ]);
    if (known.has(line)) return known.get(line);
    const reset = /^connect ECONNRESET 127\.0\.0\.1:([1-9][0-9]{0,4})$/.exec(line);
    if (reset && Number(reset[1]) <= 65535) return 'connection-reset';
    if (/^connect ECONNREFUSED 127\.0\.0\.1:[0-9]{1,5}$/.test(line)) return 'connection-refused';
    if (/^Timeout [0-9]+ms exceeded\.?$/.test(line)) return 'timeout';
    return 'other';
}

// Event ordinals establish ordering without wall-clock values or browser URLs.
// Request IDs are local counters, unrelated to Playwright IDs or private headers.
// Keep first errors independently from successful traffic, which may be large.
export function createRouteDiagnostics(input) {
    const requests = new WeakMap();
    const report = {failure_count: 0, failures_truncated: false, failures: [],
        counts: Object.fromEntries(ROUTE_STAGES.map(stage => [stage, {completed: 0, failed: 0}]))};
    let event = 0, sequence = 0;
    const bump = () => (event = Math.min(event + 1, 1000000));
    const states = new Map();
    const state = actor => {
        if (!states.has(actor)) states.set(actor, {navigation_epoch: 0, navigation_request_id: 0,
            navigation_event: 0, navigation_state: 'unobserved', page_closed: false, context_closing: false});
        return states.get(actor);
    };
    const identify = (request, actor, action) => {
        if (requests.has(request)) return requests.get(request);
        const classification = classifyRequest(input, request.url(), request.method());
        const path = new URL(request.url()).pathname;
        let kind = 'unknown', target = null;
        if (classification?.kind === 'static' && /^[a-f0-9]{64}$/.test(input.artifact_sha256[classification.file])) {
            kind = 'static'; target = input.artifact_sha256[classification.file];
        } else if (classification && DIAGNOSTIC_PATHS.includes(path)) {
            kind = classification.kind; target = path;
        }
        const method = ['GET', 'POST'].includes(request.method()) ? request.method() : 'OTHER';
        const resource = request.resourceType?.();
        const row = {request_id: Math.min(++sequence, 1000000), kind, target, method,
            resource_type: RESOURCE_TYPES.includes(resource) ? resource : 'other', actor, action,
            request_event: bump(), request_navigation_epoch: state(actor).navigation_epoch,
            fetch_complete: false, fulfill_complete: false};
        requests.set(request, row);
        // Only a real main-frame document request establishes navigation intent.
        if (request.isNavigationRequest?.() && request.frame().parentFrame() === null) {
            state(actor).navigation_request_id = row.request_id;
        }
        return row;
    };
    return {
        report, start: identify,
        navigation(actor, address) {
            const current = state(actor), url = new URL(address);
            current.navigation_epoch = Math.min(current.navigation_epoch + 1, 1000000);
            current.navigation_event = bump();
            current.navigation_state = url.origin !== input.origin ? 'other'
                : url.pathname === '/' && url.hash === '#/login' ? 'login'
                : url.pathname === '/' && ['', '#/'].includes(url.hash) ? 'root'
                : url.pathname === '/' && url.hash === '#/communication/messages' ? 'messages'
                : url.pathname === '/' && url.hash === '#/404' ? 'denied' : 'other';
        },
        closing(actor) { state(actor).context_closing = true; bump(); },
        closed(actor) { state(actor).page_closed = true; bump(); },
        stage(request, actor, action, stage, error, category) {
            assert(ROUTE_STAGES.includes(stage));
            const row = identify(request, actor, action), at = bump();
            const failed = error !== undefined || category !== undefined;
            const count = report.counts[stage];
            count[failed ? 'failed' : 'completed'] = Math.min(count[failed ? 'failed' : 'completed'] + 1, 1000000);
            if (!failed) {
                if (stage === 'fetch') row.fetch_complete = true;
                if (stage === 'fulfill') row.fulfill_complete = true;
                return;
            }
            report.failure_count = Math.min(report.failure_count + 1, 1000000);
            if (report.failures.length < 16) report.failures.push({...row, ...state(actor),
                event: at, stage, category: category || networkErrorCategory(error)});
            report.failures_truncated = report.failure_count > 16;
        },
    };
}

export function classifyRequest(input, address, method) {
    const url = new URL(address);
    if (url.origin !== input.origin || url.username || url.password) return null;
    if (input.selected_routes[url.pathname] === method) return {kind: 'selected'};
    if (input.coexistence_routes[url.pathname] === method) return {kind: 'coexistence'};
    if (method !== 'GET' || url.pathname.startsWith('/api/')
        || url.pathname.startsWith('/instance-')) return null;
    let file;
    try { file = url.pathname === '/' ? 'index.html' : decodeURIComponent(url.pathname.slice(1)); }
    catch { return null; }
    if (Object.hasOwn(input.artifact_sha256, file)) return {kind: 'static', file};
    return null;
}

// Fixed active references from UI/index.html, all aborted before network. This
// list permits no provider/font fetch and cannot be widened by caller input.
export const BLOCKED_EXTERNAL_RESOURCES = Object.freeze([
    ['https://fonts.googleapis.com/css2?family=Poppins:ital,wght@0,100;0,200;0,300;0,400;0,500;0,600;0,700;0,800;0,900;1,100;1,200;1,300;1,400;1,500;1,600;1,700;1,800;1,900&display=swap', 'stylesheet'],
    ['https://fonts.googleapis.com/css?family=Inter:300,400,500,600,700', 'stylesheet'],
    ['https://js.stripe.com/v3/', 'script'],
    ['https://accounts.google.com/gsi/client', 'script'],
].map(([url, resource_type]) => Object.freeze({url, method: 'GET', resource_type})));

export function classifyBlockedExternal(url, method, resourceType) {
    return BLOCKED_EXTERNAL_RESOURCES.some(row =>
        row.url === url && row.method === method && row.resource_type === resourceType);
}

// Playwright Route.fetch inherits the real intercepted method/body/headers when
// they are not overridden. Only the list correlation header is added. No
// continue/fallback: unsupported fetch options/errors fail closed. A 3xx is never
// fulfilled, so neither the API client nor the browser follows Location.
export async function forwardContained(route, headers, {observe, diagnostic = () => {}} = {}) {
    let response, aborted = false, firstError, stage = 'fetch';
    const abort = async () => {
        aborted = true;
        try { await route.abort('blockedbyclient'); diagnostic('abort'); }
        catch (error) { diagnostic('abort', error); firstError ||= error; }
    };
    try {
        assert.equal(typeof route.fetch, 'function', 'Redirect-contained fetch required');
        response = await route.fetch({maxRedirects: 0, maxRetries: 0, timeout: 30000,
            ...(headers ? {headers} : {})});
        diagnostic('fetch');
        if (response.status() >= 300 && response.status() < 400) {
            diagnostic('fetch', undefined, 'redirect-blocked');
            firstError = new Error('Redirect blocked before follow');
            await abort();
        } else {
            if (observe) {
                stage = 'observe';
                // Bind observation to the exact intercepted request/real response.
                assert.equal(response.url(), route.request().url());
                await observe(route.request(), response);
                diagnostic('observe');
            }
            stage = 'fulfill';
            await route.fulfill({response});
            diagnostic('fulfill');
        }
    } catch (error) {
        firstError ||= error;
        diagnostic(stage, error);
        if (!aborted) await abort();
    } finally {
        if (response) {
            try { await response.dispose(); diagnostic('dispose'); }
            catch (error) { diagnostic('dispose', error); firstError ||= error; }
        }
    }
    // Cleanup failures must never replace the first actionable forwarding error.
    if (firstError) throw firstError;
}

export async function routeRequest(route, {input, actor, action, fail, blocked, dependencies, onList,
    onForwardedResponse, diagnostics}) {
    const request = route.request();
    const classification = classifyRequest(input, request.url(), request.method());
    const redirected = request.redirectedFrom();
    if (!classification || redirected) {
        blocked.add(request); // exact harness-owned abort, never an ERR_ABORTED blanket exception
        if (!redirected && classifyBlockedExternal(request.url(), request.method(), request.resourceType())) {
            dependencies.push({actor, action, url: request.url(), method: request.method(),
                resource_type: request.resourceType(), blocked: true});
        } else fail('unexpected-request-or-redirect');
        try { await route.abort('blockedbyclient'); diagnostics?.stage(request, actor, action, 'abort'); }
        catch (error) { diagnostics?.stage(request, actor, action, 'abort', error); throw error; }
        return;
    }
    let headers;
    if (new URL(request.url()).pathname === LIST) {
        if (!['read', 'reload'].includes(action) || actor === 'restricted_collaborator'
            || new URL(request.url()).search) {
            blocked.add(request);
            fail('unexpected-list-request');
            try { await route.abort('blockedbyclient'); diagnostics?.stage(request, actor, action, 'abort'); }
            catch (error) { diagnostics?.stage(request, actor, action, 'abort', error); throw error; }
            return;
        }
        headers = {...await request.allHeaders(), 'x-correlation-id': onList(request)};
    }
    await forwardContained(route, headers, {
        observe: onForwardedResponse && ((request, response) => onForwardedResponse(actor, request, response)),
        diagnostic: (stage, error, category) => diagnostics?.stage(request, actor, action, stage, error, category),
    });
}

export async function readCompleteBody(response) {
    const bytes = await response.body();
    const completionError = await response.finished();
    assert.equal(completionError, null, 'Response body failed/aborted');
    return bytes;
}

// Both response validation tasks and requests without headers block checkpoints.
// Deadlines fail the workflow; cleanup cancellation never converts failure to a
// successful dependency. Every task has a rejection handler when registered.
export function createCompletionBarrier(fail, timeoutMs = 30000) {
    const tasks = new Set(), requests = new Map();
    return {
        start(request) {
            if (requests.has(request)) return;
            let resolve;
            const promise = new Promise(done => {resolve = done;});
            requests.set(request, {promise, resolve});
        },
        complete(request) {
            requests.get(request)?.resolve();
            requests.delete(request);
        },
        failed(request, expectedBlocked) {
            this.complete(request);
            if (!expectedBlocked) fail('requestfailed');
        },
        track(task) {
            const guarded = Promise.resolve(task).catch(error => fail('response-or-route-validation', error));
            tasks.add(guarded);
            void guarded.then(() => tasks.delete(guarded));
            return guarded;
        },
        async drain() {
            let timer;
            const deadline = new Promise((_, reject) => {
                timer = setTimeout(() => reject(new Error('Incomplete request/response body')), timeoutMs);
            });
            try {
                do {
                    await Promise.race([deadline, Promise.all([
                        ...tasks, ...[...requests.values()].map(row => row.promise),
                    ])]);
                } while (tasks.size || requests.size);
            } finally { clearTimeout(timer); }
        },
    };
}

// A cancellable response listener instead of an abandoned waitForResponse.
// Attach the rejection handler before acting; settle/detach on every exit path.
export async function withResponseAction(page, predicate, act, timeoutMs = 30000) {
    let resolve, reject, timer, settled = false;
    const waiting = new Promise((yes, no) => {resolve = yes; reject = no;});
    const outcome = waiting.then(value => ({value}), error => ({error}));
    const finish = (error, value) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        page.off('response', onResponse);
        page.off('close', onClose);
        if (error) reject(error); else resolve(value);
    };
    const onResponse = response => {
        try { if (predicate(response)) finish(null, response); }
        catch (error) { finish(error); }
    };
    const onClose = () => finish(new Error('Page closed while awaiting response'));
    page.on('response', onResponse);
    page.on('close', onClose);
    timer = setTimeout(() => finish(new Error('Response timeout')), timeoutMs);
    try {
        await act();
        const result = await outcome;
        if (result.error) throw result.error;
        return result.value;
    } catch (error) {
        finish(error);
        await outcome;
        throw error;
    }
}

/**
 * exercise({browser, expect, input, checkpoint}) -> sanitized receipt.
 *
 * browser and expect are caller-provided Playwright objects. input comes from
 * message_reads_browser.browser_input(). checkpoint({actor, action}) must await
 * durable parent observation and return {success:true}; thrown/failed/missing
 * acknowledgments fail the run. The helper never claims state preservation.
 * Parent must enforce an overall deadline and close its browser on completion.
 * Failed assertions become sanitized failures, never raw errors or page bodies.
 * onWorkflowStep(step, controlsMismatches) optionally observes authored stages.
 * A comparison failure reports only fixed CONTROL_FIELDS names; cleanup retains
 * the failed operation in workflow_failure even as later close stages run.
 */
export async function exercise({browser, expect, input, checkpoint, onForwardedResponse, onResponseCheck,
    onWorkflowStep}) {
    assert(browser && expect && typeof checkpoint === 'function', 'Explicit parent arguments required');
    assert(['v1', 'v2'].includes(input.version), 'Explicit API version required');
    assert.deepEqual(Object.keys(input.actors).sort(), [...ACTORS].sort(), 'Authored actors required');
    const origin = new URL(input.origin);
    assert.equal(origin.origin, input.origin, 'Origin must be canonical');
    assert(['127.0.0.1', '[::1]', 'localhost'].includes(origin.hostname), 'Private loopback proxy required');
    assert.equal(origin.protocol, 'http:', 'Disposable local HTTP proxy required');
    assert.equal(input.selected_routes[LIST], 'GET');
    assert(!Object.hasOwn(input.coexistence_routes, LIST), 'List cannot coexist/fallback');
    const selected = input.version === 'v1' ? 'django-v1' : 'go-v2';
    const report = {
        success: false, version: input.version, contexts_closed: false,
        browser_owned_by_parent: true, service_workers_blocked: true,
        business_responses_mocked: false, authentication_state_seeded: false,
        artifact_sha256: input.artifact_sha256, static_verified: [],
        transport: 'route.fetch(maxRedirects=0,maxRetries=0)->route.fulfill(response)',
        blocked_fixture_dependencies: [], external_integrations_excluded: true,
        font_parity_excluded: true, all_response_bodies_complete: false,
        responses: [], list_requests: [], checks: [], checkpoints: [], failures: [],
    };
    const contexts = [], verified = new Set(), requestCids = new WeakMap(), blocked = new WeakSet();
    const diagnostics = createRouteDiagnostics(input);
    report.network_diagnostics = diagnostics.report;
    let actorName = null, action = 'initialization', sequence = 0;
    let workflowStep = 'context-initialization', controlsMismatches = [];
    const mark = (step, mismatches = []) => {
        assert(WORKFLOW_STEPS.includes(step), 'Unknown authored workflow step');
        assert(mismatches.every(field => CONTROL_FIELDS.includes(field)), 'Unknown authored control field');
        workflowStep = step;
        controlsMismatches = [...mismatches];
        onWorkflowStep?.(step, [...mismatches]);
    };
    const workflowFailure = () => {
        report.workflow_failure ||= {step: workflowStep, controls_mismatches: [...controlsMismatches]};
    };
    const fail = (kind, error) => report.failures.push({
        kind, actor: actorName, action, ...(error ? {error_class: error.name || 'Error'} : {}),
    });
    const barrier = createCompletionBarrier(fail);
    const drain = async () => {
        await barrier.drain();
        assert.deepEqual(report.failures, [], 'Observed browser/proxy failure');
    };
    const checkpointNow = async () => {
        await drain();
        mark('checkpoint');
        const row = {actor: actorName, action};
        const acknowledged = await checkpoint({...row});
        assert.equal(acknowledged?.success, true, 'Parent durable checkpoint missing/failed');
        report.checkpoints.push(row);
    };

    async function fresh(actor) {
        mark('context-initialization');
        const context = await browser.newContext({
            viewport: {width: 1600, height: 1000}, locale: 'it-IT', timezoneId: 'UTC',
            serviceWorkers: 'block', acceptDownloads: false,
        });
        const entry = {context, actor, closed: false};
        contexts.push(entry);
        assert.equal(typeof context.routeWebSocket, 'function', 'Playwright WebSocket guard required');
        // Transparent forwarding only. Unknown/external sockets are rejected,
        // never answered with a fixture business frame or replayed service worker.
        await context.routeWebSocket('**/*', socket => {
            const url = new URL(socket.url());
            const httpOrigin = url.origin.replace(/^ws/, 'http');
            if (httpOrigin !== input.origin || !input.websocket_routes.includes(url.pathname)) {
                fail('unexpected-websocket');
                socket.close();
                return;
            }
            socket.connectToServer();
        });
        context.on('request', request => { diagnostics.start(request, actor, action); barrier.start(request); });
        context.on('requestfailed', request => {
            if (!blocked.has(request)) diagnostics.stage(request, actor, action, 'requestfailed',
                {message: request.failure()?.errorText});
            barrier.failed(request, blocked.has(request));
        });
        await context.route('**/*', route => barrier.track(routeRequest(route, {
            input, actor, action, fail, blocked, dependencies: report.blocked_fixture_dependencies,
            onForwardedResponse, diagnostics,
            onList(request) {
                const cid = `message-browser-${input.version}-${actor}-${action}-${++sequence}`;
                requestCids.set(request, cid);
                report.list_requests.push({actor, action, cid});
                return cid;
            },
        })));
        const page = await context.newPage();
        page.on('framenavigated', frame => {
            if (frame === page.mainFrame()) diagnostics.navigation(actor, frame.url());
        });
        page.on('close', () => diagnostics.closed(actor));
        page.setDefaultTimeout(30000);
        page.setDefaultNavigationTimeout(30000);
        page.on('pageerror', error => fail('pageerror', error));
        page.on('download', () => fail('unexpected-download'));
        page.on('popup', popup => {
            fail('unexpected-popup');
            barrier.track(popup.close());
        });
        context.on('response', response => {
            // Snapshot actor/action now, before asynchronous body observation.
            const responseAction = action;
            barrier.track((async () => {
                const checked = (check, condition) => {
                    if (!condition) { onResponseCheck?.(actor, check); assert(condition); }
                };
                const url = new URL(response.url()), request = response.request();
                const classification = classifyRequest(input, response.url(), request.method());
                checked('classification', classification);
                checked('service-worker', response.fromServiceWorker() === false);
                checked('redirect', request.redirectedFrom() === null);
                let backend;
                try { backend = await response.headerValue('x-fixture-backend'); }
                catch (error) { onResponseCheck?.(actor, 'backend-header'); throw error; }
                const row = {actor, action: responseAction, method: request.method(),
                    path: url.pathname, status: response.status(), backend, body_complete: true};
                // Headers alone never count as a completed dependency. Read ALL
                // API/static bodies and inspect Playwright's completion error.
                let bytes;
                try { bytes = await readCompleteBody(response); }
                catch (error) { onResponseCheck?.(actor, 'body-completion'); throw error; }
                if (classification.kind === 'static') {
                    checked('backend-header', backend === 'ui-static');
                    checked('static-status', response.status() === 200);
                    checked('static-body', sha256(bytes) === input.artifact_sha256[classification.file]);
                    verified.add(classification.file);
                } else {
                    checked('backend-header', backend === (classification.kind === 'selected' ? selected : 'django-coexistence'));
                    const allowed = classification.kind === 'selected' ? [200] : [200, 403, 404];
                    checked('api-status', allowed.includes(response.status()));
                }
                if (url.pathname === LIST) row.cid = requestCids.get(request);
                report.responses.push(row);
            })().finally(() => barrier.complete(response.request())));
        });
        return {entry, page};
    }

    async function close(entry, cleanup = false) {
        if (!entry.closed) {
            if (!cleanup) await drain();
            diagnostics.closing(entry.actor);
            try { mark('context-close'); }
            finally {
                await entry.context.close();
                entry.closed = true;
            }
        }
    }

    async function login(page, actor) {
        mark('login-page-navigation');
        await page.goto(input.origin + '/#/login');
        mark('login-form');
        await expect(page.locator('input[name="username_login"]')).toBeVisible();
        mark('login-session-observation');
        assert.equal(await page.evaluate(() => localStorage.getItem('sessionToken')), null,
            'Fresh context must be unauthenticated');
        mark('login-form');
        await page.locator('input[name="username_login"]').fill(actor.username);
        await page.locator('input[name="password_login"]').fill(actor.password);
        const response = await withResponseAction(page, response =>
            new URL(response.url()).pathname === '/api/oauth2/login'
            && response.request().method() === 'POST',
            () => page.locator('#bkn_login_signin_submit').click());
        mark('login-response-status');
        assert.equal(response.status(), 200);
        mark('login-response-header');
        assert.equal(await response.headerValue('x-fixture-backend'), selected);
        mark('login-response-body');
        const body = await response.json();
        mark('login-identity');
        assert.equal(body.user_data?.user_id, actor.user_id, 'Wrong login identity');
        mark('login-onboarding');
        assert.equal(body.user_data?.requires_welcome, false, 'Parent must prepare onboarding');
        // Login.svelte schedules location.href='/' one second after session
        // initialization. Wait for that real navigation before the read baseline.
        mark('login-navigation');
        await page.waitForURL(url => url.origin === input.origin && url.pathname === '/'
            && (url.hash === '' || url.hash === '#/'));
        mark('dashboard-visible');
        await expect(page.locator('#bkn_aside')).toBeVisible();
        mark('login-session-observation');
        await expect.poll(() => page.evaluate(() =>
            JSON.parse(localStorage.getItem('userData') || '{}').user_id)).toBe(actor.user_id);
        mark('login-notice');
        const notice = page.getByRole('button', {name: /Ok, grazie/});
        if (await notice.isVisible()) await notice.click();
        mark('login-network-idle');
        await page.waitForLoadState('networkidle');
        await checkpointNow();
    }

    const sidebar = page => page.locator('a[href="/#/communication/messages"]').first();
    const rows = page => page.locator('.datatable-body div.datatable-row[data-row]');
    const ids = page => rows(page).evaluateAll(nodes => nodes.map(node => {
        const actionColumn = node.querySelector('[id^="action-col-"]');
        return actionColumn?.id.slice('action-col-'.length) || null;
    }));
    async function collect(page) {
        mark('rendered-row-contents');
        await expect(page.locator('.datatable-loading')).toHaveCount(0);
        const result = [];
        for (const row of await rows(page).all()) {
            const item = {id: await row.locator('[id^="action-col-"]').getAttribute('id')};
            item.id = item.id.slice('action-col-'.length);
            for (const field of ['message', 'type', 'subject', 'created_at']) {
                const cell = row.locator(`[data-field="${field}"]`);
                await expect(cell).toBeVisible();
                item[field === 'created_at' ? 'date' : field] = normalize(await cell.innerText());
                if (['message', 'subject'].includes(field)) {
                    const paragraph = cell.locator('p');
                    item[field + '_title'] = await paragraph.getAttribute('data-original-title')
                        ?? await paragraph.getAttribute('title');
                }
            }
            result.push(item);
        }
        return result;
    }

    async function controls(page, actor) {
        const reference = actor.controls, result = {};
        const before = report.list_requests.length;
        mark('controls-headings');
        result.headings = [];
        for (const [field, label] of [['message', 'Contenuto'], ['type', 'Tipo'],
            ['subject', 'Oggetto'], ['created_at', 'Data']]) {
            const heading = page.locator(`.datatable-head [data-field="${field}"]`);
            await expect(heading).toBeVisible();
            await expect(heading).toHaveText(label);
            result.headings.push(normalize(await heading.innerText()));
        }
        mark('search');
        const search = page.getByRole('textbox', {name: 'Cerca nella tabella'});
        await expect(search).toHaveAttribute('placeholder', 'Cerca...');
        await search.fill(reference.search_query);
        await expect.poll(() => ids(page)).toEqual(reference.search_ids);
        result.search_query = await search.inputValue();
        result.search_ids = await ids(page);
        mark('search-empty');
        await search.fill(actor.negative_query);
        await expect(rows(page)).toHaveCount(0);
        await expect(page.getByText('Nessun dato disponibile', {exact: true})).toBeVisible();
        result.search_empty = true;
        mark('search-clear');
        await page.getByRole('button', {name: 'Cancella ricerca', exact: true}).click();
        await expect.poll(() => ids(page)).toEqual(actor.visible_rows.map(row => row.id));
        mark('sort-ascending');
        const sort = page.locator('.datatable-head [data-field="created_at"]');
        await sort.click();
        await expect.poll(() => ids(page)).toEqual(reference.sort_asc_ids);
        result.sort_field = 'created_at';
        result.sort_asc_ids = await ids(page);
        mark('sort-descending');
        await sort.click();
        await expect.poll(() => ids(page)).toEqual(reference.sort_desc_ids);
        result.sort_desc_ids = await ids(page);
        mark('sort-reset');
        await sort.click();
        await expect.poll(() => ids(page)).toEqual(actor.visible_rows.map(row => row.id));
        mark('page-size');
        const size = page.locator('.datatable-pager-size');
        await expect(size).toHaveValue('10');
        result.page_sizes = [Number(await size.inputValue())];
        for (const value of ['20', '10']) {
            await size.selectOption(value);
            await expect(size).toHaveValue(value);
            await expect.poll(() => ids(page)).toEqual(actor.visible_rows.map(row => row.id));
            result.page_sizes.push(Number(await size.inputValue()));
        }
        mark('page-controls');
        result.pages = await page.locator('.datatable-pager-link-number').count();
        result.next_disabled = await page.locator('.datatable-pager-link-next').isDisabled();
        result.previous_disabled = await page.locator('.datatable-pager-link-prev').isDisabled();
        result.summary = normalize(await page.locator('.datatable-pager-detail').innerText());
        await drain();
        result.list_requests_during_controls = report.list_requests.length - before;
        mark('controls-comparison');
        try {
            assert.deepEqual(result, reference, 'Client operations differ from authored expectations');
        } catch (error) {
            try {
                mark('controls-comparison', CONTROL_FIELDS.filter(field =>
                    !isDeepStrictEqual(result[field], reference[field])));
            } finally { throw error; } // Observation must retain the original assertion.
        }
        return result;
    }

    async function read(page, actor, navigate) {
        if (navigate) {
            mark('sidebar-navigation');
            await page.locator('#bkn_aside').getByText('Comunicazioni', {exact: true}).click();
            await expect(sidebar(page)).toBeVisible();
        }
        mark(navigate ? 'list-navigation' : 'reload');
        const response = await withResponseAction(page, response => new URL(response.url()).pathname === LIST
            && response.request().method() === 'GET',
            () => navigate ? sidebar(page).click() : page.reload());
        mark('list-status');
        assert.equal(response.status(), 200);
        mark('list-header');
        assert.equal(await response.headerValue('x-fixture-backend'), selected);
        mark('list-body');
        assert.deepEqual(await response.json(), actor.expected_response, 'Authored API rows differ');
        mark('rendered-header');
        await expect(page.locator('.card-title h3')).toContainText('Messaggi');
        mark('rendered-row-count');
        await expect.poll(() => rows(page).count()).toBe(actor.visible_rows.length);
        const displayed = await collect(page);
        mark('rendered-row-comparison');
        assert.deepEqual(displayed, actor.visible_rows, 'Visible row content differs');
        mark('forbidden-row-count');
        for (const id of actor.forbidden_ids)
            await expect(page.locator(`#action-col-${id}`)).toHaveCount(0);
        const checkedControls = await controls(page, actor);
        // Full rendered content must survive resetting all client controls.
        const reread = await collect(page);
        mark('reread');
        assert.deepEqual(reread, actor.visible_rows);
        report.checks.push({actor: actorName, action, rows: displayed,
            foreign_ids_absent: [...actor.forbidden_ids], controls: checkedControls,
            cid: requestCids.get(response.request())});
    }

    try {
        for (const name of ACTORS) {
            actorName = name;
            action = 'login';
            const actor = input.actors[name];
            const {entry, page} = await fresh(name);
            await login(page, actor);
            if (name === 'restricted_collaborator') {
                action = 'denied';
                const before = report.list_requests.length;
                mark('denied-navigation');
                await expect(sidebar(page)).toHaveCount(0);
                await page.goto(input.origin + '/#/communication/messages');
                await expect.poll(() => new URL(page.url()).hash).toBe('#/404');
                await expect(rows(page)).toHaveCount(0);
                await drain();
                const requests = report.list_requests.length - before;
                assert.equal(requests, 0, 'UI-denied actor must not issue an API request');
                report.checks.push({actor: name, action, sidebar_link_visible: false,
                    redirect_hash: '#/404', list_requests: requests, table_rows: 0});
                await close(entry);
                await checkpointNow();
            } else {
                action = 'read';
                await read(page, actor, true);
                await checkpointNow();
                action = 'reload';
                await read(page, actor, false);
                await close(entry);
                await checkpointNow();
            }
        }
        await drain();
        mark('final-response-counts');
        assert.equal(report.list_requests.length, 6, 'Unexpected extra/aborted UI list request');
        assert.equal(report.responses.filter(row => row.path === LIST).length, 6);
        assert.deepEqual(report.responses.filter(row => row.path === '/api/oauth2/login')
            .map(row => row.actor), ACTORS, 'Ordinary login count/order differs');
        report.all_response_bodies_complete = true;
        report.success = true;
    } catch (error) {
        workflowFailure();
        fail('workflow-assertion', error);
    } finally {
        for (const entry of contexts) {
            try { await close(entry, true); } catch (error) { workflowFailure(); fail('context-close', error); }
        }
        try { await barrier.drain(); } catch (error) { fail('incomplete-cleanup', error); }
        report.contexts_closed = contexts.length === 4 && contexts.every(entry => entry.closed);
        report.static_verified = [...verified].sort();
        report.success = report.success && report.contexts_closed && report.failures.length === 0;
    }
    return report;
}
