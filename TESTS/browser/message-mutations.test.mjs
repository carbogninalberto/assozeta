// Offline controls. Fake routes/contexts here are never acceptance evidence.
import test from 'node:test';
import assert from 'node:assert/strict';
import {childFailure} from './message-reads-runner.mjs';
import {MUTATION_WORKFLOW_STEPS, MUTATION_FAILURE_KINDS, MUTATION_ACTIONS,
    authoredRoutes, classifyMutationRequest, forwardMutationRequest, validMutationLocation,
    validateInput, validateBrowserResponse, boundedClose, exercise} from './message-mutations.mjs';

const origin = 'http://127.0.0.1:49123';
const target = 'a1000000-0000-4000-8000-000000720001';
const listDenial = {detail: "Access denied: No permission mapping for endpoint 'communications/messages/list'. Contact administrator to grant access."};
const listError = 'Impossibile caricare i dati.';
const input = () => ({origin, version: 'v2', artifact_sha256: {'index.html': 'a'.repeat(64)},
    actors: Object.fromEntries(['owner', 'full_collaborator', 'foreign_owner', 'restricted_collaborator']
        .map(actor => [actor, {username: 'private-user', password: 'PRIVATE-PASSWORD',
            user_id: target, forbidden_ids: [], ...(actor === 'restricted_collaborator' ? {} : {delete_id: target,
            create_body: {type: 'EMAIL', subject: 'Browser subject', message: 'A saved browser message with enough text.'}})}]))});

test('fixed route authorization rejects external, query, method, path and caller widening', () => {
    const value = input();
    validateInput(value);
    assert.equal(classifyMutationRequest(value, origin + '/index.html?v=0', 'GET').kind, 'static');
    assert.equal(classifyMutationRequest(value, origin + '/index.html?v=1', 'GET'), null);
    assert.equal(classifyMutationRequest(value, origin + '/api/communications/messages/add', 'POST').kind, 'selected');
    for (const [url, method] of [
        [origin + '/api/communications/messages/add', 'GET'],
        [origin + '/api/communications/messages/add?x=1', 'POST'],
        [origin + '/api/communications/send/email', 'POST'],
        [origin + '/api/communications/messages/' + target + '/delete', 'POST'],
        ['http://example.test/api/communications/messages/add', 'POST'],
        [origin + '/api/v2/communications/messages/add', 'POST'],
    ]) assert.equal(classifyMutationRequest(value, url, method), null);
    value.selected_routes = {'/api/anything': 'POST'};
    assert.equal(classifyMutationRequest(value, origin + '/api/anything', 'POST'), null);
    assert.throws(() => validateInput(value));
    for (const bad of ['http://example.test', 'https://localhost:1234', origin + '/path', origin + '?x'])
        assert.throws(() => validateInput({...input(), origin: bad}));
    assert.equal(authoredRoutes(input())['/api/communications/messages/' + target + '/delete'], 'DELETE');
});

function route(mode = 'ok', path = '/api/communications/messages/add', method = 'POST') {
    const calls = [], request = {url: () => origin + path, method: () => method,
        redirectedFrom: () => null, allHeaders: async () => ({authorization: 'PRIVATE-TOKEN'}),
        postData: () => JSON.stringify(input().actors.owner.create_body), resourceType: () => 'fetch'};
    const response = {url: () => mode === 'pairing' ? origin + '/wrong' : request.url(), status: () => mode === 'redirect' ? 302 : 200,
        dispose: async () => {calls.push('dispose');}, body: async () => Buffer.from('{}')};
    return {calls, request: () => request,
        fetch: async options => {calls.push('fetch'); assert.equal(options.maxRedirects, 0);
            assert.equal(options.maxRetries, 0); assert.equal(options.headers.authorization, 'PRIVATE-TOKEN');
            if (mode === 'fetch') throw Error('PRIVATE-NETWORK'); return response;},
        fulfill: async ({response: actual}) => {assert.equal(actual, response); calls.push('fulfill');
            if (mode === 'fulfill') throw Error('PRIVATE-FULFILL');},
        abort: async () => {calls.push('abort');}};
}
const options = () => ({input: input(), actor: 'owner', action: 'create', cid: 'authored-cid',
    used: new Set(), blocked: new WeakSet(), dependencies: [], fail: () => {}});

