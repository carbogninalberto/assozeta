import assert from 'node:assert/strict';
import {test} from 'node:test';
import {operationTitle, operationProgress, historyTitle, historyTone, recentOutcome} from './operationLabels.js';

const now = Date.parse('2026-11-09T12:00:00Z');

test('titles name the operation in plain words', () => {
    assert.equal(operationTitle({target_version: 'v1.0.34'}), 'Aggiornamento a v1.0.34');
    assert.equal(operationTitle({kind: 'restart'}), 'Riavvio dell’applicazione');
    assert.equal(historyTitle({source_version: '1.0.32', target_version: '1.0.33'}), '1.0.32 → 1.0.33');
    assert.equal(historyTitle({kind: 'restart', target_version: '1.0.33'}), 'Riavvio');
    assert.equal(historyTitle({kind: 'backup', origin: 'scheduled'}), 'Backup automatico');
    assert.equal(historyTitle({kind: 'backup', origin: 'manual'}), 'Backup manuale');
});

test('progress counts the steps of each kind of operation', () => {
    assert.deepEqual(operationProgress({stage: 'downloading'}), {label: 'Download della versione', step: 3, total: 6});
    assert.deepEqual(operationProgress({kind: 'restart', stage: 'health_check'}), {label: 'Verifica finale', step: 3, total: 3});
    assert.deepEqual(operationProgress({stage: 'queued'}), {label: 'In coda', step: 0, total: 6});
    assert.equal(operationProgress({stage: 'unknown_stage'}).total, 0);
});

test('history tones follow the outcome', () => {
    assert.equal(historyTone({stage: 'completed'}), 'ok');
    assert.equal(historyTone({stage: 'failed'}), 'bad');
    assert.equal(historyTone({stage: 'recovery_required'}), 'warn');
});

test('a just-finished operation is reported once, an old one is not', () => {
    const fresh = {id: 'a', stage: 'completed', target_version: '1.0.33', updated_at: new Date(now - 60000).toISOString()};
    assert.deepEqual(recentOutcome([fresh], now), {id: 'a', ok: true, title: 'Aggiornamento completato · 1.0.33', detail: ''});
    assert.equal(recentOutcome([{...fresh, updated_at: new Date(now - 3600000).toISOString()}], now), null);
    const failed = {id: 'b', kind: 'restart', stage: 'failed', error: 'Servizi non pronti.', updated_at: new Date(now).toISOString()};
    assert.deepEqual(recentOutcome([failed], now), {id: 'b', ok: false, title: 'Riavvio non riuscito', detail: 'Servizi non pronti.'});
    assert.equal(recentOutcome([], now), null);
});
