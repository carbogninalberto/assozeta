import test from 'node:test';
import assert from 'node:assert/strict';
import {resetApiVersionState, setActiveApiVersion} from './apiVersion.js';
import HealthWebSocket from './HealthWebSocket.js';
import {
    WS_V2_FALLBACK_KEY,
    getWebSocketUrl,
    isWebSocketV2Path,
    noteWebSocketV2OpenFailure,
    noteWebSocketV2Opened,
    resetWebSocketUrlState,
    webSocketPath,
} from './websocketUrl.js';

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
        resetWebSocketUrlState();
        resetApiVersionState();
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

test('v1 keeps the Django socket paths', t => {
    withGlobals(t, browserGlobals());
    assert.equal(getWebSocketUrl('/ws/x/'), 'wss://club.example.test/ws/x/');
    assert.equal(getWebSocketUrl('/ws/notifications/'), 'wss://club.example.test/ws/notifications/');
    assert.equal(webSocketPath('/ws/x/'), '/ws/x/');
    assert.equal(isWebSocketV2Path('/ws/x/'), false);
});

test('v2 moves socket paths under /api/v2', t => {
    withGlobals(t, browserGlobals({origin: 'http://localhost:5001'}));
    setActiveApiVersion('v2');
    assert.equal(getWebSocketUrl('/ws/x/'), 'ws://localhost:5001/api/v2/ws/x/');
    assert.equal(getWebSocketUrl('/ws/health/'), 'ws://localhost:5001/api/v2/ws/health/');
    assert.equal(webSocketPath('/ws/agent/'), '/api/v2/ws/agent/');
    assert.equal(isWebSocketV2Path('/ws/agent/'), true);
});

test('v2 uses the configured absolute domain', t => {
    withGlobals(t, browserGlobals({domain: 'https://api.example.test'}));
    setActiveApiVersion('v2');
    assert.equal(getWebSocketUrl('/ws/notifications/'), 'wss://api.example.test/api/v2/ws/notifications/');
});

test('v2 keeps token and impersonation parameters', t => {
    withGlobals(t, browserGlobals({impersonation: {session_id: 'imp-1'}}));
    setActiveApiVersion('v2');
    let url = new URL(getWebSocketUrl('/ws/notifications/', 'jwt-token'));
    assert.equal(url.pathname, '/api/v2/ws/notifications/');
    assert.equal(url.searchParams.get('token'), 'jwt-token');
    assert.equal(url.searchParams.get('impersonation'), 'imp-1');

    globalThis.localStorage.values.delete('impersonationContext');
    globalThis.localStorage.values.set('switched_superuser', 'true');
    url = new URL(getWebSocketUrl('/ws/notifications/', 'jwt-token'));
    assert.equal(url.searchParams.get('impersonation'), 'invalid');
});

test('three pre-open failures in a row fall back to /ws/ for the session', t => {
    const sessionStorage = memoryStorage();
    const warnings = [];
    withGlobals(t, {
        ...browserGlobals(),
        sessionStorage,
        console: {...console, warn: (...args) => warnings.push(args)},
    });
    setActiveApiVersion('v2');
    assert.equal(getWebSocketUrl('/ws/x/'), 'wss://club.example.test/api/v2/ws/x/');

    // One or two transient failures (dropped network, expired token, wake) keep v2.
    noteWebSocketV2OpenFailure('/ws/x/', 'close 1006 before open');
    noteWebSocketV2OpenFailure('/ws/x/', 'close 1006 before open');
    assert.equal(getWebSocketUrl('/ws/x/'), 'wss://club.example.test/api/v2/ws/x/');
    assert.equal(sessionStorage.values.has(WS_V2_FALLBACK_KEY), false);
    assert.equal(warnings.length, 0);

    noteWebSocketV2OpenFailure('/ws/x/', 'close 1006 before open');
    assert.equal(getWebSocketUrl('/ws/x/'), 'wss://club.example.test/ws/x/');
    assert.equal(warnings.length, 1);
    assert.equal(sessionStorage.values.get(WS_V2_FALLBACK_KEY), '["/ws/x/"]');

    // Repeated failures do not warn again and do not touch other socket kinds.
    noteWebSocketV2OpenFailure('/ws/x/');
    assert.equal(warnings.length, 1);
    assert.equal(getWebSocketUrl('/ws/health/'), 'wss://club.example.test/api/v2/ws/health/');

    // A page reload drops the module state but keeps the session fallback.
    resetWebSocketUrlState();
    assert.equal(getWebSocketUrl('/ws/x/'), 'wss://club.example.test/ws/x/');
    assert.equal(getWebSocketUrl('/ws/health/'), 'wss://club.example.test/api/v2/ws/health/');

    // Back on v1 every socket path is unchanged anyway.
    setActiveApiVersion('v1');
    assert.equal(getWebSocketUrl('/ws/x/'), 'wss://club.example.test/ws/x/');
});