test('restricted actor forwards exactly one UI list request and never forwards mutations', async () => {
    const opts = {...options(), actor: 'restricted_collaborator', action: 'read'}, failures = [];
    opts.fail = kind => failures.push(kind);
    const list = route('ok', '/api/communications/messages/list', 'GET');
    await forwardMutationRequest(list, opts);
    assert.deepEqual(failures, []);
    assert.deepEqual(list.calls, ['fetch', 'fulfill', 'dispose']);
    const duplicate = route('ok', '/api/communications/messages/list', 'GET');
    await forwardMutationRequest(duplicate, opts);
    assert.deepEqual(duplicate.calls, ['abort']);
    for (const action of ['read', 'create', 'delete', 'closed']) {
        for (const [path, method] of [['/api/communications/messages/add', 'POST'],
            ['/api/communications/messages/' + target + '/delete', 'DELETE']]) {
            const mutation = route('ok', path, method);
            await forwardMutationRequest(mutation, {...opts, action});
            assert.deepEqual(mutation.calls, ['abort']);
        }
    }
    assert(failures.every(kind => ['unexpected-request-list', 'unexpected-request-api'].includes(kind)));
});

test('read-only diagnostics accept only fixed locations and reject malformed or private event fields', () => {
    const location = {actor: 'restricted_collaborator', action: 'read', step: 'mutation-restricted-delete-disabled'};
    assert(validMutationLocation(location));
    for (const bad of [{...location, action: 'denied'}, {...location, action: 'create'},
        {...location, step: 'PRIVATE?token=PRIVATE'}, {...location, private: 'PRIVATE'}])
        assert(!validMutationLocation(bad));
    const diagnostic = {workflow_failure: location, failure_count: 1, failures_truncated: false,
        failures: [{kind: 'workflow-failed', ...location}]};
    const frame = value => childFailure('browser-exercise', Error('PRIVATE'), value, 'mutations');
    assert.deepEqual(frame(diagnostic).mutation_diagnostics, diagnostic);
    for (const bad of [{...diagnostic, private: 'PRIVATE'}, {...diagnostic, failure_count: 2},
        {...diagnostic, failures: [...diagnostic.failures, {kind: 'PRIVATE', ...location}]},
        {...diagnostic, failures: [{...diagnostic.failures[0], private: 'PRIVATE'}]}]) {
        const rejected = frame(bad);
        assert.equal(rejected.mutation_diagnostics.failures[0].kind, 'invalid-mutation-diagnostic');
        assert(!JSON.stringify(rejected).includes('PRIVATE'));
    }
});

test('forwarding preserves real response and observes before fulfill', async () => {
    const probe = route();
    await forwardMutationRequest(probe, {...options(), observe: async () => probe.calls.push('observe')});
    assert.deepEqual(probe.calls, ['fetch', 'observe', 'fulfill', 'dispose']);
});
for (const mode of ['fetch', 'fulfill', 'redirect', 'observe', 'pairing']) test('fails closed on ' + mode, async () => {
    const probe = route(mode);
    await assert.rejects(forwardMutationRequest(probe, {...options(), observe: async () => {
        if (mode === 'observe') throw Error('PRIVATE-OBSERVATION');
    }}));
    assert(probe.calls.includes('abort'));
    if (mode !== 'fetch') assert(probe.calls.includes('dispose'));
    if (['redirect', 'observe', 'pairing'].includes(mode)) assert(!probe.calls.includes('fulfill'));
});

test('complete real-response validation rejects wrong status/body/header/path/method and failed completion', async () => {
    for (const bad of ['none', 'status', 'body', 'tail', 'backend', 'path', 'method', 'completion', 'mime', 'worker']) {
        const request = {url: () => origin + '/api/communications/messages/add', method: () => 'POST', redirectedFrom: () => null};
        const response = {request: () => request, url: request.url, status: () => 200,
            body: async () => Buffer.from('{"msg":"message added."}'), finished: async () => null,
            fromServiceWorker: () => false,
            headerValue: async key => key === 'content-type' ? 'application/json' : 'go-v2'};
        if (bad === 'status') response.status = () => 201;
        if (bad === 'body') response.body = async () => Buffer.from('{"msg":"fabricated success"}');
        if (bad === 'tail') response.body = async () => Buffer.from('{"msg":"message added."}PRIVATE-TAIL');
        if (bad === 'backend') response.headerValue = async () => 'django-v1';
        if (bad === 'path') response.url = () => origin + '/unexpected';
        if (bad === 'method') request.method = () => 'GET';
        if (bad === 'completion') response.finished = async () => 'PRIVATE-COMPLETION';
        if (bad === 'mime') response.headerValue = async key => key === 'content-type' ? 'text/html' : 'go-v2';
        if (bad === 'worker') response.fromServiceWorker = () => true;
        if (bad === 'none') assert.equal((await validateBrowserResponse(input(), response)).body.msg, 'message added.');
        else await assert.rejects(validateBrowserResponse(input(), response));
    }
});

