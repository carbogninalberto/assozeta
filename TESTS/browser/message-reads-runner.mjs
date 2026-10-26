// Parent-only executable bridge. Importing this file does not load Playwright,
// read credentials, start a server, or launch a browser.
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {readFile, stat} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {resolve, isAbsolute} from 'node:path';
import {pathToFileURL} from 'node:url';
import {exercise, forwardContained, readCompleteBody, classifyRequest, ROUTE_STAGES,
    NETWORK_CATEGORIES, DIAGNOSTIC_PATHS, RESOURCE_TYPES, NAVIGATION_STATES,
    WORKFLOW_STEPS, CONTROL_FIELDS} from './message-reads.mjs';

const ACTORS = ['owner', 'full_collaborator', 'foreign_owner', 'restricted_collaborator'];
const ORDER = ACTORS.flatMap(actor => (actor === 'restricted_collaborator'
    ? ['login', 'denied'] : ['login', 'read', 'reload']).map(action => ({actor, action})));
const digest = bytes => createHash('sha256').update(bytes).digest('hex');
const jsonDigest = value => digest(JSON.stringify(value));
const FAILURE_CODES = Object.freeze({
    startup: 'startup-failed', 'browser-ready': 'parent-ready-failed',
    'browser-launch': 'browser-launch-failed', 'transport-probe': 'transport-probe-failed',
    'browser-exercise': 'browser-exercise-failed', 'browser-close': 'browser-close-failed',
    'binary-after': 'browser-binary-drift',
});
const ERROR_CLASSES = ['AssertionError', 'Error', 'TypeError', 'RangeError',
    'SyntaxError', 'TimeoutError', 'ReferenceError', 'URIError'];
const FAILURE_KINDS = ['unexpected-request-or-redirect', 'unexpected-list-request',
    'requestfailed', 'response-or-route-validation', 'unexpected-websocket', 'pageerror',
    'unexpected-download', 'unexpected-popup', 'workflow-assertion', 'context-close',
    'incomplete-cleanup'];
const ACTIONS = ['initialization', 'login', 'read', 'reload', 'denied'];
const RESPONSE_CHECKS = ['classification', 'service-worker', 'redirect', 'backend-header',
    'static-status', 'static-body', 'api-status', 'body-completion'];

export function sanitizedNetworkDiagnostics(value) {
    const exact = (value, keys) => value && typeof value === 'object' && !Array.isArray(value)
        && Object.keys(value).length === keys.length && Object.keys(value).every(key => keys.includes(key));
    const integer = value => Number.isInteger(value) && value >= 0 && value <= 1000000;
    if (!exact(value, ['failure_count', 'failures_truncated', 'failures', 'counts'])
        || !integer(value.failure_count) || value.failures_truncated !== (value.failure_count > 16)
        || !Array.isArray(value.failures) || value.failures.length !== Math.min(value.failure_count, 16)
        || !exact(value.counts, ROUTE_STAGES)) return null;
    for (const count of Object.values(value.counts))
        if (!exact(count, ['completed', 'failed']) || !integer(count.completed) || !integer(count.failed)) return null;
    if (value.failure_count !== Math.min(Object.values(value.counts).reduce((sum, row) => sum + row.failed, 0), 1000000)) return null;
    const numbers = ['request_id', 'request_event', 'request_navigation_epoch', 'navigation_epoch',
        'navigation_request_id', 'navigation_event', 'event'];
    const bools = ['fetch_complete', 'fulfill_complete', 'page_closed', 'context_closing'];
    const keys = [...numbers, ...bools, 'kind', 'target', 'method', 'resource_type', 'actor',
        'action', 'navigation_state', 'stage', 'category'];
    let previous = 0;
    for (const row of value.failures) {
        if (!exact(row, keys) || !numbers.every(key => integer(row[key]))
            || !bools.every(key => typeof row[key] === 'boolean')
            || !ACTORS.includes(row.actor) || !ACTIONS.includes(row.action)
            || !['GET', 'POST', 'OTHER'].includes(row.method) || !RESOURCE_TYPES.includes(row.resource_type)
            || !NAVIGATION_STATES.includes(row.navigation_state) || !ROUTE_STAGES.includes(row.stage)
            || !NETWORK_CATEGORIES.includes(row.category) || row.category === 'none'
            || !(row.kind === 'unknown' && row.target === null
                || row.kind === 'static' && typeof row.target === 'string' && /^[a-f0-9]{64}$/.test(row.target)
                || ['selected', 'coexistence'].includes(row.kind) && DIAGNOSTIC_PATHS.includes(row.target))
            || !(row.request_id > 0 && row.request_event > 0 && row.request_event < row.event
                && previous < row.event && row.request_navigation_epoch <= row.navigation_epoch
                && row.navigation_event < row.event)) return null;
        previous = row.event;
    }
    return structuredClone(value);
}