test('an open v2 socket resets its consecutive failure count', t => {
    const sessionStorage = memoryStorage();
    withGlobals(t, {...browserGlobals(), sessionStorage, console: {...console, warn() {}}});
    setActiveApiVersion('v2');

    noteWebSocketV2OpenFailure('/ws/x/');
    noteWebSocketV2OpenFailure('/ws/x/');
    noteWebSocketV2Opened('/ws/x/');
    noteWebSocketV2OpenFailure('/ws/x/');
    noteWebSocketV2OpenFailure('/ws/x/');
    assert.equal(getWebSocketUrl('/ws/x/'), 'wss://club.example.test/api/v2/ws/x/');
    assert.equal(sessionStorage.values.has(WS_V2_FALLBACK_KEY), false);
    noteWebSocketV2OpenFailure('/ws/x/');
    assert.equal(getWebSocketUrl('/ws/x/'), 'wss://club.example.test/ws/x/');

    // Counts and resets stay per socket kind.
    noteWebSocketV2OpenFailure('/ws/agent/');
    noteWebSocketV2OpenFailure('/ws/agent/');
    noteWebSocketV2Opened('/ws/health/');
    noteWebSocketV2OpenFailure('/ws/agent/');
    assert.equal(getWebSocketUrl('/ws/agent/'), 'wss://club.example.test/ws/agent/');
});

test('a failure while the browser is offline is not counted', t => {
    const sessionStorage = memoryStorage();
    withGlobals(t, {
        ...browserGlobals(),
        sessionStorage,
        navigator: {onLine: false},
        console: {...console, warn() {}},
    });
    setActiveApiVersion('v2');
    for (let attempt = 0; attempt < 5; attempt += 1) noteWebSocketV2OpenFailure('/ws/x/', 'offline');
    assert.equal(getWebSocketUrl('/ws/x/'), 'wss://club.example.test/api/v2/ws/x/');
    assert.equal(sessionStorage.values.has(WS_V2_FALLBACK_KEY), false);

    // Back online the failures count again, from the start.
    Object.defineProperty(globalThis, 'navigator', {value: {onLine: true}, configurable: true, writable: true});
    noteWebSocketV2OpenFailure('/ws/x/');
    noteWebSocketV2OpenFailure('/ws/x/');
    assert.equal(getWebSocketUrl('/ws/x/'), 'wss://club.example.test/api/v2/ws/x/');
    noteWebSocketV2OpenFailure('/ws/x/');
    assert.equal(getWebSocketUrl('/ws/x/'), 'wss://club.example.test/ws/x/');
});

test('a stored fallback survives unavailable storage and does not break v1', t => {
    const throwing = {
        getItem() {
            throw new Error('denied');
        },
        setItem() {
            throw new Error('denied');
        },
    };
    withGlobals(t, {...browserGlobals(), sessionStorage: throwing, console: {...console, warn() {}}});
    setActiveApiVersion('v2');
    assert.equal(getWebSocketUrl('/ws/x/'), 'wss://club.example.test/api/v2/ws/x/');
    assert.doesNotThrow(() => {
        noteWebSocketV2OpenFailure('/ws/x/');
        noteWebSocketV2OpenFailure('/ws/x/');
        noteWebSocketV2OpenFailure('/ws/x/');
    });
    // The in-memory fallback still applies for this page lifecycle.
    assert.equal(getWebSocketUrl('/ws/x/'), 'wss://club.example.test/ws/x/');
    setActiveApiVersion('v1');
    assert.equal(getWebSocketUrl('/ws/x/'), 'wss://club.example.test/ws/x/');
});