test('only restricted read accepts the exact authored list403; false200 and wrong403 fail', async () => {
    for (const version of ['v1', 'v2']) {
        for (const mode of ['false200', 'actual403', 'wrong403', 'extra-body', 'unavailable404',
            'incomplete', 'mime', 'tail', 'backend', 'writer', 'login', 'closed']) {
            const value = {...input(), version}, failures = [];
            const request = {url: () => origin + '/api/communications/messages/list',
                method: () => 'GET', redirectedFrom: () => null};
            const response = {request: () => request, url: request.url,
                status: () => mode === 'false200' ? 200 : mode === 'unavailable404' ? 404 : 403,
                body: async () => Buffer.from(JSON.stringify(mode === 'false200' ? []
                    : mode === 'wrong403' ? {detail: 'PRIVATE'}
                    : mode === 'extra-body' ? {...listDenial, extra: 'PRIVATE'} : listDenial)
                    + (mode === 'tail' ? 'PRIVATE-TAIL' : '')),
                finished: async () => mode === 'incomplete' ? 'PRIVATE' : null,
                fromServiceWorker: () => false,
                headerValue: async name => name === 'content-type'
                    ? mode === 'mime' ? 'text/html' : 'application/json'
                    : mode === 'backend' ? 'PRIVATE' : version === 'v1' ? 'django-v1' : 'go-v2'};
            const location = {actor: mode === 'writer' ? 'owner' : 'restricted_collaborator',
                action: ['login', 'closed'].includes(mode) ? mode : 'read'};
            const validate = () => validateBrowserResponse(value, response, kind => failures.push(kind), location);
            if (mode === 'actual403') assert.deepEqual((await validate()).body, listDenial);
            else {
                await assert.rejects(validate(), mode);
                assert.equal(failures.length, 1);
                assert(!JSON.stringify(failures).includes('PRIVATE'));
            }
        }
    }
});

test('wrong action, payload, target, duplicate mutation and redirects never reach network', async () => {
    for (const change of ['action', 'payload', 'target', 'duplicate', 'redirected']) {
        const probe = route(), opts = options();
        if (change === 'action') opts.action = 'read';
        if (change === 'payload') probe.request().postData = () => '{"type":"SMS"}';
        if (change === 'target') opts.actor = 'restricted_collaborator';
        if (change === 'duplicate') opts.used.add('owner:create');
        if (change === 'redirected') probe.request().redirectedFrom = () => ({});
        let failures = 0; opts.fail = () => failures++;
        await forwardMutationRequest(probe, opts);
        assert.equal(failures, 1); assert.deepEqual(probe.calls, ['abort']);
    }
});

test('context cleanup has a deadline and does not claim a timed-out close succeeded', async () => {
    assert.equal(await boundedClose({close: async () => {}}, 15), true);
    assert.equal(await boundedClose({close: async () => {throw Error('PRIVATE');}}, 15), false);
    assert.equal(await boundedClose({close: () => new Promise(() => {})}, 15), false);
});

