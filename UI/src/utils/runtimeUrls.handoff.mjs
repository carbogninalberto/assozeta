import test from 'node:test';
import assert from 'node:assert/strict';
import {setImmediate as settle} from 'node:timers/promises';
import {loadInstanceConfig, getApiHost, instanceConfig} from '../store/instanceStore.js';
import * as version from './apiVersion.js';
import {getWebSocketUrl} from './websocketUrl.js';

const retired = ['assozeta_api_override', 'assozeta_api_v2_probe', 'assozeta_ws_v2_fallback', 'uiApiVersion'];
function setup(t, {query = '', hash = '', seed = false, response = 200, custom = null} = {}) {
    const originals = new Map();
    for (const key of ['location', 'localStorage', 'sessionStorage', '__bakney', 'window', 'fetch']) {
        originals.set(key, Object.getOwnPropertyDescriptor(globalThis, key));
    }
    t.after(async () => {
        await settle(); // The cache-hit path refreshes configuration in the background.
        instanceConfig.set(null);
        version.resetApiVersionState?.();
        for (const [key, descriptor] of originals) {
            if (descriptor) Object.defineProperty(globalThis, key, descriptor);
            else delete globalThis[key];
        }
    });
    globalThis.location = {search: query, hash, origin: 'https://fixture.example.test'};
    globalThis.window = {location};
    for (const key of ['sessionStorage', 'localStorage']) {
        const data = new Map();
        globalThis[key] = {getItem: k => data.get(k) ?? null, setItem: (k, v) => data.set(k, String(v)),
            removeItem: k => data.delete(k)};
        if (seed) retired.forEach((k, i) => data.set(k, ['v2', 'ok', '["/ws/health/"]', 'v2'][i]));
        data.set('keep-me', 'fixture');
    }
    globalThis.__bakney = {OEM_CONFIG: {selfHosted: true}, env: {HOST: custom || '/api', DOMAIN: custom || ''}};
    const calls = [];
    globalThis.fetch = async url => {
        calls.push(String(url));
        return Response.json(url.endsWith('/config') ? {features: {uiApiVersion: 'v2', selfHosted: true}}
            : {configured: true}, {status: url.endsWith('/instance/status') ? response : 200});
    };
    return calls;
}

test('bootstrap_preferences', async t => {
    const calls = setup(t, {query: '?api=v2', seed: true});
    localStorage.setItem('assozeta_instance_config', JSON.stringify({features: {selfHosted: true, uiApiVersion: 'v2'}}));
    localStorage.setItem('assozeta_config_timestamp', Date.now());
    assert.equal(await loadInstanceConfig(), true);
    await settle();
    assert.equal(calls[0], '/api/instance/status', 'First bootstrap request must use the stable API');
    assert.equal(getApiHost(), '/api');
    const cached = JSON.parse(localStorage.getItem('assozeta_instance_config'));
    assert.equal(cached?.features?.uiApiVersion, undefined, 'Retired version must be removed from the configuration cache');
    assert.ok(calls.every(url => !url.startsWith('/api/v2/')));
    for (const storage of [localStorage, sessionStorage]) {
        for (const key of retired) assert.equal(storage.getItem(key), null, key);
        assert.equal(storage.getItem('keep-me'), 'fixture');
    }
});

test('bootstrap_no_fallback', async t => {
    const calls = setup(t, {hash: '#/profile?api=v2', response: 503});
    await loadInstanceConfig();
    assert.deepEqual(calls, ['/api/instance/status'], 'An unavailable API must not trigger a probe or alias retry');
});

test('socket_and_request_urls', async t => {
    const calls = setup(t, {seed: true});
    await loadInstanceConfig();
    assert.equal(new URL(getWebSocketUrl('/ws/notifications/', 'fixture')).pathname, '/ws/notifications/',
        'Socket URL must be independent of the old Go preference');
    assert.equal(version.rewriteApiUrl?.('/api/instance/status') ?? '/api/instance/status', '/api/instance/status');
    assert.ok(!calls.includes('/api/v2/readyz'));
    __bakney.env.HOST = 'https://custom.example.test/api';
    __bakney.env.DOMAIN = 'https://custom.example.test';
    assert.equal(getApiHost(), __bakney.env.HOST);
    assert.equal(getWebSocketUrl('/ws/health/'), 'wss://custom.example.test/ws/health/');
});
