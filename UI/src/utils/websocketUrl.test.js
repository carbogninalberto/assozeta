import test from 'node:test';
import assert from 'node:assert/strict';
import {getWebSocketUrl} from './websocketUrl.js';

function memoryStorage(initial = {}) {
    const values = new Map(Object.entries(initial));
    return {
        values,
        getItem: key => values.get(key) ?? null,
        setItem: (key, value) => values.set(key, String(value)),
        removeItem: key => values.delete(key),
    };
}

function withGlobals(t, globals) {
    const original = Object.fromEntries(
        Object.keys(globals).map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)])
    );
    for (const [key, value] of Object.entries(globals)) {
        Object.defineProperty(globalThis, key, {value, configurable: true, writable: true});
    }
    t.after(() => {
        for (const [key, descriptor] of Object.entries(original)) {
            if (descriptor) Object.defineProperty(globalThis, key, descriptor);
            else delete globalThis[key];
        }
    });
}

// The built UI injects __bakney.env.DOMAIN (empty in the standard selfhost
// build) and the callers read the page location as the fallback origin.
function browserGlobals({domain = '', origin = 'https://club.example.test', impersonation = null} = {}) {
    const localStorage = memoryStorage();
    if (impersonation) localStorage.setItem('impersonationContext', JSON.stringify(impersonation));
    return {
        __bakney: {
            env: {
                DOMAIN: domain,
                WS: {
                    NOTIFICATIONS: '/ws/notifications/',
                    HEALTH: '/ws/health/',
                    UPDATES: '/ws/updates/',
                    AGENT: '/ws/agent/',
                },
            },
        },
        window: {location: {origin}},
        localStorage,
    };
}

test('same-origin and custom-host clients use stable socket paths', t => {
    withGlobals(t, browserGlobals({origin: 'http://localhost:5001'}));
    assert.equal(getWebSocketUrl('/ws/health/'), 'ws://localhost:5001/ws/health/');
    __bakney.env.DOMAIN = 'https://custom.example.test';
    window.location.origin = 'capacitor://localhost';
    for (const kind of ['updates', 'health', 'notifications', 'agent']) {
        assert.equal(getWebSocketUrl('/ws/' + kind + '/'), 'wss://custom.example.test/ws/' + kind + '/');
    }
    assert.equal(getWebSocketUrl('/api/v2/ws/notifications/'), 'wss://custom.example.test/api/v2/ws/notifications/');
});

test('stable socket URLs keep token and impersonation parameters', t => {
    withGlobals(t, browserGlobals({impersonation: {session_id: 'imp-1'}}));
    let url = new URL(getWebSocketUrl('/ws/notifications/', 'jwt-token'));
    assert.equal(url.pathname, '/ws/notifications/');
    assert.equal(url.searchParams.get('token'), 'jwt-token');
    assert.equal(url.searchParams.get('impersonation'), 'imp-1');

    globalThis.localStorage.values.delete('impersonationContext');
    globalThis.localStorage.values.set('switched_superuser', 'true');
    url = new URL(getWebSocketUrl('/ws/notifications/', 'jwt-token'));
    assert.equal(url.searchParams.get('impersonation'), 'invalid');
});