test('failed workflow returns only authored diagnostics and closes context', async () => {
    let closed = 0;
    const browser = {newContext: async () => ({close: async () => {closed++;},
        routeWebSocket: async () => {throw Error('PRIVATE-PASSWORD PRIVATE-TOKEN');}})};
    const receipt = await exercise({browser, expect: {}, input: input(), checkpoint: async () => ({success: true})});
    assert.equal(receipt.success, false); assert.equal(closed, 1);
    assert(!JSON.stringify(receipt).includes('PRIVATE'));
    assert.equal(receipt.contexts_closed, true);
});
test('ordinary login permits only the existing logo/dashboard query shapes', () => {
    const value = input();
    for (const name of ['subscriptions', 'payments', 'associates', 'bestcourses', 'todaylessons',
        'expiringcarnets', 'expiringmedicalcertificates', 'expiredmedicalcertificates',
        'subscriptionstoapprove', 'incomeAndExpenses', 'expiredPayments'])
        assert.equal(classifyMutationRequest(value, origin + '/api/statistic/dashboard?widget=' + name, 'GET').kind,
            'coexistence');
    assert.equal(classifyMutationRequest(value, origin + '/api/instance/logo.png?v=20261026010101000000', 'GET').kind,
        'coexistence');
    for (const path of ['/api/statistic/dashboard?widget=unknown', '/api/statistic/dashboard?widget=%70ayments',
        '/api/statistic/dashboard?widget=payments&widget=associates', '/api/statistic/dashboard?widget=payments&',
        '/api/instance/logo.png?v=0', '/api/instance/logo.png?v=20261026010101000000&token=PRIVATE',
        '/api/communications/messages/list?widget=payments'])
        assert.equal(classifyMutationRequest(value, origin + path, 'GET'), null);
});

test('DELETE forwards the same request method and empty body, rejects other actor targets and retries', async () => {
    const value = input();
    value.actors.foreign_owner.delete_id = 'a1000000-0000-4000-8000-000000720002';
    for (const mode of ['ok', 'body', 'target', 'method', 'duplicate']) {
        const path = '/api/communications/messages/' + value.actors[mode === 'target' ? 'foreign_owner' : 'owner'].delete_id + '/delete';
        const probe = route('ok', path, mode === 'method' ? 'POST' : 'DELETE');
        probe.request().postData = () => mode === 'body' ? '{"PRIVATE":true}' : null;
        const opts = {...options(), input: value, action: 'delete'};
        if (mode === 'duplicate') opts.used.add('owner:delete');
        const fetch = probe.fetch;
        probe.fetch = async settings => {
            assert.equal(probe.request().method(), 'DELETE');
            assert.equal(probe.request().postData(), null);
            assert(!Object.hasOwn(settings, 'method') && !Object.hasOwn(settings, 'postData'));
            return await fetch(settings);
        };
        let failed = 0; opts.fail = () => failed++;
        await forwardMutationRequest(probe, opts);
        assert.equal(failed, mode === 'ok' ? 0 : 1);
        assert.deepEqual(probe.calls, mode === 'ok' ? ['fetch', 'fulfill', 'dispose'] : ['abort']);
    }
});

