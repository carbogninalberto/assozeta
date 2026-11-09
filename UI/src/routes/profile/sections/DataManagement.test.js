import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';

const read = name => readFileSync(new URL(`./${name}`, import.meta.url), 'utf8');

test('exports restore an active export as a progress row without polling', () => {
    const source = read('DataExports.svelte');
    assert.match(source, /notificationService\.syncActiveExport\(\)/);
    assert.match(source, /class="export-running"/);
    assert.match(source, /\{visibleExportLabel\}/);
    assert.match(source, /\{visibleExportPercent\}%/);
    assert.match(source, /role="progressbar"/);
    assert.match(source, /\{#if startingExport \|\| exporting\}/);
    assert.match(source, /handledTerminalTasks\.has\(exportTaskId\)/);
    assert.match(source, /handleTerminalExport\(exportStatus, announceTerminal\)/);
    assert.match(source, /if \(shouldAnnounce\) toast\.success/);
    assert.match(source, /status === 'SUCCESS'[\s\S]*await loadExports\(\)/);
    assert.doesNotMatch(source, /setInterval/);
    assert.doesNotMatch(source, /EXPORT\.STATUS/);
});

test('the export limit disables the start button with its reason', () => {
    const source = read('DataExports.svelte');
    assert.match(source, /const LIMIT = 3;/);
    assert.match(source, /atLimit \? `Puoi tenere \$\{LIMIT\} export: eliminane uno\.`/);
    assert.match(source, /disabled=\{[^}]*!!startReason\}/);
    assert.match(source, /class="si-reason start-reason">\{startReason\}/);
});

test('exports keep the same endpoints and ask before deleting', () => {
    const source = read('DataExports.svelte');
    assert.match(source, /apiFetch\(EXPORT\.LIST/);
    assert.match(source, /apiFetch\(EXPORT\.START, \{method: 'POST'\}\)/);
    assert.match(source, /apiFetch\(EXPORT\.DELETE, \{\s*method: 'DELETE'/);
    assert.match(source, /\?download=true&download_token=/);
    assert.match(source, /<InstanceDialog open=\{!!removing\} title="Eliminare l’export\?"/);
    assert.match(source, /data-autofocus[^>]*>Annulla</);
});

test('Gestione Dati on Bakney is the export list; self-hosted gets it in the Backup tab', () => {
    assert.match(read('DataManagement.svelte'), /<DataExports \{instanceOwner\} \{accessPending\}/);
    assert.doesNotMatch(read('DataManagement.svelte'), /DataRestore|DataImport/);
    assert.match(read('InstanceBackups.svelte'), /<DataTransfer \/>/);
    const transfer = read('DataTransfer.svelte');
    assert.match(transfer, /title="Trasferisci dati"/);
    assert.match(transfer, /variant="drawer"/);
    assert.match(transfer, /<DataImport bind:busy=\{importBusy\} \/>/);
});

test('the import keeps the typed confirmation and the media consent', () => {
    const source = read('DataImport.svelte');
    assert.match(source, /confirmation === 'RIPRISTINA' && \(!selected\.preview\.missing_media \|\| allowMissing\)/);
    assert.match(source, /body: JSON\.stringify\(\{confirmation, allow_missing_media: allowMissing\}\)/);
    assert.doesNotMatch(source, /<details|InstanceAlert|InstanceAccordion/);
});

test('recovered terminal exports are synchronized without a completion announcement', () => {
    const source = readFileSync(new URL('../../../utils/NotificationService.js', import.meta.url), 'utf8');
    assert.match(source, /exportProgress\.applyCompleted\(terminal, false\)/);
    assert.match(source, /exportProgress\.applyFailed\(terminal, false\)/);
});
