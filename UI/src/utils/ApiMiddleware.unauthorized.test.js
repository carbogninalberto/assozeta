import test from 'node:test';
import assert from 'node:assert/strict';

const values = new Map();
let cleared = 0;
let reloaded = 0;
globalThis.window = globalThis;
globalThis.localStorage = {
    getItem: key => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, String(value)),
    removeItem: key => values.delete(key),
    clear: () => { cleared += 1; values.clear(); },
};
globalThis.location = {reload: () => { reloaded += 1; }};
globalThis.XMLHttpRequest = class {};
globalThis.__bakney = {env: {
    HOST: 'http://localhost:5001/api',
    API: {OAUTH2: {LOGIN: '/api/login', SIGNUP: '/api/signup', RESET: '/api/reset'}},
}};
globalThis.fetch = async () => new Response('{}', {status: 401, headers: {'content-type': 'application/json'}});
const {apiFetch} = await import('./ApiMiddleware.js');

test('a 401 on a public page does not wipe and reload an anonymous visitor', async () => {
    // Importing ApiMiddleware persisted the anonymous default: sessionToken === 'null'.
    assert.equal(localStorage.getItem('sessionToken'), 'null');
    const res = await apiFetch('http://localhost:5001/api/public');
    assert.equal(res.status, 401);
    assert.equal(res.error, true);
    assert.equal(cleared, 0);
    assert.equal(reloaded, 0);
});

test('a 401 for a logged-in user still ends the session', async () => {
    localStorage.setItem('sessionToken', JSON.stringify('expired-access'));
    await apiFetch('http://localhost:5001/api/private');
    assert.equal(cleared, 1);
    assert.equal(reloaded, 1);
});

test('skipForbidden keeps the session on a 401', async () => {
    localStorage.setItem('sessionToken', JSON.stringify('expired-access'));
    await apiFetch('http://localhost:5001/api/private', {method: 'GET', skipForbidden: true});
    assert.equal(cleared, 1);
    assert.equal(reloaded, 1);
});