// Drive the real helper through its actions. No browser, socket or service exists.
async function diagnosticControl(targetStep, targetActor = 'restricted_collaborator', mode = 'operation') {
    const {EventEmitter} = await import('node:events');
    const value = input(), actors = Object.keys(value.actors), steps = [], checkpoints = [];
    let step, actorName, actionName, faulted = false, closes = 0, routeHandler;
    const guard = () => {
        if (!faulted && mode !== 'requestfailed' && mode !== 'response' && mode !== 'flood'
            && actorName === targetActor && (typeof targetStep === 'string' ? step === targetStep
                : step === targetStep.step && actionName === targetStep.action)) {
            faulted = true; throw Error('PRIVATE-PASSWORD URL?token=PRIVATE DOM BODY STACK');
        }
    };
    const browser = {async newContext() {
        guard();
        const actor = value.actors[actorName], context = new EventEmitter(), page = new EventEmitter();
        let rows = [{message_id: target}], creates = 0, releaseBody, expanded = false, hash = '#/';
        const readonly = actorName === 'restricted_collaborator';
        const send = async (path, method) => {
            const badResponse = mode === 'response' && actorName === targetActor && step === targetStep;
            const request = {url: () => origin + path, method: () => method, redirectedFrom: () => null,
                allHeaders: async () => ({}), postData: () => method === 'POST' && path.endsWith('/add')
                    ? JSON.stringify(actor.create_body) : null, resourceType: () => 'fetch'};
            const denied = readonly && path.endsWith('/list');
            const body = path.endsWith('/login') ? {user_data: {user_id: actor.user_id, requires_welcome: false}}
                : denied ? mode === 'false200' ? [] : mode === 'wrong403' ? {detail: 'PRIVATE'}
                    : mode === 'extra-body' ? {...listDenial, extra: 'PRIVATE'} : listDenial
                : path.endsWith('/list') ? rows : {msg: path.endsWith('/add') ? 'message added.' : 'message deleted.'};
            const response = {request: () => request, url: request.url, status: () => denied && mode !== 'false200' ? 403 : 200,
                fromServiceWorker: () => false, finished: async () => null,
                headerValue: async key => key === 'content-type' ? 'application/json'
                    : badResponse ? 'PRIVATE' : 'go-v2',
                body: async () => {
                    if (badResponse) await new Promise(resolve => {releaseBody = resolve;});
                    return Buffer.from(JSON.stringify(body));
                }, dispose: async () => {}};
            context.emit('request', request);
            await routeHandler({request: () => request, fetch: async () => response,
                fulfill: async () => {context.emit('response', response); page.emit('response', response);},
                abort: async () => {context.emit('requestfailed', request);}});
        };
        const locator = (selector, row) => ({selector,
            first() {return this;}, getByText() {return this;}, locator: child => locator(child, row),
            fill: async () => {guard();}, selectOption: async () => {guard();},
            click: async () => {
                guard();
                if (selector === '#bkn_login_signin_submit') await send('/api/oauth2/login', 'POST');
                if (selector === '#bkn_aside') expanded = !expanded;
                if (selector === 'a[href="/#/communication/messages"]') {
                    assert(expanded, 'Sidebar must be expanded before navigation');
                    hash = mode === 'wrong-hash' && readonly ? '#/404' : '#/communication/messages';
                    await send('/api/communications/messages/list', 'GET');
                    if (readonly && mode === 'extra-list') await send('/api/communications/messages/list', 'GET');
                    if (readonly && mode === 'mutation-on-read') await send('/api/communications/messages/add', 'POST');
                }
                if (selector === '#communications_form button[type="submit"]') {
                    await send('/api/communications/messages/add', 'POST');
                    rows = [...rows, {message_id: `b1000000-0000-4000-8000-00000072000${++creates}`}];
                    await send('/api/communications/messages/list', 'GET');
                }
                if (selector === '.swal2-confirm') {
                    await send('/api/communications/messages/' + actor.delete_id + '/delete', 'DELETE');
                    rows = rows.filter(row => row.message_id !== actor.delete_id);
                    await send('/api/communications/messages/list', 'GET');
                }
            },
            isVisible: async () => {
                guard();
                if (selector === 'a[href="/#/communication/messages"]') return expanded && !(readonly && mode === 'sidebar-missing');
                if (selector === '.datatable-error [role="alert"]') return readonly && mode !== 'error-missing';
                return selector !== 'notice';
            },
            isEnabled: async () => {guard(); return !readonly || mode === 'delete-enabled';},
            count: async () => {
                guard();
                if (selector === '.datatable-error [role="alert"]') return readonly ? 1 : 0;
                if (selector === '[id^="action-col-"] button[title="Elimina"]:enabled')
                    return readonly ? (mode === 'delete-enabled' ? 1 : 0) : rows.length;
                if (selector === '.datatable-body div.datatable-row[data-row]' ||
                    selector === '[id^="action-col-"] button[title="Elimina"]')
                    return readonly ? (mode === 'forged-rows' ? 1 : 0) : rows.length;
                if (selector === '[data-target="#addModal"], [data-target="#addModalPost"]')
                    return readonly ? (mode === 'create-visible' ? 1 : 0) : 2;
                return 0;
            },
            all: async () => {guard(); return rows.map(row => locator('row', row));},
            getAttribute: async name => {guard(); return name === 'id' ? 'action-col-' + row.message_id : 'fixture';},
            innerText: async () => {guard(); return selector === '.datatable-error [role="alert"]'
                ? mode === 'wrong-error' ? 'PRIVATE' : listError : 'fixture';},
        });
        page.locator = selector => {guard(); return locator(selector);};
        page.getByRole = () => {guard(); return locator('notice');};
        page.getByText = () => {guard(); return locator('confirm');};
        page.goto = async () => {guard();};
        page.reload = async () => {guard(); await send('/api/communications/messages/list', 'GET');};
        page.waitForURL = async () => {guard();};
        page.waitForLoadState = async () => {
            guard(); releaseBody?.();
            if (actorName === targetActor && mode === 'requestfailed') {
                const request = {}; context.emit('request', request);
                context.emit('requestfailed', request);
            }
            if (actorName === targetActor && mode === 'flood') {
                for (let index = 0; index < 25; index++) page.emit('pageerror', Error('PRIVATE'));
            }
        };
        page.evaluate = async () => {guard(); return step === 'mutation-login-session' ? actor.user_id : null;};
        page.url = () => {guard(); return origin + '/' + hash;};
        page.setDefaultTimeout = page.setDefaultNavigationTimeout = () => {};
        context.routeWebSocket = async () => {guard();};
        context.route = async (pattern, handler) => {guard(); routeHandler = handler;};
        context.newPage = async () => {guard(); return page;};
        context.close = async () => {closes++; if (mode === 'close' || mode === 'operation') guard();};
        return context;
    }};
    const expect = locator => ({
        toBeVisible: async () => {guard(); assert.equal(await locator.isVisible(), true);},
        toBeEnabled: async () => {guard(); assert.equal(await locator.isEnabled(), true);},
        toBeDisabled: async () => {guard(); assert.equal(await locator.isEnabled(), false);},
        toHaveCount: async expected => {guard(); assert.equal(await locator.count(), expected);},
        toHaveText: async expected => {guard(); assert.equal(await locator.innerText(), expected);},
        not: {toBeChecked: async () => {guard();}},
    });
    expect.poll = fn => ({toBe: async expected => {guard(); assert.equal(await fn(), expected);}});
    const receipt = await exercise({browser, expect, input: value,
        onWorkflowStep: (current, location) => {
            step = current; actorName = location.actor; actionName = location.action;
            steps.push({...location, step});
            if (mode === 'callback' || current === 'mutation-restricted-mutation-absence') guard();
        },
        checkpoint: async payload => {
            guard(); checkpoints.push(payload);
            return {success: true, checkpoint: checkpoints.length};
        }});
    return {receipt, steps, checkpoints, closes, faulted};
}

