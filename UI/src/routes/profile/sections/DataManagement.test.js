import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';

test('Data Management restores active exports as a history row without polling', () => {
    const source = readFileSync(new URL('./DataManagement.svelte', import.meta.url), 'utf8');
    assert.match(source, /notificationService\.syncActiveExport\(\)/);
    assert.match(source, /class="export-running-row/);
    assert.match(source, /\{visibleExportLabel\}/);
    assert.match(source, /\{visibleExportPercent\}%/);
    assert.match(source, /role="progressbar"/);
    assert.match(source, /exports\.length === 0 && !startingExport && !exporting/);
    assert.match(source, /handledTerminalTasks\.has\(exportTaskId\)/);
    assert.match(source, /handleTerminalExport\(exportStatus, announceTerminal\)/);
    assert.match(source, /if \(shouldAnnounce\) toast\.success/);
    assert.match(source, /status === 'SUCCESS'[\s\S]*await loadExports\(\)/);
    assert.doesNotMatch(source, /setInterval/);
    assert.doesNotMatch(source, /EXPORT\.STATUS/);
});

test('Data Management hides the start button at the limit and renders a warning alert', () => {
    const source = readFileSync(new URL('./DataManagement.svelte', import.meta.url), 'utf8');
    assert.match(source, /\{#if exports\.length >= 3\}/);
    assert.match(source, /alert alert-custom alert-light-warning/);
    assert.match(source, /export-limit-alert/);
    assert.doesNotMatch(source, /border-warning/);
    assert.match(source, /Hai raggiunto il limite di 3 export/);
    assert.match(source, /\{:else\}[\s\S]*Avvia Export/);
});

test('Data Management uses borderless, softened export states and spaced history', () => {
    const source = readFileSync(new URL('./DataManagement.svelte', import.meta.url), 'utf8');
    const runningRowClasses = source.match(/class="(export-running-row[^\"]*)"/)?.[1];
    assert.match(source, /form-group row mb-0 mt-8/);
    assert.ok(runningRowClasses);
    assert.match(runningRowClasses, /rounded-lg/);
    assert.doesNotMatch(runningRowClasses, /\bborder\b/);
    assert.match(source, /color-mix\(in srgb, var\(--primary/);
    assert.match(source, /color-mix\(in srgb, var\(--warning/);
    assert.match(source, /\.export-limit-alert[\s\S]*border: 0 !important/);
});

test('recovered terminal exports are synchronized without a completion announcement', () => {
    const source = readFileSync(new URL('../../../utils/NotificationService.js', import.meta.url), 'utf8');
    assert.match(source, /exportProgress\.applyCompleted\(terminal, false\)/);
    assert.match(source, /exportProgress\.applyFailed\(terminal, false\)/);
});
