import test from 'node:test';
import assert from 'node:assert/strict';
import {locationLabel, remoteStatus, recoveryKeyLine, keyFile} from './remoteBackup.js';

const KEY = 'AGE-SECRET-KEY-1' + 'Q'.repeat(58);

test('location labels default to local', () => {
    assert.equal(locationLabel({location: 'both'}), 'Locale e remoto');
    assert.equal(locationLabel({location: 'remote'}), 'Solo remoto');
    assert.equal(locationLabel({}), 'Solo locale');
});

test('status line follows setup, uploads and failures', () => {
    const now = Date.parse('2026-11-09T12:00:00Z');
    assert.equal(remoteStatus(null).text, 'Non configurata');
    assert.equal(remoteStatus({configured: true, enabled: false}).text, 'Disattivata');
    assert.deepEqual(remoteStatus({configured: true, enabled: true, uploading: 'a'}), {text: 'Copia in corso…', tone: 'info', pill: 'In corso'});
    const ok = remoteStatus({configured: true, enabled: true, last_upload: {at: '2026-11-09T10:00:00Z'}}, now);
    assert.equal(ok.text, 'Ultima copia 2 ore fa');
    assert.equal(ok.pill, '');
    assert.equal(remoteStatus({configured: true, enabled: true, last_failure: {error: 'x'}}, now).pill, 'Copia non riuscita');
    assert.equal(remoteStatus({configured: true, enabled: true, status: 'error'}, now).text, 'Nessuna copia ancora');
});

test('the key is found in a pasted key file', () => {
    assert.equal(recoveryKeyLine(`# created\n# public key: age1x\n${KEY.toLowerCase()}\n`), KEY);
    assert.equal(recoveryKeyLine('nothing'), '');
    const file = keyFile(KEY, 'abcd1234', new Date('2026-11-09T00:00:00Z'));
    assert.match(file, /ID chiave: abcd1234/);
    assert.equal(recoveryKeyLine(file), KEY);
});

test('a fetch from the remote copy has its own labels', async () => {
    const {operationTitle, operationProgress, historyTitle, recentOutcome} = await import('./operationLabels.js');
    assert.equal(operationTitle({kind: 'fetch'}), 'Recupero dalla copia remota');
    assert.deepEqual(operationProgress({kind: 'fetch', stage: 'downloading'}), {label: 'Download e decifratura', step: 2, total: 2});
    assert.equal(historyTitle({kind: 'fetch'}), 'Recupero da remoto');
    const at = new Date().toISOString();
    assert.equal(recentOutcome([{id: 'f', kind: 'fetch', stage: 'completed', updated_at: at}]).title, 'Backup recuperato sul server');
});