test('actual mutation workflow retains every restricted assertion, drain, checkpoint and close failure', async () => {
    const successful = await diagnosticControl('never');
    assert.equal(successful.receipt.success, true, JSON.stringify(successful.receipt));
    assert.equal(successful.checkpoints.length, 24);
    assert.equal(successful.closes, 4);
    const targets = ['mutation-sidebar-navigation', 'mutation-list-navigation',
        'mutation-list-row-contents', 'mutation-restricted-create-absence',
        'mutation-restricted-delete-disabled', 'mutation-restricted-mutation-absence',
        'mutation-checkpoint', 'mutation-context-close'];
    for (const target of targets) {
        const {receipt, faulted, checkpoints, steps} = await diagnosticControl(target);
        assert(faulted, target);
        assert.equal(receipt.success, false, target);
        const action = target === 'mutation-checkpoint' ? 'login' : 'read';
        assert.deepEqual(receipt.workflow_failure, {actor: 'restricted_collaborator', action, step: target});
        assert(receipt.failures.some(row => row.step === target && row.actor === 'restricted_collaborator'));
        assert.equal(checkpoints.length, target === 'mutation-checkpoint' ? 21 : target === 'mutation-context-close' ? 23 : 22);
        assert(steps.some(row => row.step === 'mutation-context-close'));
        assert(!JSON.stringify(receipt).includes('PRIVATE'));
    }
    // Observer failure at drain must retain that stage even when finally closes.
    const drained = await diagnosticControl('mutation-barrier-drain', 'restricted_collaborator', 'callback');
    assert.equal(drained.receipt.workflow_failure.step, 'mutation-barrier-drain');
    assert.equal(drained.receipt.contexts_closed, true);
});

