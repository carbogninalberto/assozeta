import test from 'node:test';
import assert from 'node:assert/strict';
import {uploadProblem} from './backupUpload.js';

test('upload accepts only non-empty .tar.gz archives that fit on the disk', () => {
    assert.equal(uploadProblem(null), '');
    assert.equal(uploadProblem({name: 'assozeta-20261106T200459Z.tar.gz', size: 10}, 100), '');
    assert.equal(uploadProblem({name: 'ASSOZETA.TAR.GZ', size: 10}), '');
    assert.match(uploadProblem({name: 'export.zip', size: 10}), /\.tar\.gz/);
    assert.match(uploadProblem({name: 'a.tar.gz', size: 0}), /vuoto/);
    assert.match(uploadProblem({name: 'a.tar.gz', size: 200}, 100), /Spazio/);
});

test('a rejected upload shows the server message, also for a 413 without a body', async () => {
    const {uploadError, TOO_LARGE} = await import('./backupUpload.js');
    const message = 'Il file supera la dimensione massima consentita per il caricamento.';
    assert.equal(uploadError(413, {error: message, max_bytes: 1024}), message);
    assert.equal(uploadError(413, {}), TOO_LARGE);
    assert.equal(uploadError(400, {detail: 'Archivio non valido.'}), 'Archivio non valido.');
    assert.equal(uploadError(502, {}), 'Caricamento non riuscito. Riprova.');
});
