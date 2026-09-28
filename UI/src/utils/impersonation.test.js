import assert from 'node:assert/strict';
import test from 'node:test';

function storage() {
    const values = {};
    return new Proxy({
        getItem: key => values[key] ?? null,
        setItem: (key, value) => {values[key] = String(value);},
        removeItem: key => {delete values[key];},
        clear: () => {for (const key of Object.keys(values)) delete values[key];},
    }, {ownKeys: () => Object.keys(values), getOwnPropertyDescriptor: () => ({enumerable:true, configurable:true})});
}
globalThis.localStorage = storage();
globalThis.sessionStorage = storage();
let reloads = 0;
const listeners = new Map();
globalThis.window = {
    history: {replaceState() {}}, location: {reload: () => reloads++},
    addEventListener: (name, listener) => listeners.set(name, listener),
    removeEventListener: name => listeners.delete(name),
};
const {startImpersonation, stopImpersonation, impersonationEndpoint, canImpersonate, observeIdentityChanges, readImpersonation, recoverInvalidImpersonation} = await import('./impersonation.js');
const {sessionToken, refreshToken, selectedGroup, permissions} = await import('../store/stores.js');
for (const store of [sessionToken, refreshToken, selectedGroup, permissions]) store.useLocalStorage();

test('association owner entry and actor-specific endpoints survive a target change', async () => {
    localStorage.setItem('userData', JSON.stringify({can_impersonate:true}));
    assert(canImpersonate({can_impersonate:true}));
    assert(!canImpersonate({can_impersonate:false}));
    assert.equal(impersonationEndpoint(), '/api/association/impersonation');
    sessionToken.set('original-access');
    refreshToken.set('original-refresh');
    selectedGroup.set('old-group');
    permissions.set(['old-permission']);
    localStorage.setItem('bkn_datatable_members', 'old');
    sessionStorage.setItem('private-data', 'old');
    const calls = [];
    globalThis.fetch = async (url, options) => {
        calls.push({url, options});
        return {ok:true, status:201, json:async () => ({session_id:'session', actor_role:'association', target:{user_id:'athlete'}})};
    };
    await startImpersonation({user_id:'athlete'});
    assert.equal(calls[0].url, '/api/association/impersonation');
    assert.deepEqual(JSON.parse(calls[0].options.body), {target_user_id:'athlete'});
    assert.equal(readImpersonation().target.user_id, 'athlete');
    assert.equal(localStorage.getItem('sessionToken'), '"original-access"');
    assert.equal(localStorage.getItem('refreshToken'), '"original-refresh"');
    assert.equal(localStorage.getItem('selectedGroup'), 'null');
    assert.equal(localStorage.getItem('bkn_datatable_members'), null);
    assert.equal(sessionStorage.getItem('private-data'), null);
    assert.equal(impersonationEndpoint(), '/api/association/impersonation');
    assert(canImpersonate({can_impersonate:false}));
    globalThis.fetch = async (url, options) => {
        assert.equal(url, '/api/association/impersonation');
        assert.equal(options.method, 'DELETE');
        return {ok:true, status:204};
    };
    await stopImpersonation();
    assert.equal(readImpersonation(), null);
    assert.equal(localStorage.getItem('USER_ID'), null);
    assert.equal(localStorage.getItem('sessionToken'), '"original-access"');
    assert.equal(reloads, 2);
});

test('administrator and legacy sessions retain their control endpoint', () => {
    localStorage.setItem('userData', JSON.stringify({is_superuser:true}));
    assert.equal(impersonationEndpoint(), '/api/administration/impersonation');
    localStorage.setItem('impersonationContext', JSON.stringify({session_id:'legacy', target:{user_id:'owner'}}));
    localStorage.setItem('userData', '{}');
    assert.equal(impersonationEndpoint(), '/api/administration/impersonation');
    localStorage.removeItem('impersonationContext');
});

test('failed switching preserves the current identity and other tabs reload on changes', async () => {
    const context = JSON.stringify({actor_role:'association', session_id:'current', target:{user_id:'one'}});
    localStorage.setItem('impersonationContext', context);
    globalThis.fetch = async () => ({ok:false, status:403, json:async () => ({detail:'Unavailable'})});
    await assert.rejects(startImpersonation({user_id:'two'}), /Unavailable/);
    assert.equal(localStorage.getItem('impersonationContext'), context);
    const stop = observeIdentityChanges();
    const before = reloads;
    listeners.get('storage')({key:'impersonationContext',oldValue:context,newValue:null});
    assert.equal(reloads, before + 1);
    stop();
    assert(!listeners.has('storage'));
});


test('expiry restores the original login while late failures cannot clear a newer identity', () => {
    const expired = JSON.stringify({actor_role:'association', session_id:'expired', target:{user_id:'one'}});
    localStorage.setItem('impersonationContext', expired);
    assert(!recoverInvalidImpersonation({detail:'Permission denied'}, expired));
    localStorage.setItem('impersonationContext', 'newer-session');
    assert(!recoverInvalidImpersonation({impersonation_invalid:true}, expired));
    assert.equal(localStorage.getItem('impersonationContext'), 'newer-session');
    localStorage.setItem('impersonationContext', expired);
    assert(recoverInvalidImpersonation({impersonation_invalid:true}, expired));
    assert.equal(readImpersonation(), null);
    assert.equal(localStorage.getItem('sessionToken'), '"original-access"');
});
