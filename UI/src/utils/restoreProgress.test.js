import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mergeRestoreProgress} from './restoreProgress.js';

test('restore progress rejects unrelated, stale and rolled-back attempt events', () => {
    const op = {id:'one', state:'running', attempts:2};
    const current = {operation_id:'one', attempt:2, sequence:3};
    for (const event of [{operation_id:'other', attempt:2, sequence:5},
        {operation_id:'one', attempt:1, sequence:100}, {operation_id:'one', attempt:2, sequence:2}]) {
        assert.equal(mergeRestoreProgress(current, event, op), current);
    }
    const next = {...current, sequence:4};
    assert.equal(mergeRestoreProgress(current, next, op), next);
    assert.equal(mergeRestoreProgress(current, next, {...op,state:'completed'}), current);
    const retry = {...current, attempt:3, sequence:1};
    assert.equal(mergeRestoreProgress(current, retry, op), retry);
});

test('notification socket dispatches restore progress without treating it as export', async () => {
    const {default: NotificationWebSocket} = await import('./NotificationWebSocket.js');
    const ws = new NotificationWebSocket();
    let received;
    ws.onRestoreProgress = value => { received = value; };
    ws.onExportProgress = () => assert.fail('restore event routed as export');
    const event = {type:'restore_progress', operation_id:'one', phase:'files'};
    ws.handleMessage(event);
    assert.equal(received, event);
});
