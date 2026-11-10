import test from 'node:test';
import assert from 'node:assert/strict';
import {
    API_OVERRIDE_KEY, API_PROBE_KEY, activeApiHost, activeApiVersion, clearApiOverride, fallbackToDefaultApi, parseApiParam,
    probeApiV2, readApiOverride, resetApiVersionState, rewriteApiUrl, rewriteFetchInput, selectApiVersion, setActiveApiVersion,
} from './apiVersion.js';

function memoryStorage() {
    const values = new Map();
    return {values, getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, String(value)), removeItem: key => values.delete(key)};
}

function withGlobals(t, globals) {
    const original = Object.fromEntries(Object.keys(globals).map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
    for (const [key, value] of Object.entries(globals)) {
        Object.defineProperty(globalThis, key, {value, configurable: true, writable: true});
    }
    t.after(() => {
        for (const [key, descriptor] of Object.entries(original)) {
            if (descriptor) Object.defineProperty(globalThis, key, descriptor);
            else delete globalThis[key];
        }
        resetApiVersionState();
    });
}

test('the api parameter is read from the query string or the hash route query', () => {
    assert.equal(parseApiParam({search: '?api=v2', hash: ''}), 'v2');
    assert.equal(parseApiParam({search: '', hash: '#/profile?page=self-instance&api=V1'}), 'v1');
    assert.equal(parseApiParam({search: '?api=auto', hash: ''}), 'auto');
    assert.equal(parseApiParam({search: '?api=v3', hash: '#/x?api=nope'}), null);
    assert.equal(parseApiParam({search: '', hash: '#/profile'}), null);
    assert.equal(parseApiParam(undefined), null);
});

test('the per-browser override is persisted, cleared by auto and survives unavailable storage', t => {
    const localStorage = memoryStorage();
    withGlobals(t, {localStorage});
    assert.equal(readApiOverride({search: '', hash: ''}), null);
    assert.equal(readApiOverride({search: '?api=v2', hash: ''}), 'v2');
    assert.equal(localStorage.values.get(API_OVERRIDE_KEY), 'v2');
    // Later page loads without the parameter keep the stored choice.
    assert.equal(readApiOverride({search: '', hash: '#/dashboard'}), 'v2');
    assert.equal(readApiOverride({search: '', hash: '#/x?api=v1'}), 'v1');
    assert.equal(readApiOverride({search: '?api=auto', hash: ''}), null);
    assert.equal(localStorage.values.has(API_OVERRIDE_KEY), false);
    localStorage.values.set(API_OVERRIDE_KEY, 'garbage');
    assert.equal(readApiOverride({search: '', hash: ''}), null);
    readApiOverride({search: '?api=v2', hash: ''});
    clearApiOverride();
    assert.equal(readApiOverride({search: '', hash: ''}), null);

    const throwing = {getItem() { throw new Error('denied'); }, setItem() { throw new Error('denied'); }, removeItem() { throw new Error('denied'); }};
    Object.defineProperty(globalThis, 'localStorage', {value: throwing, configurable: true, writable: true});
    assert.equal(readApiOverride({search: '?api=v2', hash: ''}), null);
    assert.doesNotThrow(() => clearApiOverride());
    Object.defineProperty(globalThis, 'localStorage', {get() { throw new Error('SecurityError'); }, configurable: true});
    assert.equal(readApiOverride({search: '?api=v2', hash: ''}), null);
});

test('the v2 probe succeeds once, is cached for the session and can be forced', async t => {
    const sessionStorage = memoryStorage();
    withGlobals(t, {sessionStorage});
    let calls = 0;
    const fetcher = async (url, options) => {
        calls++;
        assert.equal(url, '/api/v2/readyz');
        assert.equal(options.cache, 'no-store');
        assert.ok(options.signal);
        return {ok: true};
    };
    assert.equal(await probeApiV2({fetcher}), true);
    assert.equal(await probeApiV2({fetcher}), true);
    assert.equal(calls, 1);
    assert.equal(sessionStorage.values.get(API_PROBE_KEY), 'ok');
    // A new module state (page reload) reads the session result.
    resetApiVersionState();
    assert.equal(await probeApiV2({fetcher}), true);
    assert.equal(calls, 1);
    assert.equal(await probeApiV2({fetcher: async () => ({ok: false, status: 503}), force: true}), false);
    assert.equal(sessionStorage.values.get(API_PROBE_KEY), 'fail');
    assert.equal(await probeApiV2({fetcher}), false);
});

test('concurrent probes share one request', async t => {
    withGlobals(t, {sessionStorage: memoryStorage()});
    let calls = 0;
    const fetcher = () => { calls++; return new Promise(resolve => setTimeout(() => resolve({ok: true}), 5)); };
    assert.deepEqual(await Promise.all([probeApiV2({fetcher}), probeApiV2({fetcher})]), [true, true]);
    assert.equal(calls, 1);
});

test('probe failures, network errors and timeouts keep v1', async t => {
    withGlobals(t, {sessionStorage: memoryStorage()});
    assert.equal(await selectApiVersion('v2', {fetcher: async () => ({ok: false, status: 502}), force: true}), 'v1');
    assert.equal(await selectApiVersion('v2', {fetcher: async () => { throw new TypeError('Failed to fetch'); }, force: true}), 'v1');
    const hanging = (url, {signal}) => new Promise((resolve, reject) => signal.addEventListener('abort', () => reject(new Error('aborted'))));
    const started = Date.now();
    assert.equal(await selectApiVersion('v2', {fetcher: hanging, timeout: 20, force: true}), 'v1');
    assert.ok(Date.now() - started < 2000);
    assert.equal(activeApiHost(), '/api');
    assert.equal(await selectApiVersion('v2', {fetcher: async () => ({ok: true}), force: true}), 'v2');
    assert.equal(activeApiHost(), '/api/v2');
    assert.equal(await selectApiVersion('v1'), 'v1');
});

test('storage that throws does not break the probe', async t => {
    const throwing = {getItem() { throw new Error('denied'); }, setItem() { throw new Error('denied'); }};
    withGlobals(t, {sessionStorage: throwing});
    assert.equal(await probeApiV2({fetcher: async () => ({ok: true})}), true);
});

test('the fallback returns the session to /api and marks v2 unavailable', async t => {
    const sessionStorage = memoryStorage();
    withGlobals(t, {sessionStorage, console: {...console, warn() {}}});
    setActiveApiVersion('v2');
    fallbackToDefaultApi('HTTP 502');
    assert.equal(activeApiVersion(), 'v1');
    assert.equal(sessionStorage.values.get(API_PROBE_KEY), 'fail');
    assert.equal(await selectApiVersion('v2', {fetcher: async () => ({ok: true})}), 'v1');
});

test('URLs are rewritten to /api/v2 only while v2 is active', t => {
    withGlobals(t, {location: {origin: 'https://club.example.test', search: '', hash: ''}});
    assert.equal(rewriteApiUrl('/api/profile/info'), '/api/profile/info');
    setActiveApiVersion('v2');
    const cases = [
        ['/api/profile/info', '/api/v2/profile/info'],
        ['/api', '/api/v2'],
        ['/api?x=1', '/api/v2?x=1'],
        ['/api/document/retrieve/abc?token=t', '/api/v2/document/retrieve/abc?token=t'],
        ['https://club.example.test/api/instance/status', 'https://club.example.test/api/v2/instance/status'],
        ['/api/v2/instance/config', '/api/v2/instance/config'],
        ['/api/v1/instance/config', '/api/v1/instance/config'],
        ['/apiary', '/apiary'],
        ['/ws/notifications/', '/ws/notifications/'],
        ['/healthz', '/healthz'],
        ['https://api.example.test/api/profile', 'https://api.example.test/api/profile'],
        ['//other.example.test/api/x', '//other.example.test/api/x'],
        ['data:text/csv;base64,YQ==', 'data:text/csv;base64,YQ=='],
        ['relative/api/x', 'relative/api/x'],
    ];
    for (const [input, expected] of cases) assert.equal(rewriteApiUrl(input), expected, input);
    const url = rewriteApiUrl(new URL('https://club.example.test/api/a?b=1'));
    assert.ok(url instanceof URL);
    assert.equal(url.href, 'https://club.example.test/api/v2/a?b=1');
    const foreign = new URL('https://elsewhere.test/api/a');
    assert.equal(rewriteApiUrl(foreign), foreign);
    const request = rewriteApiUrl(new Request('https://club.example.test/api/upload', {method: 'POST', body: 'x', headers: {'X-Test': '1'}}));
    assert.equal(request.url, 'https://club.example.test/api/v2/upload');
    assert.equal(request.method, 'POST');
    assert.equal(request.headers.get('X-Test'), '1');
    assert.equal(rewriteApiUrl(null), null);
    assert.equal(rewriteApiUrl(42), 42);
});

test('a rewritten fetch Request carries its buffered body, not a stream', async t => {
    withGlobals(t, {location: {origin: 'https://club.example.test', search: '', hash: ''}});
    const original = new Request('https://club.example.test/api/oauth2/login', {method: 'POST', body: '{"username":"u"}',
        headers: {'Content-Type': 'application/json'}});
    assert.equal(await rewriteFetchInput(original), original);
    setActiveApiVersion('v2');
    const rewritten = await rewriteFetchInput(original);
    assert.equal(rewritten.url, 'https://club.example.test/api/v2/oauth2/login');
    assert.equal(rewritten.method, 'POST');
    assert.equal(rewritten.headers.get('content-type'), 'application/json');
    assert.equal(await rewritten.text(), '{"username":"u"}');
    assert.equal(await original.text(), '{"username":"u"}', 'the original body stays readable');
    const read = await rewriteFetchInput(new Request('https://club.example.test/api/profile/info'));
    assert.equal(read.url, 'https://club.example.test/api/v2/profile/info');
    assert.equal(await rewriteFetchInput('/api/x'), '/api/v2/x');
});

test('the override survives a localStorage.clear() (login, logout) within the tab', t => {
    const localStorage = memoryStorage(), sessionStorage = memoryStorage();
    withGlobals(t, {localStorage, sessionStorage});
    assert.equal(readApiOverride({search: '?api=v1', hash: ''}), 'v1');
    localStorage.values.clear();
    assert.equal(readApiOverride({search: '', hash: '#/'}), 'v1');
    assert.equal(localStorage.values.get(API_OVERRIDE_KEY), 'v1', 'restored to localStorage');
    clearApiOverride();
    assert.equal(sessionStorage.values.has(API_OVERRIDE_KEY), false);
    assert.equal(readApiOverride({search: '', hash: ''}), null);
});
