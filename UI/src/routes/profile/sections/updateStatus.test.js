import assert from 'node:assert/strict';
import test from 'node:test';
import {createCompletionRefresh, mergeRunnerStatus, restartResult} from './updateStatus.js';

test('restart recovery matches the accepted request even when its POST response was lost', () => {
    const operation = {kind: 'restart', request_id: 'same-request', status: 'running', stage: 'restarting'};
    assert.equal(restartResult({available: true, active: operation}, 'same-request', 0, 10).phase, 'waiting');
    const completed = {...operation, status: 'succeeded', stage: 'completed', verified_at: 'now'};
    const status = {available: true, history: [completed]};
    assert.equal(restartResult(status, 'same-request', 0, 10).phase, 'ready');
    assert.equal(restartResult(status, 'other-request', 0, 10).phase, 'waiting');
    assert.equal(restartResult({...status, available: false}, 'same-request', 0, 10).phase, 'waiting');
    assert.equal(restartResult({available: true, history: [{...completed, verified_at: null}]}, 'same-request', 0, 10).phase, 'waiting');
});

test('restart failure and timeout are explicit and late verified recovery is accepted', () => {
    const operation = {kind: 'restart', request_id: 'request', status: 'failed', error: 'Failed', recovery: 'Inspect services'};
    const failed = restartResult({available: true, history: [operation]}, 'request', 0, 10);
    assert.equal(failed.phase, 'failed');
    assert.equal(failed.operation.recovery, 'Inspect services');
    assert.equal(restartResult({available: false}, 'request', 0, 600000).phase, 'timeout');
    assert.equal(restartResult({available: true, history: [{...operation, status: 'succeeded', stage: 'completed', verified_at: 'now'}]}, 'request', 0, 700000).phase, 'ready');
});

test('temporary runner outages retain the operation until authoritative status returns', () => {
    const operation = {id: 'durable-id', stage: 'migrating'};
    const previous = {available: true, active: operation, history: [operation]};
    const unavailable = {available: false, active: null, history: [], reason: 'Restarting'};
    const disconnected = mergeRunnerStatus(previous, unavailable);
    assert.equal(disconnected.available, false);
    assert.equal(disconnected.active.id, 'durable-id');
    assert.deepEqual(disconnected.history, previous.history);
    assert.equal(disconnected.reason, 'Restarting');

    const completed = {available: true, active: null, history: [{...operation, stage: 'completed'}]};
    assert.deepEqual(mergeRunnerStatus(disconnected, completed), completed);
});

test('completion refresh retries after a subsequent CLI operation takes the API down', async () => {
    const refresh = createCompletionRefresh();
    refresh.observe({active: {id: 'completed-job'}}, {available: true, active: null});
    let infoCalls = 0;
    let catalogCalls = 0;
    const loadInfo = async () => {
        if (++infoCalls === 1) throw new Error('CLI backup temporarily stopped the API');
    };
    const loadCatalog = async () => { catalogCalls += 1; return true; };
    await assert.rejects(refresh.run(loadInfo, loadCatalog));
    refresh.observe({active: null}, {available: true, active: null});
    await refresh.run(loadInfo, loadCatalog);
    await refresh.run(loadInfo, loadCatalog);
    assert.equal(infoCalls, 2);
    assert.equal(catalogCalls, 1);
});

test('failed catalog refresh remains pending after installed-version refresh succeeds', async () => {
    const refresh = createCompletionRefresh();
    refresh.observe({active: {id: 'completed-job'}}, {available: true, active: null});
    let attempts = 0;
    const loadCatalog = async () => ++attempts > 1;
    await refresh.run(async () => {}, loadCatalog);
    await refresh.run(async () => {}, loadCatalog);
    await refresh.run(async () => {}, loadCatalog);
    assert.equal(attempts, 2);
});
