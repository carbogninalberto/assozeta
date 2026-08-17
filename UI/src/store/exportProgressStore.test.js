import test from 'node:test';
import assert from 'node:assert/strict';

import {exportProgress} from './exportProgressStore.js';

function currentState() {
    let state;
    const unsubscribe = exportProgress.subscribe(value => {
        state = value;
    });
    unsubscribe();
    return state;
}

test('export store rejects regressive, stale, and post-terminal progress', () => {
    exportProgress.reset();
    exportProgress.applyProgress({
        task_id: 'task-1',
        status: 'PROGRESS',
        updated_at: '2026-01-01T10:00:00Z',
        progress: {percent: 60, phase: 'file_retrieval'},
    });
    exportProgress.applyProgress({
        task_id: 'task-1',
        status: 'PROGRESS',
        updated_at: '2026-01-01T10:00:01Z',
        progress: {percent: 40, phase: 'model_serialization'},
    });
    assert.equal(currentState().progress.percent, 60);

    exportProgress.applyCompleted({
        task_id: 'task-1',
        updated_at: '2026-01-01T10:00:02Z',
        progress: {percent: 100, phase: 'completed'},
    });
    exportProgress.applyProgress({
        task_id: 'task-1',
        status: 'PROGRESS',
        updated_at: '2026-01-01T10:00:03Z',
        progress: {percent: 99, phase: 'storage_upload'},
    });
    assert.equal(currentState().status, 'SUCCESS');
    assert.equal(currentState().active, false);
});

test('export store rejects a delayed event from an older task', () => {
    exportProgress.reset();
    exportProgress.applyProgress({
        task_id: 'new-task',
        status: 'PROGRESS',
        updated_at: '2026-01-01T10:00:05Z',
        progress: {percent: 20, phase: 'model_serialization'},
    });
    exportProgress.applyCompleted({
        task_id: 'old-task',
        status: 'SUCCESS',
        updated_at: '2026-01-01T10:00:02Z',
        progress: {percent: 100, phase: 'completed'},
    });

    assert.equal(currentState().taskId, 'new-task');
    assert.equal(currentState().active, true);
});

test('terminal snapshots can be restored without announcing a new completion', () => {
    exportProgress.reset();
    exportProgress.applyCompleted(
        {
            task_id: 'recovered-task',
            updated_at: '2026-01-01T10:00:02Z',
            progress: {percent: 100, phase: 'completed'},
        },
        false
    );

    assert.equal(currentState().status, 'SUCCESS');
    assert.equal(currentState().announceTerminal, false);

    exportProgress.applyCompleted({
        task_id: 'live-task',
        updated_at: '2026-01-01T10:00:03Z',
        progress: {percent: 100, phase: 'completed'},
    });
    assert.equal(currentState().announceTerminal, true);
});
