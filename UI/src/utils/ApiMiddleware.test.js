import test from 'node:test';
import assert from 'node:assert/strict';
import {uploadInstanceLogo} from '../store/instanceStore.js';

test('multipart uploads survive the application fetch interceptor and API middleware', async t => {
    const originalGlobals = Object.fromEntries(
        ['window', 'fetch', 'localStorage', 'XMLHttpRequest', '__bakney'].map(key => [key, globalThis[key]])
    );
    const storage = new Map([
        ['sessionToken', JSON.stringify('test-access-token')],
        ['refreshToken', JSON.stringify('test-refresh-token')],
        ['expires', String(Date.now() + 3600000)],
    ]);
    const requests = [];
    globalThis.window = globalThis;
    globalThis.localStorage = {
        getItem: key => storage.get(key) ?? null,
        setItem: (key, value) => storage.set(key, value),
        removeItem: key => storage.delete(key),
    };
    globalThis.XMLHttpRequest = class {};
    globalThis.__bakney = {env: {
        HOST: 'http://localhost:5001/api',
        API: {OAUTH2: {LOGIN: '/api/login', SIGNUP: '/api/signup', RESET: '/api/reset'}},
    }};
    globalThis.fetch = async (...args) => {
        requests.push(new Request(...args));
        return Response.json({success: true, logo_url: '/api/instance/logo.png?v=3'});
    };

    try {
        const {apiFetch} = await import('./ApiMiddleware.js');
        // Match main.js: fetch-intercept wraps the installed API middleware and
        // passes it a Request with an already serialized multipart body.
        const {default: fetchIntercept} = await import('fetch-intercept');
        const unregister = fetchIntercept.register({request: (url, config) => [url, config]});
        t.after(unregister);

        await t.test('signed-in logo upload preserves boundary, file bytes, and authorization', async () => {
            await uploadInstanceLogo(new File(['test-image-bytes'], 'logo (5).png', {type: 'image/png'}));
            const request = requests.pop();
            assert.match(request.headers.get('Content-Type'), /^multipart\/form-data; boundary=/);
            assert.equal(request.headers.get('Authorization'), 'Bearer test-access-token');
            const file = (await request.formData()).get('file');
            assert.equal(file.name, 'logo (5).png');
            assert.equal(await file.text(), 'test-image-bytes');
        });

        await t.test('apiFetch also supports FormData even with a default JSON header', async () => {
            const body = new FormData();
            body.append('file', new File(['image'], 'logo.png', {type: 'image/png'}));
            await apiFetch('http://localhost:5001/api/instance/logo', {
                method: 'POST', body, headers: {'Content-Type': 'application/json'},
            });
            const request = requests.pop();
            assert.match(request.headers.get('Content-Type'), /^multipart\/form-data; boundary=/);
            assert.equal(await (await request.formData()).get('file').text(), 'image');
        });

        await t.test('JSON API requests keep their content type and body', async () => {
            await apiFetch('http://localhost:5001/api/profile/settings', {
                method: 'POST', body: JSON.stringify({online_payments: true}),
            });
            const request = requests.pop();
            assert.equal(request.headers.get('Content-Type'), 'application/json');
            assert.deepEqual(await request.json(), {online_payments: true});
        });

        await t.test('first-run logo upload preserves its setup token without a login', async () => {
            storage.clear();
            await uploadInstanceLogo(new File(['setup-image'], 'logo.png', {type: 'image/png'}), 'setup-token');
            const request = requests.pop();
            assert.match(request.headers.get('Content-Type'), /^multipart\/form-data; boundary=/);
            assert.equal(request.headers.get('X-Setup-Token'), 'setup-token');
            assert.equal(request.headers.get('Authorization'), null);
            assert.equal(await (await request.formData()).get('file').text(), 'setup-image');
        });
    } finally {
        Object.assign(globalThis, originalGlobals);
    }
});
