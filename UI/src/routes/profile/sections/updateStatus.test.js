import assert from 'node:assert/strict';
import test from 'node:test';
import {createCompletionRefresh, mergeRunnerStatus} from './updateStatus.js';

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
