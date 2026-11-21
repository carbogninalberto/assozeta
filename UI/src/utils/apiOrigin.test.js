import test from 'node:test';
import assert from 'node:assert/strict';
import {uploadArchive} from '../routes/profile/sections/backupUpload.js';

const api = 'https://service.example.test/api';
const sessionHeaders = ['authorization', 'user-id', 'x-group-id', 'x-impersonation-id'];

async function middleware(t) {
    const names = ['window', 'fetch', 'location', 'localStorage', 'XMLHttpRequest', '__bakney'];
    const originals = new Map(names.map(name => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
    t.after(() => {
        for (const [name, descriptor] of originals) {
            if (descriptor) Object.defineProperty(globalThis, name, descriptor);
            else delete globalThis[name];
        }
    });
    globalThis.location = new URL('https://app.example.test/ui/settings/');
    globalThis.window = globalThis;
    globalThis.__bakney = {env: {HOST: api, API: {OAUTH2: {LOGIN: api + '/login', SIGNUP: api + '/signup',
        RESET: api + '/reset', REFRESH_TOKEN: api + '/refresh'}}}};
    const values = new Map(Object.entries({sessionToken: '"fixture"', refreshToken: '"fixture"',
        expires: String(Date.now() + 3600000), selectedGroup: '"group"', switched_superuser: 'true',
        USER_ID: 'sample', impersonationContext: '{"session_id":"sample"}'}));
    globalThis.localStorage = {getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value)};

    // Capture the request handed to the transport. All responses are local fixtures.
    const requests = [];
    globalThis.fetch = async (input, init) => {
        const target = input instanceof Request ? input : new URL(input, location.href);
        const request = new Request(target, init);
        requests.push({headers: request.headers, method: request.method, redirect: request.redirect, request});
        return Response.json({});
    };
    globalThis.XMLHttpRequest = class extends EventTarget {
        upload = new EventTarget();
        headers = new Headers();
        open(method, address) {
            this.method = method;
            this.address = new URL(address, location.href);
        }
        setRequestHeader(key, value) { this.headers.set(key, value); }
        send(body) {
            requests.push({headers: this.headers, method: this.method, address: this.address});
            if (!body) return;
            queueMicrotask(() => {
                const progress = Object.assign(new Event('progress'), {lengthComputable: true, loaded: 1, total: 2});
                this.upload.dispatchEvent(progress);
                this.status = 201;
                this.responseText = '{}';
                this.dispatchEvent(new Event('load'));
            });
        }
    };
    // Each case installs the wrapper over its own transport.
    const {apiFetch} = await import('./ApiMiddleware.js?case=' + encodeURIComponent(t.name));
    async function send(kind, address, init = {}) {
        const before = requests.length;
        if (kind === 'xhr') {
            const xhr = new XMLHttpRequest();
            xhr.open('GET', address);
            for (const [key, value] of new Headers(init.headers)) xhr.setRequestHeader(key, value);
            await xhr.send();
        } else if (kind === 'apiFetch') {
            await apiFetch(address, init);
        } else {
            await window.fetch(address, init);
        }
        assert.equal(requests.length, before + 1, 'The transport receives one request');
        return requests.at(-1);
    }
    return {apiFetch, send, requests, storage: values};
}

function contextNames(headers) {
    return sessionHeaders.filter(name => headers.has(name));
}

test('configured_api_session_headers', async t => {
    const {send} = await middleware(t);
    const results = [];
    for (const kind of ['fetch', 'apiFetch', 'xhr']) {
        const seen = [];
        const destinations = [api + '/document', '//service.example.test/api/document',
            'https://media.example.test/api/document', 'https://service.example.test:9443/api/document', '/api/document'];
        for (const address of destinations) {
            const {headers} = await send(kind, address, {headers: {'X-Caller': 'fixture'}});
            assert.equal(headers.get('X-Caller'), 'fixture');
            seen.push(contextNames(headers));
        }
        const expected = kind === 'xhr' ? sessionHeaders.slice(1) : sessionHeaders;
        results.push({kind, seen, expected: [expected, expected, [], [], []]});
    }
    assert.deepEqual(results.map(row => [row.kind, row.seen]), results.map(row => [row.kind, row.expected]),
        'Send session headers only to the configured API origin');
});

test('api_address_resolution', async t => {
    const {send} = await middleware(t);
    const cases = [
        [api + '?page=1', true], [api + '/v2/items', true], ['https://SERVICE.example.test:443/api/items', true],
        [api + 'ary/items', false], [api + '/../assets/items', false], ['https://service.example.test.extra.test/api', false],
        ['https://other-service.example.test/api', false], ['http://service.example.test/api', false],
        ['//media.example.test/api', false], ['//service.example.test@media.example.test/api', false],
    ];
    async function check(address, allowed) {
        for (const kind of ['fetch', 'apiFetch', 'xhr']) {
            // Native Fetch rejects a URL with embedded user information before sending.
            if (kind !== 'xhr' && new URL(address, location.href).username) {
                await assert.rejects(send(kind, address), TypeError);
                continue;
            }
            const {headers} = await send(kind, address, {headers: {'X-Caller': 'fixture'}});
            assert.equal(headers.get('X-Caller'), 'fixture');
            assert.deepEqual(contextNames(headers), allowed ? (kind === 'xhr' ? sessionHeaders.slice(1) : sessionHeaders) : [],
                kind + ' resolves ' + address);
        }
    }
    for (const [address, allowed] of cases) await check(address, allowed);
    const userInfo = new URL(api + '/document');
    userInfo.username = 'sample';
    userInfo.password = 'fixture';
    await assert.rejects(window.fetch(userInfo), TypeError);
    try {
        const call = await send('xhr', userInfo);
        assert.deepEqual(contextNames(call.headers), []);
    } catch (error) {
        // A transport may reject URL credentials before sending.
        assert.ok(error instanceof TypeError);
    }
    __bakney.env.HOST = '/api/';
    await check('../../api/document', true);
    await check('document', false);
    await check('/apiary/document', false);
    await check('//app.example.test/api/document', true);
    globalThis.location = {origin: 'capacitor://localhost', href: 'capacitor://localhost/index.html'};
    __bakney.env.HOST = api;
    await check('/api/document', false);
    await check(api + '/document', true);
    await check('//service.example.test/api/document', false);
    __bakney.env.HOST = 'https://service.example.test/club/backend/';
    await check(new URL('https://service.example.test/club/backend/items'), true);
    await check('https://service.example.test/club/backends/items', false);
});

test('fetch_request_objects', async t => {
    const {send} = await middleware(t);
    for (const [address, allowed] of [[api + '/document', true], ['https://media.example.test/api/document', false]]) {
        const input = new Request(address, {method: 'POST', body: 'fixture', headers: {'X-Caller': 'fixture'}});
        const call = await send('fetch', input);
        assert.equal(call.method, 'POST');
        assert.equal(call.headers.get('X-Caller'), 'fixture');
        assert.equal(await call.request.text(), 'fixture');
        assert.deepEqual(contextNames(call.headers), allowed ? sessionHeaders : []);
    }
});

test('api_fetch_request_objects', async t => {
    const {apiFetch, requests, storage} = await middleware(t);
    const outcomes = [];
    for (const method of ['GET', 'POST']) {
        const input = new Request(api + '/document', {method, body: method === 'POST' ? 'fixture' : undefined,
            headers: {'X-Caller': 'fixture'}});
        try {
            await apiFetch(input);
        } catch (error) {
            outcomes.push({method, error: error.name});
            continue;
        }
        const call = requests.at(-1);
        outcomes.push({method: call.method, header: call.headers.get('X-Caller'), body: await call.request.text()});
        assert.deepEqual(contextNames(call.headers), sessionHeaders);
    }
    // The helper also supports a session with an access token alone.
    storage.delete('refreshToken');
    const attached = [];
    for (const address of [api + '/document', 'https://media.example.test/api/document']) {
        for (const input of [address, new Request(address)]) {
            await apiFetch(input);
            attached.push(requests.at(-1).headers.has('authorization'));
        }
    }
    assert.deepEqual({outcomes, attached}, {
        outcomes: [{method: 'GET', header: 'fixture', body: ''}, {method: 'POST', header: 'fixture', body: 'fixture'}],
        attached: [true, true, false, false],
    }, 'apiFetch preserves Request headers, method and body');
});

for (const kind of ['fetch', 'apiFetch']) {
    test('request_redirect_mode_' + kind, async t => {
        const {send} = await middleware(t);
        const url = api + '/documents/fixture';
        const manual = new Request(url, {redirect: 'manual'});
        const modes = [];
        for (const [input, options] of [[url], [manual], [manual, {redirect: 'follow'}], [url, {redirect: 'error'}]]) {
            modes.push((await send(kind, input, options)).redirect);
        }
        assert.deepEqual(modes, ['follow', 'manual', 'follow', 'error'], 'Honor the caller’s fetch redirect mode');
    });
}

test('backup_upload_progress_in_context', async t => {
    const {storage} = await middleware(t);
    const updates = [];
    for (const [group, impersonating] of [[false, false], [true, false], [false, true], [true, true]]) {
        storage.set('selectedGroup', group ? '"group"' : 'null');
        storage.set('switched_superuser', String(impersonating));
        const progress = [];
        const archive = new Blob(['fixture'], {type: 'application/gzip'});
        const upload = uploadArchive(api + '/instance/admin/backups/upload', archive,
            {token: 'fixture', onProgress: fraction => progress.push(fraction)});
        await upload.done;
        updates.push(progress);
    }
    assert.deepEqual(updates, [[0.5], [0.5], [0.5], [0.5]], 'Report backup upload progress in each session context');
});
