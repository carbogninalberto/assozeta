import test from 'node:test';
import assert from 'node:assert/strict';
import {get} from 'svelte/store';
import {aiEnabled, manualEnabled, assistantEnabled, instanceConfig, instanceStatus, oemConfig, uploadInstanceLogo, loadInstanceConfig, getApiHost, desiredApiVersion} from './instanceStore.js';
import {resetApiVersionState, setActiveApiVersion} from '../utils/apiVersion.js';

test('upload updates reactive branding and cached config only after success', async () => {
    const originalFetch = globalThis.fetch;
    const originalStorage = globalThis.localStorage;
    const originalConfig = globalThis.__bakney;
    const cache = new Map();
    globalThis.localStorage = {setItem: (key, value) => cache.set(key, value)};
    globalThis.__bakney = {env: {HOST: '/api'}};
    instanceConfig.set({oem: {name: 'Test ASD', logo: '/old.png'}});
    try {
        globalThis.fetch = async (url, options) => {
            assert.equal(url, '/api/instance/logo');
            assert.ok(options.body instanceof FormData);
            assert.ok(options.body.get('file'));
            return {ok: true, json: async () => ({success: true, logo_url: '/api/instance/logo.png?v=2'})};
        };
        await uploadInstanceLogo(new Blob(['image'], {type: 'image/png'}));
        assert.equal(get(oemConfig).logo, '/api/instance/logo.png?v=2');
        assert.equal(globalThis.__bakney.OEM_CONFIG.logo, get(oemConfig).logo);
        assert.equal(JSON.parse(cache.get('assozeta_instance_config')).oem.logo, get(oemConfig).logo);
        globalThis.fetch = async () => ({ok: false, json: async () => ({error: 'Upload denied'})});
        await assert.rejects(uploadInstanceLogo(new Blob(['image'])), /Upload denied/);
        assert.equal(get(oemConfig).logo, '/api/instance/logo.png?v=2');
    } finally {
        globalThis.fetch = originalFetch;
        globalThis.localStorage = originalStorage;
        globalThis.__bakney = originalConfig;
        instanceConfig.set(null);
    }
});


test('AI visibility requires an explicit enabled flag and reacts to saved configuration', () => {
    for (const config of [null, {}, {features: {}}, {features: {aiEnabled: false}}]) {
        instanceConfig.set(config);
        assert.equal(get(aiEnabled), false);
    }
    instanceConfig.set({features: {aiEnabled: true}});
    assert.equal(get(aiEnabled), true);
    instanceConfig.set({features: {aiEnabled: false}});
    assert.equal(get(aiEnabled), false);
    instanceConfig.set(null);
});

test('legacy installations load optional feature visibility from the server status', async () => {
    const originalFetch = globalThis.fetch;
    const originalConfig = globalThis.__bakney;
    globalThis.__bakney = {OEM_CONFIG: {name: 'Legacy'}, env: {HOST: '/api'}};
    try {
        for (const enabled of [true, false]) {
            instanceConfig.set(null);
            globalThis.fetch = async url => {
                assert.equal(url, '/api/instance/status');
                return {ok: true, json: async () => ({ai_enabled: enabled, manual_enabled: !enabled})};
            };
            assert.equal(await loadInstanceConfig(), true);
            assert.equal(get(aiEnabled), enabled);
            assert.equal(get(manualEnabled), !enabled);
        }
    } finally {
        globalThis.fetch = originalFetch;
        globalThis.__bakney = originalConfig;
        instanceConfig.set(null);
    }
});


test('manual-only assistance requires a configured manual and AI remains independent', () => {
    for (const [ai, manual] of [[false, false], [true, false], [false, true], [true, true]]) {
        instanceConfig.set({features: {aiEnabled: ai, manualEnabled: manual}});
        assert.equal(get(manualEnabled), manual);
        assert.equal(get(assistantEnabled), ai || manual);
    }
    instanceConfig.set(null);
    assert.equal(get(manualEnabled), false);
    assert.equal(get(assistantEnabled), false);
});

function apiVersionGlobals(t, {search = '', cached = null, env = {HOST: '/api'}} = {}) {
    const keys = ['fetch', 'localStorage', 'sessionStorage', 'location', '__bakney'];
    const original = Object.fromEntries(keys.map(key => [key, globalThis[key]]));
    const local = new Map(cached ? [['assozeta_instance_config', JSON.stringify(cached)], ['assozeta_config_timestamp', String(Date.now())]] : []);
    const session = new Map();
    const storage = values => ({getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, String(value)), removeItem: key => values.delete(key)});
    globalThis.localStorage = storage(local);
    globalThis.sessionStorage = storage(session);
    globalThis.location = {origin: 'https://club.example.test', search, hash: ''};
    globalThis.__bakney = {env};
    t.after(() => {
        for (const key of keys) globalThis[key] = original[key];
        resetApiVersionState();
        instanceConfig.set(null);
    });
    return {local, session};
}

