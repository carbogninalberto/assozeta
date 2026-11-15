import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const source = readFileSync(new URL('./NotificationService.js', import.meta.url),'utf8')
    .replace(/^import .*;\n/gm,'').replace('export default notificationService;', 'globalThis.service = notificationService;');
function fixture(accountRole) {
    let calls=0, resets=0;
    const sandbox={role:{value:accountRole},get:store=>store.value,
        apiFetch:async()=>{calls++;return {error:false,response:{active:false}};},
        exportProgress:{reset:()=>resets++},console,
        __bakney:{env:{API:{ASSOCIATION:{EXPORT:{ACTIVE:'/export/active'}}}}}};
    vm.runInNewContext(source,sandbox);
    return {service:sandbox.service,calls:()=>calls,resets:()=>resets};
}
test('athlete notification connections do not request association export state',async()=>{
    const f=fixture('athlete');await f.service.syncActiveExport();assert.equal(f.calls(),0);
});
test('association connections still recover their export state',async()=>{
    const f=fixture('association');await f.service.syncActiveExport();assert.equal(f.calls(),1);assert.equal(f.resets(),1);
});
test('administrator connections recover the managed association export state',async()=>{
    const f=fixture('administrator');await f.service.syncActiveExport();assert.equal(f.calls(),1);assert.equal(f.resets(),1);
});

function socketFixture() {
    const listeners = new Map();
    function add(type, handler) {
        if (!listeners.has(type)) listeners.set(type, new Set());
        listeners.get(type).add(handler);
    }
    function remove(type, handler) {
        listeners.get(type)?.delete(handler);
    }
    function dispatch(event) {
        for (const handler of listeners.get(event?.type) ?? []) handler(event);
        return true;
    }
    class SocketStub {
        constructor() { this.calls = []; }
        connect() { this.calls.push('connect'); }
        disconnect() { this.calls.push('disconnect'); }
        resetReconnectAttempts() { this.calls.push('reset'); }
    }
    for (const name of ['setOnNotifications', 'setOnNewNotification', 'setOnExportProgress', 'setOnExportCompleted',
        'setOnExportFailed', 'setOnStaffBoardChanged', 'setOnConnect', 'setOnReadConfirmed', 'setOnAllReadConfirmed']) {
        SocketStub.prototype[name] = () => {};
    }
    let socket = null;
    const sandbox = {
        NotificationWebSocket: class extends SocketStub {
            constructor(token) { super(); this.token = token; socket = this; }
        },
        role: {value: 'athlete'}, get: store => store.value,
        apiFetch: async () => ({error: false, response: {active: false}}),
        exportProgress: {reset() {}}, console,
        __bakney: {env: {API: {ASSOCIATION: {EXPORT: {ACTIVE: '/export/active'}}}}},
        document: {visibilityState: 'visible', addEventListener: add, removeEventListener: remove},
        addEventListener: add,
        removeEventListener: remove,
        dispatchEvent: dispatch,
    };
    vm.runInNewContext(source, sandbox);
    return {
        service: sandbox.service,
        socket: () => socket,
        listeners: type => listeners.get(type)?.size ?? 0,
        dispatch,
    };
}
test('an API version change reconnects the notification socket until disconnect', () => {
    const f = socketFixture();
    const store = () => ({set() {}});
    f.service.init(store(), store());
    assert.deepEqual(f.socket().calls, ['connect']);
    assert.equal(f.listeners('assozeta:api-version'), 1);
    f.dispatch({type: 'assozeta:api-version', detail: {version: 'v2'}});
    assert.deepEqual(f.socket().calls, ['connect', 'reset', 'disconnect', 'connect']);
    f.service.disconnect();
    assert.equal(f.listeners('assozeta:api-version'), 0);
    f.dispatch({type: 'assozeta:api-version', detail: {version: 'v1'}});
    assert.deepEqual(f.socket().calls, ['connect', 'reset', 'disconnect', 'connect', 'disconnect'],
        'no reconnect after disconnect');
});