test('paths outside /ws/ are never rewritten', t => {
    withGlobals(t, browserGlobals());
    setActiveApiVersion('v2');
    for (const path of ['/api/v2/ws/notifications/', '/api/ws/x/', '/healthz', 'ws/x/', '']) {
        assert.equal(webSocketPath(path), path);
        assert.equal(isWebSocketV2Path(path), false);
    }
    assert.equal(getWebSocketUrl('/api/v2/ws/notifications/'), 'wss://club.example.test/api/v2/ws/notifications/');
    noteWebSocketV2OpenFailure('/healthz');
    assert.equal(webSocketPath('/ws/x/'), '/api/v2/ws/x/');
});

class FakeWebSocket {
    static CONNECTING = 0;
    static OPEN = 1;
    static CLOSED = 3;
    static instances = [];

    constructor(url) {
        this.url = url;
        this.readyState = FakeWebSocket.CONNECTING;
        FakeWebSocket.instances.push(this);
    }

    send() {}

    open() {
        this.readyState = FakeWebSocket.OPEN;
        this.onopen?.();
    }

    fail(code = 1006) {
        this.readyState = FakeWebSocket.CLOSED;
        this.onerror?.(new Error('connection refused'));
        this.onclose?.({code});
    }

    close(code = 1000) {
        this.readyState = FakeWebSocket.CLOSED;
        this.onclose?.({code});
    }
}

test('each failed attempt is counted once and an open resets the count', t => {
    FakeWebSocket.instances = [];
    const sessionStorage = memoryStorage();
    const document = {visibilityState: 'visible', addEventListener() {}, removeEventListener() {}};
    withGlobals(t, {
        ...browserGlobals({origin: 'http://localhost:5001'}),
        document,
        sessionStorage,
        WebSocket: FakeWebSocket,
        console: {...console, error() {}, log() {}, warn() {}},
    });
    setActiveApiVersion('v2');

    const ws = new HealthWebSocket();
    const failAttempt = () => {
        // One failed attempt fires onerror and onclose.
        FakeWebSocket.instances.at(-1).fail();
        clearTimeout(ws.reconnectTimer);
        ws.reconnectTimer = null;
    };
    const connect = () => {
        ws.connect();
        return FakeWebSocket.instances.at(-1).url;
    };

    assert.equal(connect(), 'ws://localhost:5001/api/v2/ws/health/');
    failAttempt();
    // A single attempt is one failure, even with both events.
    assert.equal(sessionStorage.values.has(WS_V2_FALLBACK_KEY), false);
    assert.equal(connect(), 'ws://localhost:5001/api/v2/ws/health/');
    failAttempt();
    assert.equal(sessionStorage.values.has(WS_V2_FALLBACK_KEY), false);
    assert.equal(connect(), 'ws://localhost:5001/api/v2/ws/health/');
    failAttempt();
    assert.deepEqual(JSON.parse(sessionStorage.values.get(WS_V2_FALLBACK_KEY)), ['/ws/health/']);
    assert.equal(connect(), 'ws://localhost:5001/ws/health/');

    // An open v2 socket resets the count; failures before it opened still count.
    resetWebSocketUrlState();
    sessionStorage.values.clear();
    ws.disconnect();
    assert.equal(connect(), 'ws://localhost:5001/api/v2/ws/health/');
    FakeWebSocket.instances.at(-1).open();
    failAttempt();
    for (let attempt = 0; attempt < 2; attempt += 1) {
        assert.equal(connect(), 'ws://localhost:5001/api/v2/ws/health/');
        failAttempt();
    }
    assert.equal(sessionStorage.values.has(WS_V2_FALLBACK_KEY), false);
    assert.equal(connect(), 'ws://localhost:5001/api/v2/ws/health/');
    failAttempt();
    assert.deepEqual(JSON.parse(sessionStorage.values.get(WS_V2_FALLBACK_KEY)), ['/ws/health/']);
    assert.equal(connect(), 'ws://localhost:5001/ws/health/');
    ws.disconnect();
});