function server(routes) {
    const requests = [];
    globalThis.fetch = async url => {
        requests.push(url);
        const route = routes[url];
        if (!route) throw new Error('unexpected request ' + url);
        if (route instanceof Error) throw route;
        return {ok: route.status < 300, status: route.status, json: async () => route.body};
    };
    return requests;
}

const STATUS = {status: 200, body: {configured: true, ai_enabled: false, manual_enabled: false}};
const config = version => ({oem: {name: 'ASD'}, features: {selfHosted: true, uiApiVersion: version}});

test('getApiHost: default /api, a custom build host wins, otherwise the active version', t => {
    apiVersionGlobals(t);
    assert.equal(getApiHost(), '/api');
    setActiveApiVersion('v2');
    assert.equal(getApiHost(), '/api/v2');
    globalThis.__bakney = {env: {HOST: 'https://api.example.test'}};
    assert.equal(getApiHost(), 'https://api.example.test');
    assert.equal(desiredApiVersion(config('v2')), 'v1');
});

test('the per-browser override selects v2 after a successful probe', async t => {
    const {local} = apiVersionGlobals(t, {search: '?api=v2'});
    const requests = server({'/api/v2/readyz': {status: 200}, '/api/v2/instance/status': STATUS, '/api/v2/instance/config': {status: 200, body: config('v1')}});
    assert.equal(await loadInstanceConfig(), true);
    assert.deepEqual(requests, ['/api/v2/readyz', '/api/v2/instance/status', '/api/v2/instance/config']);
    assert.equal(local.get('assozeta_api_override'), 'v2');
    // The override wins over the instance setting.
    assert.equal(getApiHost(), '/api/v2');
});

test('the instance setting selects v2; baked endpoints are rewritten too', async t => {
    apiVersionGlobals(t, {cached: config('v2'), env: {HOST: '/api', API: {INSTANCE: {STATUS: '/api/instance/status', CONFIG: '/api/instance/config'}}}});
    const requests = server({'/api/v2/readyz': {status: 200}, '/api/v2/instance/status': STATUS, '/api/v2/instance/config': {status: 200, body: config('v2')}});
    assert.equal(await loadInstanceConfig(), true);
    await new Promise(resolve => setTimeout(resolve, 0)); // background refresh
    assert.deepEqual(requests, ['/api/v2/readyz', '/api/v2/instance/status', '/api/v2/instance/config']);
    assert.equal(getApiHost(), '/api/v2');
});

test('a fresh configuration enables v2 for later requests', async t => {
    apiVersionGlobals(t);
    const requests = server({'/api/instance/status': STATUS, '/api/instance/config': {status: 200, body: config('v2')}, '/api/v2/readyz': {status: 200}});
    assert.equal(await loadInstanceConfig(), true);
    assert.deepEqual(requests, ['/api/instance/status', '/api/instance/config', '/api/v2/readyz']);
    assert.equal(getApiHost(), '/api/v2');
});

test('a failed probe keeps /api without touching v2 bootstrap routes', async t => {
    apiVersionGlobals(t, {cached: config('v2')});
    const requests = server({'/api/v2/readyz': {status: 502}, '/api/instance/status': STATUS, '/api/instance/config': {status: 200, body: config('v2')}});
    assert.equal(await loadInstanceConfig(), true);
    await new Promise(resolve => setTimeout(resolve, 0));
    assert.deepEqual(requests, ['/api/v2/readyz', '/api/instance/status', '/api/instance/config']);
    assert.equal(getApiHost(), '/api');
    assert.equal(get(instanceStatus).error, null);
});

for (const [name, failure] of [['5xx', {status: 503, body: {}}], ['network', new TypeError('Failed to fetch')]]) {
    test(`a ${name} failure on v2 bootstrap falls back to /api silently`, async t => {
        const {session} = apiVersionGlobals(t, {search: '?api=v2'});
        const originalWarn = console.warn;
        console.warn = () => {};
        t.after(() => { console.warn = originalWarn; });
        const requests = server({'/api/v2/readyz': {status: 200}, '/api/v2/instance/status': STATUS, '/api/v2/instance/config': failure,
            '/api/instance/config': {status: 200, body: config('v2')}});
        assert.equal(await loadInstanceConfig(), true);
        assert.deepEqual(requests, ['/api/v2/readyz', '/api/v2/instance/status', '/api/v2/instance/config', '/api/instance/config']);
        assert.equal(getApiHost(), '/api');
        assert.equal(session.get('assozeta_api_v2_probe'), 'fail');
        assert.equal(get(instanceStatus).error, null);
        assert.equal(get(instanceConfig).features.uiApiVersion, 'v2');
    });
}
