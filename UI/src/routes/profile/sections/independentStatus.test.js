import assert from 'node:assert/strict';
import test from 'node:test';
import {readIndependentStatus, readStoredStatus, applicationReady} from './independentStatus.js';

test('browser recovery requires both web and API readiness without touching authentication', async () => {
    const urls = [];
    assert.equal(await applicationReady(async (url, options) => {
        urls.push(url);
        assert.equal(options.cache, 'no-store');
        assert.equal(options.headers, undefined);
        return new Response('{}');
    }, '/api'), true);
    assert.deepEqual(urls, ['/healthz', '/api/readyz']);
    assert.equal(await applicationReady(async url => new Response('{}', {status: url === '/healthz' ? 200 : 503}), '/api'), false);
    assert.equal(await applicationReady(async () => {throw new Error('disconnected');}, '/api'), false);
});

test('status follows middleware token rotation and never reuses a removed session', async () => {
    let current = 'first-access-token';
    const storage = {getItem: () => JSON.stringify(current)};
    const sent = [];
    const fetcher = async (url, options) => {
        sent.push(options.headers.Authorization);
        return new Response(JSON.stringify({is_owner: true, protocol: 1, history: []}));
    };
    await readStoredStatus(fetcher, storage);
    current = 'rotated-access-token';
    await readStoredStatus(fetcher, storage);
    current = null;
    assert.deepEqual(await readStoredStatus(fetcher, storage), {kind: 'denied'});
    assert.deepEqual(sent, ['Bearer first-access-token', 'Bearer rotated-access-token']);
});

test('independent status uses the authenticated read-only route and keeps full recovery history', async () => {
    const status = {is_owner: true, protocol: 1, available: true, active: null,
        history: [{id: 'operation', stage: 'recovery_required', recovery: 'Restore the matching backup'}]};
    const result = await readIndependentStatus('signed-token', async (url, options) => {
        assert.equal(url, '/instance-update-status');
        assert.equal(options.method, 'POST');
        assert.equal(options.body, '{}');
        assert.equal(options.headers.Authorization, 'Bearer signed-token');
        assert.equal(options.cache, 'no-store');
        assert.ok(options.signal instanceof AbortSignal);
        return new Response(JSON.stringify(status));
    });
    assert.deepEqual(result, {kind: 'owner', status});
});

test('missing or rejected sessions never expose private operation state', async () => {
    assert.deepEqual(await readIndependentStatus(null, () => assert.fail('must not request')), {kind: 'denied'});
    for (const status of [401, 403]) {
        assert.deepEqual(await readIndependentStatus('token', async () => new Response('{}', {status})), {kind: 'denied'});
    }
});

test('outages and unexpected responses do not grant owner access or clear the login session', async () => {
    const fetchers = [
        async () => { throw new Error('Restarting'); },
        async () => new Response('<html>Maintenance</html>', {status: 503}),
        async () => new Response('invalid JSON'),
        async () => new Response(JSON.stringify({is_owner: false, protocol: 1, history: []})),
        async () => new Response(JSON.stringify({is_owner: true, protocol: 2, history: []})),
    ];
    for (const fetcher of fetchers) {
        assert.deepEqual(await readIndependentStatus('token', fetcher), {kind: 'unavailable'});
    }
});
