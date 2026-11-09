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
