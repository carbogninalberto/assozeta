import test from 'node:test';
import assert from 'node:assert/strict';

test('API middleware preserves supplied endpoints, authentication and XHR options', async t => {
    const keys = ['window', 'fetch', 'localStorage', 'XMLHttpRequest', '__bakney', 'location'];
    const original = Object.fromEntries(keys.map(key => [key, globalThis[key]]));
    t.after(() => {
        for (const key of keys) globalThis[key] = original[key];
    });
    const storage = new Map([
        ['sessionToken', JSON.stringify('test-access-token')],
        ['refreshToken', JSON.stringify('test-refresh-token')],
        ['expires', String(Date.now() + 3600000)],
    ]);
    const requests = [];
    const opened = [];
    let lastInput;
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
        lastInput = input;
        requests.push({url, authorization: new Headers(init?.headers || input?.headers).get('Authorization')});
        if (url.endsWith('/oauth2/refresh-token')) {
            return Response.json({access_token: 'new-access', refresh_token: 'new-refresh', expires_in: 3600});
        }
        return Response.json({ok: true});
    };
    const {apiFetch} = await import('./ApiMiddleware.js');

    await t.test('same-origin paths stay unversioned', async () => {
        await window.fetch('/api/profile/info');
        assert.equal(requests.pop().url, '/api/profile/info');
    });

    const host = 'https://custom.example.test/api';
    __bakney.env.HOST = host;
    __bakney.env.API.OAUTH2.REFRESH_TOKEN = host + '/oauth2/refresh-token';
    globalThis.location.origin = 'capacitor://localhost';

    await t.test('fetch preserves strings, URLs, Request objects and credentials', async () => {
        await window.fetch(host + '/profile/info');
        assert.deepEqual(requests.pop(), {url: host + '/profile/info', authorization: 'Bearer test-access-token'});
        await window.fetch(new URL('https://club.example.test/api/a'));
        assert.equal(requests.pop().url, 'https://club.example.test/api/a');
        const request = new Request('https://club.example.test/api/b', {method: 'POST', body: 'x', headers: {'X-Test': '1'}});
        await window.fetch(request);
        assert.equal(requests.pop().url, 'https://club.example.test/api/b');
        assert.equal(lastInput, request);
        assert.equal(lastInput.method, 'POST');
        assert.equal(lastInput.headers.get('X-Test'), '1');
        assert.equal(await lastInput.clone().text(), 'x');
        for (const path of ['/api', '/api?x=1', '/apiary', '/healthz', '/ws/notifications/', '/api/v2/instance/config',
            '/api/document/retrieve/example?token=fixture', '//other.example.test/api/x', 'relative/api/x', 'data:text/plain,fixture']) {
            await window.fetch(path);
            assert.equal(requests.pop().url, path);
        }
        await window.fetch('/api/v1/instance/status');
        assert.equal(requests.pop().url, '/api/v1/instance/status');
        await window.fetch('https://cdn.example.test/api/x');
        assert.equal(requests.pop().url, 'https://cdn.example.test/api/x');
        await apiFetch(host + '/course/list');
        assert.equal(requests.pop().url, host + '/course/list');
    });

    await t.test('token refresh reaches the configured host', async () => {
        storage.set('expires', String(Date.now() - 1000));
        await window.fetch(host + '/profile/info');
        const [refresh, call] = requests.splice(-2);
        assert.equal(refresh.url, host + '/oauth2/refresh-token');
        assert.deepEqual(call, {url: host + '/profile/info', authorization: 'Bearer new-access'});
    });

    await t.test('XHR preserves the URL and argument count', () => {
        const xhr = new window.XMLHttpRequest();
        xhr.open('POST', '/api/instance/admin/backups/upload');
        assert.deepEqual(opened.pop(), ['POST', '/api/instance/admin/backups/upload']);
        xhr.open('GET', '/api/v2/already', false);
        assert.deepEqual(opened.pop(), ['GET', '/api/v2/already', false]);
        xhr.open('GET', '/static/file.js');
        assert.deepEqual(opened.pop(), ['GET', '/static/file.js']);
    });
});

test('custom build keeps document, PDF and report URLs on the configured API', async t => {
    const {buildConfig} = await import('../../endpoints.js');
    const {downloadPdf} = await import('./download.js');
    const host = 'https://custom.example.test/api';
    const {env} = JSON.parse(buildConfig({apiHost: host, domain: 'https://custom.example.test'}).__bakney);
    for (const url of [env.API.DOCUMENT.RETRIEVE, env.API.DOCUMENT.DOWNLOAD, env.API.DOCUMENT.SUBSCRIPTION_PREVIEW,
        env.API.SAVED_REPORTS.RUN]) assert.ok(url.startsWith(host + '/'), url);
    const original = new Map(['window', 'localStorage'].map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
    t.after(() => {
        for (const [key, descriptor] of original) {
            if (descriptor) Object.defineProperty(globalThis, key, descriptor);
            else delete globalThis[key];
        }
    });
    const calls = [];
    globalThis.window = {location: {origin: 'capacitor://localhost'}, open: (...args) => calls.push(args)};
    globalThis.localStorage = {getItem: () => JSON.stringify('fixture')};
    const url = env.API.DOCUMENT.RETRIEVE + '/example';
    downloadPdf(url);
    assert.deepEqual(calls, [[url + '?download=true&token=fixture', '_blank']]);
});