export function sanitizedExerciseFailures(rows) {
    const invalid = () => ({kind: 'invalid-exercise-failure', actor: null,
        action: 'initialization', error_class: 'Other'});
    if (!Array.isArray(rows) || rows.length < 1) return [invalid()];
    const result = [];
    for (const row of rows.slice(0,16)) {
        if (!row || typeof row !== 'object' || Array.isArray(row)
            || Object.keys(row).some(key => !['kind','actor','action','error_class'].includes(key))
            || !FAILURE_KINDS.includes(row.kind) || !ACTIONS.includes(row.action)
            || !(ACTORS.includes(row.actor) || (row.actor === null && row.action === 'initialization')))
            { result.push(invalid()); continue; }
        result.push({kind: row.kind, actor: row.actor, action: row.action,
            error_class: ERROR_CLASSES.includes(row.error_class) ? row.error_class : 'Other'});
    }
    return result;
}

export function childFailure(stage, error, diagnostics) {
    const safeStage = Object.hasOwn(FAILURE_CODES, stage) ? stage : 'startup';
    const result = {type: 'failure', stage: safeStage, code: FAILURE_CODES[safeStage],
        error_class: ERROR_CLASSES.includes(error?.name) ? error.name : 'Other'};
    if (safeStage === 'browser-exercise' && diagnostics) {
        result.workflow_step = WORKFLOW_STEPS.includes(diagnostics.workflow_step)
            ? diagnostics.workflow_step : 'context-initialization';
        const fields = diagnostics.controls_mismatches === undefined ? [] : diagnostics.controls_mismatches;
        result.controls_mismatches = Array.isArray(fields) && fields.length <= CONTROL_FIELDS.length
            && fields.every(field => CONTROL_FIELDS.includes(field)) && new Set(fields).size === fields.length
            && (fields.length === 0 || result.workflow_step === 'controls-comparison') ? [...fields] : null;
        result.exercise_failures = sanitizedExerciseFailures(diagnostics.failures);
        result.failure_count = Array.isArray(diagnostics.failures) ? Math.min(diagnostics.failures.length, 1000000) : 0;
        result.failures_truncated = result.failure_count > 16;
        result.response_checks = (diagnostics.response_checks || []).slice(0,32).map(row =>
            row && ACTORS.includes(row.actor) && RESPONSE_CHECKS.includes(row.check)
                && Object.keys(row).every(key => ['actor','check'].includes(key))
                ? {actor:row.actor,check:row.check} : {actor:null,check:'invalid-response-diagnostic'});
        if (diagnostics.network_diagnostics !== undefined)
            result.network_diagnostics = sanitizedNetworkDiagnostics(diagnostics.network_diagnostics);
    }
    return result;
}