test('restricted list403 and inline error are observed after menu expansion with no mutation traffic', async () => {
    const {receipt, checkpoints} = await diagnosticControl('never');
    assert.equal(receipt.success, true, JSON.stringify(receipt));
    assert.equal(receipt.requests.length, 26);
    const restricted = checkpoints.filter(row => row.actor === 'restricted_collaborator');
    assert.deepEqual(restricted.map(row => row.action), ['login', 'read', 'closed']);
    const view = restricted[1].view;
    assert.equal(view.sidebar_visible, true);
    assert.equal(view.hash, '#/communication/messages');
    assert.deepEqual(Object.keys(view).sort(), ['response', 'rows', 'error_text', 'error_visible',
        'create_controls', 'delete_controls', 'enabled_delete_controls', 'sidebar_visible', 'hash'].sort());
    assert.deepEqual(view.response, listDenial);
    assert.equal(view.error_visible, true);
    assert.equal(view.error_text, listError);
    assert.equal(view.create_controls, 0);
    assert.equal(view.enabled_delete_controls, 0);
    assert.equal(view.delete_controls, 0);
    assert.deepEqual(view.rows, []);
    assert.equal(restricted[1].requests[0].status, 403);
    assert.deepEqual(restricted[1].requests.map(row => [row.method, row.path]),
        [['GET', '/communications/messages/list']]);
});

for (const mode of ['sidebar-missing', 'wrong-hash', 'create-visible', 'delete-enabled', 'mutation-on-read', 'extra-list',
    'false200', 'wrong403', 'extra-body', 'error-missing', 'wrong-error', 'forged-rows'])
    test('read-only workflow rejects ' + mode, async () => {
        const {receipt, checkpoints} = await diagnosticControl('never', 'restricted_collaborator', mode);
        assert.equal(receipt.success, false);
        assert.equal(receipt.contexts_closed, true);
        assert(!checkpoints.some(row => row.actor === 'restricted_collaborator' && row.action === 'read'));
        assert(!JSON.stringify(receipt).includes('PRIVATE'));
    });

test('route diagnostics report fixed failure categories from actual forwarding failures', async () => {
    for (const mode of ['fetch', 'fulfill', 'redirect', 'observe', 'pairing', 'dispose', 'abort']) {
        const probe = route(mode), failures = [];
        if (mode === 'dispose') {
            const fetch = probe.fetch;
            probe.fetch = async settings => ({...await fetch(settings), dispose: async () => {throw Error('PRIVATE');}});
        }
        if (mode === 'abort') {
            probe.request().postData = () => 'PRIVATE';
            probe.abort = async () => {throw Error('PRIVATE');};
        }
        await assert.rejects(forwardMutationRequest(probe, {...options(), fail: kind => failures.push(kind),
            observe: async () => {if (mode === 'observe') throw Error('PRIVATE');}}));
        assert(failures.includes('route-' + (mode === 'pairing' ? 'observe' : mode) +
            (mode === 'redirect' ? '-blocked' : '-failed')), JSON.stringify({mode, failures}));
        assert(!JSON.stringify(failures).includes('PRIVATE'));
    }
});

test('response diagnostics distinguish fixed checks without exposing private bytes', async () => {
    for (const [mode, expected] of [['pairing', 'pairing'], ['status', 'api-status'], ['body', 'mutation-body'],
        ['backend', 'backend'], ['completion', 'body-completion'], ['mime', 'content-type'],
        ['json', 'json'], ['worker', 'classification']]) {
        const failures = [], request = {url: () => origin + '/api/communications/messages/add',
            method: () => 'POST', redirectedFrom: () => null};
        const response = {request: () => request, url: request.url, status: () => mode === 'status' ? 599 : 200,
            body: async () => Buffer.from(mode === 'json' ? 'PRIVATE' : JSON.stringify(mode === 'body'
                ? {msg: 'PRIVATE'} : {msg: 'message added.'})),
            finished: async () => mode === 'completion' ? 'PRIVATE' : null,
            fromServiceWorker: () => mode === 'worker',
            headerValue: async name => name === 'content-type' ? mode === 'mime' ? 'PRIVATE' : 'application/json'
                : mode === 'backend' ? 'PRIVATE' : 'go-v2'};
        if (mode === 'pairing') response.url = () => origin + '/PRIVATE?token=PRIVATE';
        await assert.rejects(validateBrowserResponse(input(), response, kind => failures.push(kind)));
        assert.deepEqual(failures, ['response-' + expected + '-failed']);
    }
});


