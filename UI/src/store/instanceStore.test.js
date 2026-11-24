import test from 'node:test';
import assert from 'node:assert/strict';
import {get} from 'svelte/store';
import {aiEnabled, manualEnabled, assistantEnabled, instanceConfig, oemConfig, uploadInstanceLogo, loadInstanceConfig, getApiHost} from './instanceStore.js';

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

test('a cold selfhost client uses its explicit build host for status and configuration', async t => {
    const host = 'https://custom.example.test/api';
    apiVersionGlobals(t, {env: {HOST: host}});
    const requests = server({[host + '/instance/status']: STATUS, [host + '/instance/config']: {status: 200, body: config('v2')}});
    assert.equal(await loadInstanceConfig(), true);
    assert.equal(getApiHost(), host);
    assert.deepEqual(requests, [host + '/instance/status', host + '/instance/config']);
});
