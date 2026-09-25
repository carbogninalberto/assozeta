import assert from 'node:assert/strict';
import test from 'node:test';
import {get} from 'svelte/store';

function storage() {
    const values = new Map();
    return {getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, String(value)),
        removeItem: key => values.delete(key), clear: () => values.clear()};
}
globalThis.localStorage = storage();
globalThis.sessionStorage = storage();
const {initializeLoginSession, isBakneyLogin, reloadAppAt, observeLoginIdentity} = await import('./loginSession.js');
const stores = await import('../store/stores.js');

test('handoff replaces all identity caches and initializes the ordinary login stores', () => {
    localStorage.setItem('impersonationContext', 'old');
    localStorage.setItem('selectedGroup', 'old');
    localStorage.setItem('permissions', 'old');
    sessionStorage.setItem('redirectAfterLogin', 'old');
    initializeLoginSession({access_token:'local-access', refresh_token:'local-refresh', expires_in:3600,
        role:'athlete', user_data:{user_id:'new-user'}}, true);
    for (const key of ['impersonationContext','selectedGroup','permissions']) assert.equal(localStorage.getItem(key), null);
    assert.equal(sessionStorage.getItem('redirectAfterLogin'), null);
    assert.equal(get(stores.sessionToken), 'local-access');
    assert.equal(JSON.parse(localStorage.getItem('refreshToken')), 'local-refresh');
    assert.equal(get(stores.role), 'athlete');
    assert.equal(get(stores.currentPage), 'dashboard');
    assert.equal(localStorage.getItem('loginIdentity'), 'new-user');
});

test('malformed response does not clear an existing session', () => {
    const token = localStorage.getItem('sessionToken');
    assert.throws(() => initializeLoginSession({access_token:'bad'}, true));
    assert.equal(localStorage.getItem('sessionToken'), token);
});

test('the callback route is exact and full document navigation is explicit', () => {
    assert(isBakneyLogin({hash:'#/bakney-login?error=handoff_expired'}));
    assert(!isBakneyLogin({hash:'#/bakney-login-other'}));
    const calls = [];
    globalThis.history = {replaceState: (...args) => calls.push(args)};
    globalThis.window = {location:{reload:()=>calls.push('reload')}};
    reloadAppAt('/');
    assert.deepEqual(calls, [[null,'','/'], 'reload']);
});

test('an identity change in another tab reloads and closes its old document', () => {
    let listener, reloads = 0;
    globalThis.window = {addEventListener: (name, fn) => {assert.equal(name,'storage');listener=fn;}, location:{reload:()=>reloads++}};
    observeLoginIdentity();
    listener({key:'sessionToken',newValue:'refreshed-token'});
    listener({key:'loginIdentity',newValue:'new-user'});
    assert.equal(reloads,0);
    listener({key:'loginIdentity',newValue:'another-user'});
    assert.equal(reloads,1);
});