// Observe actual Page Response reads without changing their results or identity
// of response.request(). The shared helper still executes every original check.
export function observeBrowserResponse(response, input, record) {
    const request = response.request();
    const classification = classifyRequest(input,response.url(),request.method());
    if (!classification) record('classification');
    if (response.fromServiceWorker()) record('service-worker');
    if (request.redirectedFrom()) record('redirect');
    return new Proxy(response, {get(target,key) {
        if (key === 'headerValue') return async name => {
            const value = await target.headerValue(name);
            if (name.toLowerCase() === 'x-fixture-backend' && classification) {
                const expected = classification.kind === 'static' ? 'ui-static'
                    : classification.kind === 'coexistence' ? 'django-coexistence'
                    : input.version === 'v1' ? 'django-v1' : 'go-v2';
                if (value !== expected) record('backend-header');
            }
            return value;
        };
        if (key === 'status') return () => {
            const value = target.status();
            if (classification?.kind === 'static' && value !== 200) record('static-status');
            if (classification && classification.kind !== 'static'
                && !(classification.kind === 'selected' ? [200] : [200,403,404]).includes(value)) record('api-status');
            return value;
        };
        if (key === 'body' || key === 'finished') return async (...args) => {
            try {
                const value = await target[key](...args);
                if (key === 'finished' && value !== null) record('body-completion');
                if (key === 'body' && classification?.kind === 'static'
                    && digest(value) !== input.artifact_sha256[classification.file]) record('static-body');
                return value;
            } catch (error) {record('body-completion'); throw error;}
        };
        const value=Reflect.get(target,key,target);
        return typeof value === 'function' ? value.bind(target) : value;
    }});
}

export async function observeLoginContract(actor, input, request, response, mark) {
    if (new URL(request.url()).pathname !== '/api/oauth2/login') return;
    mark('login-response-body');
    const body = await response.json();
    mark('login-identity');
    assert.equal(body.user_data?.user_id, input.actors[actor].user_id);
    mark('login-onboarding');
    assert.equal(body.user_data?.requires_welcome, false);
    mark('login-contract-complete');
}

// A read-only adapter at the real Route boundary. No response is replaced or
// modified: fetch returns the exact APIResponse and fulfill receives the exact
// same options. The observer runs only after the helper rejects redirects, before
// fulfillment/disposal. Its rejection flows through forwardContained's abort.
export function observeForwardedRoute(route, observe) {
    let fetched;
    const request = route.request();
    return new Proxy(route, {
        get(target, key) {
            if (key === 'fetch') return async options => {
                assert.equal(fetched, undefined, 'Only one real fetch permitted');
                fetched = await target.fetch(options);
                return fetched;
            };
            if (key === 'fulfill') return async options => {
                assert.deepEqual(Object.keys(options), ['response']);
                assert(fetched && options.response === fetched, 'Exact fetched response required');
                assert(!(fetched.status() >= 300 && fetched.status() < 400));
                assert.equal(fetched.url(), request.url(), 'Forwarded request/response pairing differs');
                await observe(request, fetched);
                return await target.fulfill(options);
            };
            const value = Reflect.get(target, key, target);
            return typeof value === 'function' ? value.bind(target) : value;
        },
    });
}

export async function forwardedLoginCookie(actor, input, request, response) {
    const address = new URL(request.url());
    if (address.pathname !== '/api/oauth2/login') return null;
    assert(ACTORS.includes(actor), 'Cookie actor must be fixture-authored');
    assert.equal(address.origin, input.origin);
    assert.equal(address.search, '');
    assert.equal(request.method(), 'POST');
    assert.equal(response.status(), 200);
    assert.equal(response.url(), request.url());
    const values = (await response.headersArray())
        .filter(row => row.name.toLowerCase() === 'set-cookie').map(row => row.value);
    assert(requiredLoginCookie(values), 'Required actual BKN_AUTH cookie missing/invalid');
    return {actor, method: 'POST', path: '/api/oauth2/login', sha256: jsonDigest(values),
        bkn_auth_cookie_present: true};
}

