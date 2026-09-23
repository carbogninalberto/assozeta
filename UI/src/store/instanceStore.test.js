import test from 'node:test';
import assert from 'node:assert/strict';
import {get} from 'svelte/store';
import {aiEnabled, instanceConfig, oemConfig, uploadInstanceLogo, loadInstanceConfig} from './instanceStore.js';

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

test('legacy installations load AI visibility from the server status', async () => {
    const originalFetch = globalThis.fetch;
    const originalConfig = globalThis.__bakney;
    globalThis.__bakney = {OEM_CONFIG: {name: 'Legacy'}, env: {HOST: '/api'}};
    try {
        for (const enabled of [true, false]) {
            instanceConfig.set(null);
            globalThis.fetch = async url => {
                assert.equal(url, '/api/instance/status');
                return {ok: true, json: async () => ({ai_enabled: enabled})};
            };
            assert.equal(await loadInstanceConfig(), true);
            assert.equal(get(aiEnabled), enabled);
        }
    } finally {
        globalThis.fetch = originalFetch;
        globalThis.__bakney = originalConfig;
        instanceConfig.set(null);
    }
});
