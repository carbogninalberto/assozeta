import test from 'node:test';
import assert from 'node:assert/strict';
import {createRuntimeStore} from './runtimeSwitchStore.js';
import {setTimeout as delay} from 'node:timers/promises';
import {get} from 'svelte/store';
import {controller, snapshot} from '../../scripts/p5-runtime-fixtures.mjs';

const api = '/api/instance/admin/runtime';
const socket = '/instance-update-status';
const json = (value, status = 200) => Response.json(value, {status});

async function store(t, handler) {
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
    void model.submit('go', 'fixture');
    await delay(0);
    assert.equal(calls.filter(call => call.method === 'POST').length, 1, 'Only one pending submission');
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
    void model.submit('go', 'fixture');
    await delay(0);
    assert.equal(calls.filter(call => call.method === 'POST').length, 1, 'Follow the active operation without resubmitting');
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

for (const receipt of ['noop', 'lost']) {
    test('completion_' + receipt, async () => {
        let submitted = false;
        const done = snapshot('completed');
        if (receipt === 'noop') {
            done.operation.previous_runtime = 'go';
            done.operation.expected_generation = done.operation.source_generation = 8;
            done.operation.outcome = 'noop';
            done.operation.committed_generation = null;
        }
        const {createRuntimeStore} = await import('./runtimeSwitchStore.js');
        const model = createRuntimeStore({readToken: () => 'fixture', fetcher: async (url, init) => {
            if (init.method === 'GET') return Response.json(submitted ? done : receipt === 'noop' ? snapshot(null, {source: 'go', generation: 8}) : snapshot());
            submitted = true;
            if (receipt === 'lost') throw new TypeError('Fixture connection closed');
            return Response.json({operation: done.operation, authority: {runtime: 'go', generation: 8}, replayed: false});
        }});
        await model.refresh();
        await model.submit('go', 'fixture');
        assert.equal(get(model).phase, 'completed');
        assert.equal(get(model).error, '', 'The completed server operation must not be reported as unstarted');
    });
}

test('submit_reconciliation', async () => {
    const polling = Promise.withResolvers(), release = Promise.withResolvers();
    const queued = snapshot('queued');
    let posts = 0;
    const {createRuntimeStore} = await import('./runtimeSwitchStore.js');
    const model = createRuntimeStore({readToken: () => 'fixture', fetcher: async (url, init) => {
        if (init.method === 'POST') {
            posts++;
            return Response.json({operation: queued.active, replayed: false}, {status: 202});
        }
        if (!posts) return Response.json(snapshot());
        polling.resolve();
        await release.promise;
        return Response.json(queued);
    }});
    await model.refresh();
    const first = model.submit('go', 'fixture');
    await polling.promise;
    const second = model.submit('go', 'fixture');
    release.resolve();
    await Promise.all([first, second]);
    assert.equal(posts, 1, 'Only one submission until the accepted operation is reconciled');
});


test('remote_status_origin', async () => {
    const origin = 'https://custom.example.test';
    const endpoint = origin + '/api/instance/admin/runtime';
    const paths = [];
    const {createRuntimeStore} = await import('./runtimeSwitchStore.js');
    const model = createRuntimeStore({endpoint, readToken: () => 'fixture', fetcher: async (url, init) => {
        paths.push([url, init.method]);
        if (init.method === 'GET') return new Response(null, {status: 503});
        return Response.json({protocol: 1, is_owner: true, history: [], runtime: snapshot('settle')});
    }});
    await model.refresh();
    assert.deepEqual(paths, [[endpoint, 'GET'], [origin + '/instance-update-status', 'POST']], 'Independent status must use the configured API origin');
    assert.equal(get(model).phase, 'in_progress');
});

test('unmatched_lost_reply', async t => {
    for (const [target, generation] of [['django', 7], ['go', 9]]) {
        let submitted = false;
        const observed = snapshot('completed', {source: target === 'go' ? 'django' : 'go', generation});
        const {model, state, calls} = await store(t, request => {
            if (request.method === 'GET') return json(submitted ? observed : snapshot());
            submitted = true;
            throw new TypeError('Fixture connection closed');
        });
        await model.refresh();
        await model.submit('go', 'fixture');
        assert.match(state().error, /Nessuna risposta/i, 'A different target or source generation does not confirm the request');
        assert.equal(calls.filter(request => request.method === 'POST').length, 1);
    }
});

test('password_confirmation_responses', async t => {
    for (const [code, message, remainsOpen] of [
        [400, 'Password non corretta.', true],
        [400, 'Richiesta non valida.', false],
        [429, 'Troppi tentativi. Riprova tra qualche minuto.', false],
        [400, 'Servizio password temporaneamente non disponibile.', false],
    ]) {
        const {model} = await store(t, request => request.method === 'GET' ? json(snapshot()) : json({error: message}, code));
        await model.refresh();
        const result = await model.submit('go', 'fixture');
        assert.equal(result.error, message);
        assert.equal(result.done, !remainsOpen, 'Only the specific wrong-password response requests another password');
    }
});
