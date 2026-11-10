import test from 'node:test';
import assert from 'node:assert/strict';
import {resetApiVersionState, setActiveApiVersion} from './apiVersion.js';

test('the API middleware sends same-origin /api requests to the active version', async t => {
    const keys = ['window', 'fetch', 'localStorage', 'XMLHttpRequest', '__bakney', 'location'];
    const original = Object.fromEntries(keys.map(key => [key, globalThis[key]]));
    t.after(() => {
        for (const key of keys) globalThis[key] = original[key];
        resetApiVersionState();
    });
    const storage = new Map([
        ['sessionToken', JSON.stringify('test-access-token')],
        ['refreshToken', JSON.stringify('test-refresh-token')],
        ['expires', String(Date.now() + 3600000)],
    ]);
    const requests = [];
    const opened = [];
    globalThis.window = globalThis;
    globalThis.location = {origin: 'https://club.example.test', search: '', hash: ''};
    globalThis.localStorage = {
        getItem: key => storage.get(key) ?? null,
        setItem: (key, value) => storage.set(key, String(value)),
        removeItem: key => storage.delete(key),
        clear: () => storage.clear(),
    };
    globalThis.XMLHttpRequest = class {
        open(...args) { opened.push(args); }
        send() {}
        setRequestHeader() {}
        addEventListener() {}
    };
    globalThis.__bakney = {env: {
        HOST: '/api',
        API: {OAUTH2: {LOGIN: '/api/login', SIGNUP: '/api/signup', RESET: '/api/reset', REFRESH_TOKEN: '/api/oauth2/refresh-token'}},
    }};
    globalThis.fetch = async (input, init) => {
        const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
        requests.push({url, authorization: new Headers(init?.headers || input?.headers).get('Authorization')});
        if (url.endsWith('/oauth2/refresh-token')) {
            return Response.json({access_token: 'new-access', refresh_token: 'new-refresh', expires_in: 3600});
        }
        return Response.json({ok: true});
    };
    const {apiFetch} = await import('./ApiMiddleware.js');

    await t.test('v1 leaves URLs unchanged', async () => {
        await window.fetch('/api/profile/info');
        assert.equal(requests.pop().url, '/api/profile/info');
    });

    setActiveApiVersion('v2');

    await t.test('fetch with strings, URLs and Requests is rewritten; auth is kept', async () => {
        await window.fetch('/api/profile/info');
        assert.deepEqual(requests.pop(), {url: '/api/v2/profile/info', authorization: 'Bearer test-access-token'});
        await window.fetch(new URL('https://club.example.test/api/a'));
        assert.equal(requests.pop().url, 'https://club.example.test/api/v2/a');
        await window.fetch(new Request('https://club.example.test/api/b', {method: 'POST', body: 'x'}));
        assert.equal(requests.pop().url, 'https://club.example.test/api/v2/b');
        await window.fetch('/api/v1/instance/status');
        assert.equal(requests.pop().url, '/api/v1/instance/status');
        await window.fetch('https://cdn.example.test/api/x');
        assert.equal(requests.pop().url, 'https://cdn.example.test/api/x');
        await apiFetch('/api/course/list');
        assert.equal(requests.pop().url, '/api/v2/course/list');
    });

    await t.test('token refresh goes to the active version', async () => {
        storage.set('expires', String(Date.now() - 1000));
        await window.fetch('/api/profile/info');
        const [refresh, call] = requests.splice(-2);
        assert.equal(refresh.url, '/api/v2/oauth2/refresh-token');
        assert.deepEqual(call, {url: '/api/v2/profile/info', authorization: 'Bearer new-access'});
    });

    await t.test('XHR open is rewritten and keeps its argument count', () => {
        const xhr = new window.XMLHttpRequest();
        xhr.open('POST', '/api/instance/admin/backups/upload');
        assert.deepEqual(opened.pop(), ['POST', '/api/v2/instance/admin/backups/upload']);
        xhr.open('GET', '/api/v2/already', false);
        assert.deepEqual(opened.pop(), ['GET', '/api/v2/already', false]);
        xhr.open('GET', '/static/file.js');
        assert.deepEqual(opened.pop(), ['GET', '/static/file.js']);
    });
});