export function requiredLoginCookie(values) {
    const matching = values.filter(value => value.startsWith('BKN_AUTH='));
    if (matching.length !== 1) return false;
    const [first, ...parts] = matching[0].split(';');
    if (!/^BKN_AUTH=[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/.test(first)) return false;
    const attributes = new Map(parts.map(part => {
        const [name, ...value] = part.trim().split('=');
        return [name.toLowerCase(), value.join('=')];
    }));
    return attributes.has('httponly') && attributes.has('secure') && attributes.get('path') === '/'
        && attributes.get('samesite')?.toLowerCase() === 'strict'
        && /^[1-9][0-9]*$/.test(attributes.get('max-age') || '');
}

export function createBridge({readable, send, timeoutMs = 30000}) {
    let pending = null, buffer = '', ended = false, sequence = 0, ready = false, closures = 0;
    const fail = error => {
        ended = true;
        if (pending) {
            clearTimeout(pending.timer);
            const {reject} = pending;
            pending = null;
            reject(error);
        }
    };
    const onData = chunk => {
        try {
            buffer += chunk.toString('utf8');
            assert(Buffer.byteLength(buffer) <= 65536, 'Ack frame bound');
            while (buffer.includes('\n')) {
                const end = buffer.indexOf('\n');
                const raw = buffer.slice(0, end);
                buffer = buffer.slice(end + 1);
                const frame = JSON.parse(raw);
                assert(pending && !ended, 'Unsolicited ack');
                const expected = pending.kind === 'ready'
                    ? {type: 'ready_ack', success: true}
                    : {type: pending.kind === 'context' ? 'context_ack' : 'ack',
                        seq: pending.seq, success: true};
                assert.deepEqual(frame, expected, 'Wrong/missing checkpoint ack');
                const {resolve: done, timer} = pending;
                clearTimeout(timer);
                pending = null;
                done({success: true});
            }
        } catch (error) { fail(error); }
    };
    const onEnd = () => fail(new Error('Parent pipe closed'));
    const onError = () => fail(new Error('Parent pipe failed'));
    readable.on('data', onData);
    readable.on('end', onEnd);
    readable.on('error', onError);
    async function request(kind, payload) {
        assert(!pending && !ended, 'Single outstanding checkpoint required');
        return await new Promise((resolve, reject) => {
            const timer = setTimeout(() => fail(new Error('Parent ack deadline')), timeoutMs);
            pending = {kind, seq: payload.seq, timer, resolve, reject};
            try { send(payload); } catch (error) { fail(error); }
        });
    }
    return {
        async ready(browserBinary) {
            assert(!ready && sequence === 0, 'Duplicate ready');
            await request('ready', {type: 'ready', browser_binary: browserBinary});
            assert(!ended, 'Parent disappeared during ready');
            ready = true;
        },
        async checkpoint(row) {
            assert(ready && sequence < ORDER.length, 'Checkpoint before ready/after completion');
            assert.deepEqual(row, ORDER[sequence], 'Checkpoint order differs');
            sequence++;
            return await request('checkpoint', {type: 'checkpoint', seq: sequence, ...row});
        },
        async contextClosed(actor) {
            assert(ready && actor === ACTORS[closures] && sequence === [2, 5, 8, 10][closures],
                'Context closure order differs');
            closures++;
            return await request('context', {type: 'context_closed', seq: closures, actor});
        },
        complete() { assert(ready && !pending && !ended && sequence === ORDER.length && closures === 4); },
        close() {
            fail(new Error('Bridge closed'));
            readable.off('data', onData);
            readable.off('end', onEnd);
            readable.off('error', onError);
        },
    };
}

export async function validateTransport(browser, origin) {
    const address = new URL(origin);
    assert.equal(address.origin, origin);
    assert.equal(address.protocol, 'http:');
    assert.equal(address.hostname, '127.0.0.1');
    const context = await browser.newContext({serviceWorkers: 'block'});
    let redirectsBlocked = 0;
    let cookieHeadersObserved = 0;
    const tasks = [];
    try {
        await context.route('**/*', route => {
            const task = (async () => {
                const request = route.request(), url = new URL(request.url());
                assert.equal(url.origin, origin);
                const redirect = /^\/redirect\/(301|302|303|307|308)$/.test(url.pathname);
                assert.equal(request.method(), url.pathname === '/post' ? 'POST' : 'GET');
                assert(url.pathname === '/cookie' || url.pathname === '/post' || redirect);
                assert.equal(url.search, '');
                try { await forwardContained(observeForwardedRoute(route, async (actualRequest, actualResponse) => {
                    if (new URL(actualRequest.url()).pathname !== '/cookie') return;
                    const headers = (await actualResponse.headersArray())
                        .filter(row => row.name.toLowerCase() === 'set-cookie');
                    assert.deepEqual(headers.map(row => row.value), [
                        'probe_a=one; Path=/; HttpOnly; SameSite=Strict',
                        'probe_b=two; Path=/; SameSite=Lax',
                    ]);
                    cookieHeadersObserved++;
                })); }
                catch (error) {
                    if (!redirect || error.message !== 'Redirect blocked before follow') throw error;
                    redirectsBlocked++;
                }
            })();
            // A handled outcome prevents errors in route callbacks being lost.
            tasks.push(task.then(() => null, error => error));
            return task.catch(async () => { try { await route.abort('blockedbyclient'); } catch {} });
        });
        const page = await context.newPage();
        page.setDefaultNavigationTimeout(10000);
        const response = await page.goto(origin + '/cookie');
        assert.equal(response.status(), 200);
        await readCompleteBody(response);
        const cookies = await context.cookies(origin);
        assert.deepEqual(cookies.map(row => [row.name, row.value, row.httpOnly, row.sameSite,
            row.secure, row.path, row.domain, row.expires]).sort(), [
            ['probe_a', 'one', true, 'Strict', false, '/', '127.0.0.1', -1],
            ['probe_b', 'two', false, 'Lax', false, '/', '127.0.0.1', -1],
        ]);
        await readCompleteBody(await page.reload()); // real Cookie header round-trip
        const postBody = await page.evaluate(async () => {
            const response = await fetch('/post', {method: 'POST', body: 'private-probe-body=unchanged'});
            if (response.status !== 200) throw new Error('Probe POST failed');
            return await response.text();
        });
        assert.equal(postBody, 'private-probe-body=unchanged');
        for (const status of [301, 302, 303, 307, 308]) {
            let navigationFailed = false;
            try { await page.goto(origin + '/redirect/' + status); } catch { navigationFailed = true; }
            assert.equal(navigationFailed, true);
        }
        assert((await Promise.all(tasks)).every(error => error === null));
        assert.equal(redirectsBlocked, 5);
        assert.equal(cookieHeadersObserved, 2);
        return {redirect_blocked: true, cookies_preserved: true, post_body_preserved: true};
    } finally { await context.close(); }
}

function options(argv) {
    const result = {};
    for (let i = 0; i < argv.length; i += 2) {
        assert(['--input', '--playwright-module', '--probe-origin'].includes(argv[i]));
        assert(argv[i + 1] && !Object.hasOwn(result, argv[i]));
        result[argv[i]] = argv[i + 1];
    }
    assert.equal(Object.keys(result).length, 3);
    return result;
}

async function run(argv, status, send) {
    const args = options(argv);
    assert(isAbsolute(args['--input']) && isAbsolute(args['--playwright-module']));
    const mode = (await stat(args['--input'])).mode & 0o777;
    assert.equal(mode, 0o600, 'Private credential file required');
    const input = JSON.parse(await readFile(args['--input'], 'utf8'));
    const require = createRequire(pathToFileURL(resolve(args['--playwright-module'], 'package.json')));
    const playwright = require(args['--playwright-module']);
    assert(playwright.chromium && playwright.expect, 'Existing @playwright/test module required');
    const packageData = require(resolve(args['--playwright-module'], 'package.json'));
    assert.match(packageData.version, /^\d+\.\d+\.\d+$/);
    const binary = playwright.chromium.executablePath();
    const browserBinary = {path: binary, sha256: digest(await readFile(binary))};
    const bridge = createBridge({readable: process.stdin, send});
    let browser, browserClosed = false, receipt, transport;
    const cookies = [];
    const shutdown = async () => {
        bridge.close();
        if (browser) await browser.close();
        process.exitCode = 1;
    };
    const onTerm = () => { void shutdown().finally(() => process.exit(1)); };
    process.once('SIGTERM', onTerm);
    process.once('SIGINT', onTerm);
    try {
        status.stage = 'browser-ready';
        await bridge.ready(browserBinary); // parent hashes executable before launch
        status.stage = 'browser-launch';
        browser = await playwright.chromium.launch({executablePath: binary, headless: true, timeout: 30000,
            args: ['--disable-background-networking', '--disable-component-update', '--disable-sync',
                '--no-proxy-server', '--disable-quic', '--dns-prefetch-disable',
                '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1',
                '--force-webrtc-ip-handling-policy=disable_non_proxied_udp']});
        status.stage = 'transport-probe';
        transport = await validateTransport(browser, args['--probe-origin']);
        let actorIndex = 0;
        const observedBrowser = {
            async newContext(settings) {
                delete status.failure_step;
                const actor = ACTORS[actorIndex++];
                assert(actor, 'Unexpected extra context');
                const context = await browser.newContext(settings);
                const close = context.close.bind(context);
                let closing;
                context.close = (...options) => {
                    closing ||= (async () => {
                        await close(...options);
                        await bridge.contextClosed(actor);
                    })();
                    return closing;
                };
                return context;
            },
        };
        status.stage = 'browser-exercise';
        status.workflow_step = 'context-initialization';
        status.response_checks = [];
        receipt = await exercise({browser: observedBrowser, expect: playwright.expect, input,
            onWorkflowStep: step => {status.workflow_step = step;},
            onResponseCheck: (actor, check) => {
                status.failure_step ||= 'response-observation';
                if (!status.response_checks.some(row => row.actor === actor && row.check === check))
                    status.response_checks.push({actor, check});
            },
            onForwardedResponse: async (actor, request, response) => {
                let forwardedStep = 'response-observation';
                try {
                    const observation = await forwardedLoginCookie(actor, input, request, response);
                    if (observation) cookies.push(observation);
                    await observeLoginContract(actor, input, request, response,
                        step => {forwardedStep = step;});
                } catch (error) {
                    status.failure_step ||= forwardedStep;
                    throw error;
                }
            },
            checkpoint: row => bridge.checkpoint(row)});
        assert.equal(receipt.success, true, 'Browser exercise failed');
        bridge.complete();
    } catch (error) {
        status.failure = childFailure(status.stage, error,
            {workflow_step: status.failure_step || receipt?.workflow_failure?.step || status.workflow_step,
            controls_mismatches: status.failure_step ? [] : receipt?.workflow_failure?.controls_mismatches,
            failures: receipt?.failures, response_checks: status.response_checks,
            network_diagnostics: receipt?.network_diagnostics});
        throw error;
    } finally {
        try {
            if (browser) { await browser.close(); browserClosed = true; }
        } catch (error) {
            status.failure ||= childFailure('browser-close', error);
            throw error;
        } finally {
            bridge.close();
            process.off('SIGTERM', onTerm);
            process.off('SIGINT', onTerm);
        }
    }
    status.stage = 'binary-after';
    assert.equal(digest(await readFile(binary)), browserBinary.sha256, 'Browser executable changed');
    send({type: 'result', receipt, transport, cookies, browser_closed: browserClosed,
        browser_binary: browserBinary});
    process.stdin.pause();
    return 0;
}

export async function main(argv = process.argv.slice(2),
    send = value => process.stdout.write(JSON.stringify(value) + '\n')) {
    const status = {stage: 'startup'};
    try { return await run(argv, status, send); }
    catch (error) {
        send(status.failure || childFailure(status.stage, error));
        process.stdin.pause();
        return 1;
    }
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
    main().then(code => { process.exitCode = code; }, () => {
        // No raw exception/page/URL/token/password enters the public protocol.
        process.stderr.write('message-browser-runner-failed\n');
        process.exitCode = 1;
        process.stdin.pause();
    });
}