test('actual mutation failure frames survive the closed wire', async t => {
    const frames = [], safe = receipt => childFailure('browser-exercise', Error('PRIVATE'), {
        workflow_failure: receipt.workflow_failure, failure_count: receipt.failure_count,
        failures_truncated: receipt.failures_truncated, failures: receipt.failures}, 'mutations');
    const targets = [
        ['restricted_collaborator', 'read', 'mutation-sidebar-navigation'],
        ['restricted_collaborator', 'read', 'mutation-list-navigation'],
        ['restricted_collaborator', 'read', 'mutation-list-row-contents'],
        ['restricted_collaborator', 'read', 'mutation-restricted-create-absence'],
        ['restricted_collaborator', 'read', 'mutation-restricted-delete-disabled'],
        ['restricted_collaborator', 'read', 'mutation-restricted-mutation-absence'],
        ['restricted_collaborator', 'login', 'mutation-checkpoint'],
        ['restricted_collaborator', 'read', 'mutation-context-close'],
        ['owner', 'login', 'mutation-login-submit'],
        ['owner', 'read', 'mutation-list-navigation'],
        ['full_collaborator', 'create', 'mutation-create-form'],
        ['full_collaborator', 'create_reload', 'mutation-create-reload'],
        ['foreign_owner', 'delete', 'mutation-delete-confirmation'],
        ['foreign_owner', 'delete_reload', 'mutation-delete-reload'],
        ['foreign_owner', 'delete_reload', 'mutation-deleted-row-absence'],
        ['owner', 'closed', 'mutation-checkpoint'],
    ];
    for (const [actor, action, step] of targets) {
        const {receipt, faulted} = await diagnosticControl({step, action}, actor);
        assert(faulted, JSON.stringify({actor, action, step}));
        assert.equal(receipt.success, false);
        assert.deepEqual(receipt.workflow_failure, {actor, action, step});
        const frame = safe(receipt);
        assert.deepEqual(frame.mutation_diagnostics.workflow_failure, receipt.workflow_failure);
        assert(!JSON.stringify(frame).includes('PRIVATE'));
        frames.push(frame);
    }
    // The real barrier sees an unexpected request failure, then drain fails.
    const {receipt: failed} = await diagnosticControl('never', 'restricted_collaborator', 'requestfailed');
    assert.equal(failed.success, false);
    assert.deepEqual(failed.workflow_failure, {actor: 'restricted_collaborator', action: 'login',
        step: 'mutation-login-network-idle'});
    assert.equal(failed.failures[0].kind, 'request-failed');
    assert(failed.failures.some(row => row.kind === 'workflow-failed' && row.step === 'mutation-barrier-drain'));
    frames.push(safe(failed));
    // Response validation rejects the actual bad backend header inside exercise.
    const {receipt: response} = await diagnosticControl('mutation-login-submit', 'owner', 'response');
    assert.equal(response.success, false);
    assert.deepEqual(response.workflow_failure, {actor: 'owner', action: 'login', step: 'mutation-login-submit'});
    assert.equal(response.failures[0].kind, 'response-backend-failed');
    frames.push(safe(response));
    const {receipt: flooded} = await diagnosticControl('never', 'owner', 'flood');
    assert.equal(flooded.failures.length, 16);
    assert.equal(flooded.failure_count, 26); // 25 pageerrors plus workflow assertion.
    assert.equal(flooded.failures_truncated, true);
    frames.push(safe(flooded));
    if (process.env.MUTATION_DIAGNOSTIC_WIRE === '1') t.diagnostic(JSON.stringify({mutation_failure_frames: frames,
        steps: MUTATION_WORKFLOW_STEPS, kinds: MUTATION_FAILURE_KINDS, actions: MUTATION_ACTIONS}));
});

test('rejected requests expose only fixed route categories', async () => {
    for (const [path, category] of [
        ['/api/communications/messages/list', 'list'],
        ['/api/oauth2/login', 'login'],
        ['/api/PRIVATE-UNKNOWN', 'api'],
        ['/index.html?PRIVATE=SECRET', 'query'],
        ['/#PRIVATE-SECRET', 'fragment'],
        ['/PRIVATE-UNKNOWN', 'page'],
    ]) {
        const probe = route('ok', path, 'GET'), kinds = [];
        await forwardMutationRequest(probe, {...options(), actor: 'restricted_collaborator',
            action: 'closed', fail: kind => kinds.push(kind)});
        assert.deepEqual(kinds, ['unexpected-request-' + category]);
        assert.deepEqual(probe.calls, ['abort']);
        assert(!JSON.stringify(kinds).includes('PRIVATE'));
    }
});
