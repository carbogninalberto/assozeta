import test from 'node:test';
import assert from 'node:assert/strict';
import {existsSync} from 'node:fs';
import {setTimeout as delay} from 'node:timers/promises';
import {get} from 'svelte/store';
import {controller, snapshot} from '../../scripts/p5-runtime-fixtures.mjs';

const api = '/api/instance/admin/runtime';
const socket = '/instance-update-status';
const json = (value, status = 200) => Response.json(value, {status});

async function store(t, handler) {
    const url = new URL('./runtimeSwitchStore.js', import.meta.url);
    assert.ok(existsSync(url), 'Missing runtimeSwitchStore.js: owner runtime status store');
    const {createRuntimeStore} = await import(url);
    const calls = [];
    const model = createRuntimeStore({
        fetcher: async (url, init = {}) => {
            const request = new Request(new URL(url, 'http://localhost'), init);
            const call = {path: new URL(request.url).pathname, method: request.method,
                headers: request.headers, body: await request.text()};
            calls.push(call);
            return handler(call);
        },
        readToken: () => 'fixture-session', pollInterval: 15,
    });
    t.after(() => model.stop());
    return {model, calls, state: () => get(model)};
}

test('store_transitions', async t => {
    let value = snapshot();
    let accepted;
    const {model, state, calls} = await store(t, call => call.method === 'GET' ? json(value)
        : new Promise(resolve => { accepted = resolve; }));
    await model.refresh();
    assert.equal(state().phase, 'idle');
    const pending = model.submit('go', 'fixture');
    assert.equal(state().phase, 'submitting');
    await delay(0);
    const request = calls.at(-1);
    assert.equal(request.path, api);
    assert.equal(request.method, 'POST');
    assert.equal(request.headers.get('authorization'), 'Bearer fixture-session');
    const body = JSON.parse(request.body);
    assert.match(body.idempotency_key, /^[\da-f]{8}-(?:[\da-f]{4}-){3}[\da-f]{12}$/i);
    assert.deepEqual(body, {target: 'go', expected_generation: 7, password: 'fixture',
        idempotency_key: body.idempotency_key});
    value = snapshot('queued');
    accepted(json({operation: value.active, authority: {runtime: 'django', generation: 7, operation_id: null},
        replayed: false}, 202));
    await pending;
    await model.refresh();
    assert.equal(state().phase, 'in_progress');
    for (const [view, phase] of [
        [snapshot('stage_backlog', {failed: true, blocked: true}), 'blocked'],
        [snapshot('preflight', {failed: true}), 'failed'],
        [snapshot('activate', {failed: true}), 'recovery_required'],
        [snapshot('verify'), 'in_progress'], [snapshot('completed'), 'completed'],
    ]) {
        value = view;
        await model.refresh();
        assert.equal(state().phase, phase);
        assert.equal(state().status.effective_runtime, value.effective_runtime);
        assert.equal(state().status.intended_runtime, value.intended_runtime);
        assert.equal(state().status.operation.progress.index, value.operation.progress.index);
    }
});

test('store_poll_fallback', async t => {
    let offline = false, controllerDown = false, value = snapshot('stage_backlog');
    const {model, state, calls} = await store(t, call => {
        if (call.path === api) return offline ? json({}, 503) : json(value);
        assert.equal(call.path, socket);
        assert.equal(call.method, 'POST');
        assert.equal(call.headers.get('authorization'), 'Bearer fixture-session');
        return controllerDown ? json({}, 503) : json(controller(value));
    });
    await model.refresh();
    assert.equal(calls[0].path, api);
    assert.equal(calls.length, 1);
    offline = true;
    value = snapshot('stop_source');
    await model.refresh();
    assert.equal(calls.at(-1).path, socket);
    assert.deepEqual(JSON.parse(calls.at(-1).body), {});
    assert.equal(state().status.operation.stage, 'stop_source');
    controllerDown = true;
    await model.refresh();
    assert.equal(state().status.operation.stage, 'stop_source');
    assert.equal(state().phase, 'in_progress');
    offline = false;
    value = snapshot('activate');
    model.start();
    for (let n = 0; n < 100 && state().status.operation.stage !== 'activate'; n++) await delay(15);
    assert.equal(state().status.effective_runtime, 'go');
    assert.equal(state().phase, 'in_progress');
    value = snapshot('completed');
    for (let n = 0; n < 100 && state().phase !== 'completed'; n++) await delay(15);
    assert.equal(state().phase, 'completed');
    model.stop();
    const count = calls.length;
    await delay(70);
    assert.equal(calls.length, count);
});

test('store_denied', async t => {
    let denied = false;
    const {model, state, calls} = await store(t, () => denied ? json({}, 403) : json(snapshot('settle')));
    await model.refresh();
    assert.equal(state().status.operation.stage, 'settle');
    denied = true;
    await model.refresh();
    assert.equal(state().status, null, 'Revoked access must clear the private operation');
    assert.deepEqual(calls.map(call => call.path), [api, api]);
});
