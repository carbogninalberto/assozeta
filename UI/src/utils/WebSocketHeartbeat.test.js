import test from 'node:test';
import assert from 'node:assert/strict';
import WebSocketHeartbeat from './WebSocketHeartbeat.js';
import NotificationWebSocket from './NotificationWebSocket.js';
import AgentWebSocket from './AgentWebSocket.js';
import HealthWebSocket from './HealthWebSocket.js';

const listeners = new Map();
globalThis.document = {
    visibilityState: 'visible',
    addEventListener(type, handler) { listeners.set(type, handler); },
    removeEventListener(type, handler) {
        if (listeners.get(type) === handler) listeners.delete(type);
    },
};
globalThis.window = {location: {origin: 'http://localhost'}};
globalThis.__bakney = {
    env: {
        DOMAIN: 'http://localhost',
        WS: {NOTIFICATIONS: '/ws/notifications/', AGENT: '/ws/agent/', HEALTH: '/ws/health/'},
    },
};

class FakeWebSocket {
    constructor(url) {
        this.url = url;
        this.readyState = FakeWebSocket.CONNECTING;
        this.sent = [];
        FakeWebSocket.instances.push(this);
    }

    open() {
        this.readyState = FakeWebSocket.OPEN;
        this.onopen?.();
    }

    send(payload) {
        this.sent.push(JSON.parse(payload));
    }

    receive(payload) {
        this.onmessage?.({data: JSON.stringify(payload)});
    }

    close(code = 1000) {
        this.readyState = FakeWebSocket.CLOSED;
        this.onclose?.({code});
    }
}
FakeWebSocket.OPEN = 1;
FakeWebSocket.CONNECTING = 0;
FakeWebSocket.CLOSED = 3;
FakeWebSocket.instances = [];
globalThis.WebSocket = FakeWebSocket;

test('heartbeat sends ping, accepts matching pong, and clears timers', () => {
    const socket = new FakeWebSocket('ws://test');
    socket.readyState = FakeWebSocket.OPEN;
    const heartbeat = new WebSocketHeartbeat(() => socket);

    heartbeat.start();
    const ping = socket.sent.at(-1);
    assert.equal(ping.type, 'ping');
    assert.equal(heartbeat.handlePong({type: 'pong', timestamp: ping.timestamp}), true);
    assert.equal(heartbeat.timeout, null);

    heartbeat.stop();
    assert.equal(heartbeat.interval, null);
    assert.equal(listeners.has('visibilitychange'), false);
});

test('heartbeat closes an unresponsive visible socket but tolerates a hidden tab', async () => {
    const socket = new FakeWebSocket('ws://test');
    socket.readyState = FakeWebSocket.OPEN;
    const heartbeat = new WebSocketHeartbeat(() => socket, {intervalMs: 1000, timeoutMs: 5});

    heartbeat.start();
    await new Promise(resolve => setTimeout(resolve, 10));
    assert.equal(socket.readyState, FakeWebSocket.CLOSED);
    heartbeat.stop();

    const hiddenSocket = new FakeWebSocket('ws://hidden');
    hiddenSocket.readyState = FakeWebSocket.OPEN;
    document.visibilityState = 'hidden';
    const hiddenHeartbeat = new WebSocketHeartbeat(
        () => hiddenSocket,
        {intervalMs: 1000, timeoutMs: 5},
    );
    hiddenHeartbeat.start();
    await new Promise(resolve => setTimeout(resolve, 10));
    assert.equal(hiddenSocket.readyState, FakeWebSocket.OPEN);
    document.visibilityState = 'visible';
    listeners.get('visibilitychange')?.();
    assert.equal(hiddenSocket.sent.at(-1).type, 'ping');
    hiddenHeartbeat.handlePong({
        type: 'pong',
        timestamp: hiddenSocket.sent.at(-1).timestamp,
    });
    hiddenHeartbeat.stop();
});

for (const [name, WebSocketClass] of [
    ['NotificationWebSocket', NotificationWebSocket],
    ['AgentWebSocket', AgentWebSocket],
    ['HealthWebSocket', HealthWebSocket],
]) {
    test(`${name} starts one heartbeat and manual disconnect does not reconnect`, () => {
        FakeWebSocket.instances = [];
        const abstraction = name === 'HealthWebSocket' ? new WebSocketClass() : new WebSocketClass('token');
        abstraction.connect();
        abstraction.connect();
        const socket = FakeWebSocket.instances[0];
        socket.open();
        abstraction.connect();

        const pings = socket.sent.filter(message => message.type === 'ping');
        assert.equal(pings.length, 1);
        assert.equal(FakeWebSocket.instances.length, 1);
        socket.receive({type: 'pong', timestamp: pings[0].timestamp});
        assert.equal(abstraction.heartbeat.timeout, null);

        abstraction.disconnect();
        assert.equal(abstraction.heartbeat.interval, null);
        assert.equal(abstraction.reconnectTimer, null);
        assert.equal(FakeWebSocket.instances.length, 1);
    });
}

test('existing terminal close codes remain terminal', () => {
    for (const code of [4001, 4002, 4003]) {
        FakeWebSocket.instances = [];
        const agent = new AgentWebSocket('token');
        agent.connect();
        const socket = FakeWebSocket.instances[0];
        socket.open();
        socket.close(code);
        assert.equal(agent.reconnectTimer, null);
    }

    FakeWebSocket.instances = [];
    const notifications = new NotificationWebSocket('token');
    notifications.connect();
    const socket = FakeWebSocket.instances[0];
    socket.open();
    socket.close(4001);
    assert.equal(notifications.reconnectTimer, null);
});

test('a heartbeat timeout on an abstraction enters its reconnect path', async () => {
    FakeWebSocket.instances = [];
    const notifications = new NotificationWebSocket('token');
    notifications.heartbeat.timeoutMs = 5;
    notifications.connect();
    const socket = FakeWebSocket.instances[0];
    socket.open();

    await new Promise(resolve => setTimeout(resolve, 10));

    assert.equal(socket.readyState, FakeWebSocket.CLOSED);
    assert.notEqual(notifications.reconnectTimer, null);
    notifications.disconnect();
    assert.equal(notifications.reconnectTimer, null);
});

test('NotificationWebSocket routes export events as business events', () => {
    FakeWebSocket.instances = [];
    const notifications = new NotificationWebSocket('token');
    const received = [];
    notifications.setOnExportProgress(event => received.push(event.type));
    notifications.setOnExportCompleted(event => received.push(event.type));
    notifications.setOnExportFailed(event => received.push(event.type));
    notifications.connect();
    const socket = FakeWebSocket.instances[0];
    socket.open();

    socket.receive({type: 'export_progress', task_id: 'task-1'});
    socket.receive({type: 'export_completed', task_id: 'task-1'});
    socket.receive({type: 'export_failed', task_id: 'task-1'});

    assert.deepEqual(received, ['export_progress', 'export_completed', 'export_failed']);
    notifications.disconnect();
});
